---
name: "anti-legacy:uat-crew"
description: >
  Spawn independent UAT reviewer subagents to validate target code behavior against
  the test contracts. UAT subagents have read-only access only — they cannot modify code.
  Each subagent reviews one domain's requirements against contracts and outputs PASS/FAIL
  verdicts.  Requires GATE_3_BUILD to be cleared.
  Use when: "run UAT", "validate the generated code", "check against requirements",
  "start the UAT crew", "independent review".
---

# anti-legacy:uat-crew

Spawns read-only UAT reviewer subagents that independently validate the generated
target code against the test contracts. They receive no developer context — they
see only the target code, the test contract, and the business rules.

Independence is enforced: the UAT evaluator must be a different role from the
developer who built the code.

> **Gate independence (machine-enforced)**: the `--evaluator` you pass to
> `gate GATE_4_UAT` MUST differ from `roles.architect` in the manifest (now
> populated by `anti-legacy:setup`). gatekeeper hard-fails GATE_4_UAT when the
> UAT evaluator equals the architect/developer role — pass a distinct UAT-Lead
> identity that is independent of the dev team.

## Cross-Platform Notes

Subagent dispatch uses the host-integrated agent runtime.

## Parameters

- **domain** (optional): restrict to a single domain. Defaults to all domains.
- **req_id** (optional): run UAT for a single requirement node.

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

## Step 2: Enumerate UAT targets

Read `.anti-legacy/requirements/requirements_graph.json` and list all requirement
nodes across all domains (or the specified domain/req_id).

For each node, locate:
- Test contract: `.anti-legacy/contracts/{domain}/{req_id}.contract.json`
- Target file: from blueprint `{target_path}/{package}/{ClassName}.{ext}`
- Business rules: from requirements graph node

## Step 3: Dispatch UAT subagents

For each domain (or in parallel for all domains), dispatch a UAT reviewer:

```
@uat_reviewer

## UAT Assignment — {domain}: {req_id}

You are an independent UAT reviewer. You have READ-ONLY access. You cannot
modify any files.

### Your task
Validate that the target implementation matches the specified business rules
and test contract scenarios.

### Target file to review
{target_file_path}

Read this file using your read tool.

### Business rules (from requirements)
{business_rules_numbered_list}

### Validation rules
{validations_list}

### Test contract scenarios
{test_scenarios_json}

### Parity rules
{parity_rules_list}

### What to check

For each test scenario in the contract:
1. Trace the scenario through the target code
2. Verify the code handles the input conditions described
3. Verify the expected output would be produced
4. Flag any scenario not covered by the code

For each business rule:
1. Find where the rule is implemented in the target code
2. Verify the logic matches the rule exactly (including precision for numeric operations)
3. Flag any rule that is missing or incorrectly implemented

### Report format

Return a structured verdict:

```json
{
  "req_id": "{req_id}",
  "verdict": "PASS" or "FAIL",
  "scenarios_reviewed": N,
  "scenarios_passed": N,
  "scenarios_failed": N,
  "rules_verified": N,
  "rules_missing": N,
  "findings": [
    {
      "id": "UAT-001",
      "severity": "CRITICAL|MAJOR|MINOR",
      "rule_or_scenario": "TC-002",
      "description": "...",
      "target_file_line": optional_line_number
    }
  ],
  "overall_rationale": "..."
}
```

CRITICAL findings → automatic FAIL.
MAJOR findings → automatic FAIL.
MINOR findings → PASS with notes (human reviewer decides).

### Anti-rubber-stamp rules

> **CRITICAL**: The following MUST trigger an automatic FAIL verdict:
> - The target file contains ONLY a hardcoded string return (e.g., `return "...executed successfully"`)
> - The target file contains ONLY a `System.out.println()` with no logic
> - The target file has zero conditional statements implementing business rules
> - The `overall_rationale` is generic/templated (e.g., "perfectly matches legacy")
>   — it MUST reference specific rule IDs and specific code lines where they are implemented
> - The verdict was generated without reading the actual target source file

Each finding MUST include `target_file_line` pointing to the specific line in the
target code. Verdicts without line references are INVALID.
```

> **These anti-rubber-stamp rules are now machine-enforced, not advisory.**
> GATE_4_UAT (`validator_discovery._run_gate_4_uat`) reads each verdict file's
> `verdict` field and additionally HARD-FAILS the gate when any verdict has:
> - a finding with `severity` of `CRITICAL` or `MAJOR`,
> - any finding missing `target_file_line`, or
> - an empty/missing `overall_rationale`.
>
> A verdict that violates the rules above will block GATE_4_UAT at the script
> level — there is no path to clear the gate by rubber-stamping.

For CLIs that do not support `@agent` dispatch, run inline — adopt the UAT
reviewer persona yourself, verify the code against each contract scenario, and
produce the same JSON verdict output.

## Step 4: Collect and aggregate verdicts

Collect JSON verdicts from all subagents. Aggregate:

```python
python3 -c "
import json, os, glob

verdicts = []
for f in glob.glob('.anti-legacy/evidence/uat/*.json'):
    verdicts.append(json.load(open(f)))

passed = sum(1 for v in verdicts if v['verdict'] == 'PASS')
failed = sum(1 for v in verdicts if v['verdict'] == 'FAIL')
critical = sum(
    1 for v in verdicts
    for f in v.get('findings', [])
    if f.get('severity') == 'CRITICAL'
)

print(f'UAT Results: {passed} PASS, {failed} FAIL, {critical} critical findings')
overall = 'PASS' if failed == 0 and critical == 0 else 'FAIL'
print(f'Overall: {overall}')
"
```

## Step 5: Write each verdict to evidence

Write each subagent verdict to `.anti-legacy/evidence/uat/{req_id}-verdict.json`.

Each verdict file MUST carry the key `verdict` with value `PASS` or `FAIL`
(the same JSON shape produced in Step 3). This `verdict` field is exactly what
GATE_4_UAT (`validator_discovery._run_gate_4_uat`) now reads as the primary
PASS/FAIL signal — do NOT rename it to `status`. Each finding MUST keep its
`target_file_line`, and `overall_rationale` MUST be non-empty, or GATE_4 will
hard-fail the verdict (see Step 3).

> **The verdict enum is `PASS|FAIL` only.** `CONDITIONAL` — or any value other
> than `PASS` or `FAIL` — is NOT a valid verdict. `validator_discovery._run_gate_4_uat`
> treats anything that is not `PASS` (case-insensitively) as non-passing and
> hard-fails the gate, so a `CONDITIONAL` (or otherwise invalid) verdict blocks
> GATE_4_UAT exactly like a `FAIL`. There is no third state: if review surfaces
> open concerns, the verdict is `FAIL`.

## Step 6: Write UAT summary report and aggregated verdicts file

Write `.anti-legacy/evidence/uat-summary.md`:
- Overall verdict (PASS / FAIL)
- Per-domain and per-requirement breakdown
- All findings with severity and description
- Items requiring human attention before sign-off

Also write a single aggregated verdicts file `.anti-legacy/evidence/uat-verdicts.json`
so the GATE_4_UAT evidence cites a real FILE (not the `evidence/uat/` directory of
per-requirement verdicts — the content-verify guard rejects directories):

```bash
python3 -c "
import json, glob
verdicts = []
for f in sorted(glob.glob('.anti-legacy/evidence/uat/*-verdict.json')):
    verdicts.append(json.load(open(f)))
passed = sum(1 for v in verdicts if v.get('verdict') == 'PASS')
failed = sum(1 for v in verdicts if v.get('verdict') == 'FAIL')
out = {
    'overall': 'PASS' if failed == 0 else 'FAIL',
    'passed': passed,
    'failed': failed,
    'total': len(verdicts),
    'verdicts': verdicts,
}
with open('.anti-legacy/evidence/uat-verdicts.json', 'w') as fh:
    json.dump(out, fh, indent=2)
print('Wrote uat-verdicts.json:', passed, 'PASS', failed, 'FAIL')
"
```

## Step 7: If overall PASS — register evidence, then prompt for GATE_4 sign-off

The new evidence content-verify guard in `manifest gate` (B1) rejects a `--opinion
passed` sign-off whose cited evidence ids are not REGISTERED artifacts with a present
file and matching checksum. Register the two UAT artifacts the gate cites BEFORE
signing, so the gate's content-verify (registered AND status not failed/pending AND
file present AND checksum matches) passes:

```bash
python3 .anti-legacy/run.py manifest register uat-summary \
  --path evidence/uat-summary.md \
  --format markdown \
  --produced-by anti-legacy:uat-crew \
  --status final

python3 .anti-legacy/run.py manifest register uat-verdicts \
  --path evidence/uat-verdicts.json \
  --format json \
  --produced-by anti-legacy:uat-crew \
  --status final
```

Display: "All UAT checks passed. Request sign-off from UAT Lead (independent of dev team)."

Provide the sign-off command:

`{uat-lead-name}` MUST be an independent UAT-Lead identity that differs from
`roles.architect` in the manifest. Independence is now machine-enforced in
`validator_discovery._run_gate_4_uat` (M2): the runner hard-fails GATE_4_UAT when
the GATE_4_UAT evaluator equals `roles.architect` OR equals the recorded
GATE_1_DESIGN signer (read from `audit.jsonl`). Pass a distinct UAT-Lead identity.

The `--evidence` ids MUST be the two registered artifacts from Step 7 above —
`uat-summary,uat-verdicts` — the same canonical ids the gatekeeper skill cites for
GATE_4_UAT. The `manifest gate` content-verify guard (B1) will reject the sign-off
if either id is unregistered, has status `failed`/`pending`, is missing on disk, or
its checksum has drifted, so do not cite an unregistered id (e.g. a bare
`build-integrity` is owned by GATE_3_BUILD, not GATE_4):

```bash
python3 .anti-legacy/run.py manifest gate GATE_4_UAT \
  --opinion passed \
  --evaluator "{uat-lead-name}" \
  --rationale "All {req_count} requirements passed UAT with {scenario_count} scenarios" \
  --evidence "uat-summary,uat-verdicts"

git add .anti-legacy/
git commit -m "gate: GATE_4_UAT cleared by {uat-lead-name}"
```

## Step 8: Store UAT results in git-brain

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "UAT [{project_name}]: {passed}/{total} requirements passed. {critical} critical findings. Overall: {verdict}. Key failures: {top_findings}." \
  --tags "discovery,uat,{project_name}" \
  --category learnings
```

## Step 9: Done-gate, then advance phase

**Done-gate (content assertion).** Before registering/advancing, prove the UAT
run is real and clean. Assert that EVERY active requirement has a verdict file,
every verdict file has `verdict` in `{PASS,FAIL}`, the overall result is PASS
(0 FAIL verdicts), no finding has severity `CRITICAL` or `MAJOR`, every finding
carries a `target_file_line`, and every verdict has a non-empty
`overall_rationale`:

```bash
python3 -c "
import json, glob, sys
req = json.load(open('.anti-legacy/requirements/requirements_graph.json'))
active = set()
for d in req.get('domains', {}).values():
    for rid, node in d.get('requirements', {}).items():
        if node.get('status', 'active') != 'inactive':
            active.add(rid)
verdicts = {}
for f in glob.glob('.anti-legacy/evidence/uat/*-verdict.json'):
    v = json.load(open(f))
    verdicts[v.get('req_id')] = v
problems = []
missing = active - set(verdicts)
if missing:
    problems.append('missing verdict files for: ' + ', '.join(sorted(missing)))
fails = []
for rid, v in verdicts.items():
    if v.get('verdict') not in ('PASS', 'FAIL'):
        problems.append(f'{rid}: verdict not PASS/FAIL ({v.get(\"verdict\")})')
    if v.get('verdict') == 'FAIL':
        fails.append(rid)
    if not (v.get('overall_rationale') or '').strip():
        problems.append(f'{rid}: empty overall_rationale')
    for fnd in v.get('findings', []):
        if fnd.get('severity') in ('CRITICAL', 'MAJOR'):
            problems.append(f'{rid}: {fnd.get(\"severity\")} finding {fnd.get(\"id\")}')
        if fnd.get('target_file_line') in (None, ''):
            problems.append(f'{rid}: finding {fnd.get(\"id\")} missing target_file_line')
if fails:
    problems.append('FAIL verdicts: ' + ', '.join(sorted(fails)))
if problems:
    print('UAT DONE-GATE FAILED:')
    for p in problems:
        print('  - ' + p)
    sys.exit(1)
print('UAT done-gate: all requirements PASS, no blocking findings ✓')
"
```

**If this assertion FAILS, do NOT run `manifest gate`, do NOT register, and do
NOT run `advance`.** Surface the specific failing requirements/findings to the
user and route back to `anti-legacy:swarm` to fix the failing requirements, then
re-run UAT. The `gate`/`advance` steps below are CONDITIONAL on this assertion
passing.

Only when the done-gate passes, advance:

```bash
python3 .anti-legacy/run.py manifest advance uat
python3 .anti-legacy/run.py learn_coordinator --phase uat
```

## Output

- `.anti-legacy/evidence/uat/*.json` — per-requirement UAT verdicts
- `.anti-legacy/evidence/uat-verdicts.json` — aggregated verdicts file (registered artifact id `uat-verdicts`)
- `.anti-legacy/evidence/uat-summary.md` — human-readable UAT summary (registered artifact id `uat-summary`)
- Manifest: artifacts `uat-summary` and `uat-verdicts` registered (status `final`); GATE_4_UAT evidence
- Git-brain: UAT findings stored

**Next step**: If GATE_4_UAT cleared → `anti-legacy:deploy` to package and deploy the modernized application.
If GATE_4_UAT failed → `anti-legacy:swarm` to fix failing requirements, then re-run.
