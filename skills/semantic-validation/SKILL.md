---
name: "anti-legacy:semantic-validation"
description: >
  Deploy validator subagents to review the new and old code in context of dependency chains,
  determine if there are semantic/functional gaps, and record them back to the requirements graph.
  Produces semantic-validation-report. Requires GATE_3_BUILD to be cleared.
  Use when: "run semantic validation", "validate code semantics", "find gaps between old and new code",
  "review code by dependency chains", "update graph with gaps".
---

# anti-legacy:semantic-validation

Deploys validator subagents to verify the behavior and semantic equivalence of target code directly against the original legacy source code. Rather than verifying isolated nodes, the validation is organized around connected dependency chains to check end-to-end flow and interfaces. 

Any logical, mathematical, or flow discrepancy is recorded as a "semantic gap" and back-propagated into `.anti-legacy/requirements/requirements_graph.json`.

## Prerequisites

- Target code compiles and passes automated contract tests (GATE_3_BUILD is `passed`).

## Step 1: Verify GATE_3_BUILD is cleared

```bash
python3 -c "
import json, sys
m = json.load(open('.anti-legacy/manifest.json'))
g = m['gates']['GATE_3_BUILD']
if g['status'] != 'passed':
    print(f'BLOCKED: GATE_3_BUILD is {g[\"status\"]}. Run anti-legacy:target-review first.')
    sys.exit(1)
print('GATE_3_BUILD: cleared ✓')
"
```

## Step 2: Partition by connected dependency chains

Analyze the requirements graph and extract dependency chains (connected components):

```bash
python3 .anti-legacy/run.py semantic_validator list-chains
```

This groups requirements into validation slices (e.g. `Chain 1: REQ_REPO, REQ_SERVICE, REQ_API`).

## Step 3: Dispatch validator subagents

For each dependency chain, dispatch a `@validator` subagent:

```
@validator

## Semantic Validation Assignment — Chain: {chain_id}

You are an expert code auditor. Your task is to perform a side-by-side comparison of the original legacy source code and the newly generated target code for the following chain of requirements.

### Traversal Chain
{traversal_chain_list}

### Legacy Source Files
{legacy_files_paths}

### Target Source Files
{target_files_paths}

### Business and Validation Rules
{rules_and_validations_per_requirement}

### Instructions

Read both the legacy source files and target source files. For each requirement in the chain, perform a semantic and logic parity audit:
1. **Arithmetic Parity**: Verify that numeric calculations (precision, scaling, rounding mode) match exactly. Watch out for float division instead of decimal arithmetic.
2. **Boundary & Validation Rules**: Verify that all legacy validation logic (null checks, length limits, range constraints) is implemented correctly.
3. **Control Flow & Error Handling**: Verify that error branches, exceptions, transaction rollbacks, and default fallback states behave identically.
4. **Interface Contract**: Verify that the data passed between components in the dependency chain matches legacy semantics.

### Reporting Gaps

If you identify any discrepancy, logic omission, or incorrect type usage, you must record it as a semantic gap.
For each gap found, run the following command to record it:

```bash
python3 .anti-legacy/run.py semantic_validator record-gap \
  --req-id "{req_id}" \
  --gap-id "{gap_id}" \
  --severity "HIGH|MEDIUM|LOW" \
  --description "{description of discrepancy}" \
  --legacy-loc "{filename:line_range}" \
  --target-loc "{filename:line_range}" \
  --remediation "{proposed fix}"
```

`record-gap` writes the gap **nested per-requirement** at
`requirements_graph.json -> domains[*].requirements[{req_id}].semantic_gaps[]`
with keys `{id, severity, description, legacy_location, target_location, remediation, detected_at}`.
A freshly recorded gap carries NO `status`/`resolved` field, so it is treated as
**unresolved** by default. `GATE_3B_SEMANTIC` reads these nested gaps and BLOCKS when any
gap with severity `HIGH`/`CRITICAL` is still unresolved. A reviewer marks a gap resolved
(before clearing GATE_3B) via:

```bash
python3 .anti-legacy/run.py semantic_validator resolve-gap \
  --req-id "{req_id}" \
  --gap-id "{gap_id}"
```

If no gaps are found, do not run the command.
```

For CLIs that do not support `@agent` dispatch, run inline — adopt the validator persona yourself, verify the code side-by-side, and record gaps using the command.

## Step 4: Generate validation reports

Re-compile the validation evidence and human-readable report:

```bash
python3 .anti-legacy/run.py semantic_validator generate-report
```

Because `record-gap` mutated `requirements/requirements_graph.json` (nested
`semantic_gaps`), RE-REGISTER the requirements graph so `manifest check` stays green
(its recorded hash must reflect the new content):

```bash
python3 .anti-legacy/run.py manifest register requirements-graph \
  --path requirements/requirements_graph.json \
  --format json \
  --produced-by anti-legacy:semantic-validation \
  --status draft
```

## Step 5: Register artifacts in manifest

**Done-gate (assert before registering/advancing):** the JSON validation evidence MUST
exist. If this assertion FAILS, do NOT run `register --status final`, do NOT advance, and
do NOT prompt for GATE_3B — surface the gap to the user and stop (they may retry/fix).
`register --status final` and `advance` are CONDITIONAL on this assertion passing.

```bash
python3 -c "
import os, sys
p = '.anti-legacy/evidence/semantic-validation-report.json'
if not os.path.isfile(p):
    sys.stderr.write('BLOCKED: %s missing — re-run generate-report before registering.\n' % p)
    sys.exit(1)
print('semantic-validation-report.json: present')
sys.exit(0)
"
```

Only if the assertion passes, register the evidence artifacts:

```bash
python3 .anti-legacy/run.py manifest register semantic-validation-report \
  --path evidence/semantic-validation-report.json \
  --format json \
  --produced-by anti-legacy:semantic-validation \
  --status final \
  --depends-on build-integrity

python3 .anti-legacy/run.py manifest register semantic-validation-markdown \
  --path evidence/semantic_validation_report.md \
  --format markdown \
  --produced-by anti-legacy:semantic-validation \
  --status final \
  --depends-on build-integrity
```

## Step 6: Advance phase and prompt for GATE_3B sign-off

Advance the pipeline (the `advance` target literal `semantic-validation` is a legal phase
enum value; only run this once the Step 5 done-gate assertion has passed):

```bash
python3 .anti-legacy/run.py manifest advance semantic-validation
```

Before prompting the Tech Lead to sign off, check there are no **unresolved HIGH/CRITICAL**
semantic gaps. GATE_3B_SEMANTIC will BLOCK while any such gap remains unresolved
(`status` not `resolved` and `resolved` not `true`), so resolve or downgrade them first via
`semantic_validator resolve-gap` (Step 3):

```bash
python3 -c "
import json, sys
m = json.load(open('.anti-legacy/requirements/requirements_graph.json'))
blocking = []
for d in m.get('domains', {}).values():
    for req_id, req in (d.get('requirements', {}) or {}).items():
        for gap in (req.get('semantic_gaps', []) or []):
            sev = str(gap.get('severity', '')).upper()
            resolved = str(gap.get('status', '')).lower() == 'resolved' or gap.get('resolved') is True
            if sev in ('HIGH', 'CRITICAL') and not resolved:
                blocking.append('[%s] %s on %s: %s' % (sev, gap.get('id'), req_id, gap.get('description')))
if blocking:
    sys.stderr.write('UNRESOLVED HIGH/CRITICAL gaps block GATE_3B_SEMANTIC:\n')
    for b in blocking:
        sys.stderr.write('  - %s\n' % b)
    sys.exit(1)
print('No unresolved HIGH/CRITICAL gaps — GATE_3B_SEMANTIC may be signed off.')
sys.exit(0)
"
```

If that assertion FAILS, do NOT prompt for sign-off; surface the blocking gaps to the user
and stop until they are resolved (via `resolve-gap`) or downgraded.

Only when no unresolved HIGH/CRITICAL gaps remain, prompt the Tech Lead:
- Gaps summary is at `.anti-legacy/evidence/semantic_validation_report.md`
- **Pipeline paused at GATE_3B_SEMANTIC**
- Sign off using:
  ```bash
  python3 .anti-legacy/run.py manifest gate GATE_3B_SEMANTIC \
    --opinion passed \
    --evaluator "{tech-lead-name}" \
    --rationale "Reviewed semantic validation report. Gaps are resolved or accepted." \
    --evidence "semantic-validation-report"
  ```

## Output

- `.anti-legacy/requirements/requirements_graph.json` — updated with nested per-requirement
  `semantic_gaps` details (`domains[*].requirements[*].semantic_gaps[]`; re-registered as the
  `requirements-graph` artifact so `manifest check` stays green)
- `.anti-legacy/evidence/semantic-validation-report.json` — JSON validation evidence
- `.anti-legacy/evidence/semantic_validation_report.md` — human-readable validation checklist
