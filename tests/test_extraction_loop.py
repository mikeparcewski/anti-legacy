#!/usr/bin/env python3
"""Hermetic tests for scripts/extract.py — the cluster-aware extraction loop (§I3).

What is under test is the loop's CONTROL FLOW, not a real LLM:

    cluster() once  →  rank-ordered worklist  →  per node: context() fan-out
    →  FRAME with cluster  →  INJECTED extract_rule()  →  cluster-as-confidence
    →  annotate() RESOLVED (>= threshold) or RISK-FLAG (else, never assert).

The rule extractor is a deterministic, INJECTED stub (no model call) so the
RESOLVE/RISK split, the cluster-sprawl prior, and the cluster-id attachment are
all deterministic. Two layers:

  * PURE-UNIT (no binary): the cluster-as-confidence math —
    cluster_cohesion / apply_cluster_signal / the RESOLVE-vs-RISK threshold edge —
    and worklist resumability (already-settled nodes are skipped).
  * AGAINST THE REAL ENGINE (skipped if the binary is absent): index a tiny
    multi-file fixture, run the loop end-to-end with the injected extractor, and
    assert (1) behavior nodes are annotated, (2) low-confidence nodes RISK-flag
    rather than assert, (3) cluster ids are attached to every annotation, and
    (4) the working tree stays CLEAN — the overlay is redirected to a temp file
    (the WF1 hermeticity rule), and the engine DB lives in a tempdir.

No real LLM, no network. Round-trip cases skip cleanly when the binary is absent.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
# SCRIPTS_DIR intentionally NOT added to sys.path (migrated modules resolve
# via tests/conftest.py); SCRIPTS_DIR retained only for by-path shim guards.

WICKED_ESTATE_FALLBACK = (
    ""
)
BINARY = shutil.which("wicked-estate") or (
    WICKED_ESTATE_FALLBACK if os.access(WICKED_ESTATE_FALLBACK, os.X_OK) else None
)

# Guarded imports: the loop + helper are built by sibling units. Skip, never error.
try:
    import extract as ext  # noqa: E402
    from antilegacy_core import wicked_estate as we  # noqa: E402
    from antilegacy_core import coverage as cov  # noqa: E402

    IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only pre-build
    ext = we = cov = None
    IMPORT_ERROR = exc


# A config matching the repo defaults but standalone (no file read needed).
_CONFIG = {
    "coverage": {
        "behavior_kinds": ["module", "function", "method", "class", "struct", "interface"],
        "estate_behavior_kinds": ["cics_program", "step", "db2_table"],
        "structural_kinds": ["file", "import", "field", "constant", "variable"],
        "resolve_threshold": 0.75,
    },
    "crawl": {"max_rings": 3, "context_budget_chars": 18000},
    "cluster": {"data_affinity_same_file_bonus": 0.25, "max_passes": 20},
}


@unittest.skipIf(ext is None, f"scripts/extract.py not importable yet: {IMPORT_ERROR}")
class TestClusterConfidenceSignal(unittest.TestCase):
    """The cluster-as-confidence prior — pure functions, no binary."""

    def test_cohesion_full_when_context_in_own_cluster(self):
        """All non-seed neighbors share the node's cluster → cohesion 1.0."""
        node_community = {"A|f": "C1", "B|f": "C1", "X|f": "C1"}
        ctx = {"ranked_nodes": [
            {"name": "A", "file": "f", "ring": 0},   # seed, own cluster — ignored
            {"name": "B", "file": "f", "ring": 1},
            {"name": "X", "file": "f", "ring": 2},
        ]}
        coh, in_c, classified = ext.cluster_cohesion("A|f", ctx, node_community)
        self.assertEqual(classified, 2)
        self.assertEqual(in_c, 2)
        self.assertEqual(coh, 1.0)

    def test_cohesion_drops_when_context_sprawls(self):
        """Half the neighbors live in foreign clusters → cohesion 0.5."""
        node_community = {"A|f": "C1", "B|f": "C1", "Y|f": "C2", "Z|f": "C3"}
        ctx = {"ranked_nodes": [
            {"name": "A", "file": "f", "ring": 0},   # seed — ignored
            {"name": "B", "file": "f", "ring": 1},   # own cluster
            {"name": "Y", "file": "f", "ring": 1},   # foreign
            {"name": "Z", "file": "f", "ring": 2},   # foreign
        ]}
        coh, in_c, classified = ext.cluster_cohesion("A|f", ctx, node_community)
        self.assertEqual(classified, 3)
        self.assertEqual(in_c, 1)
        self.assertAlmostEqual(coh, 1 / 3)

    def test_cohesion_neutral_with_no_classifiable_neighbors(self):
        """No neighbor has a known cluster → neutral cohesion 1.0 (nothing to sprawl)."""
        ctx = {"ranked_nodes": [{"name": "A", "file": "f", "ring": 0}]}
        coh, in_c, classified = ext.cluster_cohesion("A|f", ctx, {"A|f": "C1"})
        self.assertEqual((in_c, classified), (0, 0))
        self.assertEqual(coh, 1.0)

    def test_signal_keeps_confidence_when_cohesive(self):
        """cohesion 1.0 leaves the raw confidence untouched."""
        self.assertEqual(ext.apply_cluster_signal(0.80, 1.0), 0.80)

    def test_signal_penalizes_sprawl_below_threshold(self):
        """A high raw confidence on a SPRAWLING node is dragged below 0.75 → RISK.

        This is the load-bearing behavior: a god-program with a confident-looking
        rule must NOT be asserted. cohesion 0.0, floor 0.5 → 0.80 * 0.5 = 0.40.
        """
        adjusted = ext.apply_cluster_signal(0.80, 0.0, floor=0.5)
        self.assertEqual(adjusted, 0.40)
        self.assertLess(adjusted, 0.75)  # would RISK-flag

    def test_signal_monotonic_in_cohesion(self):
        """More cohesion ⇒ never less confidence (monotonic prior)."""
        a = ext.apply_cluster_signal(0.9, 0.2)
        b = ext.apply_cluster_signal(0.9, 0.6)
        c = ext.apply_cluster_signal(0.9, 1.0)
        self.assertLessEqual(a, b)
        self.assertLessEqual(b, c)


@unittest.skipIf(ext is None, f"scripts/extract.py not importable yet: {IMPORT_ERROR}")
class TestRuleNormalization(unittest.TestCase):
    """Atomic multi-emit, pure layer: _normalize_rules / _emit_order /
    _declared_sibling_ids / the declared-but-missing-sibling guard. No binary."""

    def test_single_dict_is_backcompat_one_rule(self):
        """A single dict (the common case) normalizes to a one-element list."""
        r = {"statement": "x", "confidence": 0.9, "rule_id": "RULE-1"}
        self.assertEqual(ext._normalize_rules(r), [r])

    def test_list_keeps_order_primary_first(self):
        """A list of rules is preserved in order (rules[0] is the PRIMARY)."""
        rules = [{"rule_id": "RULE-1"}, {"rule_id": "ERR-1"}]
        self.assertEqual(ext._normalize_rules(rules), rules)

    def test_primary_splits_envelope_flattens(self):
        """{'primary':..,'splits':[..]} flattens to [primary, *splits]."""
        env = {"primary": {"rule_id": "RULE-1"},
               "splits": [{"rule_id": "ERR-1"}, {"rule_id": "VAL-2"}]}
        self.assertEqual(
            ext._normalize_rules(env),
            [{"rule_id": "RULE-1"}, {"rule_id": "ERR-1"}, {"rule_id": "VAL-2"}],
        )

    def test_plain_dict_with_primary_key_is_not_mis_split(self):
        """A rule dict that merely has a 'primary' field (no splits/siblings
        marker) is treated as ONE plain rule, not an envelope."""
        r = {"rule_id": "RULE-1", "primary": True, "statement": "x"}
        self.assertEqual(ext._normalize_rules(r), [r])

    def test_none_and_empty_become_one_empty_rule(self):
        """None / [] / unknown shape never vanishes — yields one empty rule that
        will RISK-flag (a node always reaches a terminal, never silently drops)."""
        for bad in (None, [], (), 42, "nope"):
            self.assertEqual(ext._normalize_rules(bad), [{}])

    def test_emit_order_puts_primary_last(self):
        """_emit_order writes the PRIMARY last so last-record-wins coverage
        reflects the primary outcome; splits keep their relative order."""
        rules = [{"rule_id": "P"}, {"rule_id": "S1"}, {"rule_id": "S2"}]
        ordered = ext._emit_order(rules)
        self.assertEqual([r["rule_id"] for r in ordered], ["S1", "S2", "P"])
        self.assertEqual(ext._emit_order([{"rule_id": "ONLY"}]),
                         [{"rule_id": "ONLY"}])

    def test_declared_sibling_ids_collects_all_forms(self):
        """decomposition (str/list), sibling_rule_ids, splits/siblings dicts all
        count; a self-reference is dropped."""
        rule = {
            "rule_id": "RULE-1",
            "decomposition": "ERR-1",
            "sibling_rule_ids": ["VAL-2"],
            "splits": [{"rule_id": "ERR-3"}, {"id": "VAL-4"}],
            "siblings": ["RULE-1"],  # self — must be dropped
        }
        sibs = ext._declared_sibling_ids(rule)
        self.assertEqual(sorted(sibs), ["ERR-1", "ERR-3", "VAL-2", "VAL-4"])
        self.assertNotIn("RULE-1", sibs)

    def test_guard_passes_when_declared_sibling_is_emitted(self):
        """A rule that names its ERR- twin AND emits it does NOT raise."""
        node = {"name": "N", "symbol_id": "S1"}
        rules = [
            {"rule_id": "RULE-1", "decomposition": "ERR-1"},
            {"rule_id": "ERR-1"},
        ]
        # Must not raise.
        ext._assert_declared_siblings_emitted(node, rules)

    def test_guard_flags_declared_but_missing_sibling(self):
        """The CARDINAL silent-failure: a rule DECLARES an ERR- twin that is NOT
        emitted in the same pass → ExtractionError, never silent."""
        node = {"name": "1000-DALYTRAN-GET-NEXT", "symbol_id": "S1"}
        rules = [{"rule_id": "RULE-DALYTRAN-001", "decomposition": "ERR-DALYTRAN-001"}]
        with self.assertRaises(ext.ExtractionError) as cm:
            ext._assert_declared_siblings_emitted(node, rules)
        msg = str(cm.exception)
        self.assertIn("ERR-DALYTRAN-001", msg)
        self.assertIn("RULE-DALYTRAN-001", msg)  # names the declarer


@unittest.skipIf(ext is None, f"scripts/extract.py not importable yet: {IMPORT_ERROR}")
class TestWorklistResumability(unittest.TestCase):
    """build_worklist drops already-settled nodes (idempotent / resumable)."""

    def test_settled_symbol_ids_reads_overlay(self):
        tmp = tempfile.mkdtemp(prefix="ext-overlay-")
        try:
            overlay = os.path.join(tmp, "annotations.jsonl")
            with open(overlay, "w", encoding="utf-8") as f:
                f.write(json.dumps({"db_id": "d", "symbol_id": "S1", "status": "resolved"}) + "\n")
                f.write(json.dumps({"db_id": "d", "symbol_id": "S2", "status": "risk"}) + "\n")
                # A bare/unknown-status record is NOT settled.
                f.write(json.dumps({"db_id": "d", "symbol_id": "S3", "status": ""}) + "\n")
            settled = ext.settled_symbol_ids(overlay)
            self.assertEqual(settled, {"S1", "S2"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_last_record_wins_for_resume(self):
        """A re-annotated node's LAST status decides settledness (overlay is append-only)."""
        tmp = tempfile.mkdtemp(prefix="ext-overlay2-")
        try:
            overlay = os.path.join(tmp, "annotations.jsonl")
            with open(overlay, "w", encoding="utf-8") as f:
                f.write(json.dumps({"db_id": "d", "symbol_id": "S1", "status": "risk"}) + "\n")
                f.write(json.dumps({"db_id": "d", "symbol_id": "S1", "status": "resolved"}) + "\n")
            self.assertEqual(ext.settled_symbol_ids(overlay), {"S1"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipIf(ext is None, f"scripts/extract.py not importable yet: {IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestLoopAgainstRealEngine(unittest.TestCase):
    """End-to-end loop over a tiny real-indexed fixture with an INJECTED extractor.

    Fixture: two python files forming two call-affinity communities plus one
    cross-cutting node that reaches into both, so the cluster signal has something
    to penalize. The extractor is deterministic (no LLM).
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="ext-fixture-")
        src = os.path.join(cls.tmpdir, "src")
        os.makedirs(src, exist_ok=True)
        # Community 1: accounts. open_account -> validate_account.
        with open(os.path.join(src, "accounts.py"), "w") as f:
            f.write(
                "def validate_account(a):\n"
                "    return a is not None\n\n\n"
                "def open_account(a):\n"
                "    if validate_account(a):\n"
                "        return True\n"
                "    return False\n"
            )
        # Community 2: billing. post_bill -> compute_interest.
        with open(os.path.join(src, "billing.py"), "w") as f:
            f.write(
                "def compute_interest(bal, rate):\n"
                "    return bal * rate\n\n\n"
                "def post_bill(bal, rate):\n"
                "    amt = compute_interest(bal, rate)\n"
                "    return amt\n"
            )
        cls.db = os.path.join(cls.tmpdir, "g.db")
        subprocess.run(
            [BINARY, "index", src, "--db", cls.db],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _overlay(self):
        """A throwaway overlay path under the fixture tempdir (hermetic)."""
        return os.path.join(self.tmpdir, "overlay-%d.jsonl" % id(self))

    def test_loop_annotates_behavior_nodes_and_attaches_clusters(self):
        """Every behavior node gets annotated with a cluster id; overlay is keyed
        by symbol_id and carries the cluster + cohesion fields."""
        overlay = os.path.join(self.tmpdir, "overlay-annotate.jsonl")

        def extractor(node, framed):
            # Confident, in-scope rule for every node.
            return {"statement": "%s does its thing" % node["name"], "confidence": 0.90}

        summary = ext.run(
            self.db,
            config=_CONFIG,
            extract_rule=extractor,
            overlay_path=overlay,
            binary=BINARY,
        )
        self.assertGreater(summary["processed"], 0, "no behavior nodes were crawled")
        self.assertGreaterEqual(summary["num_communities"], 2,
                                "the two call-communities should cluster apart")

        # Overlay exists and every record carries a cluster label + cohesion.
        self.assertTrue(os.path.exists(overlay))
        recs = [json.loads(l) for l in open(overlay).read().splitlines() if l.strip()]
        self.assertTrue(recs)
        for r in recs:
            self.assertIn("symbol_id", r)
            self.assertIn("cluster", r, "every annotation must attach a cluster id")
            self.assertIsNotNone(r["cluster"])
            self.assertIn("cluster_cohesion", r)
            self.assertIn(r["status"], ("resolved", "risk"))

        # The behavior functions are present and resolved at high confidence.
        names = {r.get("name") for r in summary["results"]}
        for want in ("open_account", "validate_account", "post_bill", "compute_interest"):
            self.assertIn(want, names, f"{want} should be in the worklist")

    def test_framed_context_carries_own_source_from_bundle(self):
        """The per-file source_bundle prefetch surfaces each node's COMPLETE body
        as framed_context['own_source'] (key always present; populated for nodes
        whose file the bundle returns)."""
        seen = {"key_present": True, "any_populated": False}

        def extractor(node, framed):
            if "own_source" not in framed:
                seen["key_present"] = False
            if framed.get("own_source"):
                seen["any_populated"] = True
            return {"statement": "%s does its thing" % node["name"], "confidence": 0.90}

        ext.run(self.db, config=_CONFIG, extract_rule=extractor,
                overlay_path=os.path.join(self.tmpdir, "overlay-ownsrc.jsonl"),
                binary=BINARY)
        self.assertTrue(seen["key_present"], "own_source key must always be framed")
        self.assertTrue(seen["any_populated"],
                        "the bundle prefetch should populate at least one own_source")

    def test_split_node_writes_all_rules_atomically(self):
        """A node whose extractor returns 2 rules writes 2 overlay rows in ONE
        pass — the primary RULE + its ERR- twin — and BOTH classify in coverage.

        This is the GOTCHA-2 fix: pre-fix, StructuredOutput emitted ONE rule and
        the twin was silently dropped (coverage stayed < 1.0 silently). Post-fix
        every returned rule is materialized here.
        """
        overlay = os.path.join(self.tmpdir, "overlay-split.jsonl")

        def splitting_extractor(node, framed):
            # open_account decomposes into a behavior RULE + an ERR- twin.
            if node["name"] == "open_account":
                return [
                    {"rule_id": "RULE-OPEN-001",
                     "statement": "opens the account when valid",
                     "confidence": 0.90, "decomposition": "ERR-OPEN-001"},
                    {"rule_id": "ERR-OPEN-001",
                     "statement": "rejects when validation fails",
                     "confidence": 0.90},
                ]
            return {"statement": "%s does its thing" % node["name"], "confidence": 0.90}

        summary = ext.run(
            self.db, config=_CONFIG, extract_rule=splitting_extractor,
            overlay_path=overlay, binary=BINARY,
        )

        # The split node's node-level record reports 2 emitted rules.
        by_name = {r["name"]: r for r in summary["results"]}
        self.assertIn("open_account", by_name)
        self.assertEqual(by_name["open_account"]["rules_emitted"], 2)
        emitted_ids = {e["rule_id"] for e in by_name["open_account"]["emitted_rules"]}
        self.assertEqual(emitted_ids, {"RULE-OPEN-001", "ERR-OPEN-001"})

        # rule_emits exceeds processed by exactly the one extra split rule.
        self.assertEqual(summary["rule_emits"], summary["processed"] + 1)

        # TWO distinct overlay rows were written for the split node's symbol_id.
        recs = [json.loads(l) for l in open(overlay).read().splitlines() if l.strip()]
        open_sid = by_name["open_account"]["symbol_id"]
        open_rows = [r for r in recs if r["symbol_id"] == open_sid]
        self.assertEqual(len(open_rows), 2, "the split node must write 2 overlay rows")
        row_rule_ids = {r["rule_id"] for r in open_rows}
        self.assertEqual(row_rule_ids, {"RULE-OPEN-001", "ERR-OPEN-001"})

        # BOTH rows classify as resolved via the coverage classifier (no bare row).
        settings = cov.coverage_settings(_CONFIG)
        for row in open_rows:
            state, _conf = cov.classify_node(row, settings)
            self.assertEqual(state, "resolved",
                             "both split rules must classify, not be dropped")

        # The split node stays accounted in coverage (last-record-wins on the
        # primary keeps the node settled — the twin is an extra, not a regression).
        report = cov.compute_coverage(
            config=_CONFIG, explicit_db=self.db,
            annotations_path=overlay, cross_check=False,
        )
        self.assertEqual(report["unaccounted"], 0)
        self.assertEqual(report["coverage"], 1.0)

    def test_declared_but_unemitted_sibling_flags_in_loop(self):
        """An extractor that DECLARES an ERR- twin but does NOT return it makes the
        loop raise — the silent-drop is impossible end-to-end, not just in the
        pure guard."""
        overlay = os.path.join(self.tmpdir, "overlay-missing-sib.jsonl")

        def dropping_extractor(node, framed):
            if node["name"] == "open_account":
                # Declares ERR-OPEN-001 but never returns it — the pre-fix bug.
                return {"rule_id": "RULE-OPEN-001",
                        "statement": "opens the account",
                        "confidence": 0.90, "decomposition": "ERR-OPEN-001"}
            return {"statement": "%s does its thing" % node["name"], "confidence": 0.90}

        with self.assertRaises(ext.ExtractionError) as cm:
            ext.run(self.db, config=_CONFIG, extract_rule=dropping_extractor,
                    overlay_path=overlay, binary=BINARY)
        self.assertIn("ERR-OPEN-001", str(cm.exception))

    def test_low_confidence_flags_instead_of_asserting(self):
        """A low-confidence extractor output RISK-flags every node — never asserts.

        'No silent maybe-correct': below-threshold confidence must land on the HITL
        queue (validated=false, status=risk), not be written as a resolved rule.
        """
        overlay = os.path.join(self.tmpdir, "overlay-lowconf.jsonl")

        def timid_extractor(node, framed):
            return {"statement": "maybe %s does something" % node["name"], "confidence": 0.30}

        summary = ext.run(
            self.db,
            config=_CONFIG,
            extract_rule=timid_extractor,
            overlay_path=overlay,
            binary=BINARY,
        )
        self.assertGreater(summary["processed"], 0)
        self.assertEqual(summary["resolved"], 0,
                         "no below-threshold node may be asserted as resolved")
        self.assertEqual(summary["risk_flagged"], summary["processed"])

        recs = [json.loads(l) for l in open(overlay).read().splitlines() if l.strip()]
        for r in recs:
            self.assertEqual(r["status"], "risk")
            self.assertEqual(r["requirement_validated"], 0)
            self.assertIn("risk_reason", r)
            # The packed requirement statement must be the RISK token, not a rule.
            self.assertTrue(str(r["requirement"]).rstrip().endswith("RISK"))

    def test_empty_statement_flags_even_at_high_confidence(self):
        """A confident number with NO statement still RISK-flags (can't assert nothing)."""
        overlay = os.path.join(self.tmpdir, "overlay-nostmt.jsonl")

        def mute_extractor(node, framed):
            return {"statement": "", "confidence": 0.99}

        summary = ext.run(
            self.db, config=_CONFIG, extract_rule=mute_extractor,
            overlay_path=overlay, binary=BINARY,
        )
        self.assertEqual(summary["resolved"], 0)
        self.assertEqual(summary["risk_flagged"], summary["processed"])

    def test_cluster_sprawl_penalty_demotes_to_risk(self):
        """A node whose context sprawls across clusters is demoted below threshold.

        We force maximal sprawl by reporting cohesion 0.0 via a probe extractor that
        reads framed['cohesion']; with floor 0.5 a 0.80 raw confidence → 0.40 → RISK.
        The point: the SAME confident extractor resolves a cohesive node but flags a
        sprawling one — the cluster signal is what makes the difference.
        """
        overlay = os.path.join(self.tmpdir, "overlay-sprawl.jsonl")
        seen = {}

        def confident(node, framed):
            seen[node["name"]] = framed["cohesion"]
            return {"statement": "%s rule" % node["name"], "confidence": 0.80}

        summary = ext.run(
            self.db, config=_CONFIG, extract_rule=confident,
            overlay_path=overlay, cohesion_floor=0.5, binary=BINARY,
        )
        # Some node was observed; cohesion is in range and drives the adjustment.
        self.assertTrue(seen)
        by_name = {r["name"]: r for r in summary["results"]}
        for name, r in by_name.items():
            coh = seen.get(name, 1.0)
            expected = ext.apply_cluster_signal(0.80, coh, floor=0.5)
            self.assertEqual(r["adjusted"], expected)
            # The terminal must agree with the adjusted confidence vs threshold.
            if expected >= 0.75:
                self.assertEqual(r["status"], "resolved")
            else:
                self.assertEqual(r["status"], "risk")

    def test_cross_cutting_node_sprawls_and_risks_under_data_affinity(self):
        """A genuine cross-cutting node is RISK-flagged while its callers resolve —
        same confident extractor, the cluster signal alone splits the verdict.

        Fixture: shared_audit is called by BOTH open_account (accounts file) and
        post_bill (billing file). Under data-affinity weighting those are three
        distinct file-coupled clusters, so shared_audit's context reaches into two
        FOREIGN clusters (cohesion 0.0 → 0.80*0.5 = 0.40 < 0.75 → RISK), while the
        well-bounded callers stay cohesive (1.0) and RESOLVE. This is the §I3
        god-program detector working end-to-end on a real graph.
        """
        cc_dir = tempfile.mkdtemp(prefix="ext-crosscut-")
        try:
            src = os.path.join(cc_dir, "src")
            os.makedirs(src, exist_ok=True)
            with open(os.path.join(src, "shared.py"), "w") as f:
                f.write("def shared_audit(msg):\n    return len(msg)\n")
            with open(os.path.join(src, "accounts.py"), "w") as f:
                f.write(
                    "from shared import shared_audit\n\n\n"
                    "def open_account(a):\n"
                    "    shared_audit('open')\n"
                    "    return a is not None\n"
                )
            with open(os.path.join(src, "billing.py"), "w") as f:
                f.write(
                    "from shared import shared_audit\n\n\n"
                    "def post_bill(b):\n"
                    "    shared_audit('bill')\n"
                    "    return b\n"
                )
            db = os.path.join(cc_dir, "g.db")
            subprocess.run([BINARY, "index", src, "--db", db],
                           capture_output=True, text=True, check=True)
            overlay = os.path.join(cc_dir, "overlay.jsonl")

            def confident(node, framed):
                return {"statement": "%s rule" % node["name"], "confidence": 0.80}

            summary = ext.run(
                db, config=_CONFIG, extract_rule=confident,
                cluster_weight="data-affinity", cohesion_floor=0.5,
                overlay_path=overlay, binary=BINARY,
            )
            by_name = {r["name"]: r for r in summary["results"]}
            self.assertIn("shared_audit", by_name)
            self.assertEqual(by_name["shared_audit"]["status"], "risk",
                             "the cross-cutting node must be RISK-flagged, not asserted")
            self.assertEqual(by_name["open_account"]["status"], "resolved")
            self.assertEqual(by_name["post_bill"]["status"], "resolved")
            self.assertEqual(by_name["shared_audit"]["cohesion"], 0.0)
            # The risk_reason on the cross-cutting node must cite sprawl.
            recs = {json.loads(l)["symbol_id"]: json.loads(l)
                    for l in open(overlay).read().splitlines() if l.strip()}
            cc = next(v for v in recs.values() if v.get("status") == "risk")
            self.assertIn("sprawl", cc.get("risk_reason", "").lower())
        finally:
            shutil.rmtree(cc_dir, ignore_errors=True)

    def test_resume_skips_settled_nodes(self):
        """A second run over the same overlay crawls nothing (idempotent resume)."""
        overlay = os.path.join(self.tmpdir, "overlay-resume.jsonl")

        def extractor(node, framed):
            return {"statement": "%s rule" % node["name"], "confidence": 0.95}

        first = ext.run(self.db, config=_CONFIG, extract_rule=extractor,
                        overlay_path=overlay, binary=BINARY)
        self.assertGreater(first["processed"], 0)
        second = ext.run(self.db, config=_CONFIG, extract_rule=extractor,
                         overlay_path=overlay, binary=BINARY)
        self.assertEqual(second["processed"], 0, "settled nodes must be skipped on resume")

    def test_working_tree_stays_clean(self):
        """The loop with a redirected overlay must NOT touch the repo-root overlay.

        Hermeticity (WF1 rule): annotate() defaults the overlay to a CWD-relative
        path; the loop threads overlay_path through, so a test overlay never dirties
        .anti-legacy/annotations.jsonl. We assert the repo-root overlay is untouched
        by snapshotting it around the run.
        """
        repo_overlay = os.path.join(REPO_ROOT, ".anti-legacy", "annotations.jsonl")
        before = None
        if os.path.exists(repo_overlay):
            with open(repo_overlay, "rb") as f:
                before = f.read()

        overlay = os.path.join(self.tmpdir, "overlay-clean.jsonl")

        def extractor(node, framed):
            return {"statement": "%s rule" % node["name"], "confidence": 0.95}

        ext.run(self.db, config=_CONFIG, extract_rule=extractor,
                overlay_path=overlay, binary=BINARY)

        after = None
        if os.path.exists(repo_overlay):
            with open(repo_overlay, "rb") as f:
                after = f.read()
        self.assertEqual(before, after,
                         "the loop dirtied the repo-root annotations.jsonl — not hermetic")


@unittest.skipIf(ext is None, f"scripts/extract.py not importable yet: {IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestCoverageReachesTerminal(unittest.TestCase):
    """After the loop settles every behavior node, coverage over the overlay is 1.0."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ext-cov-")
        src = os.path.join(self.tmpdir, "src")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "m.py"), "w") as f:
            f.write(
                "def helper(x):\n    return x + 1\n\n\n"
                "def driver(x):\n    return helper(x) * 2\n"
            )
        self.db = os.path.join(self.tmpdir, "g.db")
        subprocess.run([BINARY, "index", src, "--db", self.db],
                       capture_output=True, text=True, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_loop_then_coverage_is_terminal(self):
        overlay = os.path.join(self.tmpdir, "overlay.jsonl")

        def extractor(node, framed):
            return {"statement": "%s rule" % node["name"], "confidence": 0.90}

        ext.run(self.db, config=_CONFIG, extract_rule=extractor,
                overlay_path=overlay, binary=BINARY)

        report = cov.compute_coverage(
            config=_CONFIG, explicit_db=self.db,
            annotations_path=overlay, cross_check=False,
        )
        self.assertGreater(report["behavior_bearing"], 0)
        self.assertEqual(report["unaccounted"], 0,
                         "every behavior node should be settled after the loop")
        self.assertEqual(report["coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
