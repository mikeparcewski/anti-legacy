# Demo: differential-equivalence on BILLING.cbl (GATE_3C_DIFFERENTIAL, ISS-7)

This worked example makes `GATE_3C_DIFFERENTIAL` **non-vacuous** on the bundled COBOL demo —
it flips the gate from `NOT_APPLICABLE` (no golden corpus) to a real `PASS`/`FAIL` verdict, and
shows it catching the canonical silent COMP-3 divergence.

## The golden source (be honest about it)

The gate needs the outputs the **legacy** system produced for known inputs. `BILLING.cbl` is not
runnable standalone here (it needs a VSAM `CUSTMAST` file, a DB2 `TAX_CONFIG` table, and the
`PAY-GATE` subprogram). So `billing_oracle.py` is a **reference oracle derived from the legacy
source arithmetic** — the standard substitute a migration team builds when it cannot run the
source system. It is *not* an LLM guess; every value traces to `BILLING.cbl 1000-CALC-INVOICE`,
and the one semantic that matters is encoded exactly:

```cobol
COMPUTE INV-TAX   = INV-AMOUNT * WS-TAX-RATE     *> INV-TAX  PIC S9(5)V99 COMP-3 — NO `ROUNDED`
COMPUTE INV-TOTAL = INV-AMOUNT + INV-TAX         *> INV-TOTAL PIC S9(7)V99 COMP-3
```

No `ROUNDED` clause → COBOL **truncates** the product to the receiving field's 2 decimals. A
modern target that **rounds** (Java `BigDecimal` HALF_UP, the natural default) diverges silently:
`10.00 × 0.0725 = 0.7250` → legacy `0.72`, rounding target `0.73`. That one cent, across millions
of invoices, is the bug GATE_3C exists to catch.

## Run it

```bash
cd demo/differential-equivalence

# 1. Generate the golden corpus (legacy truncation semantics) and two candidate target outputs:
python3 billing_oracle.py golden   > /tmp/corpus.json
python3 billing_oracle.py faithful > /tmp/actuals-faithful.json   # truncates like COBOL
python3 billing_oracle.py rounding > /tmp/actuals-rounding.json   # naive HALF_UP target

# 2. Run the gate harness against each (contracts dir supplies the parity_rules):
python3 ../../.anti-legacy/run.py differential_equivalence run \
  --corpus /tmp/corpus.json --actuals /tmp/actuals-faithful.json \
  --contracts . --out /tmp/report-faithful.json        # -> status PASS  (exit 0)

python3 ../../.anti-legacy/run.py differential_equivalence run \
  --corpus /tmp/corpus.json --actuals /tmp/actuals-rounding.json \
  --contracts . --out /tmp/report-rounding.json        # -> status FAIL  (exit 1)
```

The faithful (truncating) target matches the golden on all four scenarios → **PASS**. The naive
rounding target diverges on the two between-cent scenarios (`INV-CA-TRUNC`, `INV-TX-TRUNC`) →
**FAIL**, with the exact `INV-TAX`/`INV-TOTAL` golden-vs-actual values reported per field.

`tests/test_differential_equivalence_demo.py` locks this in (PASS for faithful, FAIL for rounding).

## Files

- `billing_oracle.py` — the source-derived reference oracle (golden corpus + faithful/rounding actuals).
- `billing.contract.json` — the `parity_rules` for `INV-TAX`/`INV-TOTAL` (precision 2, COMP-3).
