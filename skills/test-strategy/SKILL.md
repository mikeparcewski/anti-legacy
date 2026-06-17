---
name: "anti-legacy:test-strategy"
description: >
  Generate a shift-left test strategy from the target blueprint. Produces one test
  contract per requirement node — inputs, assertions, boundary conditions, data parity
  verification rules (legacy output vs modern output), and integration test plans.
  Queries git-brain for prior test patterns.
  Use when: "create a test strategy", "generate test contracts", "write the test plan",
  "what do we need to test", "parity testing approach".
---

# anti-legacy:test-strategy

Translates the blueprint into a concrete, verifiable test strategy. Each
requirement node gets a test contract that the UAT crew can execute independently.
Parity testing (legacy output vs. modern output on the same inputs) is the
primary verification mechanism for modernization correctness.

## Cross-Platform Notes

File operations use the agent's native Read/Write tools.

## Config

```bash
python3 -c "import json; c=json.load(open('.anti-legacy/config.json')); print(c['target_stack'])"
```

(Config read uses a plain python one-liner; it does not invoke a pipeline script,
so it is not routed through `run.py`.)

## Parameters

- **scope** (optional): restrict to a single domain name. Defaults to all domains.
- **parity_mode** (optional): `side-by-side` (run both legacy and modern) or `golden-file`
  (compare output against pre-captured legacy output). Defaults to `golden-file`.

## Step 1: Verify prerequisites

```bash
python3 .anti-legacy/run.py manifest status
```

Confirm `blueprint-json` is present and `final` or `draft`. If only `draft`,
remind the user that test strategy can proceed but will need updating after
GATE_1_DESIGN sign-off.

## Step 2: Query git-brain for test patterns

```bash
python3 .anti-legacy/run.py git_brain search \
  --query "modernization test strategy parity testing {source_language} {target_stack} contracts" \
  --limit 5
```

Apply recalled patterns to the contract generation below.

## Step 3: Read inputs

Read both:
- `.anti-legacy/requirements/requirements_graph.json` (business rules, edge cases)
- `.anti-legacy/requirements/blueprint.json` (API signatures, entity schemas)

## Step 4: Generate test contracts per requirement node

For each requirement node across all domains:

### 4a. Identify test scenarios from business rules

> **CRITICAL — NO GENERIC TEMPLATES**: Each test scenario MUST be derived from a
> specific business rule, validation, or error path in the requirements graph.
> Contracts with identical inputs across different requirements are INVALID.
> If two contracts share the same `inputs` JSON, the generation has failed.

For each requirement node, generate scenarios as follows:

1. **One scenario per `business_rules[]` entry**: Read the rule text and create an
   input that exercises that specific rule. Use the requirement's `data_access[]`
   entities and their column schemas from `blueprint.json` to construct realistic
   field values (e.g., real card numbers for card rules, real account IDs for
   account rules, real transaction amounts for financial rules).

2. **One scenario per `validations[]` entry**: Create an input that intentionally
   violates the validation and verify the expected rejection. The input field names
   and values MUST reflect the actual validation being tested (e.g., if VAL-001 says
   "Payment amount must not exceed outstanding balance", the input must include
   `paymentAmount: 5000.00` with `outstandingBalance: 1000.00`).

3. **One scenario per `error_paths[]` entry**: Create an input that triggers the
   specific error condition and verify the error response matches.

4. **Boundary conditions**: For any numeric field in the entity schema (DECIMAL,
   INTEGER), add min/max/zero boundary tests.

5. **Edge cases**: null/missing required fields, duplicate keys, concurrent access.

The minimum scenario count per requirement = len(business_rules) + len(validations)
+ len(error_paths). Generic "happy path" / "error flow" labels WITHOUT specific
rule references are NOT ACCEPTABLE.

### 4b. Write the test contract

#### Deliverable contract (`contracts/{domain}/{req_id}.contract.json`)

This file is the test-strategy phase's primary artifact and its schema is the
contract — produce these fields or flag why you cannot:

- **`req_id`** — the requirement id this contract verifies.
- **`domain`** — the owning domain name (matches the `{domain}` path segment).
- **`legacy_components[]`** — **INHERITED from the requirement node, never
  removed.** The same file_path(s) carried on the requirement's
  `legacy_components`. This keeps the traceability thread (requirement → source)
  intact through the test layer. Dropping it breaks parity provenance.
- **`scenarios[]`** — one entry per test case, each
  `{id, type, description, inputs, expected_output, expected_error}`:
  - `id` — `TC-NNN` (3 digits, unique within the contract).
  - `type` — one of `happy_path` | `boundary` | `error` | `parity`.
  - `description` — what business rule / validation / error path this exercises
    (cite the `RULE-NNN` / `VAL-NNN` / `ERR-NNN` id from Step 4a).
  - `inputs` — the input field map (rule-specific per Step 4a; never generic).
  - `expected_output` — expected field values on success (omit / leave empty for
    pure error scenarios).
  - `expected_error` — expected error code/message for `error`-type scenarios
    (omit for happy_path / boundary success cases).
- **`parity_rules[]`** — one entry per numeric output, each an **OBJECT**
  `{field, precision, source_type}` (NOT a bare string):
  - `field` — the output field name being parity-checked.
  - `precision` — required decimal/precision match (e.g. `2` for 2 decimal
    places, or `exact` for non-fractional equality).
  - `source_type` — the legacy type the precision derives from (e.g.
    `"COMP-3 PIC 9(9)V99"`), inherited from the entity field's `source_type`.

**Assertions** (enforced at GATE_1; do not advance if violated):
- Every active requirement has a contract with **≥1 `happy_path` scenario AND
  ≥1 `error` scenario**.
- Any numeric output has **≥1 `parity_rules` entry** (COMP-3 precision loss is
  silent and catastrophic — money, rates, percentages, and counts all need one).

Write each contract to `.anti-legacy/contracts/{domain}/{req_id}.contract.json`:

```json
{
  "req_id": "{req_id}",
  "domain": "{domain}",
  "legacy_components": ["COBOL/CBTRN02C.cbl"],
  "scenarios": [
    {
      "id": "TC-001",
      "type": "happy_path",
      "description": "RULE-002 — prime customer earns 10% discount on valid transaction",
      "inputs": {
        "ACCT-NUM": "1234567890",
        "TRANS-AMT": 150.00,
        "CUST-TYPE": "PRIME"
      },
      "expected_output": {
        "RESULT-CODE": "00",
        "DISC-AMOUNT": 15.00,
        "GROSS-AMOUNT": 135.00
      },
      "expected_error": null
    },
    {
      "id": "TC-002",
      "type": "error",
      "description": "ERR-001 — inactive customer is rejected before pricing",
      "inputs": { "ACCT-NUM": "9999999999", "CUST-STATUS": "INACTIVE" },
      "expected_output": {},
      "expected_error": "ERR-INACT"
    },
    {
      "id": "TC-003",
      "type": "parity",
      "description": "GROSS-AMOUNT must match legacy COMP-3 output exactly",
      "inputs": {
        "ACCT-NUM": "1234567890",
        "TRANS-AMT": 9999999.99,
        "CUST-TYPE": "PRIME"
      },
      "expected_output": { "GROSS-AMOUNT": 8999999.99 },
      "expected_error": null
    }
  ],
  "parity_rules": [
    { "field": "GROSS-AMOUNT", "precision": 2, "source_type": "COMP-3 PIC 9(9)V99" },
    { "field": "RESULT-CODE", "precision": "exact", "source_type": "PIC X(2)" }
  ]
}
```

### 4c. Integration test plan

For requirement nodes with `dependencies`, write a cross-component integration
scenario that exercises the full call chain. Write to
`.anti-legacy/contracts/{domain}/{req_id}.integration.json`.

## Step 5: Write the master test strategy document

Write `.anti-legacy/contracts/test-strategy.md`:

```markdown
# Test Strategy — {project_name}

## Approach
{parity_mode} parity testing against {source_language} legacy baseline.

## Coverage
- {total_req} requirement nodes
- {contract_count} test contracts
- {scenario_count} test scenarios
- {integration_count} integration scenarios

## Test Tiers
| Tier | Scope | Count |
|------|-------|-------|
| Unit | Per requirement, isolated | {unit_count} |
| Integration | Cross-requirement chains | {integration_count} |
| Parity | Legacy output vs modern output | {parity_count} |
| UAT | Business acceptance | {uat_count} |

## Domain Coverage
{domain coverage table}

## Data Setup Requirements
{list of seed data required per domain}

## Parity Verification Rules
{critical precision and equality rules}

## Executable Functional Test scripts
These contracts are AUTHORED into executable acceptance tests BEFORE the build
(shift-left) by `anti-legacy:functional-tests`, then RUN against the built target
in `anti-legacy:target-review`.

Pre-build authoring + contract runnability gate:
`python3 .anti-legacy/run.py functional_tests author --contracts .anti-legacy/contracts --stack {target_stack} --output {target_path}/src/test/java/acceptance --report .anti-legacy/evidence/functional-authoring-report.json`

Post-build execution (target-review):
`python3 .anti-legacy/run.py test_runner --workspace {target_path} --stack {target_stack} --report .anti-legacy/evidence/functional-test-report.json`
```

## Step 6: Store strategy in git-brain

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "Test strategy [{project_name}]: {scenario_count} scenarios across {req_count} requirements. Parity mode: {parity_mode}. Integration chains: {integration_count}. Key precision rules: {precision_rules}." \
  --tags "pattern,test-strategy,{target_stack}" \
  --category patterns
```

## Step 7: Done-gate, register artifacts, and advance phase

### 7a. Content assertion (BLOCKING)

Before registering or advancing, prove the contracts are real and that the
skill's own CRITICAL rule (Step 4a, lines 67-71) held: at least one contract
file exists under `contracts/`, and NO two contracts share identical `inputs`
JSON across any of their test scenarios. A duplicate-`inputs` collision means
the generation degenerated into a generic template and is INVALID.

```bash
python3 -c "import json,glob,sys; files=glob.glob('.anti-legacy/contracts/**/*.contract.json', recursive=True); seen={}; dupes=[]; ok=len(files)>=1; [ ( [ ( dupes.append((seen[k], '%s::%s' % (f, sc.get('id','?')))) if (k:=json.dumps(sc.get('inputs', {}), sort_keys=True)) in seen else seen.__setitem__(k, '%s::%s' % (f, sc.get('id','?'))) ) for sc in json.load(open(f)).get('scenarios', []) ] ) for f in files ]; sys.stderr.write('No contract files under .anti-legacy/contracts/\n') if not ok else None; [ sys.stderr.write('DUPLICATE inputs: %s == %s\n' % d) for d in dupes ]; sys.exit(0 if (ok and not dupes) else 1)"
```

If this assertion FAILS (no contract files, or any duplicate-`inputs`
collision), do NOT run `register --status draft` and do NOT run `advance`.
Surface the specific colliding scenarios to the user and regenerate the
offending contracts with rule-specific inputs (Step 4a) before retrying.
`register` and `advance` are CONDITIONAL on this assertion passing.

### 7b. Register and advance (only on success)

```bash
python3 .anti-legacy/run.py manifest register test-strategy \
  --path contracts/test-strategy.md \
  --format markdown \
  --produced-by anti-legacy:test-strategy \
  --status draft \
  --depends-on blueprint-json

python3 .anti-legacy/run.py manifest advance test-strategy
```

## Step 8: Adversarially self-review the test-strategy contract set (advisory — AGENTS.md §8)

The Step 7a assertion only proves contracts exist and that no two share identical
`inputs` — it cannot see a money/rate/count output that shipped with NO `parity_rules`
entry (COMP-3 precision loss is silent and catastrophic), a contract that dropped its
inherited `legacy_components` (breaking the §2 thread req_id → source), or a scenario
whose `expected_output` does not actually exercise the rule it cites. Before you report
done, adversarially review the contract set you just produced under
`contracts/{domain}/*.contract.json` — the duplicate-inputs check is trusting; this is
the loop that distrusts it. Resolve the single-artifact critic target, then dispatch the
read-only critic against it:

```bash
python3 .anti-legacy/run.py refine_loop descriptor --artifact test-strategy --json
```

That resolves the rendered artifact + the source data the critic must cross-check (the
requirements-graph §2 spine + this artifact's manifest `depends_on` — the blueprint).
Dispatch `anti-legacy:adversarial-review` (single-artifact mode) against the descriptor;
point it at the two failure modes this phase owns — **`parity_rules` on every numeric
output**, and **§2 traceability** (every contract's `req_id` → its inherited
`legacy_components`). On `REVISE`/`BLOCK`, run the bounded loop — `refine_loop decide
--verdict <v> --attempt <n> --artifact test-strategy` — re-running
`anti-legacy:test-strategy` to fix at source and re-reviewing, capped at §7's three
attempts (then recon), or proceed under a **stated** `--forced` override. **Advisory: it
clears no gate and advances no phase.**

## Output

- `.anti-legacy/contracts/{domain}/*.contract.json` — per-requirement test contracts
- `.anti-legacy/contracts/{domain}/*.integration.json` — integration scenarios
- `.anti-legacy/contracts/test-strategy.md` — master test strategy document
- `.anti-legacy/evidence/functional-test-report.json` — programmatically run results from `test_runner.py`
- git-brain: test patterns stored for reuse

**Next step**: `anti-legacy:functional-tests` to author the executable acceptance
tests from these contracts (pre-build, shift-left) and validate the contracts are
runnable/unambiguous, then `anti-legacy:review-packet` to compile everything for
human review before GATE_1.
