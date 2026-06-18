---
name: "anti-legacy:capture-corpus"
description: >
  Assemble a GOLDEN CORPUS for the differential-equivalence gate (GATE_3C_DIFFERENTIAL) from
  whatever is available on the project, and grade how trustworthy it is. Most projects never have
  pre-captured legacy I/O, so without this the gate is permanently NOT_APPLICABLE. This skill
  builds the best-available golden — from the test contracts' expected_output (always present after
  test-strategy), a source-derived reference oracle, or real captured legacy I/O — tags every entry
  with its provenance + confidence, and emits a provenance report that explains, in plain English,
  why a verdict built on it should or should not be trusted. The gate then WARNS graded by that
  confidence ("the data could be incorrect, and here is why") rather than hard-blocking on a golden
  that may itself be wrong. Only a FAIL against CAPTURED legacy blocks.
  Use when: "capture corpus", "build a golden corpus", "GATE_3C is NOT_APPLICABLE / vacuous",
  "I don't have legacy I/O", "differential equivalence has no golden", "assemble parity golden".
---

# anti-legacy:capture-corpus

`GATE_3C_DIFFERENTIAL` proves the target produces the same outputs as the legacy — but only if it
has a **golden corpus** (legacy inputs + the outputs the legacy produced). The hard truth on a real
project: **you usually do not have captured legacy I/O**, and you may never get it (the mainframe is
gone, the data is sensitive, the system can't be safely run). Left there, the gate is permanently
`NOT_APPLICABLE` and proves nothing.

This skill makes the gate *useful anyway*: it assembles the **best-available** golden from what the
project actually has, and is **honest about how much to trust it**. The gate then becomes a
**provenance-graded warning** — it tells you the parity result *and* tells you how much that result
is worth, based on where the golden came from. It is **not a hard gate** unless the golden is
captured legacy: a divergence against an assumed/derived golden is a *warning that the data could be
incorrect* (the target may be wrong, or the golden may be wrong), not a build-breaker.

## The provenance spectrum (strongest → weakest)

| Provenance | Confidence | What it is | Where it comes from |
|---|---|---|---|
| `captured-legacy` | **high** | a real legacy run / recorded production I/O | instrument or replay the legacy system; recorded outputs |
| `source-oracle` | **medium** | a reference oracle faithful to the legacy **source** arithmetic | hand-built from the source (see `demo/differential-equivalence/`) |
| `contract-expected` | **low** | the test contracts' `scenarios[].expected_output` — the **assumed** behavior authored from the extracted rules | every project has this after `anti-legacy:test-strategy` |

The gate's trust in a verdict is only as strong as the **weakest** golden it used. A FAIL against a
`captured-legacy` golden **blocks** (real divergence → kick back to `build`); a FAIL against a
`source-oracle` or `contract-expected` golden **warns** (it might be the golden that's wrong).

## Cross-Platform Notes

The one command (`capture_corpus`) is pure standard-library Python through the dispatcher — `os`,
`json`, no shell-isms — identical on macOS / Linux / WSL / Windows.

## Step 1: Assemble the best-available corpus

```bash
python3 .anti-legacy/run.py capture_corpus assemble \
  --contracts .anti-legacy/contracts \
  --out .anti-legacy/evidence/corpus.json \
  --report .anti-legacy/evidence/corpus-provenance.json --json
```

With nothing but contracts, you get a `contract-expected` (low-confidence) corpus — the assumed
behavior, NOT the legacy's actual output. That is honest and still useful: it makes the gate run and
warn. Overlay higher-confidence sources as you obtain them (they replace lower ones by `scenario_id`):

```bash
  --oracle  <source-oracle-corpus.json>     # medium: a reference oracle (see Step 3)
  --captured <captured-legacy-corpus.json>  # high:   real recorded legacy I/O
```

Exit `0` = corpus assembled, `1` = nothing available (no contracts/oracle/captured → the gate will
be `NOT_APPLICABLE`), `2` = bad inputs.

## Step 2: Read the provenance report — believe it, don't oversell it

The report carries `golden_confidence` (the weakest tier present), the `provenance` distribution,
and `warnings`. **Surface the warnings to the human.** If confidence is `low`/`medium`, a subsequent
GATE_3C `FAIL` is a *warning to investigate*, not proof the target is broken — the golden itself may
encode an assumption that is wrong. Never present a low-confidence PASS as "parity proven."

## Step 3: Raise confidence when you can (optional, recommended for money paths)

- **Source oracle (medium).** When the legacy can't be run, re-implement the *source arithmetic*
  faithfully and emit its outputs as the golden — the standard migration substitute.
  `demo/differential-equivalence/billing_oracle.py` is a worked example: it encodes `BILLING.cbl`'s
  `COMPUTE INV-TAX` with its real COBOL semantics (no `ROUNDED` → truncation) so the gate catches a
  target that silently rounds.
- **Captured legacy (high).** If you *can* run or replay the legacy system, record its inputs +
  outputs and feed them via `--captured`. This is the only golden that makes GATE_3C a **hard** gate.

## Step 4: Feed the gate

```bash
python3 .anti-legacy/run.py differential_equivalence run \
  --corpus .anti-legacy/evidence/corpus.json \
  --actuals .anti-legacy/evidence/actuals.json \
  --contracts .anti-legacy/contracts \
  --out .anti-legacy/evidence/differential-equivalence-report.json
```

The report's `gate_posture` is `PASS` / `WARN` / `BLOCK` / `NOT_APPLICABLE`. `anti-legacy:gatekeeper`
honors it: `WARN` surfaces loudly but does **not** block; only `BLOCK` (a FAIL against captured
legacy) kicks back to `build`. See `anti-legacy:differential-equivalence` for the run + record flow.

## Done-gate

- `corpus.json` exists (or the report honestly says nothing was assemblable).
- A provenance report with `golden_confidence` + `warnings` is produced and **surfaced to the human**.
- Your status report states (§6): how many scenarios, at what confidence, from which sources, and the
  explicit caveat that a low/medium-confidence verdict is advisory — what is NOT proven, and how to
  raise it.

## Failure cases

- **No contracts, no oracle, no captured I/O** → empty corpus, `golden_confidence: none`; GATE_3C
  stays `NOT_APPLICABLE`. Run `anti-legacy:test-strategy` first (it produces the contracts whose
  `expected_output` seeds a contract-expected corpus).
- **Tempted to relabel a contract-expected corpus as captured-legacy to force a hard gate** → don't.
  The whole point is honest provenance; a mislabeled golden turns a warning into a false block.
