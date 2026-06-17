#!/usr/bin/env python3
"""Unit tests for wicked_estate.correspond() — interface E (the cross-repo
node-correspondence primitive, the merge-alignment unit for multi-repo semantic-join).

correspond(db_a, db_b, *, kinds, min_score, use_semantic, binary) is NATIVE-FIRST
when a code `kinds` filter is given (v0.1.5 ships native `correspond --db-a A --db-b B
--kind K --min-score F --json`), and falls back to the pure-stdlib SHIM (list_nodes +
the read-only edges-table exception + difflib) when `kinds` is None (only the shim
pairs File/structural nodes — native's is_correspond_kind excludes them) or on any
native failure. The contract these tests pin:

  * Two DBs indexing similar code yield correspondences pairing the like-named /
    like-shaped symbols (same de-quoted kind, exact-name primary tier).
  * A near-rename (difflib name ratio >= name_ratio_threshold) pairs on the
    secondary fuzzy tier; an UNRELATED symbol does NOT (it produces no pair).
  * Greedy 1:1 assignment is deterministic and each symbol maps to at most one
    counterpart; pairs below `min_score` are dropped.
  * The `kinds` filter passes straight through to list_nodes (function-only,
    file nodes excluded).

TWO test surfaces, mirroring tests/test_wicked_estate.py:
  * SHIM-DIRECT (no binary needed): two hand-built SQLite DBs matching the
    schema the helper reads — symbols(sid,sym) / nodes(symbol,name,kind,file) /
    edges(source,target,kind,confidence,file). A NONEXISTENT `binary=` path is
    threaded so _probe_native_subcommand() hits OSError and selects the shim,
    making these run with NO engine present.
  * REAL-BINARY (skipped if the engine is absent): two tiny real-indexed repos,
    exactly the setUpClass pattern of the sibling suite, threading binary=.

HERMETIC: every DB / repo lives under a tempfile dir torn down in tearDown /
tearDownClass. correspond() is READ-ONLY (no annotate, no overlay writes), so
no ANTI_LEGACY_ANNOTATIONS redirect is needed — the working tree stays clean.
The guarded import keeps collection green if the helper is not importable yet.
"""
import os
import sys
import shutil
import sqlite3
import unittest
import tempfile
import subprocess

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts")
)
# SCRIPTS_DIR intentionally NOT added to sys.path (migrated modules resolve
# via tests/conftest.py); SCRIPTS_DIR retained only for by-path shim guards.

# The known-good v0.0.1 binary used by the spike (also the helper's documented
# priority-4 fallback). Real-binary tests skip cleanly when it is absent.
WICKED_ESTATE_FALLBACK = (
    ""
)
BINARY = shutil.which("wicked-estate") or (
    WICKED_ESTATE_FALLBACK if os.access(WICKED_ESTATE_FALLBACK, os.X_OK) else None
)

# Guarded import: stay GREEN (skip, never error at collection) if the helper is
# not importable yet during the parallel build.
try:
    from antilegacy_core import wicked_estate as we  # noqa: E402

    HELPER_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only pre-helper
    we = None
    HELPER_IMPORT_ERROR = exc


# ---------------------------------------------------------------------------
# Helpers for the hand-built (no-binary) shim fixtures.
# ---------------------------------------------------------------------------
def _build_db(path, nodes, edges):
    """Build a minimal SQLite DB matching the schema list_nodes/_read_edges read.

    `nodes`: list of (sym, name, kind, file). `kind` MUST be JSON-quoted exactly
    as the engine stores it (e.g. '"function"') so _dekind() round-trips.
    `edges`: list of (src_sym, tgt_sym, kind, confidence, file).
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE symbols (sid INTEGER PRIMARY KEY, sym TEXT)")
        conn.execute("CREATE TABLE nodes (symbol INTEGER, name TEXT, kind TEXT, file TEXT)")
        conn.execute(
            "CREATE TABLE edges (source INTEGER, target INTEGER, kind TEXT, "
            "confidence REAL, file TEXT)"
        )
        sym_to_sid = {}
        for i, (sym, name, kind, file_) in enumerate(nodes, start=1):
            conn.execute("INSERT INTO symbols (sid, sym) VALUES (?, ?)", (i, sym))
            conn.execute(
                "INSERT INTO nodes (symbol, name, kind, file) VALUES (?, ?, ?, ?)",
                (i, name, kind, file_),
            )
            sym_to_sid[sym] = i
        for (s_sym, t_sym, kind, conf, file_) in edges:
            conn.execute(
                "INSERT INTO edges (source, target, kind, confidence, file) "
                "VALUES (?, ?, ?, ?, ?)",
                (sym_to_sid[s_sym], sym_to_sid[t_sym], kind, conf, file_),
            )
        conn.commit()
    finally:
        conn.close()


def _names(pairs):
    """Set of (a_name, b_name) tuples from a correspond() pairs list."""
    return {(p["a_name"], p["b_name"]) for p in pairs}


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestCorrespondShimDirect(unittest.TestCase):
    """Shim path on hand-built DBs — runs with NO engine binary present.

    A nonexistent `binary=` path is threaded into correspond() so its native
    probe (`<bin> correspond --help`) raises OSError and the shim is selected.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-corr-shim-")
        # A path that does NOT exist -> _probe_native_subcommand OSError -> shim.
        self.no_bin = os.path.join(self.tmpdir, "nonexistent-wicked-estate")
        self.db_a = os.path.join(self.tmpdir, "a.db")
        self.db_b = os.path.join(self.tmpdir, "b.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_like_named_like_shaped_symbols_pair(self):
        """Exact-name + same-kind symbols correspond; unrelated/extra symbols don't.

        Repo A: open_account, validate, close_account (close_account A-only).
        Repo B: open_account, validate, unrelated_xyz (unrelated_xyz B-only).
        Expect EXACTLY the two shared symbols to pair; no false pair for the
        A-only or B-only symbol.
        """
        _build_db(
            self.db_a,
            [
                ("a::open", "open_account", '"function"', "acct.py"),
                ("a::val", "validate", '"function"', "acct.py"),
                ("a::close", "close_account", '"function"', "acct.py"),
            ],
            [("a::open", "a::val", '"calls"', 0.65, "acct.py")],
        )
        _build_db(
            self.db_b,
            [
                ("b::open", "open_account", '"function"', "acct.py"),
                ("b::val", "validate", '"function"', "acct.py"),
                ("b::weird", "unrelated_xyz", '"function"', "acct.py")
            ],
            [("b::open", "b::val", '"calls"', 0.65, "acct.py")],
        )
        res = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)

        self.assertEqual(res["count"], 2, f"expected 2 pairs, got {res!r}")
        self.assertEqual(
            _names(res["pairs"]),
            {("open_account", "open_account"), ("validate", "validate")},
        )
        # No false pair: the A-only and B-only names must appear on NEITHER side.
        a_names = {p["a_name"] for p in res["pairs"]}
        b_names = {p["b_name"] for p in res["pairs"]}
        self.assertNotIn("close_account", a_names)
        self.assertNotIn("unrelated_xyz", b_names)
        # Exact-name + same-kind + equal degree -> perfect structural score.
        for p in res["pairs"]:
            self.assertEqual(p["kind"], "function")
            self.assertAlmostEqual(p["score"], 1.0, places=6)
            self.assertEqual(p["signals"]["name_ratio"], 1.0)
            self.assertEqual(p["signals"]["kind_match"], 1.0)
            # Pairs carry the FULL interned symbol ids from both sides.
            self.assertTrue(p["a_symbol_id"].startswith("a::"))
            self.assertTrue(p["b_symbol_id"].startswith("b::"))

    def test_fuzzy_rename_pairs_unrelated_does_not(self):
        """A near-rename pairs on the fuzzy tier (ratio >= threshold); foo/bar don't.

        calculate_total vs calculate_totals -> difflib ratio ~0.97 (>= 0.85) so it
        pairs on the secondary fuzzy tier; zebra vs giraffe -> ratio ~0.0 so it
        produces NO pair (the false-positive guard).
        """
        _build_db(
            self.db_a,
            [
                ("a::calc", "calculate_total", '"function"', "calc.py"),
                ("a::zebra", "zebra", '"function"', "calc.py"),
            ],
            [],
        )
        _build_db(
            self.db_b,
            [
                ("b::calc", "calculate_totals", '"function"', "calc.py"),
                ("b::gir", "giraffe", '"function"', "calc.py"),
            ],
            [],
        )
        res = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)

        self.assertEqual(res["count"], 1, f"only the fuzzy near-rename should pair: {res!r}")
        pair = res["pairs"][0]
        self.assertEqual(pair["a_name"], "calculate_total")
        self.assertEqual(pair["b_name"], "calculate_totals")
        # Fuzzy tier: ratio is < 1.0 but above the 0.85 threshold.
        self.assertGreaterEqual(pair["signals"]["name_ratio"], 0.85)
        self.assertLess(pair["signals"]["name_ratio"], 1.0)
        # zebra / giraffe (ratio 0) must not appear anywhere.
        self.assertNotIn("zebra", _names(res["pairs"]).__str__())
        self.assertNotIn("giraffe", _names(res["pairs"]).__str__())

    def test_min_score_drops_low_confidence_pairs(self):
        """min_score filters: a fuzzy pair (score < 1.0) is dropped, an exact one kept.

        With both an exact match (score 1.0) and a fuzzy near-rename (score ~0.98)
        present, min_score=0.99 keeps only the exact pair.
        """
        _build_db(
            self.db_a,
            [
                ("a::open", "open_account", '"function"', "acct.py"),
                ("a::calc", "calculate_total", '"function"', "acct.py"),
            ],
            [],
        )
        _build_db(
            self.db_b,
            [
                ("b::open", "open_account", '"function"', "acct.py"),
                ("b::calc", "calculate_totals", '"function"', "acct.py"),
            ],
            [],
        )
        # No floor: both pair.
        res_all = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)
        self.assertEqual(res_all["count"], 2)

        # High floor: only the exact-name pair survives.
        res_hi = we.correspond(
            self.db_a, self.db_b, kinds=["function"], min_score=0.99, binary=self.no_bin
        )
        self.assertEqual(res_hi["count"], 1, f"min_score=0.99 should keep only exact: {res_hi!r}")
        self.assertEqual(_names(res_hi["pairs"]), {("open_account", "open_account")})

    def test_greedy_one_to_one_assignment_is_deterministic(self):
        """Each symbol maps to at most one counterpart; output is stable across runs.

        Repo B has TWO candidates that fuzzy-match A's `handler`: an exact `handler`
        (score 1.0) and a near-rename `handlers` (lower score). Greedy descending
        assignment must bind A's `handler` to B's exact `handler` (the best), and
        the result must be byte-identical run to run (deterministic tie-break).
        """
        _build_db(
            self.db_a,
            [("a::h", "handler", '"function"', "h.py")],
            [],
        )
        _build_db(
            self.db_b,
            [
                ("b::h", "handler", '"function"', "h.py"),
                ("b::hs", "handlers", '"function"', "h.py"),
            ],
            [],
        )
        r1 = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)
        r2 = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)

        # A's single `handler` is consumed by exactly one B node — the best (exact).
        self.assertEqual(r1["count"], 1)
        self.assertEqual(r1["pairs"][0]["a_name"], "handler")
        self.assertEqual(r1["pairs"][0]["b_name"], "handler")
        # No symbol used twice (greedy 1:1).
        a_ids = [p["a_symbol_id"] for p in r1["pairs"]]
        b_ids = [p["b_symbol_id"] for p in r1["pairs"]]
        self.assertEqual(len(a_ids), len(set(a_ids)))
        self.assertEqual(len(b_ids), len(set(b_ids)))
        # Deterministic: identical structure across calls.
        self.assertEqual(_names(r1["pairs"]), _names(r2["pairs"]))
        self.assertEqual(r1["count"], r2["count"])

    def test_kinds_filter_excludes_non_function_nodes(self):
        """The `kinds` filter passes through to list_nodes — file nodes are excluded.

        Both DBs carry a `file` node named acct.py; with kinds=["function"] the
        file nodes must not produce a pair (only the function pairs).
        """
        _build_db(
            self.db_a,
            [
                ("a::file", "acct.py", '"file"', "acct.py"),
                ("a::open", "open_account", '"function"', "acct.py"),
            ],
            [],
        )
        _build_db(
            self.db_b,
            [
                ("b::file", "acct.py", '"file"', "acct.py"),
                ("b::open", "open_account", '"function"', "acct.py"),
            ],
            [],
        )
        res = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)
        self.assertEqual(res["count"], 1)
        self.assertEqual(_names(res["pairs"]), {("open_account", "open_account")})
        for p in res["pairs"]:
            self.assertEqual(p["kind"], "function")

    def test_disjoint_repos_yield_no_pairs(self):
        """Two repos with NO shared/similar symbols return zero correspondences."""
        _build_db(
            self.db_a,
            [
                ("a::alpha", "alpha_widget", '"function"', "a.py"),
                ("a::beta", "beta_gadget", '"function"', "a.py"),
            ],
            [],
        )
        _build_db(
            self.db_b,
            [
                ("b::gamma", "gamma_sprocket", '"function"', "b.py"),
                ("b::delta", "delta_flange", '"function"', "b.py"),
            ],
            [],
        )
        res = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)
        self.assertEqual(res["count"], 0, f"unrelated repos must not pair: {res!r}")
        self.assertEqual(res["pairs"], [])

    def test_shape_of_result(self):
        """Result carries the documented top-level keys and per-pair signal block."""
        _build_db(
            self.db_a, [("a::x", "shared", '"function"', "x.py")], []
        )
        _build_db(
            self.db_b, [("b::x", "shared", '"function"', "x.py")], []
        )
        res = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=self.no_bin)
        for key in ("db_a", "db_b", "pairs", "count"):
            self.assertIn(key, res)
        self.assertEqual(res["count"], len(res["pairs"]))
        p = res["pairs"][0]
        for key in ("a_symbol_id", "a_name", "b_symbol_id", "b_name", "kind", "score", "signals"):
            self.assertIn(key, p)
        for sig in ("name_ratio", "kind_match", "degree_sim"):
            self.assertIn(sig, p["signals"])


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestCorrespondRealBinary(unittest.TestCase):
    """correspond() against two tiny REAL-indexed repos (skips without the engine)."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="we-corr-real-")
        # Repo A: open_account -> validate, plus an A-only close_account.
        src_a = os.path.join(cls.tmpdir, "a")
        os.makedirs(src_a, exist_ok=True)
        with open(os.path.join(src_a, "acct.py"), "w") as f:
            f.write(
                "def open_account(x):\n    return validate(x) + 1\n\n\n"
                "def validate(y):\n    return y * 2\n\n\n"
                "def close_account(z):\n    return z - 1\n"
            )
        cls.db_a = os.path.join(cls.tmpdir, "a.db")
        subprocess.run(
            [BINARY, "index", src_a, "--db", cls.db_a],
            capture_output=True, text=True, check=True,
        )
        # Repo B: same open_account/validate pair, plus a B-only unrelated_widget.
        src_b = os.path.join(cls.tmpdir, "b")
        os.makedirs(src_b, exist_ok=True)
        with open(os.path.join(src_b, "acct.py"), "w") as f:
            f.write(
                "def open_account(x):\n    return validate(x) + 1\n\n\n"
                "def validate(y):\n    return y * 2\n\n\n"
                "def unrelated_widget(q):\n    return q\n"
            )
        cls.db_b = os.path.join(cls.tmpdir, "b.db")
        subprocess.run(
            [BINARY, "index", src_b, "--db", cls.db_b],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_shared_functions_correspond_extras_do_not(self):
        """The two shared functions pair; the A-only / B-only functions do not."""
        res = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=BINARY)
        names = _names(res["pairs"])
        self.assertIn(("open_account", "open_account"), names)
        self.assertIn(("validate", "validate"), names)
        self.assertEqual(res["count"], 2, f"only the two shared functions should pair: {res!r}")
        a_names = {p["a_name"] for p in res["pairs"]}
        b_names = {p["b_name"] for p in res["pairs"]}
        self.assertNotIn("close_account", a_names)
        self.assertNotIn("unrelated_widget", b_names)
        # Real interned ids round-trip onto the pairs and are non-empty.
        for p in res["pairs"]:
            self.assertTrue(p["a_symbol_id"])
            self.assertTrue(p["b_symbol_id"])
            self.assertNotEqual(p["a_symbol_id"], p["a_name"])
            self.assertEqual(p["kind"], "function")

    def test_real_min_score_floor_drops_all_when_unreachable(self):
        """A min_score floor above 1.0 drops every pair (no exact match can clear it)."""
        res = we.correspond(
            self.db_a, self.db_b, kinds=["function"], min_score=1.0001, binary=BINARY
        )
        self.assertEqual(res["count"], 0, f"nothing should clear an unreachable floor: {res!r}")

    def test_real_default_kinds_includes_file_node(self):
        """Without a kinds filter the shared file node (acct.py) also corresponds."""
        res = we.correspond(self.db_a, self.db_b, binary=BINARY)
        names = _names(res["pairs"])
        self.assertIn(("acct.py", "acct.py"), names)
        self.assertIn(("open_account", "open_account"), names)

    def test_native_correspond_is_used_and_finds_pairs(self):
        """v0.1.5 ships NATIVE `correspond` (probe -> True); correspond() is native-first
        when a kinds filter is given. Self-correspondence and cross-fixture both find the
        expected code pairs, with the full public return shape preserved."""
        self.assertTrue(we._probe_native_subcommand("correspond", binary=BINARY),
                        "v0.1.5 ships native `correspond`")
        # native ingredients still present
        self.assertTrue(we._probe_native_subcommand("cross-graph", binary=BINARY))
        self.assertTrue(we._probe_native_subcommand("semantic", binary=BINARY))
        # self-correspondence (db_a vs db_a) finds the shared code pairs via native.
        self_res = we.correspond(self.db_a, self.db_a, kinds=["function"], binary=BINARY)
        self_names = _names(self_res["pairs"])
        self.assertIn(("open_account", "open_account"), self_names)
        self.assertIn(("validate", "validate"), self_names)
        # cross-fixture: full public shape preserved.
        res = we.correspond(self.db_a, self.db_b, kinds=["function"], binary=BINARY)
        for key in ("db_a", "db_b", "pairs", "count"):
            self.assertIn(key, res)
        self.assertIn(("open_account", "open_account"), _names(res["pairs"]))
        for p in res["pairs"]:
            self.assertIn("a_symbol_id", p); self.assertIn("b_symbol_id", p)
            self.assertIn("kind", p); self.assertIn("score", p); self.assertIn("signals", p)


if __name__ == "__main__":
    unittest.main()
