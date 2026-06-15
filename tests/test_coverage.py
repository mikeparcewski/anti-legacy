#!/usr/bin/env python3
"""Unit tests for scripts/coverage.py — the WF1 §I2 resolved-or-flagged metric.

`coverage.py` is the provable terminal of the extraction model: a node-level
"resolved-or-flagged" coverage metric over the BEHAVIOR-BEARING nodes of the
wicked-estate code graph. These tests pin its load-bearing contract from the
shared WF1 spec, using SYNTHETIC inputs only — no wicked-estate binary, no
network. They exercise three seams:

  * the pure DENOMINATOR predicate `is_behavior_bearing(node, settings)`
    (kind ∈ behavior_kinds, with data-only `module`s that have 0 outgoing
    calls/uses edges EXCLUDED, and structural/leaf kinds EXCLUDED) — driven by
    the config-resolved `coverage_settings(config)`,
  * the pure CLASSIFIER `classify_node(annotation, settings, ...)` mapping an
    overlay record to RESOLVED / RISK / (UNACCOUNTED when bare),
  * the end-to-end `compute_coverage(...)` + CLI over a synthetic in-memory node
    set (monkeypatched enumerate) and a hand-built minimal SQLite DB matching
    the stable wicked-estate intern-table schema, asserting the formula
    `(resolved + risk_flagged)/behavior_bearing`, the `coverage == 1.0`
    terminal, the non-zero gate exit + unaccounted SymbolId list when < 1.0, and
    `resolved_rate` / `mean_confidence` math + 4-dp determinism.

The CLI fixture builds the three tables coverage.py's read-only lookup queries
(`symbols(sid,sym)`, `nodes(symbol,name,kind,file)`, `edges(source,kind)`), so
the subprocess path is fully hermetic and exercises the real gate exit codes.
"""
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
COVERAGE_PATH = os.path.join(SCRIPTS_DIR, "coverage.py")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_coverage():
    """Import scripts/coverage.py, or None if WF1 has not landed it yet."""
    if not os.path.isfile(COVERAGE_PATH):
        return None
    try:
        return importlib.import_module("coverage")
    except Exception:
        return None


_COV = _load_coverage()
_SKIP_REASON = None
if _COV is None:
    _SKIP_REASON = "scripts/coverage.py (WF1 unit) not present yet"


# ----------------------------------------------------------------------------
# Synthetic fixtures.
#
# Node records mirror coverage.enumerate_nodes' output shape:
#   {symbol_id, name, kind, file, _has_behavior_out_edge}
# The `_has_behavior_out_edge` flag is the signal coverage.py uses to exclude a
# copybook/data-only `module` (kind matches but 0 outgoing calls/uses edges).
# ----------------------------------------------------------------------------
def _node(symbol_id, name, kind, file="f", out_edge=True):
    return {
        "symbol_id": symbol_id,
        "name": name,
        "kind": kind,
        "file": file,
        "_has_behavior_out_edge": out_edge,
    }


def _sample_nodes():
    """A graph slice spanning behavior-bearing and structural/leaf kinds."""
    return [
        # Behavior-bearing: program module WITH an outgoing calls/uses edge.
        _node("sym:CBACT01C", "CBACT01C", "module", "app/cbl/CBACT01C.cbl", True),
        # Behavior-bearing function / method / class / struct / interface.
        _node("sym:PROCESS", "PROCESS-ENTER-KEY", "function", "app/cbl/COMEN01C.cbl"),
        _node("sym:postTxn", "postTransaction", "method", "src/Txn.java"),
        _node("sym:Account", "Account", "class", "src/Account.java"),
        _node("sym:RecStruct", "Rec", "struct", "src/rec.rs"),
        _node("sym:Ledger", "Ledger", "interface", "src/Ledger.java"),
        # EXCLUDED — copybook / data-only module: kind module BUT 0 out edges.
        _node("sym:CVACT01Y", "CVACT01Y", "module", "app/cpy/CVACT01Y.cpy", False),
        # EXCLUDED — structural / leaf kinds carry no standalone rule.
        _node("sym:field", "ACCT-ID", "field", out_edge=False),
        _node("sym:import", "java.util.List", "import", out_edge=False),
        _node("sym:const", "MAX-LIMIT", "constant", out_edge=False),
        _node("sym:file", "CBACT01C.cbl", "file", out_edge=False),
        _node("sym:var", "WS-COUNTER", "variable", out_edge=False),
        _node("sym:param", "amount", "parameter", out_edge=False),
    ]


# The 6 behavior-bearing SymbolIds the denominator must select from _sample_nodes.
def _behavior_ids():
    return [
        "sym:Account",   # class
        "sym:CBACT01C",  # module w/ edges
        "sym:Ledger",    # interface
        "sym:PROCESS",   # function
        "sym:RecStruct", # struct
        "sym:postTxn",   # method
    ]


def _resolved_ann(symbol_id, conf, ring=0):
    """An overlay record that must classify RESOLVED: rule + conf + validated."""
    return {
        "db_id": "app",
        "symbol_id": symbol_id,
        "status": "resolved",
        "rule_id": "REQ_" + symbol_id.split(":")[-1],
        "statement": "does a thing",
        "confidence": conf,
        "validated": True,
        "provenance": "ring0:self",
        "resolved_by": "extraction-skill@ring%d" % ring,
        "ring_depth": ring,
    }


def _risk_ann(symbol_id, ring=3):
    return {
        "db_id": "app",
        "symbol_id": symbol_id,
        "status": "risk",
        "rule_id": "REQ_" + symbol_id.split(":")[-1],
        "confidence": 0.0,
        "validated": False,
        "provenance": "ring%d:exhausted" % ring,
        "risk_reason": "budget exhausted at max rings",
        "ring_depth": ring,
    }


# ----------------------------------------------------------------------------
# 1. The denominator predicate (pure, no DB).
# ----------------------------------------------------------------------------
@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class TestResolveAppDbsGraphsDir(unittest.TestCase):
    """ISS-23: per-app DB paths must anchor on the WORKSPACE graphs dir passed in
    (beside the loaded config), never the plugin-install GRAPHS_DIR from __file__.
    The no-`--db` multi-repo path otherwise searches the plugin tree and crashes."""

    def test_uses_passed_graphs_dir_not_module_default(self):
        config = {"source_apps": [{"name": "carddemo"}, {"name": "creditcard"}]}
        apps = _COV.resolve_app_dbs(config, graphs_dir="/ws/.anti-legacy/graphs")
        self.assertEqual(
            apps,
            [("carddemo", "/ws/.anti-legacy/graphs/carddemo.db"),
             ("creditcard", "/ws/.anti-legacy/graphs/creditcard.db")],
        )
        for _, db in apps:                         # never the plugin-install path
            self.assertFalse(db.startswith(_COV.GRAPHS_DIR))

    def test_explicit_db_wins_over_graphs_dir(self):
        apps = _COV.resolve_app_dbs({}, explicit_db="/tmp/x.db",
                                    graphs_dir="/ws/.anti-legacy/graphs")
        self.assertEqual(apps, [("x", "/tmp/x.db")])

    def test_default_graphs_dir_preserves_legacy_behavior(self):
        config = {"source_apps": [{"name": "app1"}]}
        apps = _COV.resolve_app_dbs(config)         # no graphs_dir -> module default
        self.assertEqual(apps, [("app1", os.path.join(_COV.GRAPHS_DIR, "app1.db"))])


class TestBehaviorBearingDenominator(unittest.TestCase):
    def setUp(self):
        self.settings = _COV.coverage_settings({})  # all defaults

    def _selected(self, nodes, settings=None):
        s = settings if settings is not None else self.settings
        return {n["symbol_id"] for n in nodes if _COV.is_behavior_bearing(n, s)}

    def test_selects_exactly_the_behavior_bearing_kinds(self):
        self.assertEqual(
            self._selected(_sample_nodes()), set(_behavior_ids()),
            "denominator must select module(+edges)/function/method/class/"
            "struct/interface and nothing else",
        )

    def test_excludes_structural_and_leaf_kinds(self):
        got = self._selected(_sample_nodes())
        for excluded in (
            "sym:field", "sym:import", "sym:const", "sym:file",
            "sym:var", "sym:param",
        ):
            self.assertNotIn(
                excluded, got,
                "structural/leaf kind node %s must not be in the denominator"
                % excluded,
            )

    def test_excludes_dataonly_module_with_zero_out_edges(self):
        # A copybook/data-only `module` (kind matches, but 0 outgoing calls/uses)
        # carries no standalone rule and must NOT inflate the denominator.
        got = self._selected(_sample_nodes())
        self.assertNotIn(
            "sym:CVACT01Y", got,
            "data-only module with 0 outgoing calls/uses edges must be excluded",
        )
        self.assertIn(
            "sym:CBACT01C", got,
            "module WITH outgoing calls/uses edges IS behavior-bearing",
        )

    def test_behavior_kinds_predicate_is_config_driven(self):
        # Narrowing behavior_kinds to {class} (a G2 non-COBOL tune) must shrink
        # the denominator to just the class node — proving the set is honored,
        # not hard-coded. estate_behavior_kinds/structural emptied so only the
        # configured language kind selects.
        settings = _COV.coverage_settings({
            "coverage": {
                "behavior_kinds": ["class"],
                "estate_behavior_kinds": [],
            }
        })
        self.assertEqual(
            self._selected(_sample_nodes(), settings), {"sym:Account"},
            "denominator must be driven by the configured behavior_kinds set",
        )

    def test_estate_resource_kind_is_behavior_bearing(self):
        # Estate behavior origins (cics_program, jcl step, db2_table) belong in
        # the denominator alongside the language kinds, and normalize from the
        # serialized estate object form {"other":"cics_program"}.
        n = _node("sym:CICSPGM", "COMEN01C", '{"other":"cics_program"}', out_edge=False)
        self.assertTrue(
            _COV.is_behavior_bearing(n, self.settings),
            "estate cics_program resource node must be behavior-bearing",
        )


# ----------------------------------------------------------------------------
# 2. The per-node classifier (pure, no DB).
# ----------------------------------------------------------------------------
@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class TestClassifyNode(unittest.TestCase):
    def setUp(self):
        self.settings = _COV.coverage_settings({})  # resolve_threshold = 0.75

    def test_bare_node_is_unaccounted(self):
        state, conf = _COV.classify_node(None, self.settings)
        self.assertEqual(state, "unaccounted")
        self.assertIsNone(conf)

    def test_resolved_above_threshold(self):
        state, conf = _COV.classify_node(_resolved_ann("sym:x", 0.9), self.settings)
        self.assertEqual(state, "resolved")
        self.assertEqual(conf, 0.9)

    def test_at_threshold_boundary_is_resolved(self):
        # confidence == resolve_threshold (0.75) is RESOLVED (>= floor).
        state, _ = _COV.classify_node(_resolved_ann("sym:x", 0.75), self.settings)
        self.assertEqual(state, "resolved")

    def test_explicit_risk_status_is_risk(self):
        state, _ = _COV.classify_node(_risk_ann("sym:x"), self.settings)
        self.assertEqual(state, "risk")

    def test_below_threshold_annotation_is_risk_not_resolved(self):
        # A resolved-tagged record below the floor must NOT count RESOLVED; the
        # crawl contract says such a settled node is on the HITL (risk) queue,
        # never silently resolved.
        ann = _resolved_ann("sym:x", 0.50)
        state, _ = _COV.classify_node(ann, self.settings)
        self.assertEqual(state, "risk")

    def test_native_validated_disagreement_demotes_to_risk(self):
        # Overlay says resolved, but the in-graph requirement_validated cross-check
        # says NOT validated (0) -> divergence -> risk (forces re-crawl).
        ann = _resolved_ann("sym:x", 0.9)
        state, _ = _COV.classify_node(
            ann, self.settings, native_validated=0
        )
        self.assertEqual(state, "risk")


# ----------------------------------------------------------------------------
# 3. End-to-end compute_coverage over synthetic nodes (monkeypatched enumerate).
# ----------------------------------------------------------------------------
@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class TestComputeCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="anti-legacy-cov-")
        self.overlay = os.path.join(self.tmp, "annotations.jsonl")
        # Patch coverage.py's node enumeration and overlay LOADER at their seams
        # so compute_coverage runs over our synthetic data with no DB and no
        # helper cross-check. (load_annotations binds its default path at
        # definition time and compute_coverage calls it with no arg, so we must
        # patch the function, not the ANNOTATIONS_PATH constant.)
        self._orig_load = _COV.load_annotations
        self._orig_enumerate = _COV.enumerate_nodes
        self._nodes = _sample_nodes()
        _COV.enumerate_nodes = lambda db_path, helper=None: list(self._nodes)
        _COV.load_annotations = lambda *a, **k: self._orig_load(self.overlay)

    def tearDown(self):
        import shutil
        _COV.load_annotations = self._orig_load
        _COV.enumerate_nodes = self._orig_enumerate
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_overlay(self, rows):
        with open(self.overlay, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def _config(self):
        # Single explicit app named "app" so the overlay db_id="app" matches.
        return {"source_apps": [{"name": "app", "path": "app", "language": "cobol"}]}

    def _compute(self):
        return _COV.compute_coverage(config=self._config(), cross_check=False)

    def test_full_resolved_is_terminal_coverage_one(self):
        self._write_overlay([_resolved_ann(s, 0.9) for s in _behavior_ids()])
        rep = self._compute()
        self.assertEqual(rep["behavior_bearing"], 6)
        self.assertEqual(rep["resolved"], 6)
        self.assertEqual(rep["risk_flagged"], 0)
        self.assertEqual(rep["unaccounted"], 0)
        self.assertEqual(rep["coverage"], 1.0)
        # total counts ALL nodes (denominator is the behavior subset of total).
        self.assertEqual(rep["total"], len(self._nodes))

    def test_resolved_and_risk_both_count_toward_coverage(self):
        ids = _behavior_ids()
        rows = [_resolved_ann(s, 0.8) for s in ids[:4]]
        rows += [_risk_ann(s) for s in ids[4:]]
        self._write_overlay(rows)
        rep = self._compute()
        self.assertEqual(rep["resolved"], 4)
        self.assertEqual(rep["risk_flagged"], 2)
        self.assertEqual(rep["unaccounted"], 0)
        self.assertEqual(rep["coverage"], 1.0)

    def test_bare_node_is_unaccounted_and_breaks_terminal(self):
        ids = _behavior_ids()
        self._write_overlay([_resolved_ann(s, 0.9) for s in ids[:5]])
        rep = self._compute()
        self.assertEqual(rep["unaccounted"], 1)
        self.assertEqual(rep["coverage"], round(5 / 6, 4))
        surfaced = [u["symbol_id"] for u in rep["unaccounted_nodes"]]
        self.assertEqual(surfaced, [ids[5]])  # the bare behavior-bearing node

    def test_resolved_rate_and_mean_confidence_math(self):
        # 4 resolved (conf 0.8, 0.9, 1.0, 0.9) + 2 risk → resolved_rate = 4/6,
        # mean_confidence = mean over the 4 resolved = 0.9 (risk conf excluded).
        ids = _behavior_ids()
        confs = [0.8, 0.9, 1.0, 0.9]
        rows = [_resolved_ann(ids[i], confs[i]) for i in range(4)]
        rows += [_risk_ann(s) for s in ids[4:]]
        self._write_overlay(rows)
        rep = self._compute()
        self.assertEqual(rep["resolved_rate"], round(4 / 6, 4))
        self.assertEqual(rep["mean_confidence"], 0.9)

    def test_floats_rounded_to_four_dp_and_unaccounted_sorted(self):
        # 1 of 6 resolved → coverage 0.1667, rounded to 4 dp; unaccounted sorted.
        ids = _behavior_ids()
        self._write_overlay([_resolved_ann(ids[0], 0.9)])
        rep = self._compute()
        self.assertEqual(rep["coverage"], round(1 / 6, 4))
        self.assertLessEqual(
            len(str(rep["coverage"]).split(".")[-1]), 4,
            "coverage float must be rounded to 4 dp for determinism",
        )
        surfaced = [u["symbol_id"] for u in rep["unaccounted_nodes"]]
        self.assertEqual(
            surfaced, sorted(surfaced),
            "unaccounted_nodes must be sorted by SymbolId for determinism",
        )
        # The 5 bare behavior nodes are exactly ids[1:].
        self.assertEqual(set(surfaced), set(ids[1:]))

    def test_report_schema_keys_present(self):
        self._write_overlay([_resolved_ann(_behavior_ids()[0], 0.9)])
        rep = self._compute()
        for key in (
            "total", "behavior_bearing", "resolved", "risk_flagged",
            "unaccounted", "coverage", "resolved_rate", "mean_confidence",
            "per_app", "unaccounted_nodes",
        ):
            self.assertIn(key, rep, "coverage report missing key %r" % key)


# ----------------------------------------------------------------------------
# 4. The CLI gate over a hermetic, hand-built SQLite DB (real exit codes).
# ----------------------------------------------------------------------------
@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class TestCoverageGateCLI(unittest.TestCase):
    """Build the minimal intern-table schema coverage.py's read-only lookup
    queries, then drive `python scripts/coverage.py --db <db>` to assert the
    gate exit codes + the emitted report. Fully hermetic: no wicked-estate
    binary, no helper, the synthetic DB IS the graph."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="anti-legacy-cov-cli-")
        self.db = os.path.join(self.tmp, "app.db")
        self._build_db(self.db, _sample_nodes())
        self.overlay = os.path.join(self.tmp, "annotations.jsonl")
        self.out_json = os.path.join(self.tmp, "coverage-report.json")
        self.out_md = os.path.join(self.tmp, "coverage-report.md")
        self.config = os.path.join(self.tmp, "config.json")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({
                "project_name": "cov-test",
                "source_apps": [],
                "coverage": {"resolve_threshold": 0.75},
            }, fh)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _build_db(path, nodes):
        """Create symbols/nodes/edges matching coverage.py's read-only queries.

        Queries: nodes JOIN symbols ON nodes.symbol=symbols.sid (s.sym,n.name,
        n.kind,n.file); edges JOIN symbols ON edges.source=symbols.sid (s.sym,
        e.kind). kind is stored JSON-quoted as the DB does (`"module"`), so
        normalize_kind round-trips it.
        """
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            cur.execute("CREATE TABLE symbols (sid INTEGER PRIMARY KEY, sym TEXT)")
            cur.execute("CREATE TABLE nodes (symbol INTEGER, name TEXT, kind TEXT, file TEXT)")
            cur.execute("CREATE TABLE edges (source INTEGER, target INTEGER, kind TEXT)")
            for i, n in enumerate(nodes):
                cur.execute("INSERT INTO symbols (sid, sym) VALUES (?, ?)",
                            (i, n["symbol_id"]))
                cur.execute(
                    "INSERT INTO nodes (symbol, name, kind, file) VALUES (?, ?, ?, ?)",
                    (i, n["name"], '"%s"' % n["kind"], n["file"]),
                )
                # Give behavior-bearing modules a real outgoing 'calls' edge so the
                # data-only-module exclusion is exercised end-to-end.
                if n["_has_behavior_out_edge"]:
                    cur.execute(
                        "INSERT INTO edges (source, target, kind) VALUES (?, ?, ?)",
                        (i, i, '"calls"'),
                    )
            conn.commit()
        finally:
            conn.close()

    def _write_overlay(self, rows):
        with open(self.overlay, "w", encoding="utf-8") as fh:
            for r in rows:
                # db_id keyed to the DB basename 'app' (resolve_app_dbs derives the
                # app name from the --db filename), which _annotation_for matches.
                r = dict(r, db_id="app")
                fh.write(json.dumps(r) + "\n")

    def _run(self):
        # Hermetic CLI driver: import coverage, repoint its overlay LOADER at our
        # temp annotations.jsonl (load_annotations binds the default path at
        # definition time, so patching the function — not the constant — is what
        # takes effect), then call main() so the gate exit code IS the process
        # exit code. --db points at our hand-built synthetic SQLite graph, so the
        # whole path is binary-free.
        driver = (
            "import sys; sys.path.insert(0, %r); import coverage; "
            "_orig = coverage.load_annotations; "
            "coverage.load_annotations = lambda *a, **k: _orig(%r); "
            "raise SystemExit(coverage.main(["
            "'--db', %r, '--config', %r, '--json', %r, '--md', %r]))"
            % (SCRIPTS_DIR, self.overlay, self.db, self.config,
               self.out_json, self.out_md)
        )
        return subprocess.run(
            [sys.executable, "-c", driver],
            cwd=self.tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=60,
        )

    def test_exit_zero_and_report_at_terminal(self):
        self._write_overlay([_resolved_ann(s, 0.9) for s in _behavior_ids()])
        res = self._run()
        self.assertEqual(
            res.returncode, 0,
            "coverage == 1.0 must exit 0 (the terminal gate). stderr=%s"
            % res.stderr,
        )
        self.assertTrue(os.path.exists(self.out_json), "report JSON not written")
        self.assertTrue(os.path.exists(self.out_md), "report MD companion not written")
        with open(self.out_json, encoding="utf-8") as fh:
            rep = json.load(fh)
        self.assertEqual(rep["coverage"], 1.0)
        self.assertEqual(rep["unaccounted"], 0)
        self.assertEqual(rep["behavior_bearing"], 6)

    def test_exit_nonzero_with_unaccounted_list_below_terminal(self):
        ids = _behavior_ids()
        self._write_overlay([_resolved_ann(s, 0.9) for s in ids[:5]])
        res = self._run()
        self.assertNotEqual(
            res.returncode, 0,
            "coverage < 1.0 must exit non-zero (gate predicate). stdout=%s"
            % res.stdout,
        )
        with open(self.out_json, encoding="utf-8") as fh:
            rep = json.load(fh)
        self.assertEqual(rep["unaccounted"], 1)
        self.assertLess(rep["coverage"], 1.0)
        surfaced = [u["symbol_id"] for u in rep["unaccounted_nodes"]]
        self.assertIn(ids[5], surfaced)
        # The unaccounted SymbolId is printed for the operator (CI gate output).
        self.assertIn(ids[5], res.stderr)


if __name__ == "__main__":
    unittest.main()
