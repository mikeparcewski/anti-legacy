---
name: "anti-legacy:test-scripts"
description: >
  Generate stakeholder-facing FUNCTIONAL test scripts across four types — data-parity
  (legacy vs modern), UAT (Given/When/Then Gherkin), end-to-end business journeys, and
  API/contract — from the requirements graph + blueprint + test contracts, written under
  .anti-legacy/deliverables/tests/. Functional, NOT unit. Complements (does not replace)
  the gated build-binding tests from anti-legacy:functional-tests.
  Use when: "generate functional test scripts", "write the acceptance tests",
  "parity/UAT/E2E/API tests", "produce the test scripts deliverable".
---

# anti-legacy:test-scripts

Renders **functional test scripts** — the human-facing acceptance assets a delivery
team and the business sign off on — across the four test types the pipeline cares
about. It SCAFFOLDS the tree deterministically from the structured pipeline data,
then YOU (the agent) ENRICH each scaffold with the concrete assertions each test
contract already specifies. These are **functional / behavioural** tests, never unit
tests.

This is the **early, broad** sibling of `anti-legacy:functional-tests`. That skill
authors the *gated, build-binding* JUnit/pytest class-existence tests tied to a
contract's `target_component` and feeds GATE_3. **This** skill produces the wider,
business-readable functional suite (parity / UAT / E2E / API) as a *deliverable*. It
**complements** functional-tests — it does not replace it, and it does not gate.

## Mental model

One requirements graph → four test families, each answering a different question:

| Type (`tests/<dir>/`) | Question it answers | Form |
|---|---|---|
| `data-parity/` | Does the modern output equal the legacy output, to the right precision? | JUnit5 parameterized (java) / pytest (python) |
| `uat/` | Does the capability satisfy the business rule, in business language? | Gherkin `.feature` (always — stack-agnostic, business-facing) |
| `e2e/` | Does a full business journey work end-to-end, across requirements? | JUnit5 / pytest / Gherkin journey |
| `api/` | Does each API return the right status + response shape? | REST-assured+JUnit5 (java) / requests+pytest (python) |

Every file traces back: **req_id → legacy_components → rule/scenario id** (§2 of the
contract — the thread never breaks). Each generated file opens with that header.

## When it runs & prerequisites

Runs **"when the graph is ready"** — once
`.anti-legacy/requirements/requirements_graph.json` exists. It also reads (and
degrades gracefully without):

- `requirements/blueprint.json` — to find which requirements expose an
  `api{method,path}` component (drives `api/`) and the component class names.
- `contracts/{domain}/{req_id}.contract.json` — the `scenarios[]` and `parity_rules[]`
  that fill the assertions. **No contracts → scenario-less SKELETONS** from the rules,
  and the gap is named in the README. (Run `anti-legacy:test-strategy` to produce
  contracts first if you want enriched output.)

It **registers** its index and **never advances the phase** (§Universal Don'ts).

## Cross-Platform Notes

Pure Python (`test_scripts.py`), routed through `run.py`; all paths via `os.path`.
Runs identically on macOS / Linux / WSL / Windows. No shell-isms.

## Parameters

- **stack** (optional, `--stack`): override the target stack. Defaults to
  `config.target_stack`, falling back to the manifest's `project.target_stack`.
- **--no-register**: write the files but do not touch the manifest (hermetic / dry run).

## Step 1: Verify prerequisites

```bash
python3 .anti-legacy/run.py manifest status
```

Confirm `requirements-graph` is registered. If it is not, there is nothing to
scaffold — run the pipeline up to `anti-legacy:graph-translator` first. Optionally
confirm `test-strategy` is present; without it the scripts are skeletons.

## Step 2: Scaffold the four test families

```bash
python3 .anti-legacy/run.py test_scripts
```

This writes the tree under `.anti-legacy/deliverables/tests/` and prints the per-type
counts and every written path. The stack mapping is automatic:

- **java** → JUnit5 for parity/e2e/api (api uses REST-assured); Gherkin for uat.
- **python** → pytest for parity/e2e/api; Gherkin for uat.
- **any other stack** → Gherkin `.feature` for uat AND e2e; parity and api emit
  **nothing** but write an explicit `stack <x>: ... not yet supported` note in the
  README (mirrors functional-tests' no-silent-skip behaviour — never a silent empty
  emit). If your target is java/python, you should not see those notes.

Coverage rules the scaffolder applies:
- `data-parity/` — one test per active requirement that has `parity_rules` **or** any
  numeric output field in its contract scenarios. Requirements with neither are listed
  as a gap (no parity target).
- `uat/` — one `<domain>.feature`, one `Scenario` per contract scenario (tagged
  `@<req_id> @<TC-id>`), or one skeleton scenario per requirement from its first rule.
- `e2e/` — one journey per domain, its requirements ordered by intra-domain
  `dependencies` (topologically; cycles broken deterministically).
- `api/` — one test per active requirement whose blueprint component declares
  `api{method,path}`; emits a happy-path (status 200 + shape) and, when the contract
  has an error scenario, an error-path (≥400) test.

Dropped (`disposition: drop`) and `unresolvable` requirements are excluded — they are
not in the build set.

## Step 3: ENRICH the scaffolds (the real work)

The scaffolder gets the structure and the traceability right; it cannot invent the
business semantics. Open each generated file and replace the marked `// ENRICH` /
`# ENRICH` spots with concrete assertions **taken from the contract** — do not invent
fixtures the contract already specifies. Per type:

- **data-parity** — each row is `field, legacyExpected, precision, scenarioId` drawn
  from the contract's `expected_output` + `parity_rules`. Wire the *modern* value: call
  the built component with the scenario `inputs` and capture the field, replacing the
  `placeholder == legacy` line. Keep the precision/scale assertion — COMP-3 precision
  loss is silent and catastrophic (§Universal Don'ts).
- **uat** — turn each `Given/When/Then` into the exact precondition / action / outcome
  from the scenario's `inputs` / `expected_output` / `expected_error`. The `Scenario`
  name already cites the rule; keep the `@<req_id>` tag so the thread holds. Skeleton
  scenarios (no contract) must be fleshed out or explicitly left flagged.
- **e2e** — make each ordered step actually drive its requirement using the journey
  state from the prior step, and assert the end-to-end outcome. The order encodes the
  dependency chain; preserve it.
- **api** — set the base URI, send a real request body from a `happy_path` scenario,
  and assert the response shape (not just the status). For the error path, send an
  invalid body from an `error` scenario and assert the status + error code.

Do not delete the traceability header. Do not collapse two requirements into one file.

## Step 4: Done-gate (BLOCKING — assert before registering)

The script self-asserts (graph exists; the index README is non-empty) and exits
non-zero otherwise. Confirm the index and that every active requirement is represented
or explicitly listed as a gap:

```bash
python3 -c "
import os, sys
base = '.anti-legacy/deliverables/tests'
idx = os.path.join(base, 'README.md')
ok = os.path.exists(idx) and os.path.getsize(idx) > 0
n = sum(len(fs) for _, _, fs in os.walk(base))
sys.stdout.write('OK index present, %d file(s)\n' % n if ok else 'BLOCKED: no index\n')
sys.exit(0 if ok and n > 1 else 1)
"
```

If this fails, do **not** register — surface the gap (no requirements graph, or an
empty index) and stop.

## Step 5: Register the index (register only — never advance)

The scaffold step (Step 2) already registers the index when the manifest exists. To
re-register after manual enrichment (idempotent — same artifact id, re-checksummed):

```bash
python3 .anti-legacy/run.py test_scripts
```

This registers `tests/README.md` as artifact id **`deliverable-test-scripts`**
(`fmt=markdown`, `produced_by=anti-legacy:test-scripts`,
`depends_on=["requirements-graph","test-strategy"]`) and appends an
`anti-legacy:artifact-registered` audit row. It **never** advances the phase. Use
`--no-register` for a hermetic dry run.

## Output

- `.anti-legacy/deliverables/tests/data-parity/<domain>/…` — parity tests.
- `.anti-legacy/deliverables/tests/uat/<domain>.feature` — Gherkin acceptance.
- `.anti-legacy/deliverables/tests/e2e/<domain>…` — per-domain journeys.
- `.anti-legacy/deliverables/tests/api/<domain>/…` — API/contract tests.
- `.anti-legacy/deliverables/tests/README.md` — the index: what each dir holds, file
  count per type, requirements covered, and the GAPS (no-contract / no-parity-target /
  no-rules requirements, plus any unsupported-stack notes). Registered as
  `deliverable-test-scripts`.

## Failure cases

- **No requirements graph** → exit 2, clear stderr. Nothing to scaffold; run the
  pipeline to graph-translator first.
- **Contracts absent / empty** → scripts are scaffolded as scenario-less SKELETONS from
  the rules, and every such requirement is named under "Gaps" in the README. This is a
  surfaced gap, not a silent pass — enrich by running `anti-legacy:test-strategy`.
- **Unsupported stack** (not java/python) → uat + e2e still emit as Gherkin; parity +
  api emit nothing and the README carries an explicit "stack not yet supported" note.
  Do not treat the empty parity/api dirs as "done".
- **Manifest absent** → files are written but registration is a no-op (a deliverable can
  be rendered before a full workspace exists).
