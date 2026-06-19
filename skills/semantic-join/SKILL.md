---
name: "anti-legacy:semantic-join"
description: >
  Analyze and map inter-repository dependencies when multiple codebases are modernization sources.
  Scans routes and client requests, identifies dangling external links, and validates contract matches.
  Use when: "joining repositories", "mapping multi-repo interfaces", "verifying inter-app dependencies".
---

# anti-legacy:semantic-join

This skill orchestrates Phase 1B (Semantic Repository Join). It matches HTTP and API endpoints across multiple imported legacy repositories, detects dangling calls, and resolves integration questions in-band with the user.

## Step 1: Run the Semantic Join analysis

Run the boundary analyzer tool:

```bash
python3 .anti-legacy/run.py semantic_join
```

This generates:
- `.anti-legacy/requirements/semantic_join_graph.json`: A dependency topology map.
- `.anti-legacy/requirements/semantic_join_report.md`: A validation checklist of matched and unmatched API interfaces.

## Step 2: Handle Dangling Calls (Integration Gaps)

If the report contains dangling client calls, resolve each one in-band with the user before advancing.

For each dangling call, present this `single_choice` question to the user directly in the conversation and capture their answer:

- **Question id:** `GAP-{calling_service}-{dest_path_slug}`
- **Prompt:** "We detected a call from service '{calling_service}' to '{dest_path}' which does not map to any defined endpoints in other imported repos. Is this call an external third-party API or a missing repository?"
- **Options (`single_choice`):**
  1. Third-Party External API
  2. Missing Repository (Need to Import)
  3. Deprecated Code (Can Delete)

Record the user's chosen option against its question id (e.g. in `semantic_join_report.md` or your working notes) so Step 3 can assert that every dangling call has an in-band answer.

Based on the user's answer:
- **Third-Party External API**: Document the contract as a mocked external boundary in `nfrs.md`.
- **Missing Repository**: Prompt the user to import the missing Git URL via the dashboard.
- **Deprecated Code**: Mark the client code section to be stripped out during Swarm translation.

## Step 3: Done-Gate — verify the join is real before recording GATE_1B

Before recording the gate or advancing, assert that the semantic join actually completed:
`semantic_join_report.md` exists and is non-empty, AND every dangling call surfaced in
Step 2 has an in-band answer recorded against its question id. **If this assertion fails,
do NOT run `manifest.py gate` and do NOT run `advance` — surface the specific gap (missing
report, or the still-unanswered dangling-call question ids) to the user and stop.** The
gate record and advance below are CONDITIONAL on this assertion passing.

Confirm the report exists and is non-empty:

```bash
python3 -c "
import os, sys
report = os.path.join('.anti-legacy', 'requirements', 'semantic_join_report.md')
if not (os.path.isfile(report) and os.path.getsize(report) > 0):
    sys.stderr.write('GATE_1B done-gate FAILED: semantic_join_report.md missing or empty\n')
    sys.exit(1)
sys.exit(0)
"
```

Then verify in-band that every dangling call listed in the report has a recorded answer
(one of the three `single_choice` options) against its `GAP-...` question id. If any
dangling call is still unanswered, do NOT proceed — return to Step 2, resolve it with the
user, and re-run this done-gate.

## Step 4: Register the evidence artifact, then record the GATE_1B_SEMANTIC_JOIN decision

The gate's `--opinion passed` guard content-verifies every cited evidence id: each id
must be a **registered** artifact, its status must not be `failed`/`pending`, the file
must exist on disk, and its recorded checksum must match the file. A bare filename like
`semantic_join_report.md` is NOT a registered artifact id and will hard-fail the gate.

So FIRST register the real report (produced in Step 1) as the artifact id
`semantic-join-report`. Register it only after the done-gate above passes, so the file
exists and a live checksum is captured:

```bash
python3 .anti-legacy/run.py manifest register semantic-join-report \
  --path requirements/semantic_join_report.md \
  --format markdown \
  --produced-by anti-legacy:semantic-join \
  --status final
```

Then record the gate approval, citing the **registered** id `semantic-join-report`:

```bash
python3 .anti-legacy/run.py manifest gate GATE_1B_SEMANTIC_JOIN \
  --opinion passed \
  --evaluator "Architect" \
  --rationale "All inter-repository endpoints mapped and dangling boundaries clarified by the user." \
  --evidence "semantic-join-report"
```

## Step 5: Advance Phase

Only after the done-gate passed and the gate is recorded:

```bash
python3 .anti-legacy/run.py manifest advance semantic-join
```
