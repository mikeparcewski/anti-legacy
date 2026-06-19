---
name: "anti-legacy:graph-validator"
description: >
  Validates the requirements graph against the wicked-estate code graph before the blueprint
  phase. Checks every requirement's legacy field resolves to a real program node in the graph
  (not a JCL step), finds uncaptured behavior-bearing nodes with business logic that need
  requirements, and flags duplicate program references. Blocks if errors are found; warns and
  requires human review if only gaps remain.
  Run after extraction/graph-translator, before blueprint.
  Use when: "validate requirements graph", "audit the graph", "check graph accuracy",
  "are we missing any programs", "graph audit before dev", "validate before blueprint".
---

# anti-legacy:graph-validator

Audits every `legacy` program reference in the requirements graph against the actual source tree. The three failure modes that corrupt every downstream phase are:

1. **JCL-not-program**: the `legacy` field names a JCL job wrapper, not the COBOL program that holds the logic.
2. **Misidentified program**: the named program exists but its actual business function doesn't match the requirement description (caught by spot-checking the program header/comments vs the requirement statement).
3. **Missing programs**: a source file with real business logic has no requirement mapping it.

All checks are packaged into a single core command:
```bash
python3 .anti-legacy/run.py graph_validator [--app APP] [--auto-fix] [--workspace WORKSPACE]
```

---

## The Four Validation Passes

The validator runs four passes in sequence. It reports **ERRORS** (blocking — status `BLOCKED`) and **GAPS** (uncovered programs with business logic — status `GAPS`).

### Pass 1: Legacy Field Existence and Type Check
For every active requirement, validates that its `legacy` field resolves to a real program in the `wicked-estate` graph. 
- JCL steps (kind `step`) are not allowed as legacy targets (triggering `JCL_NOT_PROGRAM`).
- If `--auto-fix` is enabled: JCL step references are auto-remapped to the executed COBOL program if it can be unambiguously extracted from the JCL source (`EXEC PGM=`).
- Duplicate legacy field references among active requirements are flagged as `DUPLICATE_LEGACY` errors.
- Legacy fields referencing non-existent symbols trigger `NOT_FOUND` warnings.

### Pass 2: Program Content Spot-Check
Performs a heuristic spot-check of the program source (first 50 lines / header comments) to ensure the stated requirement `description` does not contradict the program's actual logic:
- If a requirement implies an update (e.g. contains "consolidate", "update", "write", "post", "apply"), but the program header/source implies it is a read-only report/print utility (contains print/report keywords and lacks database/file update keywords), it flags a `CONTENT_MISMATCH` error.

### Pass 3: Uncaptured Program Inventory
Finds all behavior-bearing nodes in the `wicked-estate` graph that are NOT covered by any active requirement.
It classifies them into two categories:
- `NEEDS_REQUIREMENT`: Genuine gaps representing programs containing business logic (e.g., CICS online programs, DB2 entity tables, or modules with computation paragraphs).
- `UTILITY_OMIT`: Programs correctly omitted from requirements coverage (e.g., pure sleep/wait utilities or MQ/VSAM transport adapters).

Classifications are recorded in `.anti-legacy/validation/al_pass3.json`. You can manually adjust the classification or add domain suggestions in this file, and the script will preserve your edits on subsequent runs.

**Language-specific utility patterns.** The default classification patterns are COBOL/mainframe names (`COBSWAIT`, `MVSWAIT`, `^MQ.*`, `^VSAM.*`, `^SORT.*`, `^COPY.*`, etc.). For a Java, Python, Go, or C# target these defaults may classify legitimate business code as `UTILITY_OMIT`. Override them in `config.json` under the existing `coverage` key:

```json
{
  "coverage": {
    "utility_name_exact": [],
    "utility_name_patterns": [".*Util$", ".*Helper$", ".*Logger$", ".*Adapter$", ".*Config$"]
  }
}
```

If `coverage.utility_name_patterns` is absent, the COBOL defaults apply. Set it to `[]` to disable all pattern-based omission and rely only on kind-based classification (CICS/DB2 → always `NEEDS_REQUIREMENT`, everything else → heuristic fallback).

### Pass 4: Report Generation & Done-Gate
Compiles findings into:
1. `.anti-legacy/requirements/graph-validation-report.json` — machine-readable evidence.
2. `.anti-legacy/requirements/graph-validation-report.md` — human-readable summary.

---

## Step 1: Verify prerequisites

Before running the validator, make sure the `extraction` (or `graph-translate`) phase has completed:
```bash
python3 -c "
import json, sys
m = json.load(open('.anti-legacy/manifest.json'))
completed = m.get('phase', {}).get('completed', [])
if 'extraction' not in completed and 'graph-translate' not in completed:
    print('BLOCKED: extraction phase not complete. Run anti-legacy:extraction first.')
    sys.exit(1)
print('Prerequisites OK: extraction phase completed')
"
```

## Step 2: Run the Validator

Run the script from the workspace root:
```bash
python3 .anti-legacy/run.py graph_validator [--auto-fix]
```

## Step 3: Done-gate — BLOCK if ERRORS, WARN if GAPS only

The script enforces the done-gate exit status:
* If the report status is `BLOCKED` (Pass 1 or Pass 2 errors exist): the script exits **non-zero (1)**. You must fix the errors in `requirements_graph.json` and re-run.
* If the report status is `GAPS` (only uncaptured programs with business logic exist): the script exits **zero (0)** but registers the report as `draft`. GAPS require human review and sign-off at `GATE_1_DESIGN`.
* If the report status is `CLEAN` (no errors, no gaps): the script exits **zero (0)** and registers the report as `final`.

## Step 4: Register Artifacts
Upon completion, the script automatically:
1. Registers the artifacts `graph-validation-report` and `graph-validation-md` with `manifest`.

**GATE_1_DESIGN Checklist Note**:
Reviewer checklist item 3b requires that the `graph-validation-report` status is not `BLOCKED` and that every gap is either mapped to a requirement or explicitly justified/omitted.
