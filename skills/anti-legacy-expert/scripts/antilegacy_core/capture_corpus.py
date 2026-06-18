#!/usr/bin/env python3
"""antilegacy_core.capture_corpus — assemble a golden corpus from what is available (ISS-7 follow-up).

GATE_3C_DIFFERENTIAL needs a golden corpus (legacy inputs + outputs). On a real project you
rarely have captured legacy I/O on day one — and you may never get it. This module assembles the
BEST AVAILABLE golden and is HONEST about how trustworthy it is, so the gate can WARN ("the data
could be incorrect, and here is why — based on what was available") instead of being vacuous or
hard-blocking on a golden that may itself be wrong.

Sources, strongest -> weakest (every corpus entry is tagged with its `provenance`; confidence
tiers live in differential_equivalence.PROVENANCE_CONFIDENCE):

  - captured-legacy   (high)   : a real legacy run / recorded production I/O          (--captured)
  - source-oracle     (medium) : a reference oracle faithful to the legacy SOURCE arithmetic (--oracle)
  - contract-expected (low)    : the test contracts' scenarios[].expected_output — the ASSUMED
                                 behavior authored from the extracted rules, NOT captured legacy.
                                 This is what EVERY project has after test-strategy, so it is the
                                 default: a corpus is always assemblable, just low-confidence.

Higher-confidence sources OVERLAY lower ones by `scenario_id`. The emitted corpus feeds
differential_equivalence.run_harness directly; the provenance report grades the overall
confidence and explains, in plain English, why the verdict should or should not be trusted.

CLI:  python3 .anti-legacy/run.py capture_corpus assemble \
          --contracts .anti-legacy/contracts \
          [--oracle <oracle-corpus.json>] [--captured <captured-corpus.json>] \
          --out .anti-legacy/evidence/corpus.json \
          [--report .anti-legacy/evidence/corpus-provenance.json]
        exit 0 = corpus assembled · 1 = nothing available to assemble from · 2 = bad inputs.

Pure standard library + antilegacy_core.differential_equivalence (confidence model). Cross-platform.
"""
import argparse
import json
import os
import sys

from antilegacy_core import differential_equivalence as de

# Contract scenario `type` values that represent a SUCCESS case (carry a golden output).
# Error/negative scenarios assert an expected_error, not an output, so they are not golden rows.
_SUCCESS_TYPES = {"success", "happy", "happy-path", "positive", "boundary", "edge", ""}


def from_contracts(contracts_dir):
    """contract-expected golden entries from every {req_id}.contract.json success scenario.

    Each contract carries scenarios[] = {id, type, inputs, expected_output, expected_error}.
    A success scenario with a non-empty expected_output becomes one corpus entry; error
    scenarios (empty expected_output / non-null expected_error) are skipped — they are not goldens.
    """
    entries = []
    if not contracts_dir or not os.path.isdir(contracts_dir):
        return entries
    for root, _dirs, files in os.walk(contracts_dir):
        for fn in sorted(files):
            if not fn.endswith(".contract.json"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    c = json.load(f)
            except (OSError, ValueError):
                continue
            req_id = c.get("req_id") or c.get("requirement_id") or fn[:-len(".contract.json")]
            for sc in c.get("scenarios") or []:
                expected = sc.get("expected_output")
                stype = (sc.get("type") or "").lower()
                # golden only when there's an actual expected output and it's not an error case
                if not expected or sc.get("expected_error") or stype not in _SUCCESS_TYPES:
                    continue
                sid = sc.get("id") or sc.get("scenario_id")
                entries.append({
                    "scenario_id": "%s::%s" % (req_id, sid) if sid else req_id,
                    "req_id": req_id,
                    "inputs": sc.get("inputs") or {},
                    "golden_output": expected,
                    "provenance": "contract-expected",
                })
    return entries


def _tagged(entries, default_provenance):
    """Yield entries, stamping `provenance` when the source did not already set it."""
    for e in entries or []:
        if not e.get("scenario_id"):
            continue
        out = dict(e)
        out.setdefault("provenance", default_provenance)
        yield out


def assemble(contracts_dir, oracle=None, captured=None):
    """Assemble the best-available golden corpus, higher-confidence sources overlaying lower
    ones by scenario_id. Returns (corpus, provenance_report)."""
    by_sid = {}
    for e in from_contracts(contracts_dir):                 # lowest precedence
        by_sid[e["scenario_id"]] = e
    for e in _tagged(oracle, "source-oracle"):              # overrides contract-expected
        by_sid[e["scenario_id"]] = e
    for e in _tagged(captured, "captured-legacy"):          # overrides everything
        by_sid[e["scenario_id"]] = e
    corpus = list(by_sid.values())
    return corpus, provenance_report(corpus)


def provenance_report(corpus):
    """Grade the assembled corpus: distribution, overall confidence (the WEAKEST tier present),
    and a plain-English warning about how much to trust a verdict built on it."""
    counts, weakest = {}, "high"
    for e in corpus:
        p = e.get("provenance") or "unspecified"
        counts[p] = counts.get(p, 0) + 1
        c = de._confidence_of(p)
        if de._CONFIDENCE_RANK[c] < de._CONFIDENCE_RANK[weakest]:
            weakest = c
    if not corpus:
        weakest = "none"
    warnings = []
    if not corpus:
        warnings.append("No golden corpus could be assembled: no contracts with expected_output, "
                        "no oracle, no captured legacy I/O. GATE_3C_DIFFERENTIAL will be NOT_APPLICABLE.")
    elif weakest != "high":
        dist = ", ".join("%d %s" % (n, p) for p, n in sorted(counts.items()))
        warnings.append("Golden confidence: %s. Provenance: %s. The legacy system was NOT captured "
                        "for at least some scenarios, so the golden is ASSUMED/derived behavior, not "
                        "the legacy's actual output — GATE_3C will WARN, not hard-block, on a "
                        "divergence. To raise confidence: capture real legacy I/O ('captured-legacy') "
                        "or supply a source-derived reference oracle ('source-oracle')." % (
                            weakest.upper(), dist))
    return {"scenarios": len(corpus), "provenance": counts,
            "golden_confidence": weakest, "warnings": warnings}


def _load_list(path, what):
    if not path:
        return []
    if not os.path.isfile(path):
        sys.stderr.write("capture_corpus: %s not found: %s\n" % (what, path))
        sys.exit(2)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        sys.stderr.write("capture_corpus: %s is not valid JSON: %s\n" % (what, e))
        sys.exit(2)
    if not isinstance(data, list):
        sys.stderr.write("capture_corpus: %s must be a JSON list of corpus entries\n" % what)
        sys.exit(2)
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="capture_corpus",
        description="Assemble a golden corpus for GATE_3C from what is available, graded by provenance.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assemble", help="assemble the best-available golden corpus + a provenance report")
    a.add_argument("--contracts", default=os.path.join(".anti-legacy", "contracts"),
                   help="contracts dir (scenarios[].expected_output -> contract-expected goldens)")
    a.add_argument("--oracle", default=None, help="source-oracle corpus JSON (medium confidence)")
    a.add_argument("--captured", default=None, help="captured-legacy corpus JSON (high confidence)")
    a.add_argument("--out", default=os.path.join(".anti-legacy", "evidence", "corpus.json"),
                   help="output corpus path")
    a.add_argument("--report", default=None, help="optional provenance-report output path")
    a.add_argument("--json", action="store_true", help="print the provenance report to stdout")
    args = ap.parse_args(argv)

    corpus, report = assemble(args.contracts, _load_list(args.oracle, "oracle"),
                              _load_list(args.captured, "captured"))

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(corpus, f, indent=2)
    if args.report:
        rdir = os.path.dirname(args.report)
        if rdir:
            os.makedirs(rdir, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    sys.stderr.write("capture_corpus: assembled %d scenario(s) -> %s (golden confidence: %s)\n" % (
        report["scenarios"], args.out, report["golden_confidence"]))
    for w in report["warnings"]:
        sys.stderr.write("  ⚠ %s\n" % w)
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    sys.exit(0 if corpus else 1)


if __name__ == "__main__":
    main()
