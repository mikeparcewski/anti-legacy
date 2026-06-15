"""
EVAL_T4 -- the round-trip "done" check must require RULE-LEVEL COVERAGE,
not class-name existence.

THEORY (T4)
-----------
`scripts/compare_graphs.py` computes "done" as CLASS-NAME EXISTENCE only:
it reads `title` + `legacy_components`, resolves the blueprint-mapped
`class_name`, and marks PASS iff a component with that name exists in the
target graph (compare_graphs.py:66). It NEVER reads
`business_rules` / `validations` / `error_paths`, and it emits no
machine-readable coverage report. So "done" == "a class is spelled
correctly" -- the round-trip verification is hollow and gates false-pass.

WHAT "FIXED" LOOKS LIKE (the assertions below encode POST-FIX behavior)
-----------------------------------------------------------------------
Per the design's compare_spec / eval_plan, the fixed done-check must:
  * read each component's OPTIONAL `implemented_rules` array (rule-id-keyed
    evidence) and compute per-requirement coverage over the req's
    business_rules + validations + error_paths ids;
  * NEVER mark PASS when any `error_path` id is uncovered (blocking);
  * classify each req as one of {PASS, PARTIAL, FAIL};
  * EMIT `functional_comparison_report.json` (sibling of the .md report;
    path == report path with `.json`) carrying per-req
    {req_id, status, coverage, covered_rule_ids, uncovered_rule_ids, ...}
    plus an aggregate block;
  * set a NON-ZERO exit code when a req is FAIL / aggregate coverage falls
    below --min-coverage (default 1.0).

THREE CASES (all on SYNTHETIC fixtures; `.anti-legacy/` is never touched)
-------------------------------------------------------------------------
  Case A (partial evidence): XService covers RULE-001/RULE-002/VAL-001
         (strong) but NOT the error_path ERR-001 -> must be PARTIAL/FAIL,
         'ERR-001' in uncovered_rule_ids, exit code non-zero. The class
         EXISTS, so today's existence-only check wrongly PASSes -> red.
  Case B (full evidence): add ERR-001 evidence (strong) -> PASS,
         coverage == 1.0, exit 0.
  Case C (no evidence): XService present but `implemented_rules` empty
         -> NOT PASS (class-name existence alone is insufficient). Today
         this PASSes -> red.

These tests are written to FAIL against the CURRENT compare_graphs.py
(which has no rule logic, no JSON report, and exits 0). That red IS the
proof of T4.

Determinism: no network, no clock/random dependence; inputs are the
hand-authored fixtures copied into tmp_path; the report is written under
tmp_path so the real artifacts are never read or mutated.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Locations (cwd-independent; mirrors conftest's resolution)
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, os.pardir, os.pardir))
_FIXTURES_DIR = os.path.join(_THIS_DIR, "fixtures")
_COMPARE_GRAPHS = os.path.join(_REPO_ROOT, "scripts", "compare_graphs.py")

# REQ_X (from requirements_graph_enriched.json) carries 4 rule ids:
#   business_rules: RULE-001, RULE-002 ; validations: VAL-001 ; error_paths: ERR-001
_ALL_RULE_IDS = {"RULE-001", "RULE-002", "VAL-001", "ERR-001"}
_ERROR_PATH_ID = "ERR-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fixture(name):
    path = os.path.join(_FIXTURES_DIR, name)
    assert os.path.exists(path), f"missing eval fixture: {path}"
    return path


def _stage_inputs(tmp_path, target_graph_fixture):
    """Copy the synthetic req-graph, blueprint and a chosen target graph into
    tmp_path so the run touches NOTHING under .anti-legacy/. Returns the paths
    compare_graphs.py needs plus the report path (also under tmp_path)."""
    req = tmp_path / "requirements_graph.json"
    bp = tmp_path / "blueprint.json"
    tg = tmp_path / "target_graph.json"
    shutil.copyfile(_fixture("requirements_graph_enriched.json"), req)
    shutil.copyfile(_fixture("blueprint.json"), bp)
    shutil.copyfile(_fixture(target_graph_fixture), tg)
    report_md = tmp_path / "evidence" / "functional_comparison_report.md"
    return req, bp, tg, report_md


def _run_compare(tmp_path, target_graph_fixture, extra_args=None):
    """Invoke scripts/compare_graphs.py via its CLI (stable interface).

    Returns (returncode, report_md_path, report_json_path, stdout, stderr).
    The .json report path is the .md report path with a .json suffix, per the
    compare_spec ('sibling of the .md, path = report path with .json')."""
    req, bp, tg, report_md = _stage_inputs(tmp_path, target_graph_fixture)
    cmd = [
        sys.executable,
        _COMPARE_GRAPHS,
        "--requirements-graph", str(req),
        "--blueprint", str(bp),
        "--target-graph", str(tg),
        "--report", str(report_md),
    ]
    if extra_args:
        cmd.extend(extra_args)
    proc = subprocess.run(
        cmd,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    report_json = report_md.with_suffix(".json")
    return proc.returncode, report_md, report_json, proc.stdout, proc.stderr


def _load_req_row(report_json_path, req_id="REQ_X"):
    """Load the machine-readable JSON report and return REQ_X's per-req entry.

    Tolerant of the exact container shape the fix lands (a list of per-req
    dicts, or a dict keyed by req_id, or nested under a 'requirements'/'reqs'
    key) so the eval pins the CONTRACT (status + covered/uncovered rule ids),
    not an incidental layout choice."""
    assert os.path.exists(report_json_path), (
        f"compare_graphs.py did not emit the machine-readable rule-coverage "
        f"report at {report_json_path}. The fixed done-check MUST write "
        f"functional_comparison_report.json (per-req status + coverage); "
        f"the existence-only version writes only the .md."
    )
    with open(report_json_path) as f:
        data = json.load(f)

    candidates = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        for key in ("requirements", "reqs", "per_req", "rows", "comparisons"):
            val = data.get(key)
            if isinstance(val, list):
                candidates = val
                break
            if isinstance(val, dict):
                candidates = list(val.values())
                break
        if not candidates:
            # maybe the dict is itself keyed by req_id -> per-req dict
            if req_id in data and isinstance(data[req_id], dict):
                candidates = [{"req_id": req_id, **data[req_id]}]
            else:
                # fall back to any list-of-dicts value at the top level
                for val in data.values():
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        candidates = val
                        break

    for row in candidates:
        if isinstance(row, dict) and row.get("req_id") == req_id:
            return row
    raise AssertionError(
        f"REQ_X entry not found in {report_json_path}; got: "
        f"{json.dumps(data)[:600]}"
    )


def _norm_ids(value):
    """Normalize a covered/uncovered ids container (list or dict) to a set."""
    if value is None:
        return set()
    if isinstance(value, dict):
        return set(value.keys())
    return set(value)


# ---------------------------------------------------------------------------
# Sanity: the script and fixtures the eval relies on are present.
# ---------------------------------------------------------------------------
def test_t4_harness_present():
    assert os.path.exists(_COMPARE_GRAPHS), _COMPARE_GRAPHS
    _fixture("requirements_graph_enriched.json")
    _fixture("blueprint.json")
    _fixture("target_graph_no_evidence.json")
    _fixture("target_graph_partial_evidence.json")
    _fixture("target_graph_full_evidence.json")


# ---------------------------------------------------------------------------
# CASE A -- uncovered error_path can NEVER be PASS, even with the class present
# and 3/4 rules covered. This is the core of T4.
# ---------------------------------------------------------------------------
def test_t4_case_a_uncovered_error_path_is_not_pass(tmp_path):
    rc, _md, report_json, stdout, stderr = _run_compare(
        tmp_path, "target_graph_partial_evidence.json"
    )

    row = _load_req_row(report_json)
    status = str(row.get("status", "")).upper()
    uncovered = _norm_ids(row.get("uncovered_rule_ids"))
    covered = _norm_ids(row.get("covered_rule_ids"))

    # The bound class XService EXISTS, so the old existence-only check would
    # call this PASS. The fixed check must NOT: an uncovered error_path blocks.
    assert status in {"PARTIAL", "FAIL"}, (
        f"REQ_X has an UNCOVERED error_path (ERR-001) yet status={status!r}. "
        f"An uncovered error_path can never PASS (compare_spec step 4). "
        f"row={row}"
    )
    assert _ERROR_PATH_ID in uncovered, (
        f"ERR-001 (the unimplemented error_path) must appear in "
        f"uncovered_rule_ids; got uncovered={sorted(uncovered)} row={row}"
    )
    # The three implemented ids should be recognized as covered.
    assert {"RULE-001", "RULE-002", "VAL-001"}.issubset(covered), (
        f"strong evidence for RULE-001/RULE-002/VAL-001 should be counted as "
        f"covered; got covered={sorted(covered)} row={row}"
    )

    # Non-zero exit: a non-PASS req under default --min-coverage 1.0 must make
    # the run fail (so gates / manifests don't false-green).
    assert rc != 0, (
        f"compare_graphs.py exited 0 with an uncovered error_path; the "
        f"round-trip done-check must signal failure via a non-zero exit "
        f"code.\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


# ---------------------------------------------------------------------------
# CASE B -- full rule coverage (incl. the error_path) is the ONLY PASS.
# ---------------------------------------------------------------------------
def test_t4_case_b_full_coverage_passes(tmp_path):
    rc, _md, report_json, stdout, stderr = _run_compare(
        tmp_path, "target_graph_full_evidence.json"
    )

    row = _load_req_row(report_json)
    status = str(row.get("status", "")).upper()
    covered = _norm_ids(row.get("covered_rule_ids"))
    uncovered = _norm_ids(row.get("uncovered_rule_ids"))

    assert status == "PASS", (
        f"all 4 rule ids covered with strong evidence -> REQ_X must be PASS; "
        f"got status={status!r} row={row}"
    )
    assert _ALL_RULE_IDS.issubset(covered), (
        f"every REQ_X rule id should be covered; got {sorted(covered)} row={row}"
    )
    assert not uncovered, (
        f"no rule id should be uncovered at full coverage; got "
        f"{sorted(uncovered)} row={row}"
    )

    # coverage == 1.0 (accept fractional or percentage encodings)
    coverage = row.get("coverage")
    assert coverage is not None, f"per-req coverage missing; row={row}"
    assert float(coverage) in (1.0, 100.0), (
        f"coverage should be 1.0 (or 100) at full coverage; got {coverage!r}"
    )

    assert rc == 0, (
        f"compare_graphs.py should exit 0 at full coverage; got rc={rc}.\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )


# ---------------------------------------------------------------------------
# CASE C -- class exists but ZERO rule evidence: class-name existence alone is
# insufficient. (This is the real-world 'hollow' target_graph.json shape.)
# ---------------------------------------------------------------------------
def test_t4_case_c_class_exists_but_no_rules_is_not_pass(tmp_path):
    rc, _md, report_json, stdout, stderr = _run_compare(
        tmp_path, "target_graph_no_evidence.json"
    )

    row = _load_req_row(report_json)
    status = str(row.get("status", "")).upper()
    covered = _norm_ids(row.get("covered_rule_ids"))
    uncovered = _norm_ids(row.get("uncovered_rule_ids"))

    # XService EXISTS in this graph (the only reason today's check PASSes),
    # but it implements none of REQ_X's rules. Existence != done.
    assert status != "PASS", (
        f"XService exists but carries NO implemented_rules; class-name "
        f"existence alone must NOT yield PASS. got status={status!r} row={row}"
    )
    assert not covered, (
        f"no rules are implemented in the hollow target graph; covered should "
        f"be empty, got {sorted(covered)} row={row}"
    )
    # All 4 ids (including the blocking error_path) are uncovered.
    assert _ALL_RULE_IDS.issubset(uncovered), (
        f"all of REQ_X's rule ids should be uncovered; got "
        f"{sorted(uncovered)} row={row}"
    )

    assert rc != 0, (
        f"compare_graphs.py exited 0 for a class with zero rule coverage; "
        f"the done-check must fail the run.\nstdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )


# ---------------------------------------------------------------------------
# AGGREGATE -- the report must surface a rule-coverage aggregate, not just a
# class-existence 'Parity Satisfaction Rate'. The hollow graph (0/4 covered)
# must NOT report a satisfied verdict.
# ---------------------------------------------------------------------------
def test_t4_aggregate_rule_coverage_not_full_on_hollow_graph(tmp_path):
    rc, report_md, report_json, _stdout, _stderr = _run_compare(
        tmp_path, "target_graph_no_evidence.json"
    )

    assert os.path.exists(report_json), (
        f"machine-readable rule-coverage report missing at {report_json}; the "
        f"fixed done-check must emit functional_comparison_report.json "
        f"(consumed by GATE_3B/GATE_3_BUILD)."
    )
    with open(report_json) as f:
        data = json.load(f)

    # Locate the aggregate block wherever the fix puts it.
    agg = None
    if isinstance(data, dict):
        for key in ("aggregate", "summary", "totals"):
            if isinstance(data.get(key), dict):
                agg = data[key]
                break
        if agg is None and "rule_coverage" in data:
            agg = data
    assert isinstance(agg, dict) and "rule_coverage" in agg, (
        f"report must carry an aggregate rule_coverage figure; got "
        f"{json.dumps(data)[:600]}"
    )
    rule_cov = float(agg["rule_coverage"])
    assert rule_cov < 1.0 and rule_cov < 100.0, (
        f"aggregate rule_coverage over a target graph with ZERO implemented "
        f"rules must be < 100%; got {rule_cov!r}"
    )

    # The Markdown verdict must NOT claim verifiable parity, and must drop the
    # hardcoded '58 modules' string the existence-only report prints.
    assert os.path.exists(report_md), report_md
    with open(report_md) as f:
        md = f.read()
    assert "58 legacy modules" not in md, (
        "report still prints the hardcoded '58 legacy modules ... 100%' "
        "verdict; the rewrite must compute counts and gate the verdict on rule "
        "coverage, not class existence."
    )


# ---------------------------------------------------------------------------
# ISS-01 -- the round-trip verdict must be DISPOSITION-AWARE. A requirement the
# curator intentionally DROPS (the merge+reimagine case) must be excluded from
# rule_coverage and must NOT FAIL the gate -- BUT only when it carries a reason,
# so a silent drop cannot launder past GATE_3_BUILD.
# ---------------------------------------------------------------------------
def _req_graph_with_disposition(disposition, reason):
    """Load the enriched req fixture and stamp REQ_X's disposition (+ reason)."""
    with open(_fixture("requirements_graph_enriched.json")) as f:
        rg = json.load(f)
    found = False
    for d in rg.get("domains", {}).values():
        reqs = d.get("requirements", {})
        if "REQ_X" in reqs:
            reqs["REQ_X"]["disposition"] = disposition
            if reason is not None:
                reqs["REQ_X"]["disposition_reason"] = reason
            found = True
    assert found, "REQ_X not present in the enriched fixture"
    return rg


def _run_compare_req_obj(tmp_path, rg_obj, target_graph_fixture):
    """Run compare_graphs.py with a custom (in-memory) requirements graph and the
    given target-graph fixture. Returns (rc, report_json_path, stdout, stderr)."""
    req = tmp_path / "requirements_graph.json"
    bp = tmp_path / "blueprint.json"
    tg = tmp_path / "target_graph.json"
    with open(req, "w") as f:
        json.dump(rg_obj, f)
    shutil.copyfile(_fixture("blueprint.json"), bp)
    shutil.copyfile(_fixture(target_graph_fixture), tg)
    report_md = tmp_path / "evidence" / "functional_comparison_report.md"
    cmd = [
        sys.executable, _COMPARE_GRAPHS,
        "--requirements-graph", str(req),
        "--blueprint", str(bp),
        "--target-graph", str(tg),
        "--report", str(report_md),
    ]
    proc = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    return proc.returncode, report_md.with_suffix(".json"), proc.stdout, proc.stderr


def test_t4_iss01_drop_with_reason_passes_and_is_excluded(tmp_path):
    # Same target graph as Case A (partial evidence -> REQ_X would FAIL on the
    # uncovered error_path). Dropping REQ_X WITH a reason must flip the verdict.
    rg = _req_graph_with_disposition(
        "drop", "superseded by the unified ledger capability in the target design")
    rc, report_json, stdout, stderr = _run_compare_req_obj(
        tmp_path, rg, "target_graph_partial_evidence.json")
    row = _load_req_row(report_json)
    assert str(row.get("status", "")).upper() == "DROPPED", row
    with open(report_json) as f:
        agg = json.load(f)["aggregate"]
    assert agg.get("dropped_with_reason") == 1, agg
    # REQ_X's rules are excluded from the denominator -> coverage is not dragged
    # down by the intentional drop, and the gate passes.
    assert float(agg["rule_coverage"]) in (1.0, 100.0), agg
    assert rc == 0, (
        f"an honest drop+reason must PASS GATE_3_BUILD (the merge+reimagine "
        f"case); rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}")


def test_t4_iss01_drop_without_reason_still_fails(tmp_path):
    # Loophole guard: disposition=drop but NO reason -> not honored -> evaluates
    # normally -> still FAILs on the uncovered error_path.
    rg = _req_graph_with_disposition("drop", None)
    rc, report_json, stdout, stderr = _run_compare_req_obj(
        tmp_path, rg, "target_graph_partial_evidence.json")
    row = _load_req_row(report_json)
    assert str(row.get("status", "")).upper() != "DROPPED", row
    with open(report_json) as f:
        agg = json.load(f)["aggregate"]
    assert agg.get("dropped_with_reason") == 0, agg
    assert rc != 0, (
        "a reason-less 'drop' must NOT pass the gate — silent drops cannot "
        f"launder past GATE_3_BUILD.\nstdout:\n{stdout}\nstderr:\n{stderr}")


# ---------------------------------------------------------------------------
# FIX #6 -- component-name COLLISION across domains. _index_target keyed target
# components by SIMPLE class name, so two same-named classes in different domains
# overwrote each other: one component's implemented_rules evidence was silently
# lost, producing a FALSE FAIL. The fix keys by a fully-qualified id
# (domain + "." + class) and resolves the blueprint class_name -> the RIGHT
# component (by blueprint domain), still tolerating a unique simple-name match.
# ---------------------------------------------------------------------------
# All 4 of REQ_X's rule ids, each with STRONG evidence (PASS-grade).
_FULL_EVIDENCE = [
    {"rule_id": rid, "source": "test_ledger", "evidence_strength": "strong",
     "file_path": "t.java", "line_range": "1-2"}
    for rid in ("RULE-001", "RULE-002", "VAL-001", "ERR-001")
]


def _two_domain_target_graph(customer_evidence, other_evidence):
    """A target graph with the SAME class name `XService` in TWO domains:
      * `customer`  -> the component the blueprint's REQ_X actually binds to
                       (blueprint domain `Domain_customer` slugs to `customer`);
      * `billing`   -> a DECOY same-named class in a different domain.
    Each XService carries the given implemented_rules. With simple-name keying
    one silently overwrites the other; with the qualified-key fix both survive
    and REQ_X binds to the customer one."""
    def _svc(evidence):
        return {
            "type": "service",
            "file_path": "XService.java",
            "implemented_rules": list(evidence),
        }
    return {
        "generated_at": "synthetic",
        "target_path": "./target/demoapp",
        "domains": {
            "customer": {
                "package": "com.demoapp.customer",
                "components": {"XService": _svc(customer_evidence)},
                "entities": {},
            },
            "billing": {
                "package": "com.demoapp.billing",
                "components": {"XService": _svc(other_evidence)},
                "entities": {},
            },
        },
    }


def _run_compare_target_obj(tmp_path, target_obj):
    """Run compare_graphs.py against the canonical enriched req-graph + blueprint
    fixtures but a custom (in-memory) TARGET graph. Returns (rc, report_json)."""
    req = tmp_path / "requirements_graph.json"
    bp = tmp_path / "blueprint.json"
    tg = tmp_path / "target_graph.json"
    shutil.copyfile(_fixture("requirements_graph_enriched.json"), req)
    shutil.copyfile(_fixture("blueprint.json"), bp)
    with open(tg, "w") as f:
        json.dump(target_obj, f)
    report_md = tmp_path / "evidence" / "functional_comparison_report.md"
    cmd = [
        sys.executable, _COMPARE_GRAPHS,
        "--requirements-graph", str(req),
        "--blueprint", str(bp),
        "--target-graph", str(tg),
        "--report", str(report_md),
    ]
    proc = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    return proc.returncode, report_md.with_suffix(".json"), proc.stdout, proc.stderr


def test_t4_fix6_collision_does_not_shadow_correct_component(tmp_path):
    """The blueprint-bound `customer.XService` is FULLY implemented; a same-named
    `billing.XService` decoy has ZERO evidence. Simple-name keying could let the
    empty decoy overwrite the real component in the index -> a FALSE FAIL. With
    the qualified-key fix REQ_X resolves to the customer component and PASSes."""
    tg = _two_domain_target_graph(
        customer_evidence=_FULL_EVIDENCE, other_evidence=[])
    rc, report_json, stdout, stderr = _run_compare_target_obj(tmp_path, tg)
    row = _load_req_row(report_json)
    status = str(row.get("status", "")).upper()
    covered = _norm_ids(row.get("covered_rule_ids"))
    assert status == "PASS", (
        f"REQ_X binds to customer.XService (fully implemented); a same-named "
        f"decoy in another domain must not shadow it. got status={status!r} "
        f"row={row}\nstdout:\n{stdout}\nstderr:\n{stderr}")
    assert _ALL_RULE_IDS.issubset(covered), (
        f"all of REQ_X's rules are implemented on the bound component; "
        f"covered={sorted(covered)} row={row}")
    assert rc == 0, f"rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"


def test_t4_fix6_binds_to_blueprint_domain_not_collision_winner(tmp_path):
    """Mirror image: the DECOY `billing.XService` is fully implemented but the
    blueprint-bound `customer.XService` carries NO evidence. The done-check must
    bind to the BLUEPRINT domain's component (uncovered) and NOT launder the
    decoy's evidence in — so REQ_X is NOT PASS and the run fails."""
    tg = _two_domain_target_graph(
        customer_evidence=[], other_evidence=_FULL_EVIDENCE)
    rc, report_json, stdout, stderr = _run_compare_target_obj(tmp_path, tg)
    row = _load_req_row(report_json)
    status = str(row.get("status", "")).upper()
    covered = _norm_ids(row.get("covered_rule_ids"))
    assert status != "PASS", (
        f"the bound customer.XService has NO evidence; the decoy billing.XService"
        f"'s evidence must NOT be credited to REQ_X. got status={status!r} "
        f"row={row}\nstdout:\n{stdout}\nstderr:\n{stderr}")
    assert not covered, (
        f"no rule of the bound (empty) component is implemented; covered should "
        f"be empty, got {sorted(covered)} row={row}")
    assert rc != 0, (
        f"a collision must not false-PASS by crediting another domain's "
        f"evidence; rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}")
