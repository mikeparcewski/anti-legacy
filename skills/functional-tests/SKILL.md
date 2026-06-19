---
name: "anti-legacy:functional-tests"
description: >
  Author executable functional / scenario acceptance tests from the per-requirement
  test contracts BEFORE the build exists (shift-left), and validate those contracts
  are runnable and unambiguous (hard gate). Emits JUnit 5 for a Java target stack,
  pytest for Python; any other stack returns an explicit "stack not yet supported"
  error — never a silent pass. The companion POST-BUILD execution cycle runs in
  anti-legacy:target-review via the functional test runner.
  Use when: "author the acceptance tests", "generate the functional tests",
  "write executable scenario tests", "validate the test contracts", "are the
  contracts runnable", "shift-left acceptance tests".
---

# anti-legacy:functional-tests

Turns the test contracts (`anti-legacy:test-strategy` output) into *executable*
functional acceptance tests, **before** the swarm builds anything. This is the
shift-left half of functional acceptance: the tests encode the acceptance
criteria the build must later satisfy. The matching post-build execution cycle —
running these scenarios against the BUILT target and recording a real pass/fail —
lives in `anti-legacy:target-review` (which calls `test_runner`).

These are NOT unit tests. Each emitted test is a behaviour/scenario test derived
directly from a contract scenario's `inputs` / `expected_output` /
`expected_error`, bound to the contract's `target_component`.

## Why pre-build authoring is a hard gate

A contract that cannot be turned into an executable test is a latent false-green:
it would silently author zero tests, and the post-build run would "pass" with no
acceptance coverage. So contract validation fails loudly. A contract is runnable
and unambiguous only when it has a `req_id`, a non-empty `target_component`, at
least one scenario, every scenario carries a unique `id` and an `inputs` map, and
every scenario asserts *something* (an `expected_output` or an `expected_error`).

## Cross-Platform Notes

Pure Python (`functional_tests.py`), routed through `run.py`. No shell-isms; runs
the same on macOS / Linux / WSL / Windows.

## Config

```bash
python3 -c "import json; c=json.load(open('.anti-legacy/config.json')); print(c['target_stack'])"
```

## Parameters

- **stack** (optional): override the target stack. Defaults to `target_stack`
  from `config.json` (`java` in this project).
- **contracts** (optional): contracts directory. Defaults to
  `.anti-legacy/contracts`.
- **output** (optional): directory for the authored test sources. Defaults to a
  per-stack location under the target tree (see Step 3).

## Step 1: Verify prerequisites

```bash
python3 .anti-legacy/run.py manifest status
```

Confirm the test contracts exist — at least one `*.contract.json` under
`.anti-legacy/contracts/`. If none exist, run `anti-legacy:test-strategy` first;
there is nothing to author from.

## Step 2: Validate the contracts are runnable (HARD GATE)

```bash
python3 .anti-legacy/run.py functional_tests validate \
  --contracts .anti-legacy/contracts
```

Exit code 0 = every contract is runnable and unambiguous. Non-zero = one or more
contracts cannot be authored; the command prints each offending contract and the
specific reason (missing `target_component`, duplicate scenario id, a scenario
with nothing to assert, etc.). Do NOT proceed to authoring until this exits 0 —
fix the contracts (`anti-legacy:test-strategy`) and re-run.

## Step 3: Author the executable acceptance tests

```bash
python3 .anti-legacy/run.py functional_tests author \
  --contracts .anti-legacy/contracts \
  --stack {target_stack} \
  --output {target_stack_test_path} \
  --report .anti-legacy/evidence/functional-authoring-report.json
```

Where `{target_stack_test_path}` is stack-dependent:

| Stack | `--output` value |
|---|---|
| java / maven | `{target_path}/src/test/java/acceptance` |
| python | `{target_path}/tests/acceptance` |
| go | `{target_path}/acceptance_test` |
| dotnet / csharp | `{target_path}/tests/Acceptance` |

Per stack:

- **java** → one JUnit 5 test class per contract under `--output`, one `@Test`
  per scenario. Each test documents the scenario's inputs/expected and asserts
  the named `target_component` (class + method) exists on the built classpath —
  the smallest provable claim the swarm produced the symbol. The post-build
  runner fills in the live input/output comparison.
- **python** → one pytest module per contract.
- **any other stack** → the command returns a clear "stack not yet supported"
  error and exits non-zero. It does NOT author a silent empty pass.

The authoring is also gated: if ANY contract fails validation, NO test files are
written and the command exits non-zero (the hard gate from Step 2 is re-asserted
inside `author`, so the two are never out of sync).

## PEP done-gate (AGENTS.md §10 — Full PEP)

Before declaring done, run all six steps:

**Step 3 — Antagonist (pre-build, before the producer runs)**
```bash
python3 .anti-legacy/run.py antagonist context --phase functional-tests
# Paste the output as context, then dispatch anti-legacy:antagonist.
# CRITICAL threats block — fix the plan or waive explicitly before proceeding.
```

**Steps 2 & 4 — Adversarial review + resolve loop (after the producer runs)**
```bash
python3 .anti-legacy/run.py refine_loop descriptor --artifact functional-authoring-report
# Dispatch anti-legacy:adversarial-review against the rendered output.
# Then: python3 .anti-legacy/run.py refine_loop decide --verdict <PASS|REVISE|BLOCK> --attempt <n>
# exit 0 = stop · exit 3 = refine (re-run producer) · exit 4 = cap reached → recon (§7)
```

## Step 4: Done-gate

Authoring succeeded only when the report shows `status: PASS` and one authored
file per contract:

```bash
python3 -c "
import json, sys
r = json.load(open('.anti-legacy/evidence/functional-authoring-report.json'))
ok = r.get('status') == 'PASS' and len(r.get('authored', [])) == r.get('contracts_discovered')
sys.stdout.write('OK %d/%d authored\n' % (len(r.get('authored', [])), r.get('contracts_discovered', 0)) if ok else 'BLOCKED status=%s\n' % r.get('status'))
sys.exit(0 if ok else 1)
"
```

If this fails, do NOT advance. Surface the gap (unsupported stack, or the
validation errors echoed in the report's `validation_errors`).

## Step 5: Register the authored tests

```bash
python3 .anti-legacy/run.py manifest register functional-authoring-report \
  --path evidence/functional-authoring-report.json \
  --format json \
  --produced-by anti-legacy:functional-tests \
  --status final \
  --depends-on test-strategy
```

The authored test sources travel with the target tree and are executed in
`anti-legacy:target-review`. They are the acceptance contract the build must
satisfy.

## Output

- `{target_path}/src/test/...` — one executable acceptance test file per contract
  (JUnit 5 for Java, pytest for Python), one test per scenario.
- `.anti-legacy/evidence/functional-authoring-report.json` — authoring report
  (status, per-contract authored list, any validation errors).

**Next step**: `anti-legacy:review-packet` → human review → GATE_1. The authored
tests are RUN post-build in `anti-legacy:target-review` (which writes
`.anti-legacy/evidence/functional-test-report.json` and feeds GATE_3_BUILD).
