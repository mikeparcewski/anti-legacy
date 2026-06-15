#!/usr/bin/env python3
"""Unit tests for scripts/wicked_estate.py::context() — interface C (WF2 shim).

context() is a SHIM (no native `context` subcommand — the engine's ContextPack
RetrievalTool is MCP-only). It builds the per-node fan-out the extraction loop
crawls: a bounded ring crawl seeded at the start node, expanded ring-by-ring via
blast_radius() (the transitive-dependents fan-out), ranked by rank() PageRank,
then walked pulling bounded source() slices under a HARD char cap.

The contract these tests pin:
  * The seed node is ALWAYS ring 0 and ALWAYS present in ring_of / ranked_nodes.
  * The char budget is a HARD cap — chars_used is NEVER > budget, even with a
    tiny budget that forces a mid-slice truncation (truncated=True then).
  * The neighborhood expands ring-by-ring (a dependent of the seed lands at
    ring >= 1); honoring max_hops as the hop ceiling.
  * ranked_nodes is ordered by descending PageRank score; the returned shape
    carries node/budget/max_hops/ring_of/ranked_nodes/slices/chars_used/truncated.

Two tracks, both hermetic:
  * SHIM-DIRECT (no binary): monkeypatch the helper's CLI-wrapping primitives
    (query/blast_radius/rank/source) with hand-built returns and drive context()
    on that synthetic graph. Runs even when the engine is absent.
  * REAL-BINARY: skipped unless the engine is present; indexes a tiny temp
    fixture (a 3-function call chain) with the real engine and asserts the same
    invariants against live engine output.

All fixtures live in tempfile dirs and are cleaned up; the working tree stays
clean (context() is read-only — no annotate(), no overlay writes — so there is
nothing to redirect, but tests still build everything under /tmp).
"""
import os
import sys
import shutil
import unittest
import tempfile
import subprocess

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

WICKED_ESTATE_FALLBACK = (
    ""
)
BINARY = shutil.which("wicked-estate") or (
    WICKED_ESTATE_FALLBACK if os.access(WICKED_ESTATE_FALLBACK, os.X_OK) else None
)

# Guarded import so collection stays green if the helper is mid-build.
try:
    import wicked_estate as we  # noqa: E402

    HELPER_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only pre-helper
    we = None
    HELPER_IMPORT_ERROR = exc


# ---------------------------------------------------------------------------
# SHIM-DIRECT track: drive context() against a synthetic in-memory graph by
# monkeypatching the four CLI-wrapping primitives it fans out through. No binary,
# no SQLite — exercises the pure shim logic (ring crawl, ranking, budget cap).
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestContextShimDirect(unittest.TestCase):
    """context() shim logic on a hand-built graph (binary-independent)."""

    # A synthetic call chain: alpha -> beta -> gamma (source=dependent,
    # target=dependency). blast_radius(name) returns the DEPENDENTS of name.
    #   gamma's dependents: beta, alpha
    #   beta's  dependents: alpha
    #   alpha's dependents: (none)
    # Seeded at gamma, the crawl expands gamma(ring0) -> {beta,alpha}(ring1).
    NODES = {
        "alpha": {"kind": "Function", "name": "alpha", "file": "main.py", "line": 1},
        "beta": {"kind": "Function", "name": "beta", "file": "main.py", "line": 5},
        "gamma": {"kind": "Function", "name": "gamma", "file": "main.py", "line": 9},
    }
    DEPENDENTS = {
        "gamma": ["beta", "alpha"],
        "beta": ["alpha"],
        "alpha": [],
    }
    # Distinct, easily-measured source bodies (length matters for budget tests).
    BODIES = {
        "gamma": "def gamma(z):\n    return z - 3\n",          # 30 chars
        "beta": "def beta(y):\n    return gamma(y) * 2\n",      # 38 chars
        "alpha": "def alpha(x):\n    return beta(x) + 1\n",      # 38 chars
    }
    # rank() PageRank order (highest first): gamma > beta > alpha.
    RANK = [
        {"kind": "Function", "name": "gamma", "file": "main.py", "line": 9, "score": 0.40},
        {"kind": "Function", "name": "beta", "file": "main.py", "line": 5, "score": 0.29},
        {"kind": "Function", "name": "alpha", "file": "main.py", "line": 1, "score": 0.15},
    ]

    def setUp(self):
        # Stub the four primitives context() calls. We pass binary="X" so the
        # native probe is also stubbed to "shim" (False) without a real binary.
        self._saved = {}
        for name in ("query", "blast_radius", "rank", "source",
                     "_probe_native_subcommand", "resolve_symbol_id"):
            self._saved[name] = getattr(we, name)

        def fake_query(db, name, binary=None):
            n = self.NODES.get(name)
            return {"name": name, "matches": [dict(n)] if n else []}

        def fake_blast_radius(db, name, binary=None):
            deps = [dict(self.NODES[d]) for d in self.DEPENDENTS.get(name, [])]
            return {"name": name, "dependents": deps}

        def fake_rank(db, binary=None):
            return [dict(r) for r in self.RANK]

        def fake_source(db, name, binary=None):
            body = self.BODIES.get(name, "")
            n = self.NODES.get(name)
            return {"name": name, "matches": [dict(n)] if n else [], "body": body}

        def fake_probe(sub, binary=None):
            return False  # always shim

        def fake_resolve(db, name, file=None, kind=None):
            return ["ts-py-" + name] if name in self.NODES else []

        we.query = fake_query
        we.blast_radius = fake_blast_radius
        we.rank = fake_rank
        we.source = fake_source
        we._probe_native_subcommand = fake_probe
        we.resolve_symbol_id = fake_resolve

    def tearDown(self):
        for name, fn in self._saved.items():
            setattr(we, name, fn)

    def test_seed_always_included_and_ring_zero(self):
        """The seed node is always present at ring 0 (the crawl never drops it)."""
        out = we.context("g.db", "gamma", budget=100000, max_hops=3)
        self.assertEqual(out["node"], "gamma")
        # ring_of keys are "name|file"; the seed must be present at ring 0.
        seed_keys = [k for k in out["ring_of"] if k.startswith("gamma|")]
        self.assertTrue(seed_keys, f"seed missing from ring_of: {out['ring_of']!r}")
        self.assertEqual(out["ring_of"][seed_keys[0]], 0)
        # The seed must also be in the ranked neighborhood at ring 0.
        seed_nodes = [n for n in out["ranked_nodes"] if n["name"] == "gamma"]
        self.assertEqual(len(seed_nodes), 1)
        self.assertEqual(seed_nodes[0]["ring"], 0)

    def test_seed_with_no_neighbors_is_singleton(self):
        """A seed whose blast_radius is empty yields a 1-node neighborhood (itself)."""
        out = we.context("g.db", "alpha", budget=100000, max_hops=3)
        self.assertEqual(len(out["ranked_nodes"]), 1)
        self.assertEqual(out["ranked_nodes"][0]["name"], "alpha")
        self.assertEqual(out["ranked_nodes"][0]["ring"], 0)
        # Its source slice is included; chars_used > 0; nothing truncated.
        self.assertEqual([s["name"] for s in out["slices"]], ["alpha"])
        self.assertFalse(out["truncated"])
        self.assertEqual(out["chars_used"], len(self.BODIES["alpha"]))

    def test_ring_by_ring_expansion(self):
        """blast_radius dependents land at ring >= 1; expansion is ring-by-ring."""
        out = we.context("g.db", "gamma", budget=100000, max_hops=3)
        ring_by_name = {k.split("|", 1)[0]: v for k, v in out["ring_of"].items()}
        self.assertEqual(ring_by_name["gamma"], 0)
        # beta and alpha are dependents of gamma -> first reached at ring 1.
        self.assertEqual(ring_by_name["beta"], 1)
        self.assertEqual(ring_by_name["alpha"], 1)
        # All three nodes were crawled.
        self.assertEqual(set(ring_by_name), {"gamma", "beta", "alpha"})

    def test_max_hops_is_a_hard_ceiling(self):
        """max_hops=1 stops the crawl after one ring even if more depth exists."""
        out0 = we.context("g.db", "gamma", budget=100000, max_hops=0)
        # max_hops=0 -> only the seed (no ring expansion at all).
        names0 = {n["name"] for n in out0["ranked_nodes"]}
        self.assertEqual(names0, {"gamma"})

        out1 = we.context("g.db", "gamma", budget=100000, max_hops=1)
        rings1 = {k.split("|", 1)[0]: v for k, v in out1["ring_of"].items()}
        # No node may be recorded at a ring greater than max_hops.
        self.assertTrue(all(r <= 1 for r in rings1.values()), rings1)

    def test_ranked_nodes_sorted_by_score_desc(self):
        """ranked_nodes is ordered by descending PageRank score."""
        out = we.context("g.db", "gamma", budget=100000, max_hops=3)
        scores = [n["score"] for n in out["ranked_nodes"]]
        self.assertEqual(scores, sorted(scores, reverse=True), scores)
        # The top-ranked node (gamma, 0.40) leads.
        self.assertEqual(out["ranked_nodes"][0]["name"], "gamma")
        # Each ranked node carries the joined score.
        for n in out["ranked_nodes"]:
            self.assertIn("score", n)

    def test_budget_respected_full(self):
        """With a generous budget all real slices fit; chars_used == their sum."""
        out = we.context("g.db", "gamma", budget=100000, max_hops=3)
        expected = sum(len(self.BODIES[n]) for n in ("gamma", "beta", "alpha"))
        self.assertEqual(out["chars_used"], expected)
        self.assertFalse(out["truncated"])
        self.assertLessEqual(out["chars_used"], 100000)
        # Every emitted slice has a body and a name.
        for s in out["slices"]:
            self.assertIn("body", s)
            self.assertIn("name", s)

    def test_budget_is_a_hard_cap_with_truncation(self):
        """A budget smaller than the first slice forces a mid-slice truncation.

        chars_used MUST equal budget exactly (never overshoot) and truncated=True.
        gamma is top-ranked (slice walked first) and is 30 chars; budget=10 cuts
        it mid-slice.
        """
        budget = 10
        out = we.context("g.db", "gamma", budget=budget, max_hops=3)
        self.assertLessEqual(out["chars_used"], budget,
                             "HARD char cap violated — chars_used overshot budget")
        self.assertEqual(out["chars_used"], budget)
        self.assertTrue(out["truncated"])
        # Exactly one (truncated) slice was emitted, and it is the budget length.
        self.assertEqual(len(out["slices"]), 1)
        self.assertEqual(len(out["slices"][0]["body"]), budget)

    def test_budget_cap_across_multiple_slices(self):
        """A budget between slice-1 and slice-1+2 truncates inside the 2nd slice.

        gamma=30, beta=38. budget=40 -> full gamma (30) + 10 of beta -> 40 total,
        truncated. chars_used is exactly budget, never more.
        """
        budget = 40
        out = we.context("g.db", "gamma", budget=budget, max_hops=3)
        self.assertEqual(out["chars_used"], budget)
        self.assertLessEqual(out["chars_used"], budget)
        self.assertTrue(out["truncated"])
        # First slice is full gamma; second is a 10-char truncation of beta.
        self.assertEqual(out["slices"][0]["name"], "gamma")
        self.assertEqual(len(out["slices"][0]["body"]), len(self.BODIES["gamma"]))
        self.assertEqual(out["slices"][1]["name"], "beta")
        self.assertEqual(len(out["slices"][1]["body"]), budget - len(self.BODIES["gamma"]))

    def test_zero_budget_emits_no_slices(self):
        """budget=0 -> the cap is already met; no slice is emitted, truncated=True."""
        out = we.context("g.db", "gamma", budget=0, max_hops=3)
        self.assertEqual(out["chars_used"], 0)
        self.assertEqual(out["slices"], [])
        self.assertTrue(out["truncated"])

    def test_empty_source_bodies_are_skipped(self):
        """Nodes with empty source() body are skipped and contribute 0 chars."""
        # Blank out beta's body ('source not stored' style) — it must be skipped.
        self.BODIES = dict(self.BODIES)
        self.BODIES["beta"] = ""
        try:
            out = we.context("g.db", "gamma", budget=100000, max_hops=3)
        finally:
            pass  # BODIES restored by next setUp
        slice_names = [s["name"] for s in out["slices"]]
        self.assertNotIn("beta", slice_names)
        self.assertIn("gamma", slice_names)
        self.assertIn("alpha", slice_names)
        self.assertEqual(out["chars_used"],
                         len(self.BODIES["gamma"]) + len(self.BODIES["alpha"]))

    def test_return_shape(self):
        """context() returns the documented keys."""
        out = we.context("g.db", "gamma", budget=500, max_hops=2)
        for key in ("node", "budget", "max_hops", "ring_of", "ranked_nodes",
                    "slices", "chars_used", "truncated"):
            self.assertIn(key, out, f"missing key {key!r} in context() return")
        self.assertEqual(out["budget"], 500)
        self.assertEqual(out["max_hops"], 2)


# ---------------------------------------------------------------------------
# REAL-BINARY track: index a tiny call-chain fixture with the engine and assert
# the same invariants against live blast_radius/rank/source output.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestContextRealBinary(unittest.TestCase):
    """context() against a real indexed fixture DB (alpha -> beta -> gamma)."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="we-context-")
        src = os.path.join(cls.tmpdir, "src")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "main.py"), "w") as f:
            f.write(
                "def alpha(x):\n    return beta(x) + 1\n\n\n"
                "def beta(y):\n    return gamma(y) * 2\n\n\n"
                "def gamma(z):\n    return z - 3\n"
            )
        cls.db = os.path.join(cls.tmpdir, "g.db")
        subprocess.run(
            [BINARY, "index", src, "--db", cls.db],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_seed_included_and_neighborhood_built(self):
        """Seed gamma is ring 0; its dependents (alpha/beta) are crawled."""
        out = we.context(self.db, "gamma", budget=100000, max_hops=3, binary=BINARY)
        self.assertEqual(out["node"], "gamma")
        ring_by_name = {k.split("|", 1)[0]: v for k, v in out["ring_of"].items()}
        self.assertIn("gamma", ring_by_name)
        self.assertEqual(ring_by_name["gamma"], 0)
        # gamma's dependents (alpha, beta) are reached at ring >= 1.
        self.assertIn("beta", ring_by_name)
        self.assertIn("alpha", ring_by_name)
        self.assertGreaterEqual(ring_by_name["beta"], 1)
        self.assertGreaterEqual(ring_by_name["alpha"], 1)
        # Seed appears once in ranked_nodes at ring 0.
        seeds = [n for n in out["ranked_nodes"] if n["name"] == "gamma"]
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["ring"], 0)

    def test_ranked_nodes_sorted_desc(self):
        """ranked_nodes ordered by descending real PageRank score."""
        out = we.context(self.db, "gamma", budget=100000, max_hops=3, binary=BINARY)
        scores = [n["score"] for n in out["ranked_nodes"]]
        self.assertEqual(scores, sorted(scores, reverse=True), scores)

    def test_budget_hard_cap_against_real_slices(self):
        """A tiny budget forces a mid-slice truncation; chars_used never overshoots."""
        budget = 12
        out = we.context(self.db, "gamma", budget=budget, max_hops=3, binary=BINARY)
        self.assertLessEqual(out["chars_used"], budget,
                             "HARD char cap violated on real DB — chars_used > budget")
        self.assertEqual(out["chars_used"], budget)
        self.assertTrue(out["truncated"])

    def test_full_budget_collects_real_slices(self):
        """With a generous budget, real source slices are collected under the cap."""
        budget = 100000
        out = we.context(self.db, "gamma", budget=budget, max_hops=3, binary=BINARY)
        self.assertLessEqual(out["chars_used"], budget)
        self.assertGreater(out["chars_used"], 0)
        # gamma's own body must appear among the collected slices.
        slice_names = {s["name"] for s in out["slices"]}
        self.assertIn("gamma", slice_names)
        # chars_used equals the sum of emitted slice body lengths (accounting).
        self.assertEqual(out["chars_used"], sum(len(s["body"]) for s in out["slices"]))

    def test_max_hops_zero_is_seed_only(self):
        """max_hops=0 -> only the seed node, no ring expansion."""
        out = we.context(self.db, "gamma", budget=100000, max_hops=0, binary=BINARY)
        names = {n["name"] for n in out["ranked_nodes"]}
        self.assertEqual(names, {"gamma"})

    def test_native_context_subcommand_is_present_but_shim_stays_primary(self):
        """On v0.1.5 native `context` probes True (so the OLD `if probe: raise` guard
        WOULD have fired and broken this consumer — the WF4 fix removes it). context()
        must STILL run the shim unconditionally: its return carries slices (real
        source bodies), ranked_nodes[].ring, and a HARD real-char budget — none of
        which native budget_context provides."""
        self.assertTrue(
            we._probe_native_subcommand("context", binary=BINARY),
            "native `context` IS present on v0.1.5 (this is why the old raise fired)",
        )
        out = we.context(self.db, "gamma", budget=100000, max_hops=3, binary=BINARY)
        # Slice-bearing, ring-keyed shim shape (the extraction-loop contract).
        self.assertIn("slices", out)
        self.assertGreater(len(out["slices"]), 0, "shim path must pull real source slices")
        for s in out["slices"]:
            self.assertIn("body", s)
            self.assertTrue(s["body"].strip(), "shim slices carry real source text")
        for n in out["ranked_nodes"]:
            self.assertIn("ring", n)  # native budget_context has NO ring depth
        # chars_used accounts for REAL slice characters (native budget is a proxy).
        self.assertEqual(out["chars_used"], sum(len(s["body"]) for s in out["slices"]))


# ---------------------------------------------------------------------------
# ADJUNCT: context_native() — the v0.1.5 native `context <name> --budget --json`
# neighbor-NAME list exposed for callers that want native's bidirectional
# (callee+caller) names to enrich the shim frontier. It is NOT routed through
# context() (which keeps its slice-bearing shim shape). These tests pin the
# adjunct surface AND that it does NOT change context()'s return.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestContextNativeAdjunct(unittest.TestCase):
    """context_native() over a real v0.1.5 index — read-only neighbor-name list."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="we-context-native-")
        src = os.path.join(cls.tmpdir, "src")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "main.py"), "w") as f:
            f.write(
                "def alpha(x):\n    return beta(x) + 1\n\n\n"
                "def beta(y):\n    return gamma(y) * 2\n\n\n"
                "def gamma(z):\n    return z - 3\n"
            )
        cls.db = os.path.join(cls.tmpdir, "g.db")
        subprocess.run(
            [BINARY, "index", src, "--db", cls.db],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_returns_metadata_only_neighbor_list(self):
        """context_native returns [{name,kind,file,line}, ...] — node METADATA only,
        NO source slices / ring / score (the documented native budget_context shape).
        A generous budget surfaces the function neighborhood (empirically the proxy
        budget must be large enough for Function nodes to enter past the File node)."""
        nbrs = we.context_native(self.db, "beta", budget=4000, binary=BINARY)
        self.assertIsInstance(nbrs, list)
        for item in nbrs:
            self.assertEqual(sorted(item.keys()), ["file", "kind", "line", "name"])
            # Crucially NO slices / ring / score keys (native has none).
            self.assertNotIn("body", item)
            self.assertNotIn("ring", item)
            self.assertNotIn("score", item)

    def test_adjunct_does_not_change_context_return_shape(self):
        """context() keeps its slice-bearing shape regardless of context_native()."""
        out = we.context(self.db, "beta", budget=100000, max_hops=3, binary=BINARY)
        for key in ("node", "budget", "max_hops", "ring_of", "ranked_nodes",
                    "slices", "chars_used", "truncated"):
            self.assertIn(key, out)

    def test_absent_native_returns_empty(self):
        """When native `context` is absent (probe False), context_native() degrades to
        []  — never raising — so callers can use it as a best-effort enrichment."""
        saved = we._probe_native_subcommand
        try:
            we._probe_native_subcommand = lambda sub, binary=None: False
            self.assertEqual(we.context_native(self.db, "beta", binary=BINARY), [])
        finally:
            we._probe_native_subcommand = saved


if __name__ == "__main__":
    unittest.main()
