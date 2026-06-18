#!/usr/bin/env python3
"""antilegacy_core.differential_equivalence — executed target-vs-legacy output parity (ISS-7).

The round-trip check (GATE_3_BUILD) proves rules are PRESENT in the target graph; GATE_3B
proves a human READ the rules as equivalent. Neither RUNS the systems and diffs outputs.
This module is the executed proof: given a GOLDEN corpus (legacy inputs + the outputs the
legacy system produced for them) and the target's ACTUAL outputs for the same inputs, it
asserts field-by-field parity within each contract's declared `parity_rules` tolerance —
precision-aware, so COMP-3 decimal loss (silent and catastrophic) is caught.

It feeds GATE_3C_DIFFERENTIAL, an AUTOMATED gate modeled on GATE_0_DISCOVERY: recordable +
kick-back-capable, but NOT in the advance-precondition map — so a pipeline with no golden
corpus is never blocked (the gate is VACUOUS-SAFE: absent/empty corpus -> NOT_APPLICABLE,
treated as non-blocking). When a corpus IS present, ANY parity violation -> FAIL -> kick back
to `build` to fix the divergence.

HONEST PREREQUISITE: nothing in survey/extraction captures legacy I/O today, so the golden
corpus must be SUPPLIED (a captured legacy run, or a curated golden-file set). This module
proves target == golden; capturing the golden is the documented upstream step, not magic.

Comparator semantics per `parity_rule` {field, precision, source_type}:
  - precision == "exact"  -> exact (string) equality after str() coercion.
  - precision == int N    -> numeric equality to N decimal places (Decimal.quantize,
                             ROUND_HALF_UP) — the COMP-3 parity check. Non-numeric values
                             where a numeric precision is declared -> violation (can't prove parity).
  - field missing from the actual output -> violation (the target dropped an output).

CLI:   python3 .anti-legacy/run.py differential_equivalence run \
           --corpus <golden.json> --actuals <actual.json> [--contracts .anti-legacy/contracts] \
           [--out .anti-legacy/evidence/differential-equivalence-report.json] [--json]
         exit 0 = PASS or NOT_APPLICABLE · 1 = FAIL (parity violations) · 2 = bad inputs.

Golden corpus JSON:  [{"scenario_id","req_id","inputs"?,"golden_output":{field:val,...}}, ...]
Actual outputs JSON: {"<scenario_id>": {field:val,...}, ...}   (the target's output per scenario)

Pure standard library (json, decimal, os). Cross-platform.
"""
import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

PASS, FAIL, NOT_APPLICABLE = "PASS", "FAIL", "NOT_APPLICABLE"

# Gate posture — the OVERALL stance the gate takes, graded by how trustworthy the golden is.
BLOCK, WARN = "BLOCK", "WARN"

# Golden-provenance tiers, strongest -> weakest, and the confidence each implies. A parity
# verdict is only as trustworthy as the golden it compared against: a FAIL against a low-
# confidence golden may mean the GOLDEN is wrong, not the target — so the gate WARNS instead of
# hard-blocking. Only a FAIL against a HIGH-confidence (captured-legacy) golden blocks the build.
PROVENANCE_CONFIDENCE = {
    "captured-legacy":   "high",    # a real legacy run / recorded production I/O — the gold standard
    "source-oracle":     "medium",  # reference oracle faithful to the legacy SOURCE arithmetic
    "rule-derived":      "low",     # computed from the extracted business rules
    "contract-expected": "low",     # the test contracts' assumed outputs (NOT captured legacy)
    "unspecified":       "low",     # no provenance declared on the corpus entry
}
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _confidence_of(provenance):
    """Confidence tier for a provenance label (unknown labels are treated as low)."""
    return PROVENANCE_CONFIDENCE.get(provenance or "unspecified", "low")


# --------------------------------------------------------------------------------------
# Comparator — the precision-aware heart (COMP-3 safe)
# --------------------------------------------------------------------------------------

def _to_decimal(value):
    """Coerce a value (number or numeric string) to Decimal, or None if non-numeric."""
    if isinstance(value, bool):  # bool is an int subclass — never a money value
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def compare_value(golden, actual, precision):
    """Compare one field value. Returns (parity: bool, detail: str).

    precision == "exact" -> exact equality; precision == int N -> equal to N decimals.
    """
    if precision == "exact" or precision is None:
        ok = str(golden) == str(actual)
        return ok, ("exact match" if ok else "exact mismatch: %r != %r" % (golden, actual))

    # Numeric precision (decimal places) — the COMP-3 parity check.
    try:
        ndigits = int(precision)
    except (TypeError, ValueError):
        return False, "invalid precision %r (want int or 'exact')" % (precision,)

    g, a = _to_decimal(golden), _to_decimal(actual)
    if g is None or a is None:
        return False, "non-numeric value where numeric parity (precision=%s) declared: %r vs %r" % (
            precision, golden, actual)
    q = Decimal(10) ** -ndigits
    gq = g.quantize(q, rounding=ROUND_HALF_UP)
    aq = a.quantize(q, rounding=ROUND_HALF_UP)
    ok = gq == aq
    return ok, ("match to %d dp (%s)" % (ndigits, gq) if ok
                else "PARITY LOSS at %d dp: golden %s != actual %s" % (ndigits, gq, aq))


def compare_record(golden_output, actual_output, parity_rules):
    """Compare one scenario's golden vs actual output across its parity_rules.

    Returns {status, fields:[{field, precision, golden, actual, parity, detail}]}.
    A missing field in the actual output is a violation (the target dropped an output).
    """
    fields, violations = [], 0
    for rule in parity_rules or []:
        field = rule.get("field")
        precision = rule.get("precision", "exact")
        golden = (golden_output or {}).get(field)
        present = isinstance(actual_output, dict) and field in actual_output
        actual = actual_output.get(field) if present else None
        if not present:
            parity, detail = False, "field %r MISSING from actual output" % field
        else:
            parity, detail = compare_value(golden, actual, precision)
        if not parity:
            violations += 1
        fields.append({"field": field, "precision": precision, "golden": golden,
                       "actual": actual, "parity": parity, "detail": detail})
    return {"status": PASS if violations == 0 else FAIL, "violations": violations, "fields": fields}


# --------------------------------------------------------------------------------------
# Harness — corpus x actuals -> report (vacuous-safe)
# --------------------------------------------------------------------------------------

def run_harness(corpus, actuals, parity_by_req):
    """Diff a golden corpus against the target's actual outputs.

    corpus        : [{scenario_id, req_id, golden_output}, ...]
    actuals       : {scenario_id: actual_output}
    parity_by_req : {req_id: [parity_rule, ...]}  (loaded from the test contracts)

    Vacuous-safe: an empty corpus -> NOT_APPLICABLE (the gate does not block). Any field
    violation when a corpus IS present -> FAIL. The report ALSO carries `golden_confidence`
    (the WEAKEST provenance among the entries — a verdict is only as trustworthy as its weakest
    golden), the `provenance` distribution, and plain-English `warnings`, so callers can grade
    how much to trust the verdict. See gate_posture(): a FAIL against a non-captured golden is a
    WARNING, not a build defect.
    """
    if not corpus:
        return {"claim": "differential-equivalence", "status": NOT_APPLICABLE,
                "aggregate": {"scenarios": 0, "pass": 0, "fail": 0, "fields_checked": 0, "violations": 0},
                "scenarios": [], "golden_confidence": "none", "provenance": {},
                "warnings": ["no golden corpus supplied — differential equivalence NOT EVALUATED. "
                             "Run anti-legacy:capture-corpus to assemble one from what is available "
                             "(contracts' expected_output, a source oracle, or captured legacy I/O)."],
                "note": "no golden corpus supplied — differential equivalence NOT EVALUATED."}
    scenarios, n_pass, n_fail, fields_checked, total_viol = [], 0, 0, 0, 0
    prov_counts, weakest = {}, "high"
    for entry in corpus:
        sid = entry.get("scenario_id")
        req_id = entry.get("req_id")
        golden = entry.get("golden_output") or {}
        provenance = entry.get("provenance") or "unspecified"
        prov_counts[provenance] = prov_counts.get(provenance, 0) + 1
        conf = _confidence_of(provenance)
        if _CONFIDENCE_RANK[conf] < _CONFIDENCE_RANK[weakest]:
            weakest = conf
        rules = parity_by_req.get(req_id) or []
        actual = (actuals or {}).get(sid)
        if actual is None:
            rec = {"status": FAIL, "violations": len(rules) or 1,
                   "fields": [{"field": None, "parity": False,
                               "detail": "no actual output for scenario %r" % sid}]}
        else:
            rec = compare_record(golden, actual, rules)
        fields_checked += len(rec["fields"])
        total_viol += rec["violations"]
        (n_pass, n_fail) = (n_pass + 1, n_fail) if rec["status"] == PASS else (n_pass, n_fail + 1)
        scenarios.append({"scenario_id": sid, "req_id": req_id, "status": rec["status"],
                          "provenance": provenance, "violations": rec["violations"],
                          "fields": rec["fields"]})
    status = PASS if n_fail == 0 else FAIL
    return {"claim": "differential-equivalence", "status": status,
            "aggregate": {"scenarios": len(corpus), "pass": n_pass, "fail": n_fail,
                          "fields_checked": fields_checked, "violations": total_viol},
            "scenarios": scenarios,
            "golden_confidence": weakest,
            "provenance": prov_counts,
            "warnings": _confidence_warnings(weakest, prov_counts, status, n_fail),
            "note": ("all scenarios within declared parity tolerances"
                     if status == PASS else
                     "%d scenario(s) diverge from the golden output — see violations" % n_fail)}


def _confidence_warnings(golden_confidence, prov_counts, status, n_fail):
    """Plain-English 'the data could be incorrect, and here is why' warnings, keyed to what
    golden was available. Empty when the golden is captured-legacy (the verdict stands alone)."""
    if golden_confidence == "high":
        return []
    dist = ", ".join("%d %s" % (n, p) for p, n in sorted(prov_counts.items()))
    raise_it = ("To raise confidence: capture real legacy I/O (provenance 'captured-legacy'), or "
                "supply a source-derived reference oracle (provenance 'source-oracle'; see "
                "demo/differential-equivalence/).")
    warns = ["Golden confidence: %s. Provenance: %s. The legacy system was NOT captured for this "
             "run, so the golden is ASSUMED/derived behavior, not the legacy's actual output. %s"
             % (golden_confidence.upper(), dist, raise_it)]
    if status == FAIL:
        warns.append("This is a WARNING, not a hard build failure: a %d-scenario divergence against "
                     "a %s-confidence golden may mean the TARGET is wrong OR that the golden itself "
                     "is wrong. Investigate both — do not auto-fail the build on it." % (
                         n_fail, golden_confidence))
    else:
        warns.append("PASS proves the target agrees with the ASSUMED behavior, not that it matches "
                     "the real legacy — raise the golden confidence to make this PASS meaningful.")
    return warns


def gate_posture(report):
    """The gate's overall stance, graded by golden trustworthiness (ISS-7 follow-up). It is NOT a
    hard gate unless the golden is captured legacy:
      NOT_APPLICABLE -> no corpus (non-blocking).
      PASS           -> parity holds (a caveat warning rides along when confidence < high).
      WARN           -> parity FAILed but against a < high-confidence golden — surface loudly,
                        do NOT hard-block (the golden may itself be wrong; data could be incorrect).
      BLOCK          -> parity FAILed against a HIGH-confidence (captured-legacy) golden — a real
                        divergence; block and kick back to build.
    """
    status = report.get("status")
    if status == NOT_APPLICABLE:
        return NOT_APPLICABLE
    if status == PASS:
        return PASS
    return BLOCK if report.get("golden_confidence") == "high" else WARN


# --------------------------------------------------------------------------------------
# Contract loading (parity_rules per req_id) + CLI
# --------------------------------------------------------------------------------------

def load_parity_by_req(contracts_dir):
    """Map req_id -> parity_rules[] from contracts/{domain}/{req_id}.contract.json."""
    out = {}
    if not contracts_dir or not os.path.isdir(contracts_dir):
        return out
    for root, _dirs, files in os.walk(contracts_dir):
        for fn in files:
            if not fn.endswith(".contract.json"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    c = json.load(f)
            except (OSError, ValueError):
                continue
            req_id = c.get("req_id") or c.get("requirement_id") or fn[:-len(".contract.json")]
            rules = c.get("parity_rules") or []
            # Also accept parity_rules nested per-scenario (collect the union by field).
            if not rules:
                seen = {}
                for sc in c.get("scenarios") or []:
                    for r in sc.get("parity_rules") or []:
                        seen[r.get("field")] = r
                rules = list(seen.values())
            if rules:
                out[req_id] = rules
    return out


def _load_json(path, what):
    if not path or not os.path.isfile(path):
        sys.stderr.write("differential_equivalence: %s not found: %s\n" % (what, path))
        sys.exit(2)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        sys.stderr.write("differential_equivalence: %s is not valid JSON: %s\n" % (what, e))
        sys.exit(2)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="differential_equivalence",
        description="Executed target-vs-legacy output parity within contract parity_rules (ISS-7).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="diff a golden corpus against the target's actual outputs")
    r.add_argument("--corpus", required=True, help="golden corpus JSON (legacy inputs + outputs)")
    r.add_argument("--actuals", required=True, help="target actual-outputs JSON keyed by scenario_id")
    r.add_argument("--contracts", default=os.path.join(".anti-legacy", "contracts"),
                   help="contracts dir for parity_rules (default .anti-legacy/contracts)")
    r.add_argument("--out", default=os.path.join(".anti-legacy", "evidence",
                                                  "differential-equivalence-report.json"),
                   help="report output path")
    r.add_argument("--json", action="store_true", help="print the report JSON to stdout too")
    args = ap.parse_args(argv)

    corpus = _load_json(args.corpus, "corpus")
    actuals = _load_json(args.actuals, "actuals")
    parity_by_req = load_parity_by_req(args.contracts)
    report = run_harness(corpus, actuals, parity_by_req)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    agg = report["aggregate"]
    sys.stderr.write("differential-equivalence: %s — %d scenarios, %d pass, %d fail, %d violations -> %s\n" % (
        report["status"], agg["scenarios"], agg["pass"], agg["fail"], agg["violations"], args.out))
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    sys.exit(1 if report["status"] == FAIL else 0)


if __name__ == "__main__":
    main()
