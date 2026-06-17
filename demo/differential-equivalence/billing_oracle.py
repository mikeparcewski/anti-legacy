#!/usr/bin/env python3
"""Reference oracle for the demo BILLING.cbl invoice arithmetic (ISS-7 golden corpus).

The differential-equivalence gate (GATE_3C_DIFFERENTIAL) needs a GOLDEN corpus: the outputs
the LEGACY system produces for known inputs. The mainframe here is not runnable standalone —
BILLING.cbl needs a VSAM CUSTMAST file, a DB2 TAX_CONFIG table, and the PAY-GATE subprogram —
so we do what a migration team does when it can't run the source system: build a **reference
oracle from the legacy SOURCE arithmetic**, faithful to its semantics, and treat its output as
the golden. This is NOT an LLM guess at "what the answer should be" — every line traces to
BILLING.cbl, and the one semantic that matters is encoded exactly:

    BILLING.cbl 1000-CALC-INVOICE (lines 57-58):
        COMPUTE INV-TAX   = INV-AMOUNT * WS-TAX-RATE      <- INV-TAX is PIC S9(5)V99 COMP-3
        COMPUTE INV-TOTAL = INV-AMOUNT + INV-TAX          <- INV-TOTAL is PIC S9(7)V99 COMP-3

    There is **no `ROUNDED` clause**. COBOL's default is to TRUNCATE the intermediate result to
    the receiving field's scale (2 decimals for a COMP-3 V99). So INV-TAX = trunc(amount * rate, 2).
    A modern target that ROUNDS (Java BigDecimal HALF_UP, the natural default) silently diverges —
    e.g. 10.00 * 0.0725 = 0.7250 -> legacy 0.72, rounding target 0.73. That one-cent COMP-3 drift,
    multiplied across millions of invoices, is exactly what GATE_3C exists to catch.

`WS-TAX-RATE` is `PIC V9(4)` (4 decimal places). The COBOL reads it from DB2 `TAX_CONFIG`
(EXEC SQL, lines 52-56); that table's data is not in the source tree, so the demo supplies a
representative 4-decimal tax config below (in a real migration this is the captured DB2 table data).

Run:  python3 billing_oracle.py [golden|faithful|rounding]
  golden   -> the legacy golden corpus JSON  (default)
  faithful -> a target that truncates like COBOL (should PASS the gate)
  rounding -> a naive target that rounds      (should FAIL the gate on the divergent scenarios)

Pure standard library (decimal, json). Cross-platform.
"""
import json
import sys
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

REQ_ID = "BILLING-1000-CALC-INVOICE"
TWO = Decimal("0.01")  # COMP-3 V99 receiving-field scale

# Representative state -> tax rate (PIC V9(4), 4 decimals). Stands in for the DB2 TAX_CONFIG
# rows BILLING.cbl reads (EXEC SQL ... SELECT TAX_RATE ... WHERE STATE_CODE = :CUST-STATE).
TAX_CONFIG = {"CA": "0.0725", "NY": "0.0888", "TX": "0.0625", "OR": "0.0000"}

# Invoice scenarios: (scenario_id, customer state, INV-AMOUNT). Chosen so some land exactly on a
# cent (truncate == round) and some fall between cents (truncate != round) to exercise the gate.
SCENARIOS = [
    ("INV-CA-EXACT",  "CA", "100.00"),  # 7.2500 -> 7.25 either way (agree)
    ("INV-CA-TRUNC",  "CA", "10.00"),   # 0.7250 -> trunc 0.72 vs round 0.73 (DIVERGE)
    ("INV-TX-TRUNC",  "TX", "99.99"),   # 6.249375 -> trunc 6.24 vs round 6.25 (DIVERGE)
    ("INV-OR-NOTAX",  "OR", "250.00"),  # 0.0000 -> 0.00 either way (agree)
]


def _compute(amount, rate, rounding):
    """The 1000-CALC-INVOICE arithmetic with a given rounding mode for the COMP-3 stores."""
    amount, rate = Decimal(amount), Decimal(rate)
    inv_tax = (amount * rate).quantize(TWO, rounding=rounding)
    inv_total = (amount + inv_tax).quantize(TWO, rounding=rounding)
    return {"INV-TAX": format(inv_tax, "f"), "INV-TOTAL": format(inv_total, "f")}


def legacy_output(state, amount):
    """The GOLDEN: COBOL truncates (no ROUNDED clause) -> ROUND_DOWN."""
    return _compute(amount, TAX_CONFIG[state], ROUND_DOWN)


def target_output(state, amount, mode):
    """A target's output. mode='faithful' truncates like COBOL; mode='rounding' is the
    naive HALF_UP target that silently loses COMP-3 parity on between-cent results."""
    rounding = ROUND_DOWN if mode == "faithful" else ROUND_HALF_UP
    return _compute(amount, TAX_CONFIG[state], rounding)


def golden_corpus():
    """[{scenario_id, req_id, inputs, golden_output}, ...] — the differential-equivalence corpus."""
    return [{"scenario_id": sid, "req_id": REQ_ID,
             "inputs": {"CUST-STATE": state, "INV-AMOUNT": amount},
             "golden_output": legacy_output(state, amount)}
            for sid, state, amount in SCENARIOS]


def target_actuals(mode):
    """{scenario_id: actual_output} for a target running in `mode` ('faithful' | 'rounding')."""
    return {sid: target_output(state, amount, mode) for sid, state, amount in SCENARIOS}


def main(argv=None):
    what = (argv or sys.argv[1:] or ["golden"])[0]
    if what == "golden":
        out = golden_corpus()
    elif what in ("faithful", "rounding"):
        out = target_actuals(what)
    else:
        sys.stderr.write("usage: billing_oracle.py [golden|faithful|rounding]\n")
        return 2
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
