---
name: "anti-legacy:target-review"
description: >
  Run compiler and build integrity checks on the generated target codebase.
  Supports Java (Maven/Gradle/javac), Go, C# (.NET), Python, Kotlin, TypeScript.
  Auto-clears GATE_3_BUILD only if compilation passes AND the round-trip
  rule-coverage proof passes (rule_coverage >= 1.0, zero FAIL requirements).
  Use when: "check if it compiles", "run the build", "verify the generated code",
  "target review", "build integrity check".
---

# anti-legacy:target-review

Executes deterministic compilation checks on the generated target codebase,
then a round-trip rule-coverage proof comparing the target graph against the
requirements graph + blueprint, then RUNS the pre-build functional acceptance
tests (authored by `anti-legacy:functional-tests`) against the built target.
Produces a build-integrity evidence envelope, a functional-comparison report,
and a functional-test report. GATE_3_BUILD is auto-cleared without human sign-off
ONLY when ALL of: compilation passes, the round-trip proves every business rule
is implemented (`rule_coverage` >= 1.0, zero FAIL requirements), AND the
functional-acceptance run is `PASS` (an ERROR/unsupported-stack report is NOT a
pass). Compilation alone is insufficient. If the round-trip or the functional run
fails, swarm is re-dispatched for the gap instead of clearing the gate.

## Cross-Platform Notes

The verifier script calls the target's build tool — ensure the appropriate
compiler/toolchain is installed (`javac`, `go`, `dotnet`, `python3`, `tsc`).
The script auto-detects build files (pom.xml, gradlew, go.mod, .csproj).

## Config

```bash
python3 -c "import json; c=json.load(open('.anti-legacy/config.json')); print(c['target_stack'], c['target_path'])"
```

## Parameters

- **workspace** (optional): path to target codebase. Defaults to `target_path` from config.
- **stack** (optional): override target stack detection.

## Step 1: Check swarm completion

Read `.anti-legacy/task.md` and verify all tasks are ticked (`- [x]`):

```python
python3 -c "
import re
content = open('.anti-legacy/task.md').read()
unchecked = re.findall(r'- \[ \]', content)
if unchecked:
    print(f'WARNING: {len(unchecked)} tasks still uncompleted in task.md')
    print('Run anti-legacy:swarm to complete them first, or proceed anyway to get a partial build result.')
else:
    print('All tasks complete ✓')
"
```

This is a warning, not a hard block — partial builds are valid for incremental review.

## Step 2: Run the target verifiers & validators

```bash
python3 .anti-legacy/run.py validator_discovery run \
  --gate GATE_3_BUILD \
  --workspace {target_path} \
  --config .anti-legacy/config.json
```

Exit code 0 = all required checks passed. Non-zero = compilation or required validators failed.

## Step 3: Surface warnings or errors

Read the generated JSON files in `.anti-legacy/evidence/`:
- `build-integrity.json`
- `code-quality.json`
- `security-scan.json`

If `build-integrity.json` status is `FAIL`, check `evidence.stderr_snippet` and display build compilation errors to the user.
If any optional tool is missing (e.g. `flake8` or `bandit`), status will be logged as `WARNING`. Inform the user how to install the missing tools to improve validation coverage.

Do NOT halt on failure — record the validation evidence files and let the user decide.

## Step 4: Round-trip rule-coverage proof (BLOCKING)

Compilation passing is NOT sufficient to clear `GATE_3_BUILD`. Before clearing the
gate you MUST prove the target actually IMPLEMENTS every business rule the
requirements graph demands. Scan the target Java tree to emit a target graph, then
compare it round-trip against the requirements graph + blueprint:

```bash
python3 .anti-legacy/run.py generate_target_graph \
  --workspace {target_path} \
  --output .anti-legacy/target_graph.json

python3 .anti-legacy/run.py compare_graphs \
  --requirements-graph .anti-legacy/requirements/requirements_graph.json \
  --blueprint .anti-legacy/requirements/blueprint.json \
  --target-graph .anti-legacy/target_graph.json \
  --report .anti-legacy/evidence/functional_comparison_report.md
```

`compare_graphs` writes both `functional_comparison_report.md` and
`functional_comparison_report.json`. Inspect its exit code:

- **Exit code 0** (zero FAIL requirements AND `rule_coverage` >= 1.0): the round-trip
  passes. Proceed to Step 5 to clear `GATE_3_BUILD`.
- **Non-zero exit** (any FAIL requirement OR `rule_coverage` < 1.0): the target is
  missing rule coverage. Do **NOT** record `GATE_3_BUILD`. Surface the uncovered
  requirements/rules from `functional_comparison_report.json` to the user and
  **re-dispatch `anti-legacy:swarm`** for the uncovered rules, then re-run
  target-review from Step 1 once the swarm closes the gap. The uncovered findings
  also feed `GATE_3B_SEMANTIC`.

Register the round-trip artifacts:

```bash
python3 .anti-legacy/run.py manifest register target-graph \
  --path target_graph.json \
  --format json \
  --produced-by anti-legacy:target-review \
  --status draft

python3 .anti-legacy/run.py manifest register functional-comparison-report \
  --path evidence/functional_comparison_report.json \
  --format json \
  --produced-by anti-legacy:target-review \
  --status final

python3 .anti-legacy/run.py manifest register functional-comparison-report-md \
  --path evidence/functional_comparison_report.md \
  --format markdown \
  --produced-by anti-legacy:target-review \
  --status final
```

## Step 4b: Functional acceptance execution (BLOCKING)

The pre-build acceptance tests authored by `anti-legacy:functional-tests` (one
scenario per contract) are EXECUTED here against the BUILT target. This is the
post-build half of the functional-acceptance cycle and is independent of the
round-trip rule-coverage proof: round-trip proves the rules are *present* in the
target graph; this proves the scenarios actually *pass* when run.

```bash
python3 .anti-legacy/run.py test_runner \
  --workspace {target_path} \
  --stack {target_stack} \
  --contracts .anti-legacy/contracts \
  --report .anti-legacy/evidence/functional-test-report.json
```

For a Java target the runner generates a JUnit test per scenario, runs
`mvn -Dtest=... test` against the built target, and parses the Surefire results
into a per-scenario pass/fail. The report's overall `status` is:

- **PASS** — every scenario passed. Proceed.
- **FAIL** — a scenario assertion failed against the built target. Do NOT clear
  the gate; surface the failing `req_id`/scenario and re-dispatch `anti-legacy:swarm`.
- **ERROR** — the runner could not execute (unsupported stack / missing Maven or
  JDK / missing target class). This is NOT a pass. Fix the toolchain or the build,
  then re-run. An ERROR never clears the gate — there is no false-green path.

Register the report:

```bash
python3 .anti-legacy/run.py manifest register functional-test-report \
  --path evidence/functional-test-report.json \
  --format json \
  --produced-by anti-legacy:target-review \
  --status final
```

## Step 5: Auto-clear GATE_3_BUILD if passing

**Scope caveat**: `GATE_3_BUILD` proves the code COMPILES and the named classes
EXIST; compilation ALONE is insufficient to clear the gate. The gate is only
recorded after BOTH `build-integrity.json` status is `PASS` AND the Step 4
round-trip passes (zero FAIL requirements, `rule_coverage` >= 1.0). The round-trip
is the rule-coverage check in `compare_graphs.py`
(`functional_comparison_report.json`) plus `GATE_3B_SEMANTIC` that proves
`business_rules`/`validations`/`error_paths` are implemented.

**Done-gate (BLOCKING)** — assert build integrity PASS AND round-trip pass AND the
post-build functional-acceptance run PASS before recording the gate. If this
assertion FAILS, do NOT run the `gate` command below, do NOT run
`register --status final`, and do NOT run `advance`; surface the specific gap to
the user (and re-dispatch swarm per Step 4 / Step 4b) and stop. The
gate/register/advance calls are CONDITIONAL on this assertion passing. The
functional-test-report MUST exist and be `PASS` — a missing report, a `FAIL`, or
an `ERROR` (unsupported stack / missing toolchain) all BLOCK; there is no
false-green path:

```bash
python3 -c "
import json, os, sys
bi = json.load(open('.anti-legacy/evidence/build-integrity.json'))
fc = json.load(open('.anti-legacy/evidence/functional_comparison_report.json'))
agg = fc.get('aggregate', fc)
fails = agg.get('fail_count', agg.get('fail', 0))
cov = agg.get('rule_coverage', 0.0)
ftr_path = '.anti-legacy/evidence/functional-test-report.json'
if not os.path.exists(ftr_path):
    sys.stdout.write('BLOCKED: functional-test-report.json missing (run Step 4b)\n'); sys.exit(1)
ftr = json.load(open(ftr_path))
ftr_status = str(ftr.get('status','')).upper()
ok = (str(bi.get('status','')).upper() == 'PASS') and (int(fails) == 0) and (float(cov) >= 1.0) and (ftr_status == 'PASS')
sys.stdout.write('OK\n' if ok else 'BLOCKED: build_status=%s fail_count=%s rule_coverage=%s functional_status=%s\n' % (bi.get('status'), fails, cov, ftr_status))
sys.exit(0 if ok else 1)
"
```

If all required checks pass (validator exit code 0, build-integrity PASS, and the
round-trip done-gate assertion above exits 0):

```bash
python3 .anti-legacy/run.py manifest gate GATE_3_BUILD \
  --opinion passed \
  --evaluator "anti-legacy:target-review" \
  --rationale "Compilation, code quality, security checks passed AND round-trip rule-coverage proof (compare_graphs) passed with rule_coverage>=1.0 AND post-build functional-acceptance run passed" \
  --evidence "build-integrity,code-quality,security-scan,functional-comparison-report,functional-test-report"
```

## Step 6: Register artifacts and advance phase

Only reach this step if the Step 5 done-gate passed (build-integrity PASS AND
round-trip pass). Do NOT register `--status final` or advance otherwise.

```bash
python3 .anti-legacy/run.py manifest register build-integrity \
  --path evidence/build-integrity.json \
  --format json \
  --produced-by anti-legacy:target-review \
  --status {status}

python3 .anti-legacy/run.py manifest register code-quality \
  --path evidence/code-quality.json \
  --format json \
  --produced-by anti-legacy:target-review \
  --status {status}

python3 .anti-legacy/run.py manifest register security-scan \
  --path evidence/security-scan.json \
  --format json \
  --produced-by anti-legacy:target-review \
  --status {status}

python3 .anti-legacy/run.py manifest advance target-review
```

## Deliverable: `evidence/build-integrity.json` schema

The build-integrity evidence envelope written by `validator_discovery` (Step 2) and read
back here (Step 3) and by the gatekeeper. Shape:

```json
{
  "scope": "build",
  "phase": "validation",
  "claim": "target-build-integrity",
  "status": "PASS | WARNING | FAIL",
  "evidence": {
    "command": "<the compiler/build command run, e.g. mvn -q compile>",
    "exit_code": 0,
    "stdout_snippet": "<last 2000 chars of build stdout>",
    "stderr_snippet": "<last 2000 chars of build stderr — compilation errors land here>"
  }
}
```

The compiler/build command, exit code, and the captured stdout/stderr tails live
under the nested `evidence` object (this is exactly what `validator_discovery.py`'s
`_record_evidence` writes). There is no top-level `error_count`/`errors[]` array — build
failures surface as a `FAIL` `status` with the diagnostics carried in
`evidence.stderr_snippet`.

**Field-name / case convention (shared with the gatekeeper reader):** the gate-status
field is named `status` and its value is UPPERCASE — `PASS`, `WARNING`, or `FAIL`. Both
the gatekeeper and this skill (Step 5 done-gate) read it case-sensitively via
`str(bi.get('status','')).upper()`. Do not emit lowercase, mixed-case, or an alternate
field name.

Gate 3 auto-clears ONLY when `status: PASS` (or `WARNING` for optional-tool gaps — a
missing `flake8`/`bandit`-class tool, never a compilation failure). No exceptions:
`status: FAIL` never auto-clears, and a PASS `status` is necessary but not sufficient —
the Step 4 round-trip rule-coverage proof must also pass.

## Output

- `.anti-legacy/evidence/build-integrity.json` — compilation evidence envelope
- `.anti-legacy/evidence/code-quality.json` — code quality lint evidence envelope
- `.anti-legacy/evidence/security-scan.json` — security vulnerability scanning evidence envelope
- `.anti-legacy/target_graph.json` — target Java tree graph with implemented-rule anchors
- `.anti-legacy/evidence/functional_comparison_report.json` / `.md` — round-trip rule-coverage proof
- `.anti-legacy/evidence/functional-test-report.json` — post-build functional-acceptance run (per-scenario pass/fail against the built target)
- GATE_3_BUILD: auto-cleared ONLY on build-integrity PASS AND round-trip pass (rule_coverage >= 1.0, zero FAIL reqs) AND functional-test-report PASS
- Manifest: phase = `target-review`, artifacts `build-integrity`, `code-quality`, `security-scan`, `target-graph`, `functional-comparison-report`, and `functional-test-report` registered

**Next step**: If GATE_3_BUILD cleared → `anti-legacy:uat-crew` to run independent UAT validation.
If the round-trip FAILED (uncovered rules) → re-dispatch `anti-legacy:swarm` for the uncovered rules, then re-run target-review.
If the functional-acceptance run FAILED → fix the failing scenarios (re-dispatch `anti-legacy:swarm`) and re-run; if it ERRORed → install the toolchain (Maven/JDK) or fix the build, then re-run.
If GATE_3_BUILD failed on compilation → fix compilation/quality/security errors and re-run.
