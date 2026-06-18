---
name: "anti-legacy:differential-equivalence"
description: >
  The EXECUTED output-parity gate (GATE_3C_DIFFERENTIAL, ISS-7). Runs a golden corpus of
  legacy inputs+outputs against the BUILT target's actual outputs for the same inputs, and
  asserts field-by-field parity within each contract's declared parity_rules — precision-aware,
  so COMP-3 decimal loss (silent and catastrophic) is caught. Unlike GATE_3_BUILD (proves rules
  are PRESENT in the target graph) and GATE_3B_SEMANTIC (a human READS the rules as equivalent),
  this RUNS the target and diffs outputs. Automated + vacuous-safe: with no golden corpus it is
  NOT_APPLICABLE and never blocks; with a corpus, any parity violation FAILs and kicks back to build.
  Use when: "differential equivalence", "output parity", "does the target produce the same numbers
  as legacy", "prove COMP-3 parity", "golden file comparison", running GATE_3C.
---

# anti-legacy:differential-equivalence

The pipeline builds the target **against requirements**, not against the legacy code. GATE_3_BUILD
proves the rules are *present* (round-trip graph coverage); GATE_3B proves a human *read* them as
equivalent. Neither **runs both systems and compares outputs**. This skill is that missing,
executed proof: for the same inputs, does the target emit the **same outputs** as the legacy
system, to the **declared precision**? Money, rates, percentages and counts are where a rewrite
silently drifts — a COMP-3 `PIC 9(9)V99` truncated to a float loses the third decimal and no
compiler complains. GATE_3C catches exactly that.

It feeds **GATE_3C_DIFFERENTIAL**, an automated gate modeled on GATE_0_DISCOVERY: recordable and
kick-back-capable, but **not** in the advance-precondition map — so a pipeline with no golden
corpus is never blocked. A FAIL means the target diverges from the legacy golden → rewind to `build`.

## Honest prerequisite — the golden corpus

Nothing in `survey`/`extraction` captures legacy runtime I/O today (those phases are structural +
source-reading). So the **golden corpus must be supplied**: a captured run of the legacy system, or
a curated golden-file set. This skill proves *target == golden*; capturing the golden is the
documented upstream step, not magic. Without a corpus the gate is honestly **NOT_APPLICABLE** —
it does not fabricate a pass-by-absence, it states that parity was *not evaluated*.

**Worked example — `demo/differential-equivalence/`.** When the source system can't be run (the
demo `BILLING.cbl` needs VSAM + DB2 + a subprogram), build a **reference oracle**: a faithful
re-implementation of the legacy *source arithmetic*, traceable to the source lines. `billing_oracle.py`
encodes `BILLING.cbl`'s `COMPUTE INV-TAX = INV-AMOUNT * WS-TAX-RATE` with its real COBOL semantics
(**no `ROUNDED` clause → truncation** to the COMP-3 field's 2 dp). Run it (see that README) to watch
the gate flip from NOT_APPLICABLE to a real **PASS** (a target that truncates like COBOL) / **FAIL**
(a naive HALF_UP target that silently loses a cent — the COMP-3 catch). A reference oracle is the
standard golden a migration team builds when the mainframe isn't runnable — not an LLM guess.

## Inputs

- **Golden corpus** (`corpus.json`): `[{ "scenario_id", "req_id", "inputs"?, "golden_output": {field: value, …} }, …]`
  — the legacy outputs per scenario. Reuse the `scenarios[].inputs` from the test contracts as the
  input vectors; the `golden_output` is what the *legacy* system produced for them.
- **Target actual outputs** (`actuals.json`): `{ "<scenario_id>": {field: value, …}, … }` — run the
  built target over the same inputs and capture its outputs keyed by `scenario_id`.
- **Contracts** (`.anti-legacy/contracts/{domain}/{req_id}.contract.json`): the `parity_rules`
  (`{field, precision, source_type}`; `precision` is an int = decimal places, or `"exact"`) define
  the tolerance per field. The harness loads these automatically.

## Step 1: Assemble the golden corpus (via capture-corpus) + capture target outputs

Use **`anti-legacy:capture-corpus`** to assemble `corpus.json` from whatever is available — the
test contracts' `expected_output` (always present; `contract-expected`, low confidence), a source
oracle (`source-oracle`, medium), or real captured legacy I/O (`captured-legacy`, high). It tags
each entry's `provenance` and grades the overall confidence, so you never have to "invent expected
values": a low-confidence corpus is honest and still useful (the gate WARNS instead of blocking).
Produce `actuals.json` by running the built target over the same input vectors. Both live under
`.anti-legacy/evidence/`. If nothing is assemblable, the gate is honestly NOT_APPLICABLE.

## Step 2: Run the differential harness

```bash
python3 .anti-legacy/run.py differential_equivalence run \
  --corpus .anti-legacy/evidence/corpus.json \
  --actuals .anti-legacy/evidence/actuals.json \
  --contracts .anti-legacy/contracts \
  --out .anti-legacy/evidence/differential-equivalence-report.json
```

The comparator coerces numeric strings to `Decimal` and compares to the declared decimal places
(`ROUND_HALF_UP`); a non-numeric value where numeric parity is declared, or a field missing from
the actual output, is a violation. Exit `0` = PASS or NOT_APPLICABLE, `1` = FAIL, `2` = bad inputs.
The report carries `status`, an `aggregate` block, per-scenario per-field detail, **and**
`golden_confidence` + `provenance` + `warnings` + a `gate_posture` of PASS / WARN / BLOCK /
NOT_APPLICABLE (the trust-graded stance — see below).

## Step 3: Register the evidence + record the gate (graded by posture)

The gate is **provenance-graded** (ISS-7 follow-up): a parity FAIL against a low/medium-confidence
golden is a **WARNING**, not a hard failure — only a FAIL against a **captured-legacy** golden
blocks. Read `gate_posture` from the report:

```bash
python3 .anti-legacy/run.py manifest register differential-equivalence-report \
  --path evidence/differential-equivalence-report.json --format json \
  --produced-by anti-legacy:differential-equivalence --status final

# gate_posture PASS / WARN / NOT_APPLICABLE  -> record passed (evidence required). On WARN you
# MUST surface the report's `warnings` (the data could be incorrect; say why + at what confidence):
python3 .anti-legacy/run.py manifest gate GATE_3C_DIFFERENTIAL --opinion passed \
  --evaluator anti-legacy:differential-equivalence \
  --rationale "posture=<PASS|WARN|NOT_APPLICABLE>; golden_confidence=<...>; <warnings summarized>" \
  --evidence differential-equivalence-report
```

Or let the deterministic runner decide: `validator_discovery run --gate GATE_3C_DIFFERENTIAL`
returns zero on PASS / WARN / NOT_APPLICABLE (printing the warnings) and non-zero **only** on a
captured-legacy BLOCK (vacuous-safe + warn-graded).

## Step 4: On a captured-legacy BLOCK — kick back to build

Only when `gate_posture == BLOCK` (a parity FAIL against a **captured-legacy** golden — a real,
trusted divergence):

```bash
python3 .anti-legacy/run.py manifest gate GATE_3C_DIFFERENTIAL --opinion failed \
  --evaluator anti-legacy:differential-equivalence \
  --rationale "N scenario(s) diverge from CAPTURED legacy golden — see report violations"
```

A `failed` opinion rewinds `phase.current` to `build` (`anti-legacy:swarm`), exit 3. Fix the
divergence in the target at its source, rebuild, re-capture `actuals.json`, and re-run — do **not**
loosen `parity_rules` to make a money mismatch pass (the COMP-3 Universal Don't).

On a **WARN** (FAIL against a low/medium-confidence golden) do NOT record `failed` and do NOT block:
report the divergences as a warning, name the golden confidence, and recommend raising it (capture
real legacy I/O, or supply a source oracle) before treating the divergence as a build defect — the
golden itself may be wrong. Report every diverging field with its `scenario_id`, `req_id`, and the
golden-vs-actual values (§6).

## Done-gate

- `differential-equivalence-report.json` exists with a `status` of `PASS`, `FAIL`, or
  `NOT_APPLICABLE` and a populated `aggregate`.
- The gate is recorded (`passed` on PASS/NOT_APPLICABLE with the registered evidence; `failed` on
  FAIL, which kicks back to build).
- Your report states (§6): what is verifiably equal (scenarios/fields within tolerance), what is
  NOT proven (any NOT_APPLICABLE region = no corpus = unproven parity), and the next step.

## Cross-Platform Notes

The harness is pure standard-library Python (`json`, `decimal`, `os`) through the dispatcher —
identical on macOS / Linux / WSL / Windows. No shell-isms.

## Failure cases

- **No golden corpus** → `NOT_APPLICABLE`. Honest, non-blocking. State that parity was not
  evaluated; obtain a captured legacy corpus to make the gate meaningful.
- **`actuals.json` missing a scenario** → that scenario FAILs (the target produced no output).
- **Non-numeric value where numeric `precision` is declared** → violation (parity unprovable).
- **Tempted to relax a `parity_rule`** → don't. A money/rate/percent/count mismatch is the bug the
  gate exists to catch.
