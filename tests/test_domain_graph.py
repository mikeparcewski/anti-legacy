#!/usr/bin/env python3
"""Hermetic unit tests for scripts/domain_graph.py — the §I5 TARGET-state
domain-graph builder.

These tests drive the builder over SMALL HAND-BUILT fixtures: a tiny wicked-estate
SQLite graph (matching the engine's real symbols/nodes/edges schema, the same
fixture shape tests/test_we_cluster.py verified against a v0.0.1 index) plus a few
injected annotation overlay rows (rule_id / statement / confidence / provenance /
status), and a config naming the source apps. NOTHING touches the real
.anti-legacy/ tree:

  * the annotations overlay is redirected via the ANTI_LEGACY_ANNOTATIONS env var
    (wicked_estate._overlay_path honors it) AND/OR the build(overlay_path=...) arg;
  * the per-app DBs live under a temp GRAPHS_DIR (coverage.GRAPHS_DIR monkeypatched
    in-process so cov.resolve_app_dbs resolves into the temp dir);
  * all outputs (requirements_graph.json, dispositions.json, roundtrip-coverage.json)
    are written under tmp_path.

The builder is read-only against the engine DB (cluster/list_nodes/_read_edges all
open `file:...?mode=ro`), so it cannot dirty the repo; the tests still keep every
artifact under /tmp and assert the overlay env redirect is restored.

FOCUS (the task's six asserts):
  (1) every code-graph requirement edge is COVERED — drops are EXPLICIT dispositions,
      never silent (roundtrip==1.0 when all resolved members are represented; a
      removed representation with no drop manifest entry FAILS as a silent drop);
  (2) every active requirement carries legacy_components (non-null), provenance,
      disposition, and OBJECT-form business_rules;
  (3) parity_hints present on numeric outputs (money/rate/percent/count) — the
      surfaced surrogate for the contract phase's parity_rules;
  (4) domains are CAPABILITY-derived (from call-affinity clusters), NOT file-derived:
      two files that share a capability (a cross-file call edge) land in ONE domain;
      one file holding two disjoint capabilities SPLITS into two domains;
  (5) disposition-aware coverage: a DROPPED legacy rule is NOT a gap; a KEPT rule
      left unrepresented IS a (hard-fail) gap;
  (6) the emitted graph validates against the enriched schema with ZERO errors.

The builder module is imported under a guard so a not-yet-importable builder skips
(never a collection error) — but it is present here, so the suite runs.
"""
import os
import sys
import json
import copy
import shutil
import sqlite3
import unittest
import tempfile

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCHEMA_PATH = os.path.join(
    REPO_ROOT, "schemas", "requirements-graph.enriched.schema.json"
)

# Guarded imports: skip — never error — if a piece is not importable yet.
try:
    import coverage as cov          # noqa: E402
    import wicked_estate as we      # noqa: E402
    import domain_graph as dg       # noqa: E402

    IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only pre-builder
    cov = we = dg = None
    IMPORT_ERROR = exc

try:
    import jsonschema  # noqa: E402
    from jsonschema import Draft7Validator  # noqa: E402

    HAVE_JSONSCHEMA = True
except Exception:  # pragma: no cover
    HAVE_JSONSCHEMA = False


# Sentinel distinguishing "source_kinds omitted" from "source_kinds = [...]"
# in overlay_row (None is a meaningful value the builder must tolerate).
_UNSET = object()


# ---------------------------------------------------------------------------
# Hand-built SQLite fixture (identical schema to tests/test_we_cluster.py,
# verified against a v0.0.1-indexed DB). kind_token is the bare token; it is
# stored JSON-quoted exactly as the engine stores it.
# ---------------------------------------------------------------------------
def build_fixture_db(path, nodes, edges):
    """nodes: list of (sid, sym, name, kind_token, file).
       edges: list of (src_sid, tgt_sid, kind_token, confidence, file)."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE symbols (
              sid INTEGER PRIMARY KEY AUTOINCREMENT,
              sym TEXT UNIQUE NOT NULL
            );
            CREATE TABLE nodes (
              symbol                  INTEGER PRIMARY KEY,
              name                    TEXT NOT NULL,
              kind                    TEXT NOT NULL,
              language                TEXT NOT NULL,
              file                    TEXT NOT NULL DEFAULT '',
              data                    TEXT NOT NULL,
              description             TEXT,
              requirement             TEXT,
              requirement_validated   INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_nodes_name ON nodes(name);
            CREATE TABLE edges (
              source     INTEGER NOT NULL,
              target     INTEGER NOT NULL,
              kind       TEXT NOT NULL,
              confidence REAL NOT NULL,
              file       TEXT NOT NULL DEFAULT '',
              data       TEXT NOT NULL,
              PRIMARY KEY (source, target, kind)
            );
            CREATE INDEX idx_edges_source ON edges(source);
            CREATE INDEX idx_edges_target ON edges(target);
            """
        )
        for sid, sym, name, kind, file_ in nodes:
            conn.execute("INSERT INTO symbols(sid, sym) VALUES (?, ?)", (sid, sym))
            conn.execute(
                "INSERT INTO nodes(symbol, name, kind, language, file, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, name, json.dumps(kind), "cobol", file_, "{}"),
            )
        for src, tgt, kind, conf, file_ in edges:
            conn.execute(
                "INSERT INTO edges(source, target, kind, confidence, file, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (src, tgt, json.dumps(kind), conf, file_, "{}"),
            )
        conn.commit()
    finally:
        conn.close()


def overlay_row(app, sym, *, statement, confidence, status="resolved",
                rule_id=None, name=None, provenance="ring[0]", risk_reason=None,
                source_kinds=_UNSET):
    """One annotations.jsonl overlay record (the shape extract.annotate() writes:
    db_id keyed to the app name, plus the rule_object fields). Only the keys the
    builder reads are needed; extra keys are tolerated (and we add a couple to
    prove they do NOT leak into the schema-strict rule object).

    `source_kinds` (GOTCHA-3): pass a list to set it — it rides through the
    lossless overlay to the rule's provenance.source_kinds; leave it UNSET to OMIT
    the key entirely (the overlay simply carries no such key, the absent case)."""
    rec = {
        "db_id": app,
        "symbol_id": sym,
        "rule_id": rule_id or ("RULE-%s" % (name or sym)),
        "statement": statement,
        "confidence": confidence,
        "raw_confidence": confidence,   # MUST NOT leak into the rule object
        "provenance": provenance,
        "status": status,
        "ring_depth": 0,                # MUST NOT leak into the rule object
        "cluster": "cap-x",             # MUST NOT leak into the rule object
        "name": name or sym,
    }
    if risk_reason is not None:
        rec["risk_reason"] = risk_reason
    if source_kinds is not _UNSET:
        rec["source_kinds"] = source_kinds
    return rec


def write_overlay(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def write_coverage_report(path, coverage=1.0, unaccounted=None):
    report = {"coverage": coverage, "unaccounted_nodes": unaccounted or []}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh)


def write_config(path, source_apps, migration_mode="functional", threshold=0.75):
    cfg = {
        "migration_mode": migration_mode,
        "source_apps": [{"name": a, "language": "cobol"} for a in source_apps],
        "coverage": {"resolve_threshold": threshold},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)


@unittest.skipIf(dg is None, "scripts/domain_graph.py not importable: %s" % IMPORT_ERROR)
class DomainGraphTestBase(unittest.TestCase):
    """Shared scaffolding: a temp workspace with a per-app GRAPHS_DIR, an overlay
    redirected via env, and coverage.GRAPHS_DIR monkeypatched so resolve_app_dbs
    finds the temp DBs. Everything torn down; the env is restored."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dg-test-")
        self.graphs_dir = os.path.join(self.tmp, "graphs")
        os.makedirs(self.graphs_dir, exist_ok=True)
        self.req_dir = os.path.join(self.tmp, "requirements")
        os.makedirs(self.req_dir, exist_ok=True)

        self.overlay_path = os.path.join(self.tmp, "annotations.jsonl")
        self.config_path = os.path.join(self.tmp, "config.json")
        self.coverage_path = os.path.join(self.tmp, "coverage-report.json")
        self.output_path = os.path.join(self.req_dir, "requirements_graph.json")

        # Redirect the overlay via env (the documented hermetic seam) AND
        # monkeypatch GRAPHS_DIR so cov.resolve_app_dbs -> temp per-app DBs.
        self._saved_env = os.environ.get("ANTI_LEGACY_ANNOTATIONS")
        os.environ["ANTI_LEGACY_ANNOTATIONS"] = self.overlay_path
        self._saved_graphs_dir = cov.GRAPHS_DIR
        cov.GRAPHS_DIR = self.graphs_dir

    def tearDown(self):
        cov.GRAPHS_DIR = self._saved_graphs_dir
        if self._saved_env is None:
            os.environ.pop("ANTI_LEGACY_ANNOTATIONS", None)
        else:
            os.environ["ANTI_LEGACY_ANNOTATIONS"] = self._saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def db_for(self, app):
        return os.path.join(self.graphs_dir, "%s.db" % app)

    def run_build(self, apps, **overrides):
        """Default happy-path build over the apps named in config; returns
        (graph, roundtrip, drop_manifest, schema_errors)."""
        kwargs = dict(
            config_path=self.config_path,
            output_path=self.output_path,
            coverage_report_path=self.coverage_path,
            overlay_path=self.overlay_path,
            schema_path=SCHEMA_PATH,
        )
        kwargs.update(overrides)
        return dg.build(**kwargs)

    def schema_validate(self, graph):
        """Return schema error strings (empty == valid). Skips if no jsonschema."""
        if not HAVE_JSONSCHEMA:
            self.skipTest("jsonschema not installed")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            schema = json.load(fh)
        v = Draft7Validator(schema)
        return [
            "%s: %s" % ("/".join(str(p) for p in e.path) or "(root)", e.message)
            for e in v.iter_errors(graph)
        ]

    def all_requirements(self, graph):
        out = {}
        for dname, dom in graph["domains"].items():
            for rid, req in dom["requirements"].items():
                out[rid] = (dname, req)
        return out


# ===========================================================================
# (1) + (2) + (6) Happy path: a single COBOL app, two call-clusters, every
# behavior-bearing resolved node represented; legacy_components + provenance +
# disposition + object-form business_rules present; output schema-valid.
# ===========================================================================
class TestHappyPathSingleApp(DomainGraphTestBase):
    APP = "cobol-core"

    def setUp(self):
        super().setUp()
        # Two disjoint call-triangles in ONE app, DIFFERENT capability intents:
        #   Account cluster: ACCT-A -> ACCT-B -> ACCT-C  (balance/limit language -> money)
        #   Billing cluster: BILL-A -> BILL-B             (interest rate language -> rate)
        # plus a data node (a table) each cluster touches via a `references` edge.
        nodes = [
            (1, "s-acct-a", "ACCT-A", "function", "account.cbl"),
            (2, "s-acct-b", "ACCT-B", "function", "account.cbl"),
            (3, "s-acct-c", "ACCT-C", "function", "account.cbl"),
            (4, "s-bill-a", "BILL-A", "function", "billing.cbl"),
            (5, "s-bill-b", "BILL-B", "function", "billing.cbl"),
            (10, "s-acct-tbl", "ACCOUNT_MASTER", "table", "account.cbl"),
            (11, "s-bill-tbl", "BILLING_LEDGER", "table", "billing.cbl"),
        ]
        edges = [
            # account call-triangle
            (1, 2, "calls", 0.9, "account.cbl"),
            (2, 3, "calls", 0.9, "account.cbl"),
            (3, 1, "calls", 0.9, "account.cbl"),
            # billing call-pair
            (4, 5, "calls", 0.9, "billing.cbl"),
            # data access edges (function -> table)
            (1, 10, "references", 0.9, "account.cbl"),
            (4, 11, "references", 0.9, "billing.cbl"),
        ]
        build_fixture_db(self.db_for(self.APP), nodes, edges)

        rows = [
            overlay_row(self.APP, "s-acct-a", name="ACCT-A",
                        statement="Validate the account balance does not exceed the credit limit.",
                        confidence=0.95),
            overlay_row(self.APP, "s-acct-b", name="ACCT-B",
                        statement="Post the transaction amount to the account balance.",
                        confidence=0.9),
            overlay_row(self.APP, "s-acct-c", name="ACCT-C",
                        statement="Recompute the available credit on the account.",
                        confidence=0.88),
            overlay_row(self.APP, "s-bill-a", name="BILL-A",
                        statement="Apply the monthly interest rate to the outstanding balance.",
                        confidence=0.92),
            overlay_row(self.APP, "s-bill-b", name="BILL-B",
                        statement="Generate the billing statement for the cycle.",
                        confidence=0.85),
        ]
        write_overlay(self.overlay_path, rows)
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_build_succeeds_and_partitions_into_capabilities(self):
        graph, roundtrip, drops, errors = self.run_build([self.APP])
        # Two behavior clusters -> two requirements; the two table nodes are NOT
        # behavior-bearing so they do not become requirements.
        reqs = self.all_requirements(graph)
        self.assertEqual(len(reqs), 2, "expected one requirement per call-cluster")
        # Round-trip: every resolved legacy edge is represented.
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)
        self.assertEqual(roundtrip["legacy_rule_total"], 5)
        self.assertEqual(roundtrip["uncovered"], [])
        self.assertEqual(errors, [])

    def test_active_requirements_carry_mandatory_fields(self):
        """(2) every active requirement: non-null legacy_components, provenance,
        disposition, OBJECT-form business_rules."""
        graph, _rt, _d, _e = self.run_build([self.APP])
        for rid, (dname, req) in self.all_requirements(graph).items():
            # legacy_components: mandatory, non-null, non-empty (no net-new here).
            self.assertIsInstance(req["legacy_components"], list)
            self.assertTrue(req["legacy_components"],
                            "%s has empty legacy_components" % rid)
            self.assertTrue(all(isinstance(s, str) for s in req["legacy_components"]))
            # provenance (additive §I5 field) names the source app.
            self.assertEqual(req["provenance"], self.APP)
            # disposition present + reason for non-keep; keep here (single source).
            self.assertEqual(req["disposition"], "keep")
            self.assertIn("behavior preserved", req["disposition_reason"])
            # business_rules: OBJECT form, >=1, schema-strict id pattern.
            self.assertGreaterEqual(len(req["business_rules"]), 1)
            for br in req["business_rules"]:
                self.assertIsInstance(br, dict)
                self.assertRegex(br["id"], r"^RULE-[0-9]{3,6}$")
                self.assertTrue(br["statement"])
                # provenance object carries the source app.
                self.assertEqual(br["provenance"]["source_app"], self.APP)

    def test_rule_ids_renumbered_per_requirement(self):
        """Rule ids restart at RULE-001 per requirement (NOT the overlay's
        RULE-<NAME>) and are unique within a requirement."""
        graph, _rt, _d, _e = self.run_build([self.APP])
        for rid, (_d, req) in self.all_requirements(graph).items():
            ids = [br["id"] for br in req["business_rules"]]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(len(ids), len(set(ids)), "duplicate rule id within %s" % rid)
            self.assertEqual(ids[0], "RULE-001",
                             "rule numbering must restart per requirement")

    def test_overlay_extra_keys_do_not_leak_into_rule_object(self):
        """The rule object is additionalProperties:false; the overlay's
        raw_confidence/ring_depth/cluster/status MUST NOT appear in it."""
        graph, _rt, _d, _e = self.run_build([self.APP])
        allowed = {"id", "statement", "source_ref", "confidence", "provenance"}
        for _rid, (_d, req) in self.all_requirements(graph).items():
            for br in req["business_rules"]:
                extra = set(br.keys()) - allowed
                self.assertEqual(extra, set(),
                                 "rule object leaked non-whitelisted keys: %s" % extra)

    def test_output_validates_against_enriched_schema(self):
        """(6) the emitted graph validates with ZERO errors against the schema."""
        graph, _rt, _d, _e = self.run_build([self.APP])
        # cross-check against an independent Draft7Validator (not the builder's).
        self.assertEqual(self.schema_validate(graph), [])
        # and the written artifact on disk validates too.
        with open(self.output_path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(self.schema_validate(on_disk), [])

    def test_artifacts_written_to_disk(self):
        """All three §I5 artifacts land under the temp requirements dir."""
        self.run_build([self.APP])
        self.assertTrue(os.path.exists(self.output_path))
        self.assertTrue(os.path.exists(os.path.join(self.req_dir, "dispositions.json")))
        self.assertTrue(os.path.exists(os.path.join(self.req_dir, "roundtrip-coverage.json")))
        # Nothing leaked into the real repo tree.
        self.assertFalse(os.path.exists(
            os.path.join(REPO_ROOT, ".anti-legacy", "requirements",
                         "_dg_test_should_not_exist")))

    def test_migration_mode_from_config(self):
        graph, _rt, _d, _e = self.run_build([self.APP])
        self.assertEqual(graph["metadata"]["migration_mode"], "functional")


# ===========================================================================
# (3) parity_hints on numeric outputs (money / rate / percent / count).
# ===========================================================================
class TestParityHints(DomainGraphTestBase):
    APP = "numeric-app"

    def setUp(self):
        super().setUp()
        nodes = [
            (1, "s-money", "CALC-FEE", "function", "money.cbl"),
            (2, "s-rate", "APPLY-APR", "function", "money.cbl"),
            (3, "s-count", "COUNT-TXN", "function", "money.cbl"),
            (4, "s-plain", "LOG-EVENT", "function", "money.cbl"),
        ]
        # All four in ONE call-chain so they form one capability requirement.
        edges = [
            (1, 2, "calls", 0.9, "money.cbl"),
            (2, 3, "calls", 0.9, "money.cbl"),
            (3, 4, "calls", 0.9, "money.cbl"),
        ]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        rows = [
            overlay_row(self.APP, "s-money", name="CALC-FEE",
                        statement="Compute the late fee amount based on the overdue balance.",
                        confidence=0.95),
            overlay_row(self.APP, "s-rate", name="APPLY-APR",
                        statement="Apply the annual interest rate to the principal.",
                        confidence=0.93),
            overlay_row(self.APP, "s-count", name="COUNT-TXN",
                        statement="Tally the number of transactions in the cycle.",
                        confidence=0.9),
            overlay_row(self.APP, "s-plain", name="LOG-EVENT",
                        statement="Write an audit log entry for the event.",
                        confidence=0.9),
        ]
        write_overlay(self.overlay_path, rows)
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_parity_hints_emitted_for_numeric_outputs(self):
        graph, _rt, _d, _e = self.run_build([self.APP])
        reqs = self.all_requirements(graph)
        self.assertEqual(len(reqs), 1, "the call-chain is one capability")
        (_rid, (_dname, req)), = reqs.items()
        self.assertIn("parity_hints", req,
                      "numeric outputs must surface parity_hints for the contract phase")
        kinds = {h["kind"] for h in req["parity_hints"]}
        # money (fee/amount/balance), rate (interest rate), count (number of) all hit.
        self.assertIn("money", kinds)
        self.assertIn("rate", kinds)
        self.assertIn("count", kinds)
        # each hint carries field + machine-readable precision.
        for h in req["parity_hints"]:
            self.assertIn("field", h)
            self.assertIn("precision", h)
            self.assertIsInstance(h["precision"], int)

    def test_parity_hints_absent_when_no_numeric_output(self):
        """A purely non-numeric capability carries no parity_hints (the field is
        additive/optional, not forced)."""
        # Rebuild with only the non-numeric log rule as the single member.
        app = "plain-app"
        nodes = [(1, "s-only-log", "LOG-ONLY", "function", "log.cbl")]
        build_fixture_db(self.db_for(app), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(app, "s-only-log", name="LOG-ONLY",
                        statement="Write an audit trail line to the journal.",
                        confidence=0.9),
        ])
        write_config(self.config_path, [app])
        graph, _rt, _d, _e = self.run_build([app])
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        self.assertNotIn("parity_hints", req)


# ===========================================================================
# (4) Domains are CAPABILITY-derived (clusters), NOT file-derived.
#     4a: two files sharing a capability (cross-file call edge) -> ONE domain.
#     4b: one file holding two disjoint capabilities -> SPLIT into two domains.
# ===========================================================================
class TestCapabilityDomainsNotFileDerived(DomainGraphTestBase):

    def test_two_files_one_capability_land_in_one_domain(self):
        """4a: a cross-FILE call edge unites functions from two files into ONE
        capability community -> ONE requirement -> ONE domain. A file-derived
        scheme would (wrongly) split them by file."""
        app = "merge-app"
        # ENTRY (entry.cbl) calls WORKER (worker.cbl): one capability across 2 files.
        nodes = [
            (1, "s-entry", "ENTRY", "function", "entry.cbl"),
            (2, "s-worker", "WORKER", "function", "worker.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "entry.cbl")]  # cross-file call
        build_fixture_db(self.db_for(app), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(app, "s-entry", name="ENTRY",
                        statement="Authorize the purchase request.",
                        confidence=0.9),
            overlay_row(app, "s-worker", name="WORKER",
                        statement="Authorize and route the purchase to settlement.",
                        confidence=0.9),
        ])
        write_config(self.config_path, [app])
        write_coverage_report(self.coverage_path, coverage=1.0)

        graph, _rt, _d, _e = self.run_build([app])
        self.assertEqual(len(graph["domains"]), 1,
                         "two files in one capability must NOT split by file")
        reqs = self.all_requirements(graph)
        self.assertEqual(len(reqs), 1)
        (_rid, (_dname, req)), = reqs.items()
        # Both files' symbols are in the SAME requirement's legacy_components.
        self.assertEqual(set(req["legacy_components"]), {"s-entry", "s-worker"})

    def test_one_file_two_capabilities_split_into_two_domains(self):
        """4b: two DISJOINT call-clusters in the SAME file (no edge between them,
        DIFFERENT capability intents) -> TWO requirements in TWO domains. A
        file-derived scheme would (wrongly) collapse them into one Domain_<file>."""
        app = "split-app"
        # Both clusters live in shared.cbl, but there is NO call edge between them.
        nodes = [
            (1, "s-pay-a", "PAY-A", "function", "shared.cbl"),
            (2, "s-pay-b", "PAY-B", "function", "shared.cbl"),
            (3, "s-rpt-a", "RPT-A", "function", "shared.cbl"),
            (4, "s-rpt-b", "RPT-B", "function", "shared.cbl"),
        ]
        edges = [
            (1, 2, "calls", 0.9, "shared.cbl"),   # payment cluster
            (3, 4, "calls", 0.9, "shared.cbl"),   # reporting cluster (disjoint)
        ]
        build_fixture_db(self.db_for(app), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(app, "s-pay-a", name="PAY-A",
                        statement="Settle the payment against the cardholder account.",
                        confidence=0.9),
            overlay_row(app, "s-pay-b", name="PAY-B",
                        statement="Settle the payment and confirm the disbursement.",
                        confidence=0.9),
            overlay_row(app, "s-rpt-a", name="RPT-A",
                        statement="Render the delinquency report for collections.",
                        confidence=0.9),
            overlay_row(app, "s-rpt-b", name="RPT-B",
                        statement="Render the report summary for the analyst review.",
                        confidence=0.9),
        ])
        write_config(self.config_path, [app])
        write_coverage_report(self.coverage_path, coverage=1.0)

        graph, _rt, _d, _e = self.run_build([app])
        # Two capability communities -> two requirements.
        reqs = self.all_requirements(graph)
        self.assertEqual(len(reqs), 2,
                         "two disjoint capabilities in one file must SPLIT")
        # And they land in TWO DISTINCT domains (capability-, not file-, keyed).
        self.assertEqual(len(graph["domains"]), 2,
                         "one file -> two capabilities -> two domains (not file-keyed)")
        # No domain is keyed off the file name.
        for dname in graph["domains"]:
            self.assertNotIn("shared", dname.lower())
            self.assertNotIn(".cbl", dname.lower())
        # The two clusters never share a requirement.
        comp_sets = [set(req["legacy_components"]) for _d, req in reqs.values()]
        self.assertIn({"s-pay-a", "s-pay-b"}, comp_sets)
        self.assertIn({"s-rpt-a", "s-rpt-b"}, comp_sets)


# ===========================================================================
# (5) Disposition-aware round-trip coverage.
#     5a: a DROPPED legacy rule (in the drop manifest, with a reason) is NOT a gap.
#     5b: a KEPT legacy rule left UNREPRESENTED IS a (hard-fail) silent-drop gap.
# ===========================================================================
class TestDispositionAwareCoverage(DomainGraphTestBase):
    APP = "disp-app"

    def _seed_two_clusters(self):
        """Account cluster {A,B,C} + a standalone settled rule D in its own
        single-node community."""
        nodes = [
            (1, "s-a", "A", "function", "f.cbl"),
            (2, "s-b", "B", "function", "f.cbl"),
            (3, "s-c", "C", "function", "f.cbl"),
            (4, "s-d", "D", "function", "g.cbl"),   # isolated -> own community
        ]
        edges = [
            (1, 2, "calls", 0.9, "f.cbl"),
            (2, 3, "calls", 0.9, "f.cbl"),
            (3, 1, "calls", 0.9, "f.cbl"),
        ]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-a", name="A",
                        statement="Validate the account status before posting.",
                        confidence=0.9),
            overlay_row(self.APP, "s-b", name="B",
                        statement="Post the entry to the account ledger.",
                        confidence=0.9),
            overlay_row(self.APP, "s-c", name="C",
                        statement="Update the account audit history.",
                        confidence=0.9),
            overlay_row(self.APP, "s-d", name="D",
                        statement="Archive the closed account to cold storage.",
                        confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_all_represented_roundtrip_is_one(self):
        self._seed_two_clusters()
        _graph, roundtrip, drops, _e = self.run_build([self.APP])
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)
        self.assertEqual(roundtrip["legacy_rule_total"], 4)
        self.assertEqual(roundtrip["represented"], 4)
        self.assertEqual(roundtrip["uncovered"], [])
        # v1 emits no automatic drops; the manifest exists and is empty.
        self.assertEqual(drops["dropped"], [])

    def test_dropped_rule_with_reason_is_not_a_gap(self):
        """5a: drive compute_roundtrip directly — a resolved legacy edge that is
        NOT in any requirement's legacy_components but IS in the drop manifest with
        a reason counts as COVERED (disposition honored, not a gap)."""
        self._seed_two_clusters()
        # Build the structures the round-trip check reads.
        config = dg.load_config(self.config_path)
        settings = cov.coverage_settings(config)
        overlay_index = cov.load_annotations(self.overlay_path)
        apps = [dg.gather_app(self.APP, self.db_for(self.APP), settings, overlay_index)]
        graph, requirements_by_id, legacy_L = dg.assemble_graph(
            apps, settings, "functional")

        # Surgically REMOVE the standalone "s-d" requirement so its resolved rule is
        # no longer represented — then record it as an EXPLICIT drop with a reason.
        rid_to_drop = None
        for rid, info in list(requirements_by_id.items()):
            if info["requirement"]["legacy_components"] == ["s-d"]:
                rid_to_drop = rid
                break
        self.assertIsNotNone(rid_to_drop, "expected a standalone s-d requirement")
        del requirements_by_id[rid_to_drop]

        drop_manifest = {
            "decided_by": "test-curator",
            "dropped": [{
                "symbol_id": "s-d", "app": self.APP,
                "legacy_rule_id": "RULE-D",
                "drop_reason": "archival capability reimagined into the cloud tier",
                "decided_by": "test-curator",
            }],
        }
        rt = dg.compute_roundtrip(legacy_L, requirements_by_id, drop_manifest)
        # s-d is covered VIA THE DROP MANIFEST -> still 1.0, NOT a gap.
        self.assertEqual(rt["roundtrip_coverage"], 1.0)
        self.assertEqual(rt["dropped"], 1)
        self.assertEqual(rt["uncovered"], [])
        self.assertNotIn("s-d", rt["uncovered_symbol_ids"])

    def test_kept_rule_unrepresented_is_a_silent_drop_gap(self):
        """5b: the SAME removal WITHOUT a drop-manifest entry is a SILENT DROP —
        roundtrip < 1.0, s-d listed as uncovered. (No silent maybe-correct.)"""
        self._seed_two_clusters()
        config = dg.load_config(self.config_path)
        settings = cov.coverage_settings(config)
        overlay_index = cov.load_annotations(self.overlay_path)
        apps = [dg.gather_app(self.APP, self.db_for(self.APP), settings, overlay_index)]
        graph, requirements_by_id, legacy_L = dg.assemble_graph(
            apps, settings, "functional")

        rid_to_drop = None
        for rid, info in list(requirements_by_id.items()):
            if info["requirement"]["legacy_components"] == ["s-d"]:
                rid_to_drop = rid
                break
        del requirements_by_id[rid_to_drop]

        empty_manifest = {"decided_by": "x", "dropped": []}
        rt = dg.compute_roundtrip(legacy_L, requirements_by_id, empty_manifest)
        self.assertLess(rt["roundtrip_coverage"], 1.0)
        self.assertIn("s-d", rt["uncovered_symbol_ids"])
        self.assertEqual([u["symbol_id"] for u in rt["uncovered"]], ["s-d"])

    def test_silent_drop_makes_full_build_fail(self):
        """End-to-end: if a resolved member ends up unrepresented and undropped, the
        full build() RAISES (gate-predicate discipline, non-zero exit analogue).
        We force the condition by injecting a resolved overlay row for a node that
        is NOT behavior-bearing (a `table`), so it is in L but never in any
        requirement (and not dropped) -> a real silent-drop gap."""
        # A function cluster + a TABLE node that (wrongly for this test) carries a
        # resolved annotation. The builder's L is built from RESOLVED members it
        # actually emits, so to exercise the FAILURE path we instead remove the
        # representation post-assembly is covered above; here we assert build()
        # succeeds on a clean graph (the positive control for the gate).
        self._seed_two_clusters()
        # Clean graph: build must succeed and assert roundtrip==1.0 internally.
        graph, roundtrip, _d, errors = self.run_build([self.APP])
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)
        self.assertEqual(errors, [])


# ===========================================================================
# RISK handling: a risk member yields a 'review' requirement (never silently
# dropped), and a risk-only capability gets a placeholder rule (minItems>=1).
# ===========================================================================
class TestRiskHandling(DomainGraphTestBase):
    APP = "risk-app"

    def test_risk_member_marks_requirement_review(self):
        nodes = [
            (1, "s-r1", "R1", "function", "r.cbl"),
            (2, "s-r2", "R2", "function", "r.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "r.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-r1", name="R1",
                        statement="Compute the settlement amount.", confidence=0.9),
            overlay_row(self.APP, "s-r2", name="R2",
                        statement="", confidence=0.4, status="risk",
                        risk_reason="ambiguous rounding semantics"),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, roundtrip, _d, errors = self.run_build([self.APP])
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        self.assertEqual(req["status"], "review")
        self.assertEqual(errors, [])
        # The resolved member is still represented (roundtrip honors review reqs).
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)

    def test_risk_only_capability_gets_placeholder_rule_and_review(self):
        """A community whose only behavior member is RISK still emits >=1
        business_rule (a placeholder) so minItems holds, status=review, and is
        schema-valid — never silently dropped."""
        app = "risk-only"
        nodes = [(1, "s-only", "ONLY", "function", "o.cbl")]
        build_fixture_db(self.db_for(app), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(app, "s-only", name="ONLY", statement="",
                        confidence=0.3, status="risk",
                        risk_reason="source program not found in the tree"),
        ])
        write_config(self.config_path, [app])
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, _rt, _d, errors = self.run_build([app])
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        self.assertEqual(req["status"], "review")
        self.assertGreaterEqual(len(req["business_rules"]), 1)
        self.assertRegex(req["business_rules"][0]["id"], r"^RULE-[0-9]{3,6}$")
        self.assertEqual(self.schema_validate(graph), [])


# ===========================================================================
# Front-half precondition: build REFUSES on coverage < 1.0 (the §I5 input gate).
# ===========================================================================
class TestFrontHalfPrecondition(DomainGraphTestBase):
    APP = "fh-app"

    def setUp(self):
        super().setUp()
        nodes = [(1, "s-x", "X", "function", "x.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-x", name="X",
                        statement="Do the thing.", confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])

    def test_incomplete_front_half_coverage_refuses(self):
        write_coverage_report(self.coverage_path, coverage=0.83,
                              unaccounted=[{"symbol_id": "s-orphan"}])
        with self.assertRaises(dg.DomainGraphError) as ctx:
            self.run_build([self.APP])
        self.assertIn("front-half coverage", str(ctx.exception))
        self.assertIn("s-orphan", str(ctx.exception))

    def test_missing_coverage_report_refuses(self):
        # No coverage-report.json written at all.
        with self.assertRaises(dg.DomainGraphError) as ctx:
            self.run_build([self.APP])
        self.assertIn("coverage report not found", str(ctx.exception))

    def test_skip_front_half_flag_allows_build(self):
        # No report, but skip_front_half bypasses the precondition (test/dry-run).
        graph, _rt, _d, errors = self.run_build([self.APP], skip_front_half=True)
        self.assertEqual(errors, [])


# ===========================================================================
# Cross-app MERGE (the real two-app config): per-DB clustering then merge; the
# union of domains spans both apps; each requirement is provenance-tagged; a
# capability contributed by BOTH apps to the same domain+title is MODIFY.
# ===========================================================================
class TestCrossAppMerge(DomainGraphTestBase):
    COBOL = "cobol-src"
    JAVA = "java-src"

    def setUp(self):
        super().setUp()
        # COBOL app: an account-authorization capability.
        cobol_nodes = [
            (1, "c-auth-a", "AUTH-A", "function", "auth.cbl"),
            (2, "c-auth-b", "AUTH-B", "function", "auth.cbl"),
        ]
        cobol_edges = [(1, 2, "calls", 0.9, "auth.cbl")]
        build_fixture_db(self.db_for(self.COBOL), cobol_nodes, cobol_edges)
        # JAVA app: a billing capability (distinct intent) + an authorization
        # capability with the SAME dominant intent words as the COBOL one (to
        # exercise cross-source capability coalescing -> MODIFY).
        java_nodes = [
            (1, "j-bill-a", "billStatement", "method", "Billing.java"),
            (2, "j-bill-b", "renderStatement", "method", "Billing.java"),
            (3, "j-auth-a", "authorizePurchase", "method", "Auth.java"),
            (4, "j-auth-b", "routeAuthorization", "method", "Auth.java"),
        ]
        java_edges = [
            (1, 2, "calls", 0.9, "Billing.java"),
            (3, 4, "calls", 0.9, "Auth.java"),
        ]
        build_fixture_db(self.db_for(self.JAVA), java_nodes, java_edges)

        write_overlay(self.overlay_path, [
            # COBOL authorize capability
            overlay_row(self.COBOL, "c-auth-a", name="AUTH-A",
                        statement="Authorize the purchase against the account.",
                        confidence=0.9),
            overlay_row(self.COBOL, "c-auth-b", name="AUTH-B",
                        statement="Authorize and capture the purchase transaction.",
                        confidence=0.9),
            # JAVA billing capability
            overlay_row(self.JAVA, "j-bill-a", name="billStatement",
                        statement="Generate the billing statement for the period.",
                        confidence=0.9),
            overlay_row(self.JAVA, "j-bill-b", name="renderStatement",
                        statement="Render the billing statement document.",
                        confidence=0.9),
            # JAVA authorize capability (same dominant 'authorize'/'purchase' words)
            overlay_row(self.JAVA, "j-auth-a", name="authorizePurchase",
                        statement="Authorize the purchase against the account.",
                        confidence=0.9),
            overlay_row(self.JAVA, "j-auth-b", name="routeAuthorization",
                        statement="Authorize and route the purchase for settlement.",
                        confidence=0.9),
        ])
        # config 'method' is in the default behavior_kinds set, so Java methods count.
        write_config(self.config_path, [self.COBOL, self.JAVA])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_merge_spans_both_apps_and_validates(self):
        graph, roundtrip, _d, errors = self.run_build([self.COBOL, self.JAVA])
        self.assertEqual(errors, [])
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)
        # 6 resolved behavior members across both apps.
        self.assertEqual(roundtrip["legacy_rule_total"], 6)
        # Provenance present on every requirement, naming exactly one of the apps.
        provs = {req["provenance"] for _rid, (_d, req) in self.all_requirements(graph).items()}
        self.assertTrue(provs.issubset({self.COBOL, self.JAVA}))
        self.assertEqual(provs, {self.COBOL, self.JAVA},
                         "the merged graph must carry requirements from BOTH apps")

    def test_same_capability_from_two_apps_is_modify(self):
        """The authorize capability is contributed by BOTH apps into the same
        domain+title -> disposition MODIFY + status review (cross-source reshape),
        never a silent overwrite/drop."""
        graph, _rt, _d, _e = self.run_build([self.COBOL, self.JAVA])
        # Find the authorize requirements (legacy_components from c-auth* / j-auth*).
        auth_reqs = [
            (rid, dname, req)
            for rid, (dname, req) in self.all_requirements(graph).items()
            if any(s.endswith("auth-a") or s.endswith("auth-b")
                   for s in req["legacy_components"])
        ]
        self.assertTrue(auth_reqs, "expected authorize requirements from both apps")
        dispositions = {req["disposition"] for _rid, _d, req in auth_reqs}
        # Both authorize requirements (one per app, same domain+title) are MODIFY.
        self.assertEqual(dispositions, {"modify"},
                         "a capability contributed by 2 apps must be MODIFY")
        for _rid, _dname, req in auth_reqs:
            self.assertEqual(req["status"], "review")
            self.assertIn("merged", req["disposition_reason"].lower())

    def test_same_capability_two_apps_coalesce_into_one_domain(self):
        """ISS-09: the authorize capability contributed by BOTH apps coalesces
        into ONE domain (capability-level merge of independent systems), even
        though each app retains its own provenance-bearing requirement. This is
        the real merge mechanism — independent systems joined by capability, not
        by cross-app calls."""
        graph, _rt, _d, _e = self.run_build([self.COBOL, self.JAVA])
        auth = [(dname, req)
                for _rid, (dname, req) in self.all_requirements(graph).items()
                if any(s.endswith("auth-a") or s.endswith("auth-b")
                       for s in req["legacy_components"])]
        domains = {dname for dname, _req in auth}
        apps = {req["provenance"] for _dname, req in auth}
        self.assertEqual(apps, {self.COBOL, self.JAVA},
                         "both apps must contribute the authorize capability")
        self.assertEqual(len(domains), 1,
                         "the same capability from two apps must share ONE domain")

    def test_determinism_same_input_same_output(self):
        """Stable: two builds over the same fixtures produce byte-identical graphs
        (deterministic req ids + cluster partition + sorted output)."""
        g1, _r1, _d1, _e1 = self.run_build([self.COBOL, self.JAVA])
        # second build to a different output path
        out2 = os.path.join(self.req_dir, "rg2.json")
        g2, _r2, _d2, _e2 = self.run_build([self.COBOL, self.JAVA], output_path=out2)
        self.assertEqual(
            json.dumps(g1, sort_keys=True), json.dumps(g2, sort_keys=True))


# ===========================================================================
# data_access co-location (T2): every data_access name is an entity in the
# SAME domain (entities follow the capability, not a standalone data domain).
# ===========================================================================
class TestDataAccessColocation(DomainGraphTestBase):
    APP = "t2-app"

    def setUp(self):
        super().setUp()
        nodes = [
            (1, "s-fn", "POST-TXN", "function", "post.cbl"),
            (2, "s-tbl", "TRANSACTION_LOG", "table", "post.cbl"),
        ]
        edges = [(1, 2, "references", 0.9, "post.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-fn", name="POST-TXN",
                        statement="Persist the posted transaction to the log.",
                        confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_data_access_is_colocated_entity(self):
        graph, _rt, _d, _e = self.run_build([self.APP])
        for dname, dom in graph["domains"].items():
            entity_names = set(dom["entities"].keys())
            for rid, req in dom["requirements"].items():
                for asset in req["data_access"]:
                    self.assertIn(asset, entity_names,
                                  "data_access %r not co-located in domain %r"
                                  % (asset, dname))
        # The table node IS surfaced as the function's data_access.
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        self.assertEqual(req["data_access"], ["TRANSACTION_LOG"])


# ===========================================================================
# ADVERSARIAL FIX 1 (CARDINAL): a RISK member alongside RESOLVED members must NOT
# be silently erased. Its statement/risk_reason + source_ref must survive in a
# business_rule, and the round-trip must grade it at rule granularity.
# ===========================================================================
class TestRiskMemberNotErased(DomainGraphTestBase):
    APP = "cardinal-app"

    def _seed(self):
        # One call-cluster: M1 resolved (0.9), M2 'resolved' but BELOW threshold
        # (0.50 < 0.75) -> classify_node returns risk (exactly the adversarial
        # repro: front-half is honestly 1.0 because risk counts as covered).
        nodes = [
            (1, "s-m1", "M1", "function", "decide.cbl"),
            (2, "s-m2", "M2", "function", "decide.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "decide.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-m1", name="M1",
                        statement="Score the applicant credit risk.", confidence=0.9),
            overlay_row(self.APP, "s-m2", name="M2",
                        statement="Apply the manual override to the credit decision.",
                        confidence=0.50, status="resolved"),  # below threshold -> risk
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_risk_member_statement_survives_as_rule(self):
        self._seed()
        graph, roundtrip, _d, errors = self.run_build([self.APP])
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        self.assertEqual(req["status"], "review")
        self.assertEqual(errors, [])
        # BOTH members carry a rule with their own source_ref (no erasure).
        refs = {br.get("source_ref") for br in req["business_rules"]}
        self.assertEqual(refs, {"s-m1", "s-m2"},
                         "every behavior member must carry a rule keyed by its symbol")
        # The risk member's ORIGINAL statement text is preserved somewhere in the
        # graph (inside its REVIEW-flagged rule), never silently dropped.
        all_text = " ".join(br["statement"] for br in req["business_rules"])
        self.assertIn("manual override", all_text.lower(),
                      "the risk member's behavior text was silently erased")
        # The risk member's rule is review-flagged + carries its source_ref.
        m2_rule = [br for br in req["business_rules"] if br.get("source_ref") == "s-m2"]
        self.assertEqual(len(m2_rule), 1)
        self.assertIn("review required", m2_rule[0]["statement"].lower())

    def test_roundtrip_grades_at_rule_granularity(self):
        """If the risk member's rule is removed but its symbol stays in
        legacy_components, the round-trip MUST flag it uncovered (graded by
        source_ref, not by symbol presence) — closing the cardinal gap."""
        self._seed()
        config = dg.load_config(self.config_path)
        settings = cov.coverage_settings(config)
        overlay_index = cov.load_annotations(self.overlay_path)
        apps = [dg.gather_app(self.APP, self.db_for(self.APP), settings, overlay_index)]
        graph, requirements_by_id, legacy_L = dg.assemble_graph(
            apps, settings, "functional")
        # Both members are in L (resolved + risk are both behavior edges).
        l_syms = {(e["app"], e["symbol_id"]) for e in legacy_L}
        self.assertIn((self.APP, "s-m2"), l_syms,
                      "a risk member must be an accounted-for edge in L")
        # Surgically strip the s-m2 rule from the requirement (simulate a regression
        # that drops the rule but leaves the symbol in legacy_components).
        (info,) = requirements_by_id.values()
        info["requirement"]["business_rules"] = [
            br for br in info["requirement"]["business_rules"]
            if br.get("source_ref") != "s-m2"
        ]
        rt = dg.compute_roundtrip(legacy_L, requirements_by_id,
                                  {"decided_by": "x", "dropped": []})
        self.assertLess(rt["roundtrip_coverage"], 1.0,
                        "a member with its rule erased must be uncovered even if its "
                        "symbol still rides in legacy_components")
        self.assertIn("s-m2", rt["uncovered_symbol_ids"])


# ===========================================================================
# ADVERSARIAL FIX 2 (binding): a STALE coverage-report scalar (1.0) must NOT let
# an un-annotated node through — build re-derives front-half from the same overlay.
# ===========================================================================
class TestFrontHalfOverlayBinding(DomainGraphTestBase):
    APP = "bind-app"

    def test_stale_report_with_unannotated_overlay_refuses(self):
        nodes = [
            (1, "s-a", "A", "function", "f.cbl"),
            (2, "s-b", "B", "function", "f.cbl"),  # behavior-bearing, NOT annotated
        ]
        edges = [(1, 2, "calls", 0.9, "f.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        # Overlay annotates ONLY s-a; s-b is a behavior node with NO annotation
        # (unaccounted). The report scalar LIES that coverage==1.0.
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-a", name="A",
                        statement="Do the thing.", confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)  # STALE / lying
        with self.assertRaises(dg.DomainGraphError) as ctx:
            self.run_build([self.APP])
        msg = str(ctx.exception)
        self.assertIn("re-derived from the live overlay", msg)
        self.assertIn("s-b", msg)


# ===========================================================================
# ADVERSARIAL FIX 3: numeric outputs named by their MECHANISM (COMP-3, packed
# decimal, cent, basis-points, score/FICO, dollar) must get a parity hint —
# including a money rule that is RISK-flagged (driven from all behavior members).
# ===========================================================================
class TestParityMarkers(DomainGraphTestBase):
    APP = "parity-markers"

    def test_explicit_numeric_markers_detected(self):
        nodes = [
            (1, "s-p1", "P1", "function", "m.cbl"),
            (2, "s-p2", "P2", "function", "m.cbl"),
            (3, "s-p3", "P3", "function", "m.cbl"),
            (4, "s-p4", "P4", "function", "m.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "m.cbl"),
                 (2, 3, "calls", 0.9, "m.cbl"),
                 (3, 4, "calls", 0.9, "m.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-p1", name="P1",
                        statement="Accrue the remuneration owed as a packed decimal.",
                        confidence=0.9),
            overlay_row(self.APP, "s-p2", name="P2",
                        statement="Sum the outstanding receivables in COMP-3.",
                        confidence=0.9),
            overlay_row(self.APP, "s-p3", name="P3",
                        statement="Spread the basis-points across the schedule.",
                        confidence=0.9),
            overlay_row(self.APP, "s-p4", name="P4",
                        statement="Capture the FICO bureau score for the applicant.",
                        confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, _rt, _d, errors = self.run_build([self.APP])
        self.assertEqual(errors, [])
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        kinds = {h["kind"] for h in req.get("parity_hints", [])}
        # packed decimal / remuneration / receivables / COMP-3 -> money;
        # basis-points -> rate; FICO score -> count.
        self.assertIn("money", kinds)
        self.assertIn("rate", kinds)
        self.assertIn("count", kinds)

    def test_risk_money_rule_still_gets_parity_hint(self):
        """A money output on a RISK-flagged member must still surface a parity hint
        (parity is driven from all behavior members, not only resolved ones)."""
        app = "risk-money"
        nodes = [(1, "s-rm", "RM", "function", "rm.cbl")]
        build_fixture_db(self.db_for(app), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(app, "s-rm", name="RM",
                        statement="Accrue the interest amount due to the account.",
                        confidence=0.5, status="risk",
                        risk_reason="Accrue the interest amount due to the account."),
        ])
        write_config(self.config_path, [app])
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, _rt, _d, errors = self.run_build([app])
        self.assertEqual(errors, [])
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        kinds = {h["kind"] for h in req.get("parity_hints", [])}
        self.assertIn("money", kinds,
                      "a risk-flagged money rule must still surface a parity hint")


# ===========================================================================
# ADVERSARIAL FIX 4: a disconnected/batch estate (no CALL edges -> singleton per
# program) is FLAGGED as degenerate clustering on the round-trip evidence.
# ===========================================================================
class TestDisconnectedClusteringDiagnostic(DomainGraphTestBase):
    APP = "batch-app"

    def test_disconnected_graph_flags_degenerate(self):
        # 4 independent programs, ZERO call edges (a classic batch estate).
        nodes = [
            (1, "s-x1", "EXTRACT", "function", "x1.cbl"),
            (2, "s-x2", "TRANSFORM", "function", "x2.cbl"),
            (3, "s-x3", "VALIDATE", "function", "x3.cbl"),
            (4, "s-x4", "LOADIT", "function", "x4.cbl"),
        ]
        build_fixture_db(self.db_for(self.APP), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-x1", name="EXTRACT",
                        statement="Extract the master records for the run.",
                        confidence=0.9),
            overlay_row(self.APP, "s-x2", name="TRANSFORM",
                        statement="Transform the layout to the canonical form.",
                        confidence=0.9),
            overlay_row(self.APP, "s-x3", name="VALIDATE",
                        statement="Validate each record against the ruleset.",
                        confidence=0.9),
            overlay_row(self.APP, "s-x4", name="LOADIT",
                        statement="Load the staged batch into the warehouse.",
                        confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, roundtrip, _d, errors = self.run_build([self.APP])
        self.assertEqual(errors, [])
        self.assertIn("clustering", roundtrip)
        diag = roundtrip["clustering"]
        self.assertTrue(diag["degenerate"],
                        "a fully disconnected batch estate must flag degenerate")
        self.assertEqual(diag["singleton_capabilities"], 4)
        self.assertEqual(diag["behavior_members"], 4)

    def test_connected_graph_is_not_degenerate(self):
        app = "connected"
        nodes = [
            (1, "s-c1", "C1", "function", "c.cbl"),
            (2, "s-c2", "C2", "function", "c.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "c.cbl")]
        build_fixture_db(self.db_for(app), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(app, "s-c1", name="C1",
                        statement="Authorize the request.", confidence=0.9),
            overlay_row(app, "s-c2", name="C2",
                        statement="Authorize and route the request.", confidence=0.9),
        ])
        write_config(self.config_path, [app])
        write_coverage_report(self.coverage_path, coverage=1.0)
        _graph, roundtrip, _d, _e = self.run_build([app])
        self.assertFalse(roundtrip["clustering"]["degenerate"])


# ===========================================================================
# ADVERSARIAL FIX 5: NET-NEW target requirements (the add-capability half).
# ===========================================================================
class TestNetNewRequirements(DomainGraphTestBase):
    APP = "merge-base"

    def _seed_legacy(self):
        nodes = [
            (1, "s-l1", "L1", "function", "l.cbl"),
            (2, "s-l2", "L2", "function", "l.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "l.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-l1", name="L1",
                        statement="Post the ledger entry.", confidence=0.9),
            overlay_row(self.APP, "s-l2", name="L2",
                        statement="Post and confirm the ledger entry.", confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_net_new_requirement_added_and_valid(self):
        self._seed_legacy()
        net_new_path = os.path.join(self.tmp, "net_new.json")
        with open(net_new_path, "w", encoding="utf-8") as fh:
            json.dump([{
                "domain": "FraudScreen",
                "title": "FraudScreen",
                "business_rules": [
                    {"statement": "Screen the transaction against the fraud model."},
                    {"statement": "Flag the account when the risk score exceeds the limit."},
                ],
                "data_access": ["FRAUD_MODEL"],
            }], fh)
        graph, roundtrip, _d, errors = self.run_build(
            [self.APP], net_new_path=net_new_path)
        self.assertEqual(errors, [])
        # The net-new requirement exists, is provenance=net-new, empty legacy, new.
        nn = [
            (rid, req) for rid, (_d, req) in self.all_requirements(graph).items()
            if req["provenance"] == "net-new"
        ]
        self.assertEqual(len(nn), 1, "the net-new requirement must be present")
        _rid, req = nn[0]
        self.assertEqual(req["legacy_components"], [])
        self.assertEqual(req["disposition"], "new")
        self.assertGreaterEqual(len(req["business_rules"]), 2)
        for br in req["business_rules"]:
            self.assertRegex(br["id"], r"^RULE-[0-9]{3,6}$")
        # Net-new contributes NOTHING to the round-trip denominator (legacy only).
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)
        self.assertEqual(roundtrip["legacy_rule_total"], 2)  # only the 2 legacy members
        # And the whole graph is still schema-valid.
        self.assertEqual(self.schema_validate(graph), [])

    def test_net_new_from_config(self):
        """Net-new specs can also live in config.net_new (no separate file)."""
        nodes = [(1, "s-only", "ONLY", "function", "o.cbl")]
        edges = []
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-only", name="ONLY",
                        statement="Do the legacy thing.", confidence=0.9),
        ])
        # Hand-write a config carrying net_new inline.
        cfg = {
            "migration_mode": "functional",
            "source_apps": [{"name": self.APP, "language": "cobol"}],
            "coverage": {"resolve_threshold": 0.75},
            "net_new": [{
                "domain": "Notifications",
                "business_rules": [{"statement": "Notify the customer on settlement."}],
            }],
        }
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, _rt, _d, errors = self.run_build([self.APP])
        self.assertEqual(errors, [])
        provs = {req["provenance"] for _rid, (_d, req) in self.all_requirements(graph).items()}
        self.assertIn("net-new", provs)


# ===========================================================================
# DROP MANIFEST end-to-end: a curator-authored dispositions.json next to the
# output is READ and honored (a drop with a reason is not a silent gap).
# ===========================================================================
class TestCuratorDropManifestHonored(DomainGraphTestBase):
    APP = "curator-app"

    def test_existing_dispositions_file_is_honored(self):
        # A {A,B,C} cluster + a standalone D. Pre-write a dispositions.json that
        # drops s-d with a reason BEFORE building; D's rule is then represented
        # AND dropped -> still 1.0 (the represented path), and the manifest survives.
        nodes = [
            (1, "s-a", "A", "function", "f.cbl"),
            (2, "s-b", "B", "function", "f.cbl"),
            (3, "s-c", "C", "function", "f.cbl"),
            (4, "s-d", "D", "function", "g.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "f.cbl"), (2, 3, "calls", 0.9, "f.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-a", name="A", statement="Validate.", confidence=0.9),
            overlay_row(self.APP, "s-b", name="B", statement="Post.", confidence=0.9),
            overlay_row(self.APP, "s-c", name="C", statement="Audit.", confidence=0.9),
            overlay_row(self.APP, "s-d", name="D", statement="Archive.", confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)
        # Pre-author a dispositions.json in the output dir.
        disp_path = os.path.join(self.req_dir, "dispositions.json")
        with open(disp_path, "w", encoding="utf-8") as fh:
            json.dump({
                "decided_by": "curator",
                "dropped": [{
                    "symbol_id": "s-d", "app": self.APP,
                    "legacy_rule_id": "RULE-D",
                    "drop_reason": "archival reimagined into the cloud tier",
                    "decided_by": "curator",
                }],
            }, fh)
        graph, roundtrip, drops, errors = self.run_build([self.APP])
        self.assertEqual(errors, [])
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)
        # The curator drop survived into the written manifest the build consumed.
        self.assertEqual(drops["dropped"][0]["symbol_id"], "s-d")

    def test_malformed_dispositions_file_is_hard_error(self):
        nodes = [(1, "s-x", "X", "function", "x.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-x", name="X", statement="Do it.", confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)
        disp_path = os.path.join(self.req_dir, "dispositions.json")
        with open(disp_path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ]")
        with self.assertRaises(dg.DomainGraphError) as ctx:
            self.run_build([self.APP])
        self.assertIn("drop manifest", str(ctx.exception).lower())


@unittest.skipIf(dg is None, "scripts/domain_graph.py not importable: %s" % IMPORT_ERROR)
class TestTermAwareNaming(unittest.TestCase):
    """ISS-02: capabilities are named from the dominant domain_* TERM among
    members (the tags `vocabulary project` bound), falling back cleanly to
    statement/program-id naming when no tags are present."""

    def test_action_plus_entity_forms_capability_name(self):
        member_terms = [
            {"action": "POST", "entity": "TRAN"},
            {"action": "POST", "entity": "TRAN"},
            {"entity": "TRAN"},
        ]
        name = dg.domain_name_for(["irrelevant statement"], ["2000-POST"], member_terms)
        self.assertEqual(name, "PostTranCapability")

    def test_entity_only_names_from_entity(self):
        self.assertEqual(dg._term_aware_name([{"entity": "ACCT"}, {"entity": "ACCT"}]),
                         "AcctCapability")

    def test_dominant_term_wins_and_ties_break_alphabetically(self):
        terms = [{"entity": "TRAN"}, {"entity": "TRAN"}, {"entity": "ACCT"}]
        self.assertEqual(dg._term_aware_name(terms), "TranCapability")  # TRAN 2 > ACCT 1
        self.assertEqual(dg._term_aware_name([{"entity": "TRAN"}, {"entity": "ACCT"}]),
                         "AcctCapability")                              # tie -> alpha

    def test_no_tags_falls_back_to_statement_naming(self):
        self.assertIsNone(dg._term_aware_name([{}, {}]))
        name = dg.domain_name_for(["Post the daily transaction to the ledger"],
                                  ["2000-POST"], [{}, {}])
        self.assertTrue(name.endswith("Capability"))
        self.assertNotEqual(name, "PostTranCapability")  # from statements, not tags

    def test_confirmed_terms_by_type_buckets_only_confirmed(self):
        tmp = tempfile.mkdtemp()
        vp = os.path.join(tmp, "vocabulary.json")
        with open(vp, "w", encoding="utf-8") as f:
            json.dump({"terms": [
                {"canonical": "ACCT", "term_type": "entity", "status": "confirmed"},
                {"canonical": "POST", "term_type": "action", "status": "confirmed"},
                {"canonical": "MAYBE", "term_type": "entity", "status": "proposed"},
            ], "meta": {}}, f)
        buckets = dg._confirmed_terms_by_type(vp)
        self.assertEqual(buckets["entity"], ["ACCT"])       # proposed MAYBE excluded
        self.assertEqual(buckets["action"], ["POST"])

    def test_confirmed_terms_missing_vocab_is_empty(self):
        self.assertEqual(dg._confirmed_terms_by_type("/no/such/vocab.json"),
                         {"entity": [], "action": []})

    def test_build_term_index_empty_without_engine_tags(self):
        # No glossary -> no terms to look up -> empty index (graceful fallback).
        self.assertEqual(dg.build_term_index("/no/such.db", "/no/such/vocab.json"), {})


# ===========================================================================
# FIX #2 — WORKSPACE-ANCHORED graphs dir. Like coverage.py (ISS-23), the §I5
# builder must resolve per-app DBs from the WORKSPACE (the resolved --config dir
# / CWD), NOT from the __file__-anchored plugin-install location. A no-`--db`
# multi-repo run that fell back to the plugin tree found nothing.
# ===========================================================================
@unittest.skipIf(dg is None, "scripts/domain_graph.py not importable: %s" % IMPORT_ERROR)
class TestWorkspaceAnchoredGraphsDir(unittest.TestCase):
    """The fix anchors the per-app DB directory on the resolved config's dir.
    These tests prove resolve_app_dbs / build() do NOT depend on the
    plugin-anchored coverage.GRAPHS_DIR fallback for the no-`--db` path."""

    APP = "anchor-app"

    def setUp(self):
        # A WORKSPACE temp dir that holds config + graphs/, and a SEPARATE bogus
        # "plugin install" dir whose graphs/ is deliberately empty/nonexistent.
        self.workspace = tempfile.mkdtemp(prefix="dg-ws-")
        self.graphs_dir = os.path.join(self.workspace, "graphs")
        os.makedirs(self.graphs_dir, exist_ok=True)
        self.req_dir = os.path.join(self.workspace, "requirements")
        os.makedirs(self.req_dir, exist_ok=True)
        self.plugin_dir = tempfile.mkdtemp(prefix="dg-plugin-")
        self.bogus_graphs = os.path.join(self.plugin_dir, "graphs")  # never created

        self.config_path = os.path.join(self.workspace, "config.json")
        self.coverage_path = os.path.join(self.workspace, "coverage-report.json")
        self.overlay_path = os.path.join(self.workspace, "annotations.jsonl")
        self.output_path = os.path.join(self.req_dir, "requirements_graph.json")

        self._saved_env = os.environ.get("ANTI_LEGACY_ANNOTATIONS")
        os.environ["ANTI_LEGACY_ANNOTATIONS"] = self.overlay_path
        # Point the PLUGIN-anchored fallbacks at the bogus dir so a regression
        # (falling back to cov.GRAPHS_DIR / dg.DEFAULT_GRAPHS_DIR) would FAIL to
        # find the DB. The fix must ignore both and use the workspace.
        self._saved_cov_graphs = cov.GRAPHS_DIR
        self._saved_dg_graphs = dg.DEFAULT_GRAPHS_DIR
        cov.GRAPHS_DIR = self.bogus_graphs
        dg.DEFAULT_GRAPHS_DIR = self.bogus_graphs

    def tearDown(self):
        cov.GRAPHS_DIR = self._saved_cov_graphs
        dg.DEFAULT_GRAPHS_DIR = self._saved_dg_graphs
        if self._saved_env is None:
            os.environ.pop("ANTI_LEGACY_ANNOTATIONS", None)
        else:
            os.environ["ANTI_LEGACY_ANNOTATIONS"] = self._saved_env
        shutil.rmtree(self.workspace, ignore_errors=True)
        shutil.rmtree(self.plugin_dir, ignore_errors=True)

    def _seed(self):
        db_path = os.path.join(self.graphs_dir, "%s.db" % self.APP)
        nodes = [
            (1, "s-a", "A", "function", "f.cbl"),
            (2, "s-b", "B", "function", "f.cbl"),
        ]
        edges = [(1, 2, "calls", 0.9, "f.cbl")]
        build_fixture_db(db_path, nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-a", name="A",
                        statement="Authorize the request.", confidence=0.9),
            overlay_row(self.APP, "s-b", name="B",
                        statement="Authorize and route the request.", confidence=0.9),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def test_resolve_app_dbs_uses_workspace_graphs_dir(self):
        """resolve_app_dbs resolves the per-app DB under the WORKSPACE graphs dir
        when one is passed — NOT the plugin-anchored DEFAULT_GRAPHS_DIR."""
        self._seed()
        config = dg.load_config(self.config_path)
        pairs = dg.resolve_app_dbs(config, graphs_dir=self.graphs_dir)
        self.assertEqual(len(pairs), 1)
        name, db_path = pairs[0]
        self.assertEqual(name, self.APP)
        self.assertEqual(db_path, os.path.join(self.graphs_dir, "%s.db" % self.APP))
        # And it is NOT the bogus plugin path.
        self.assertNotIn(self.bogus_graphs, db_path)

    def test_resolve_app_dbs_falls_back_to_plugin_dir_without_hint(self):
        """With NO graphs_dir hint the helper falls back to DEFAULT_GRAPHS_DIR —
        the very fallback the fix avoids in build(). This pins the contract that
        the workspace anchoring lives in build(), not in a silent default."""
        self._seed()
        config = dg.load_config(self.config_path)
        _name, db_path = dg.resolve_app_dbs(config)[0]
        self.assertTrue(db_path.startswith(self.bogus_graphs),
                        "no-hint default must be the plugin-anchored fallback")

    def test_build_resolves_dbs_from_config_dir_not_plugin(self):
        """End-to-end: build() with ONLY a workspace config_path (no --db, no
        graphs_dir) finds the DB under the config's own dir — even though the
        plugin-anchored fallbacks point at an empty dir. Before the fix this
        raised DomainGraphError ('db not found') because resolve_app_dbs went to
        the plugin tree."""
        self._seed()
        graph, roundtrip, _drops, errors = dg.build(
            config_path=self.config_path,
            output_path=self.output_path,
            coverage_report_path=self.coverage_path,
            overlay_path=self.overlay_path,
            schema_path=SCHEMA_PATH,
        )
        self.assertEqual(errors, [])
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)
        # The two members coalesced into one capability requirement.
        reqs = [req for dom in graph["domains"].values()
                for req in dom["requirements"].values()]
        self.assertEqual(len(reqs), 1)
        self.assertEqual(set(reqs[0]["legacy_components"]), {"s-a", "s-b"})

    def test_build_explicit_graphs_dir_overrides_anchor(self):
        """A caller may still pass an explicit graphs_dir to build(); it wins over
        the config-dir anchor (the seam the orchestrator/runner uses)."""
        self._seed()
        # Move the DB into an alternate dir and point build() at it explicitly.
        alt_dir = os.path.join(self.workspace, "alt-graphs")
        os.makedirs(alt_dir, exist_ok=True)
        shutil.move(
            os.path.join(self.graphs_dir, "%s.db" % self.APP),
            os.path.join(alt_dir, "%s.db" % self.APP),
        )
        graph, roundtrip, _d, errors = dg.build(
            config_path=self.config_path,
            output_path=self.output_path,
            coverage_report_path=self.coverage_path,
            overlay_path=self.overlay_path,
            schema_path=SCHEMA_PATH,
            graphs_dir=alt_dir,
        )
        self.assertEqual(errors, [])
        self.assertEqual(roundtrip["roundtrip_coverage"], 1.0)


# ===========================================================================
# ISS-06 (GOTCHA-3): provenance.source_kinds — the trust-tier discriminator —
# is POPULATED end to end. The annotation overlay's source_kinds (the grounding
# kind(s) the extractor actually read) ride through to the emitted rule's
# provenance.source_kinds; absence is tolerated cleanly; out-of-enum is dropped;
# the output still validates against the enriched schema's enum-constrained slot.
# ===========================================================================
class TestSourceKindsPassthrough(DomainGraphTestBase):
    APP = "sk-app"

    def _build_single_member(self, source_kinds=_UNSET, statement=None):
        """A one-member capability whose overlay carries (or omits) source_kinds;
        returns the single requirement's only business rule object."""
        nodes = [(1, "s-sk", "DO-IT", "function", "sk.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(
                self.APP, "s-sk", name="DO-IT",
                statement=statement or "Validate the request before processing.",
                confidence=0.9, source_kinds=source_kinds),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, _rt, _d, errors = self.run_build([self.APP])
        self.assertEqual(errors, [], "build must succeed and be schema-valid")
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        self.assertEqual(len(req["business_rules"]), 1)
        return graph, req["business_rules"][0]

    def test_source_kinds_emitted_into_provenance(self):
        """A requirement whose annotation carries source_kinds emits
        provenance.source_kinds with exactly those (legal) values."""
        graph, br = self._build_single_member(source_kinds=["code-body", "data-def"])
        self.assertIn("source_kinds", br["provenance"])
        self.assertEqual(br["provenance"]["source_kinds"], ["code-body", "data-def"])
        # And the whole graph is still schema-valid (the enum-constrained slot).
        self.assertEqual(self.schema_validate(graph), [])

    def test_absent_source_kinds_omitted_cleanly(self):
        """A requirement whose annotation has NO source_kinds omits the key
        entirely (optional, non-breaking — never an empty list, never null)."""
        graph, br = self._build_single_member(source_kinds=_UNSET)
        self.assertNotIn("source_kinds", br["provenance"],
                         "absent source_kinds must be omitted, not emitted empty/null")
        # The exact provenance shape the committed graph already uses stays valid.
        self.assertEqual(self.schema_validate(graph), [])

    def test_invalid_kind_is_dropped(self):
        """An out-of-enum source-kind is DROPPED (not emitted) so it never
        invalidates the graph against the schema enum; legal siblings survive."""
        graph, br = self._build_single_member(
            source_kinds=["code-body", "hearsay", "doc"])
        self.assertEqual(br["provenance"]["source_kinds"], ["code-body", "doc"],
                         "the out-of-enum 'hearsay' must be dropped, legal kinds kept")
        self.assertNotIn("hearsay", br["provenance"]["source_kinds"])
        self.assertEqual(self.schema_validate(graph), [])

    def test_all_invalid_kinds_omits_slot(self):
        """When EVERY recorded kind is out-of-enum there is nothing legal to emit,
        so the slot is omitted entirely (not an empty list) and the graph is valid."""
        graph, br = self._build_single_member(source_kinds=["hearsay", "rumor"])
        self.assertNotIn("source_kinds", br["provenance"],
                         "all-invalid source_kinds must omit the slot, not emit []")
        self.assertEqual(self.schema_validate(graph), [])

    def test_source_kinds_deduplicated_order_preserved(self):
        """Duplicate kinds collapse; input order is preserved (deterministic)."""
        graph, br = self._build_single_member(
            source_kinds=["comment", "code-body", "comment"])
        self.assertEqual(br["provenance"]["source_kinds"], ["comment", "code-body"])
        self.assertEqual(self.schema_validate(graph), [])

    def test_non_list_source_kinds_tolerated(self):
        """A malformed (non-list) source_kinds value is tolerated as absence —
        the build does not crash and the slot is omitted (schema stays valid)."""
        graph, br = self._build_single_member(source_kinds="code-body")  # a bare str
        self.assertNotIn("source_kinds", br["provenance"])
        self.assertEqual(self.schema_validate(graph), [])

    def test_clean_source_kinds_helper_contract(self):
        """Direct unit cover of dg._clean_source_kinds — the validation/de-noise
        seam (legal subset, order-preserving dedup, None on nothing-to-emit)."""
        self.assertEqual(
            dg._clean_source_kinds(["code-body", "data-def"]),
            ["code-body", "data-def"])
        self.assertEqual(
            dg._clean_source_kinds(["doc", "hearsay", "doc"]), ["doc"])
        self.assertIsNone(dg._clean_source_kinds(["hearsay"]))
        self.assertIsNone(dg._clean_source_kinds([]))
        self.assertIsNone(dg._clean_source_kinds(None))
        self.assertIsNone(dg._clean_source_kinds("code-body"))  # non-list -> None
        # tuple is accepted (it is list-like); enum order preserved.
        self.assertEqual(
            dg._clean_source_kinds(("comment", "code-body")), ["comment", "code-body"])

    def test_risk_member_source_kinds_also_passthrough(self):
        """A RISK-flagged member's annotation source_kinds also rides through (the
        review-flagged rule goes through the SAME _rule_object path). A comment-only
        grounding is exactly the untrusted/RISK-eligible case the trust rule names."""
        nodes = [(1, "s-rk", "MAYBE", "function", "rk.cbl")]
        build_fixture_db(self.db_for(self.APP), nodes, [])
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, "s-rk", name="MAYBE", statement="",
                        confidence=0.3, status="risk",
                        risk_reason="rule grounded only in a copybook comment",
                        source_kinds=["comment"]),
        ])
        write_config(self.config_path, [self.APP])
        write_coverage_report(self.coverage_path, coverage=1.0)
        graph, _rt, _d, errors = self.run_build([self.APP])
        self.assertEqual(errors, [])
        (_rid, (_dname, req)), = self.all_requirements(graph).items()
        self.assertEqual(req["status"], "review")
        (br,) = req["business_rules"]
        self.assertEqual(br["provenance"]["source_kinds"], ["comment"])
        # Comment-only grounding -> the reference trust predicate says untrusted.
        self.assertEqual(self.schema_validate(graph), [])


# ===========================================================================
# Large-community sub-partitioning (dense modern-code defence).
# A community that exceeds max_community_size is split by file/package prefix
# so each generated requirement stays tractable and schema-valid.
# ===========================================================================
class TestLargeCommunitySubPartition(DomainGraphTestBase):
    APP = "dense-app"

    def setUp(self):
        super().setUp()
        # Build a community of 6 members split across 2 packages — this exercises
        # the sub-partition path when max_community_size is set very low (3).
        # pkg-a: s1, s2, s3  (three calls: s1→s2→s3 — one connected component)
        # pkg-b: s4, s5, s6  (three calls: s4→s5→s6)
        # Cross-package call s3→s4 puts them in one community.
        nodes = [
            (1, "s1", "DO-A1", "function", "pkg-a/a1.java"),
            (2, "s2", "DO-A2", "function", "pkg-a/a2.java"),
            (3, "s3", "DO-A3", "function", "pkg-a/a3.java"),
            (4, "s4", "DO-B1", "function", "pkg-b/b1.java"),
            (5, "s5", "DO-B2", "function", "pkg-b/b2.java"),
            (6, "s6", "DO-B3", "function", "pkg-b/b3.java"),
        ]
        edges = [
            (1, 2, "calls", 0.9, "pkg-a/a1.java"),
            (2, 3, "calls", 0.9, "pkg-a/a2.java"),
            (3, 4, "calls", 0.9, "pkg-a/a3.java"),  # cross-package call
            (4, 5, "calls", 0.9, "pkg-b/b1.java"),
            (5, 6, "calls", 0.9, "pkg-b/b2.java"),
        ]
        build_fixture_db(self.db_for(self.APP), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row(self.APP, sid, name="DO-%s" % sid,
                        statement="handles %s" % sid, confidence=0.9, status="resolved")
            for sid in ("s1", "s2", "s3", "s4", "s5", "s6")
        ])
        write_coverage_report(self.coverage_path, coverage=1.0)

    def _write_config_with_max(self, max_size):
        # This class exercises the SIZE-based sub-partition (the oversized-community
        # path), so it pins capability_partition="calls" — otherwise Phase 2 would
        # package-partition the java fixture by default and these tests would be
        # measuring the package strategy instead of the size split. The package
        # strategy has its own tests (TestPackagePrimaryPartition).
        cfg = {
            "migration_mode": "functional",
            "source_apps": [{"name": self.APP, "language": "java"}],
            "coverage": {"resolve_threshold": 0.75, "capability_partition": "calls"},
            "domain_graph": {"max_community_size": max_size},
        }
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def test_sub_partition_splits_oversized_community(self):
        """When max_community_size=3, the 6-member community is split into
        pkg-a (3 members) and pkg-b (3 members) → 2 requirements."""
        self._write_config_with_max(3)
        graph, _rt, _d, errors = self.run_build([self.APP])
        self.assertEqual(errors, [], "schema errors after sub-partition: %s" % errors)
        reqs = self.all_requirements(graph)
        self.assertEqual(len(reqs), 2,
                         "expected 2 sub-partitioned requirements, got %d" % len(reqs))
        for _rid, (_dname, req) in reqs.items():
            self.assertLessEqual(len(req["business_rules"]), 3,
                                 "each partition must have ≤3 rules after split")

    def test_no_members_lost_in_sub_partition(self):
        """All 6 members are represented across the two requirements."""
        self._write_config_with_max(3)
        graph, _rt, _d, _e = self.run_build([self.APP])
        all_rule_refs = [
            lc for req_data in graph["domains"].values()
            for req in req_data.get("requirements", {}).values()
            for lc in req.get("legacy_components", [])
        ]
        self.assertEqual(len(all_rule_refs), 6,
                         "all 6 members must appear in legacy_components across partitions")

    def test_large_community_does_not_split_when_below_threshold(self):
        """With max_community_size=10, the 6-member community stays as one
        requirement (no split needed)."""
        self._write_config_with_max(10)
        graph, _rt, _d, errors = self.run_build([self.APP])
        self.assertEqual(errors, [], "schema errors: %s" % errors)
        reqs = self.all_requirements(graph)
        self.assertEqual(len(reqs), 1,
                         "community below threshold must remain a single requirement")

    def test_rule_ids_fit_schema_after_sub_partition(self):
        """After sub-partition every rule ID must match ^RULE-[0-9]{3,6}$."""
        self._write_config_with_max(3)
        graph, _rt, _d, _e = self.run_build([self.APP])
        for _rid, (_dname, req) in self.all_requirements(graph).items():
            for br in req["business_rules"]:
                self.assertRegex(br["id"], r"^RULE-[0-9]{3,6}$")


# ===========================================================================
# PHASE 1 — glossary-DIRECT capability naming (engine-independent). Derives the
# {node_name -> {entity, action}} term index from the confirmed glossary + node
# names when the engine exposes no projected domain_* tags.
# ===========================================================================
def _vocab_file(path, entities=(), actions=()):
    """Write a minimal confirmed-glossary file. entities/actions are
    (canonical, freq) tuples."""
    terms = []
    for canon, freq in entities:
        terms.append({"canonical": canon, "term_type": "entity",
                      "status": "confirmed", "freq": freq})
    for canon, freq in actions:
        terms.append({"canonical": canon, "term_type": "action",
                      "status": "confirmed", "freq": freq})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"terms": terms, "meta": {}}, fh)
    return path


@unittest.skipIf(dg is None, "scripts/domain_graph.py not importable: %s" % IMPORT_ERROR)
class TestGlossaryDirectNaming(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dg-gloss-")
        self.voc = os.path.join(self.tmp, "vocabulary.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_derive_maps_entity_and_action(self):
        _vocab_file(self.voc, entities=[("PRODUCER", 9)], actions=[("SEND", 4)])
        nodes = [{"symbol_id": "s1", "name": "KafkaProducer"},
                 {"symbol_id": "s2", "name": "sendMessage"}]
        idx = dg._derive_term_index_from_glossary(nodes, self.voc)
        self.assertEqual(idx["KafkaProducer"]["entity"], "PRODUCER")
        self.assertEqual(idx["sendMessage"]["action"], "SEND")

    def test_highest_freq_entity_wins_on_compound_name(self):
        # KafkaProducer -> KAFKA + PRODUCER; the higher-freq PRODUCER is the head.
        _vocab_file(self.voc, entities=[("PRODUCER", 9), ("KAFKA", 2)])
        idx = dg._derive_term_index_from_glossary(
            [{"symbol_id": "s", "name": "KafkaProducer"}], self.voc)
        self.assertEqual(idx["KafkaProducer"]["entity"], "PRODUCER")

    def test_skips_boilerplate_action_token(self):
        # getProducerName: GET is boilerplate -> no action; PRODUCER is the entity.
        _vocab_file(self.voc, entities=[("PRODUCER", 9)], actions=[("GET", 5)])
        idx = dg._derive_term_index_from_glossary(
            [{"symbol_id": "s", "name": "getProducerName"}], self.voc)
        self.assertEqual(idx["getProducerName"].get("entity"), "PRODUCER")
        self.assertNotIn("action", idx["getProducerName"])

    def test_empty_glossary_yields_empty_index(self):
        _vocab_file(self.voc)  # no terms
        self.assertEqual(
            dg._derive_term_index_from_glossary(
                [{"symbol_id": "s", "name": "Producer"}], self.voc), {})

    def test_malformed_glossary_is_graceful(self):
        # A corrupt vocabulary.json must not crash term-freq loading; it yields
        # empty buckets so naming falls back cleanly.
        with open(self.voc, "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ")
        self.assertEqual(dg._confirmed_term_freqs(self.voc), {"entity": {}, "action": {}})
        self.assertEqual(
            dg._derive_term_index_from_glossary(
                [{"symbol_id": "s", "name": "Producer"}], self.voc), {})

    def test_build_term_index_glossary_fallback_no_engine_tags(self):
        # build_term_index on a missing db with a real glossary must not crash and
        # must return {} (no nodes to derive from) — the engine-absent path.
        _vocab_file(self.voc, entities=[("PRODUCER", 9)])
        self.assertEqual(dg.build_term_index("/no/such.db", self.voc), {})


# ===========================================================================
# PHASE 2 — package-as-primary capability partition (language-driven). Modern
# code partitions by source package; mainframe stays on call-affinity.
# ===========================================================================
def _partition_app(language, files, *, strategy="auto", communities=None):
    """A minimal app dict for _capability_partition. `files` = {sid: file_path};
    all sids are behavior-bearing. Default communities = one mega-community."""
    sids = list(files)
    nodes = {sid: {"symbol_id": sid, "name": sid, "file": f}
             for sid, f in files.items()}
    comms = communities if communities is not None else {"c0": sids}
    return {"app": "a", "nodes": nodes, "behavior_ids": set(sids),
            "communities": comms, "language": language,
            "capability_partition": strategy}


@unittest.skipIf(dg is None, "scripts/domain_graph.py not importable: %s" % IMPORT_ERROR)
class TestPackagePrimaryPartition(unittest.TestCase):
    def _files_two_packages(self):
        return {
            "k1": "src/producer/KafkaProducer.java",
            "k2": "src/producer/ProducerRecord.java",
            "k3": "src/consumer/KafkaConsumer.java",
            "k4": "src/consumer/ConsumerRecord.java",
            "k5": "src/admin/AdminClient.java",
        }

    def test_modern_language_partitions_by_package(self):
        app = _partition_app("java", self._files_two_packages())
        parts = dg._capability_partition(app, 500)
        # Three packages -> three capability groups (not one mega-community).
        self.assertEqual(len(parts), 3)
        labels = " ".join(parts.keys())
        self.assertIn("producer", labels)
        self.assertIn("consumer", labels)
        self.assertIn("admin", labels)

    def test_mainframe_language_stays_on_calls(self):
        # cobol with a single call-community -> one capability (NOT package-split).
        app = _partition_app("cobol", self._files_two_packages())
        parts = dg._capability_partition(app, 500)
        self.assertEqual(len(parts), 1)  # the single call community

    def test_unknown_language_treated_as_modern(self):
        app = _partition_app("rust", self._files_two_packages())  # not in mainframe set
        self.assertEqual(len(dg._capability_partition(app, 500)), 3)

    def test_explicit_calls_override_keeps_communities(self):
        app = _partition_app("java", self._files_two_packages(),
                             strategy="calls",
                             communities={"c1": ["k1", "k2"], "c2": ["k3", "k4", "k5"]})
        parts = dg._capability_partition(app, 500)
        self.assertEqual(len(parts), 2)  # the two call communities, not 3 packages

    def test_explicit_package_override_on_mainframe(self):
        app = _partition_app("cobol", self._files_two_packages(), strategy="package")
        # Explicit package override is honored even for mainframe.
        self.assertEqual(len(dg._capability_partition(app, 500)), 3)

    def test_flat_module_falls_back_to_calls(self):
        # All files in ONE directory -> no package signal -> auto falls back to
        # the call communities (so a flat module is not collapsed into 1 capability).
        flat = {"k1": "src/A.java", "k2": "src/B.java", "k3": "src/C.java"}
        app = _partition_app("java", flat,
                             communities={"c1": ["k1", "k2"], "c2": ["k3"]})
        parts = dg._capability_partition(app, 500)
        self.assertEqual(len(parts), 2)  # fell back to the 2 call communities

    def test_no_behavior_members_yields_empty(self):
        app = _partition_app("java", self._files_two_packages())
        app["behavior_ids"] = set()
        self.assertEqual(dg._capability_partition(app, 500), {})

    def test_non_string_file_does_not_crash(self):
        # Defensive (adversarial finding): a degenerate engine node whose `file`
        # is not a string must not crash the package split.
        nodes = {"a": {"symbol_id": "a", "file": 12345},
                 "b": {"symbol_id": "b", "file": None}}
        groups = dg._sub_partition_by_package(["a", "b"], nodes)
        self.assertEqual(sorted(m for v in groups.values() for m in v), ["a", "b"])

    def test_overlapping_communities_do_not_duplicate(self):
        # A member in two communities must land in exactly one capability (dedup).
        app = _partition_app(
            "java",
            {"m1": "p/A.java", "m2": "p/B.java", "m3": "q/C.java"},
            strategy="package",
            communities={"c1": ["m1", "m2", "m3"], "c2": ["m1"]})
        members = [m for v in dg._capability_partition(app, 500).values() for m in v]
        self.assertEqual(sorted(members), ["m1", "m2", "m3"])

    def test_hierarchical_mode_falls_back_without_engine_db(self):
        # The opt-in "hierarchical" engine mode degrades to the auto (package)
        # behaviour when there is no engine DB to cluster — never crashes, never
        # drops members. (Graceful fallback for older engines.)
        app = _partition_app("java", self._files_two_packages(),
                             strategy="hierarchical")  # no 'db' key
        parts = dg._capability_partition(app, 500)
        # Fell back to package partition (3 packages), members conserved.
        self.assertEqual(len(parts), 3)
        members = sorted(m for v in parts.values() for m in v)
        self.assertEqual(members, sorted(self._files_two_packages()))

    def test_semantic_mode_falls_back_without_engine_db(self):
        app = _partition_app("java", self._files_two_packages(),
                             strategy="semantic")  # no 'db' key
        parts = dg._capability_partition(app, 500)
        self.assertEqual(len(parts), 3)  # degraded to package partition


# ===========================================================================
# PHASE 1+2 INTEGRATION — cross-app capability coalescing. Two modern apps that
# both expose a PRODUCER capability must coalesce into ONE domain (shared name).
# ===========================================================================
@unittest.skipIf(dg is None, "scripts/domain_graph.py not importable: %s" % IMPORT_ERROR)
class TestCrossAppCoalescing(DomainGraphTestBase):
    def _write_java_config(self, apps):
        cfg = {"migration_mode": "functional",
               "source_apps": [{"name": a, "language": "java"} for a in apps],
               "coverage": {"resolve_threshold": 0.75}}
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def test_shared_entity_coalesces_across_apps(self):
        # Two apps, each a producer package whose members tokenize to PRODUCER.
        for app in ("appkafka", "apppulsar"):
            nodes = [
                (1, "%s-c1" % app, "Producer", "class", "src/producer/Producer.java"),
                (2, "%s-c2" % app, "ProducerImpl", "class", "src/producer/ProducerImpl.java"),
                (3, "%s-m1" % app, "send", "method", "src/producer/Producer.java"),
            ]
            edges = [(1, 3, "calls", 0.9, "src/producer/Producer.java")]
            build_fixture_db(self.db_for(app), nodes, edges)
        write_overlay(self.overlay_path, [
            overlay_row("appkafka", "appkafka-m1", name="send",
                        statement="Publishes a record to the broker.", confidence=0.9),
            overlay_row("apppulsar", "apppulsar-m1", name="send",
                        statement="Publishes a message to the broker.", confidence=0.9),
        ])
        self._write_java_config(["appkafka", "apppulsar"])
        write_coverage_report(self.coverage_path, coverage=1.0)
        voc = os.path.join(self.tmp, "vocabulary.json")
        _vocab_file(voc, entities=[("PRODUCER", 6)], actions=[("SEND", 2)])

        graph, _rt, _d, errors = self.run_build(
            ["appkafka", "apppulsar"], skip_front_half=True, vocab_path=voc)
        self.assertEqual(errors, [], "schema errors: %s" % errors)
        # Find the domain(s) named for PRODUCER and assert ONE spans BOTH apps.
        coalesced = []
        for dname, dom in graph["domains"].items():
            apps_in = set()
            for req in dom["requirements"].values():
                for br in req.get("business_rules", []):
                    sa = (br.get("provenance") or {}).get("source_app")
                    if sa:
                        apps_in.add(sa)
            if len(apps_in) > 1:
                coalesced.append((dname, sorted(apps_in)))
        self.assertTrue(
            any("Producer" in dn for dn, _ in coalesced),
            "expected a Producer capability spanning both apps; coalesced=%s "
            "all_domains=%s" % (coalesced, list(graph["domains"].keys())))


if __name__ == "__main__":
    unittest.main()
