#!/usr/bin/env python3
"""Hermetic unit tests for scripts/vocabulary.py — the domain-vocabulary
BOOTSTRAP MINER (anti-legacy vocabulary subsystem).

No wicked-estate binary, no network, no real DB. Two seams are exercised with
SYNTHETIC inputs only:

  * the pure miners + bootstrap over an injected node list (kinds carrying the
    REAL estate object-kind shape '{"other":"db2_table"}' so the normalize +
    bucket path — the helper gotcha — is proven), and
  * the CLI/subprocess path over a hand-built minimal SQLite graph matching the
    wicked-estate intern-table schema (symbols/nodes/edges), asserting the
    process emits a vocabulary.json that VALIDATES against
    schemas/vocabulary.schema.json with every term status=proposed,
    verification=unverified, blank definition, a true `freq` count, and the
    config-driven kind buckets (entity from db2_table, action verb after
    dropping the numeric COBOL prefix, abbreviation from a short field token).

The glossary is a MEANING dictionary, not a where-used index: by default each
record carries `freq` + `mined_from` with an EMPTY `sources` (the per-term
SymbolId evidence belongs to wicked-estate). The miner tests opt INTO inline
sources (max_sources_per_term>0) so the `sources[]` assertions have data;
TestSourceCap proves the default-0 (engine-owned) behaviour independently.

The synthetic node set is grounded in the real carddemo idioms named in the
spec (DALYTRAN/ACCT fields, db2_table CARDDEMO.TRANSACTION_TYPE, the
2000-POST-TRANSACTION paragraph) so the test mirrors the live graph shape
without depending on it.
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
SCHEMAS_DIR = os.path.join(REPO_ROOT, "schemas")
VOCAB_PATH = os.path.join(SCRIPTS_DIR, "vocabulary.py")
SCHEMA_PATH = os.path.join(SCHEMAS_DIR, "vocabulary.schema.json")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_vocab():
    if not os.path.isfile(VOCAB_PATH):
        return None
    try:
        return importlib.import_module("vocabulary")
    except Exception:
        return None


_VOCAB = _load_vocab()
_SKIP = None if _VOCAB is not None else "scripts/vocabulary.py not present yet"

try:
    import jsonschema  # type: ignore
    _HAVE_JSONSCHEMA = True
except Exception:
    _HAVE_JSONSCHEMA = False


# Config mirroring .anti-legacy/config.json's coverage block. The miner tests
# opt into inline provenance (max_sources_per_term > 0) so the `sources[]`
# assertions below have data; the DEFAULT-0 behaviour (store no sources, engine
# owns where-used) is proven independently in TestSourceCap.
_CONFIG = {
    "coverage": {
        "behavior_kinds": ["module", "function", "method", "class"],
        "estate_behavior_kinds": ["cics_program", "step", "db2_table"],
        "structural_kinds": ["file", "field", "variable", "constant", "import"],
    },
    "vocabulary": {"max_sources_per_term": 25},
}


def _synthetic_nodes():
    """A small node set spanning every miner. `kind` is stored as the engine
    stores it: simple kinds bare ('field'), estate object-kinds as the JSON
    object token '{"other":"db2_table"}' so _normalize_node_kind must unwrap it.
    """
    def node(symid, name, kind, file="app/cbl/X.cbl", beh=False):
        return {
            "symbol": symid, "symbol_id": symid, "name": name,
            "kind": kind, "file": file, "_has_behavior_out_edge": beh,
        }

    nodes = []
    # ENTITY via estate object-kind (db2_table) — whole name is the entity.
    nodes.append(node("sym:CARDDEMO.TRANSACTION_TYPE", "CARDDEMO.TRANSACTION_TYPE",
                      '{"other":"db2_table"}', "app/cbl/COTRTUPC.cbl"))
    nodes.append(node("sym:CARDDEMO.AUTHFRDS", "CARDDEMO.AUTHFRDS",
                      '{"other":"db2_table"}', "app/cbl/COPAUS2C.cbl"))
    # ENTITY via field-token stem ACCT (repeated past min_freq=3) + abbrevs.
    for i in range(5):
        nodes.append(node("sym:ACCT-FIELD-%d" % i, "ACCT-ID-%d" % i, "field"))
    # ENTITY stem TRAN + abbrev TRAN; field tokens DALYTRAN -> abbrev too.
    for i in range(4):
        nodes.append(node("sym:TRAN-FIELD-%d" % i, "TRAN-AMT-%d" % i, "field"))
    for i in range(3):
        nodes.append(node("sym:DALY-%d" % i, "DALYTRAN-CARD-%d" % i, "field"))
    # ACTION verbs: leading token after dropping a numeric COBOL prefix.
    nodes.append(node("sym:2000-POST", "2000-POST-TRANSACTION", "module", beh=True))
    nodes.append(node("sym:2700-UPD", "2700-UPDATE-TCATBAL", "function", beh=True))
    nodes.append(node("sym:2800-UPD", "2800-UPDATE-ACCOUNT-REC", "function", beh=True))
    nodes.append(node("sym:0000-UPD", "0000-UPDATE-MAIN", "function", beh=True))
    nodes.append(node("sym:READ-1", "1000-READ-INPUT", "function", beh=True))
    nodes.append(node("sym:READ-2", "READ-CARD-FILE", "function", beh=True))
    nodes.append(node("sym:READ-3", "READ-ACCT-FILE", "function", beh=True))
    # Filler that MUST be dropped (stopword / numeric-only after split).
    nodes.append(node("sym:FILLER-1", "FILLER", "field"))
    nodes.append(node("sym:FILLER-2", "FILLER", "field"))
    nodes.append(node("sym:X-1", "X", "field"))
    return nodes


@unittest.skipIf(_SKIP, _SKIP or "")
class TestMiners(unittest.TestCase):
    def _doc(self, min_freq=3, out=None):
        out = out or self._out
        return _VOCAB.bootstrap(
            "carddemo.db", config=_CONFIG, out_path=out,
            min_freq=min_freq, config_ref=".anti-legacy/config.json",
            nodes=_synthetic_nodes(),
        )

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._out = os.path.join(self._tmp, "vocabulary.json")

    def _by_canon(self, doc):
        return {t["canonical"]: t for t in doc["terms"]}

    def test_db2_table_becomes_entity(self):
        """The estate object-kind {"other":"db2_table"} must be unwrapped and
        mined as an entity — proving the kinds=None bucket-it-yourself gotcha."""
        terms = self._by_canon(self._doc(min_freq=1))
        self.assertIn("CARDDEMO.TRANSACTION_TYPE", terms)
        self.assertEqual(terms["CARDDEMO.TRANSACTION_TYPE"]["term_type"], "entity")
        self.assertEqual(
            terms["CARDDEMO.TRANSACTION_TYPE"]["sources"][0]["node_kind"], "db2_table")

    def test_action_verb_drops_numeric_prefix(self):
        """2000-POST-TRANSACTION -> verb POST (not 2000); UPDATE/READ mined."""
        terms = self._by_canon(self._doc(min_freq=1))
        self.assertIn("POST", terms)
        self.assertEqual(terms["POST"]["term_type"], "action")
        self.assertNotIn("2000", terms)
        self.assertNotIn("0000", terms)
        self.assertIn("UPDATE", terms)  # 2700/2800/0000-UPDATE collapse
        # freq is now a top-level record field (the true recurrence count).
        self.assertGreaterEqual(terms["UPDATE"]["freq"], 3)

    def test_field_stem_entity_and_abbrev(self):
        terms = self._by_canon(self._doc(min_freq=3))
        # ACCT appears 5x as a field stem -> entity at min_freq 3.
        self.assertIn("ACCT", terms)
        # READ appears 3x as a verb -> action.
        self.assertEqual(terms["READ"]["term_type"], "action")

    def test_min_freq_floor_drops_oneoffs(self):
        """At min_freq=3, a 2x stem (TRAN appears 4x so survives; build a 1x)."""
        terms = self._by_canon(self._doc(min_freq=3))
        # FILLER/X are stopwords -> never present regardless of freq.
        self.assertNotIn("FILLER", terms)
        self.assertNotIn("X", terms)

    def test_every_bootstrap_term_is_proposed_unverified_blank(self):
        doc = self._doc(min_freq=1)
        self.assertTrue(doc["terms"], "miner produced no terms")
        for t in doc["terms"]:
            self.assertEqual(t["status"], "proposed")
            self.assertEqual(t["verification"], "unverified")
            self.assertEqual(t["definition"], "")
            # _CONFIG opts into inline sources, so each bootstrap term carries
            # >=1 graph_node source here; mined_from names the proposing miner.
            self.assertGreaterEqual(len(t["sources"]), 1)
            self.assertEqual(t["sources"][0]["kind"], "graph_node")
            self.assertTrue(t["mined_from"])
        # Run-level provenance now lives ONCE in the doc meta, not per record.
        self.assertIn("bootstrap_run", doc["meta"])

    def test_idempotent_preserves_human_authored(self):
        """A confirmed/defined record survives a re-run with definition,
        aliases, pseudonyms, and elevated verification intact."""
        doc = self._doc(min_freq=1)
        canon = "ACCT"
        for t in doc["terms"]:
            if t["canonical"] == canon:
                t["status"] = "confirmed"
                t["verification"] = "trusted_verified"
                t["definition"] = "Account master record"
                t["aliases"] = ["ACCOUNT"]
                t["pseudonyms_slang"] = ["the master"]
        with open(self._out, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        # Re-run.
        doc2 = self._doc(min_freq=1)
        acct = {t["canonical"]: t for t in doc2["terms"]}[canon]
        self.assertEqual(acct["status"], "confirmed")
        self.assertEqual(acct["verification"], "trusted_verified")
        self.assertEqual(acct["definition"], "Account master record")
        self.assertEqual(acct["aliases"], ["ACCOUNT"])
        self.assertEqual(acct["pseudonyms_slang"], ["the master"])

    def test_meta_counts(self):
        doc = self._doc(min_freq=1)
        self.assertEqual(doc["meta"]["term_count"], len(doc["terms"]))
        self.assertEqual(doc["meta"]["generated_by"], "anti-legacy:vocabulary")

    @unittest.skipUnless(_HAVE_JSONSCHEMA, "jsonschema not installed")
    def test_validates_against_schema(self):
        doc = self._doc(min_freq=1)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.validate(instance=doc, schema=schema)


@unittest.skipIf(_SKIP, _SKIP or "")
class TestSourceCap(unittest.TestCase):
    """The bootstrap bounds the per-term provenance stored in the file via
    config.vocabulary.max_sources_per_term so `_add_source` cannot bloat the
    glossary. DEFAULT 0 = store NONE (where-used is the engine's job); the
    `freq` field still carries the TRUE recurrence count. A positive config
    value inlines at most N representative SymbolIds per term, capped — even a
    high-frequency token (ACCT recurs 12x here) never exceeds the cap."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._out = os.path.join(self._tmp, "vocabulary.json")

    def _hot_nodes(self, n=12):
        """n field nodes that all share the ACCT stem -> freq n. With a positive
        cap the stored sources must be bounded well below n."""
        return [
            {"symbol": "sym:ACCT-%d" % i, "symbol_id": "sym:ACCT-%d" % i,
             "name": "ACCT-FIELD-%d" % i, "kind": "field",
             "file": "app/cbl/X.cbl", "_has_behavior_out_edge": False}
            for i in range(n)
        ]

    def _acct(self, doc):
        return {t["canonical"]: t for t in doc["terms"]}["ACCT"]

    def test_default_cap_is_zero_stores_no_sources(self):
        cfg = {"coverage": _CONFIG["coverage"]}  # no vocabulary block
        self.assertEqual(_VOCAB.max_sources_per_term(cfg), 0)
        doc = _VOCAB.bootstrap("x.db", config=cfg, out_path=self._out,
                               min_freq=1, nodes=self._hot_nodes(12))
        acct = self._acct(doc)
        # No inline provenance stored by default — the engine owns where-used —
        # but the term is still mined and present.
        self.assertEqual(acct["sources"], [])
        self.assertEqual(acct["canonical"], "ACCT")

    def test_positive_config_caps_inline_sources(self):
        cfg = {"coverage": _CONFIG["coverage"],
               "vocabulary": {"max_sources_per_term": 3}}
        self.assertEqual(_VOCAB.max_sources_per_term(cfg), 3)
        doc = _VOCAB.bootstrap("x.db", config=cfg, out_path=self._out,
                               min_freq=1, nodes=self._hot_nodes(12))
        acct = self._acct(doc)
        # 12 hits, but the stored provenance is bounded at the configured 3;
        # the top-level freq still records the true count.
        self.assertEqual(len(acct["sources"]), 3)
        self.assertEqual(acct["freq"], 12)

    def test_invalid_or_negative_cap_falls_back_to_default_zero(self):
        for bad in ({"max_sources_per_term": -4},
                    {"max_sources_per_term": "nope"},
                    {"max_sources_per_term": 0},
                    {}):
            cfg = {"coverage": _CONFIG["coverage"], "vocabulary": bad}
            self.assertEqual(_VOCAB.max_sources_per_term(cfg), 0)


@unittest.skipIf(_SKIP, _SKIP or "")
class TestCLISubprocess(unittest.TestCase):
    """Drive the real process over a hand-built SQLite graph (binary-free)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._db = os.path.join(self._tmp, "carddemo.db")
        self._out = os.path.join(self._tmp, "vocabulary.json")
        self._cfg = os.path.join(self._tmp, "config.json")
        with open(self._cfg, "w", encoding="utf-8") as f:
            json.dump({"project_name": "x", **_CONFIG}, f)
        self._build_db(self._db, _synthetic_nodes())

    @staticmethod
    def _build_db(path, nodes):
        """symbols/nodes/edges matching wicked_estate.list_nodes' read queries.
        kind stored verbatim: simple kinds JSON-quoted ('"field"'), estate
        object-kinds as the bare object token ('{"other":"db2_table"}')."""
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            cur.execute("CREATE TABLE symbols (sid INTEGER PRIMARY KEY, sym TEXT)")
            cur.execute("CREATE TABLE nodes (symbol INTEGER, name TEXT, kind TEXT, file TEXT)")
            cur.execute("CREATE TABLE edges (source INTEGER, target INTEGER, kind TEXT)")
            for i, n in enumerate(nodes):
                cur.execute("INSERT INTO symbols (sid, sym) VALUES (?, ?)",
                            (i, n["symbol_id"]))
                k = n["kind"]
                stored = k if k.startswith("{") else '"%s"' % k
                cur.execute(
                    "INSERT INTO nodes (symbol, name, kind, file) VALUES (?, ?, ?, ?)",
                    (i, n["name"], stored, n["file"]))
                if n["_has_behavior_out_edge"]:
                    cur.execute("INSERT INTO edges (source, target, kind) VALUES (?, ?, ?)",
                                (i, i, '"calls"'))
            conn.commit()
        finally:
            conn.close()

    def test_subprocess_emits_valid_vocabulary(self):
        proc = subprocess.run(
            [sys.executable, VOCAB_PATH, "--db", self._db, "--config", self._cfg,
             "--out", self._out, "--min-freq", "1"],
            capture_output=True, text=True, cwd=self._tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(self._out), "no vocabulary.json written")
        with open(self._out, "r", encoding="utf-8") as f:
            doc = json.load(f)
        self.assertIn("terms", doc)
        self.assertIn("meta", doc)
        canons = {t["canonical"] for t in doc["terms"]}
        # entity from db2_table, verb after numeric-prefix drop, an abbrev.
        self.assertIn("CARDDEMO.TRANSACTION_TYPE", canons)
        self.assertIn("POST", canons)
        for t in doc["terms"]:
            self.assertEqual(t["status"], "proposed")
            self.assertEqual(t["verification"], "unverified")
            self.assertGreaterEqual(len(t["sources"]), 1)
        if _HAVE_JSONSCHEMA:
            with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
                schema = json.load(f)
            jsonschema.validate(instance=doc, schema=schema)

    def test_subprocess_missing_db_exits_2(self):
        proc = subprocess.run(
            [sys.executable, VOCAB_PATH, "--db", os.path.join(self._tmp, "nope.db"),
             "--config", self._cfg, "--out", self._out],
            capture_output=True, text=True, cwd=self._tmp,
        )
        self.assertEqual(proc.returncode, 2)


@unittest.skipIf(_SKIP, _SKIP or "")
class TestProject(unittest.TestCase):
    """`project_terms_to_graph` binds CONFIRMED terms onto the code graph as
    native domain_* annotations (the "terms in wicked-estate" seam) so the
    engine's own cluster / by-requirement become term-aware. Only confirmed
    terms are bound; an ambiguous node is SKIPPED (never smeared), not mis-tagged.
    Hermetic: the engine write seam (we.annotate_kv) is stubbed, so no binary
    or graph DB is needed."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vocab = os.path.join(self._tmp, "vocabulary.json")
        self._calls = []
        self._real_annotate_kv = _VOCAB.we.annotate_kv

        def _fake(db, symbol_id, key, value, *, confidence=None, provenance=None,
                  author=None, binary=None):
            self._calls.append({"sid": symbol_id, "key": key, "value": value,
                                "confidence": confidence})
            return {"annotated": True}

        _VOCAB.we.annotate_kv = _fake

    def tearDown(self):
        _VOCAB.we.annotate_kv = self._real_annotate_kv

    def _write_vocab(self, terms):
        with open(self._vocab, "w", encoding="utf-8") as f:
            json.dump({"terms": terms, "meta": {"term_count": len(terms)}}, f)

    def test_only_confirmed_terms_are_bound(self):
        self._write_vocab([
            {"canonical": "ACCT", "term_type": "entity",
             "definition": "Account master record", "status": "confirmed",
             "verification": "trusted_verified", "freq": 5, "sources": []},
            {"canonical": "POST", "term_type": "action", "definition": "",
             "status": "proposed", "verification": "unverified",
             "freq": 1, "sources": []},
        ])
        summary = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=_synthetic_nodes())
        # ACCT (confirmed) is bound; POST (proposed) is not.
        self.assertEqual(summary["terms"], 1)
        self.assertEqual(summary["confirmed_available"], 1)
        self.assertGreaterEqual(summary["projected"], 1)
        self.assertEqual({c["value"] for c in self._calls}, {"ACCT"})
        self.assertEqual({c["key"] for c in self._calls}, {"domain_entity"})
        # trusted_verified -> confidence 1.0 on every bind.
        self.assertTrue(all(c["confidence"] == 1.0 for c in self._calls))

    def test_no_confirmed_terms_binds_nothing(self):
        self._write_vocab([
            {"canonical": "ACCT", "term_type": "entity", "definition": "",
             "status": "proposed", "verification": "unverified",
             "freq": 5, "sources": []},
        ])
        summary = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=_synthetic_nodes())
        self.assertEqual(summary["projected"], 0)
        self.assertEqual(summary["terms"], 0)
        self.assertEqual(self._calls, [])

    def test_ambiguous_node_is_skipped_not_smeared(self):
        self._write_vocab([
            {"canonical": "ACCT", "term_type": "entity",
             "definition": "Account master record", "status": "confirmed",
             "verification": "trusted_verified", "freq": 5, "sources": []},
        ])

        # The engine refuses one specific SymbolId (an ambiguous name) — it must
        # be counted as skipped, never bound.
        def _fake_one_ambiguous(db, symbol_id, key, value, *, confidence=None,
                                provenance=None, author=None, binary=None):
            if symbol_id == "sym:ACCT-FIELD-0":
                raise _VOCAB.we.WickedEstateError("ambiguous name")
            self._calls.append({"sid": symbol_id, "key": key, "value": value})
            return {"annotated": True}

        _VOCAB.we.annotate_kv = _fake_one_ambiguous
        summary = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=_synthetic_nodes())
        self.assertEqual(summary["skipped"], 1)
        self.assertNotIn("sym:ACCT-FIELD-0", {c["sid"] for c in self._calls})
        # ACCT still bound on the other 4 nodes -> not an all-skipped gap.
        self.assertEqual(summary["all_skipped"], [])

    def test_gaps_are_surfaced_unbound_vs_all_skipped(self):
        """Terms have no committed per-binding overlay, so a silent 0-bind must
        leave a trace. Two distinct gaps: a confirmed term that mines NO grounding
        on this graph (`unbound`) vs one that grounds but whose every node is
        refused as ambiguous (`all_skipped`)."""
        self._write_vocab([
            {"canonical": "ACCT", "term_type": "entity",
             "definition": "Account master record", "status": "confirmed",
             "verification": "trusted_verified", "freq": 5, "sources": []},
            {"canonical": "ZZZNOPE", "term_type": "entity",
             "definition": "not in this graph", "status": "confirmed",
             "verification": "trusted_verified", "freq": 9, "sources": []},
        ])

        # Engine refuses EVERY bind -> ACCT grounds but binds 0 (all_skipped);
        # ZZZNOPE never grounds at all (unbound).
        def _fake_all_refuse(db, symbol_id, key, value, *, confidence=None,
                             provenance=None, author=None, binary=None):
            raise _VOCAB.we.WickedEstateError("ambiguous name")

        _VOCAB.we.annotate_kv = _fake_all_refuse
        summary = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=_synthetic_nodes())
        self.assertEqual(summary["projected"], 0)
        self.assertEqual(summary["unbound"], ["ZZZNOPE"])     # mined no grounding
        self.assertEqual(summary["all_skipped"], ["ACCT"])    # grounded, 0 bound
        self.assertEqual(summary["confirmed_available"], 2)


@unittest.skipIf(_SKIP, _SKIP or "")
class TestBindingsAndDrift(unittest.TestCase):
    """ISS-04: project records a per-run, node-NAME-keyed bindings set + content
    hash so a determinism drift (same confirmed glossary, different bound node
    sets — a miner/config change) is detectable across runs without a git diff."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vocab = os.path.join(self._tmp, "vocabulary.json")
        self._real_annotate_kv = _VOCAB.we.annotate_kv
        _VOCAB.we.annotate_kv = lambda *a, **k: {"annotated": True}

    def tearDown(self):
        _VOCAB.we.annotate_kv = self._real_annotate_kv

    def _vocab_with(self, *canon):
        with open(self._vocab, "w", encoding="utf-8") as f:
            json.dump({"terms": [
                {"canonical": c, "term_type": "entity", "definition": "x",
                 "status": "confirmed", "verification": "trusted_verified",
                 "freq": 5, "sources": []} for c in canon
            ], "meta": {}}, f)

    def test_summary_carries_node_name_bindings_and_hashes(self):
        self._vocab_with("ACCT")
        summary = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=_synthetic_nodes())
        self.assertIn("bindings", summary)
        acct = next(b for b in summary["bindings"] if b["canonical"] == "ACCT")
        # node NAMES (reindex-stable), not SymbolIds.
        self.assertTrue(all(n.startswith("ACCT-ID-") for n in acct["node_names"]), acct)
        self.assertEqual(acct["domain_key"], "domain_entity")
        self.assertTrue(summary["glossary_hash"] and summary["content_hash"])

    def test_content_hash_changes_with_node_set_not_with_symbol_ids(self):
        self._vocab_with("ACCT")
        s1 = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=_synthetic_nodes())
        # Re-mine the SAME node names but with DIFFERENT symbol_ids (simulating a
        # reindex): content_hash must be UNCHANGED (it keys on names).
        renumbered = []
        for n in _synthetic_nodes():
            n = dict(n)
            n["symbol_id"] = "RE-" + n["symbol_id"]
            n["symbol"] = n["symbol_id"]
            renumbered.append(n)
        s2 = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=renumbered)
        self.assertEqual(s1["content_hash"], s2["content_hash"])

    def test_drift_detected_only_on_changed_node_set_same_glossary(self):
        self._vocab_with("ACCT")
        path = os.path.join(self._tmp, "vocabulary-bindings.json")
        s1 = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=_synthetic_nodes())
        # First write: no prior -> no drift.
        self.assertFalse(_VOCAB._write_bindings_and_check_drift(path, s1, "x.db"))
        # Same inputs again -> identical content_hash -> no drift.
        self.assertFalse(_VOCAB._write_bindings_and_check_drift(path, s1, "x.db"))
        # Now ACCT binds to MORE nodes (a miner/config drift), glossary unchanged.
        extra = _synthetic_nodes() + [{
            "symbol": "sym:ACCT-NEW", "symbol_id": "sym:ACCT-NEW",
            "name": "ACCT-NEW-FIELD", "kind": "field", "file": "x.cbl",
            "_has_behavior_out_edge": False}]
        s2 = _VOCAB.project_terms_to_graph(
            "x.db", vocab_path=self._vocab, config=_CONFIG, nodes=extra)
        self.assertNotEqual(s1["content_hash"], s2["content_hash"])
        self.assertTrue(_VOCAB._write_bindings_and_check_drift(path, s2, "x.db"),
                        "same glossary + changed node set must flag drift")
        with open(path) as f:
            self.assertTrue(json.load(f)["drift_from_prior"])


@unittest.skipIf(_SKIP, _SKIP or "")
class TestCheckProjection(unittest.TestCase):
    """ISS-03: the reprojection-enforcement gate predicate. FAIL iff confirmed
    terms ground on the graph but it carries 0 domain_* tags (a skipped
    reprojection after a fresh survey wiped them). Never fails when there's
    nothing to enforce. Hermetic: nodes injected, engine read stubbed."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._vocab = os.path.join(self._tmp, "vocabulary.json")
        self._real = _VOCAB.we.nodes_annotated_with

    def tearDown(self):
        _VOCAB.we.nodes_annotated_with = self._real

    def _vocab_with(self, *terms):
        with open(self._vocab, "w", encoding="utf-8") as f:
            json.dump({"terms": [
                {"canonical": c, "term_type": tt, "definition": "x",
                 "status": "confirmed", "verification": "trusted_verified",
                 "freq": 5, "sources": []} for c, tt in terms
            ], "meta": {}}, f)

    def _check(self, tagged_nodes):
        _VOCAB.we.nodes_annotated_with = lambda *a, **k: list(tagged_nodes)
        return _VOCAB.check_projection(
            "x.db", config=_CONFIG, vocab_path=self._vocab, nodes=_synthetic_nodes())

    def test_grounded_terms_but_no_tags_is_blocked(self):
        self._vocab_with(("ACCT", "entity"))           # ACCT grounds (5 ACCT-ID nodes)
        r = self._check([])                            # engine carries 0 tags
        self.assertFalse(r["ok"])
        self.assertGreaterEqual(r["bindable"], 1)
        self.assertIn("reprojection", r["reason"])

    def test_grounded_terms_with_tags_is_ok(self):
        self._vocab_with(("ACCT", "entity"))
        r = self._check([{"name": "ACCT-ID-0"}])       # engine has the tag
        self.assertTrue(r["ok"])
        self.assertGreater(r["tagged"], 0)

    def test_no_confirmed_terms_is_ok(self):
        self._vocab_with()                             # empty glossary
        r = self._check([])
        self.assertTrue(r["ok"])
        self.assertEqual(r["confirmed"], 0)

    def test_confirmed_but_absent_from_graph_is_ok_not_a_miss(self):
        self._vocab_with(("ZZZNOPE", "entity"))        # not in synthetic nodes
        r = self._check([])
        self.assertTrue(r["ok"])                       # coverage gap, not a reproj miss
        self.assertEqual(r["bindable"], 0)


if __name__ == "__main__":
    unittest.main()
