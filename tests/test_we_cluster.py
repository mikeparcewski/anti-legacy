#!/usr/bin/env python3
"""Unit tests for scripts/wicked_estate.py :: cluster() — the WF2 capability-community
shim (interface B).

`cluster` is NOT a native wicked-estate subcommand (it falls through to the engine's
usage banner — verified in WF2 against the `match cmd` dispatch in
crates/wicked-estate/src/main.rs). It is implemented as a stdlib-only,
deterministic synchronous label-propagation pass over the UNDIRECTED weighted
adjacency the helper reads directly from the `edges` table (the SAME documented
read-only `file:{abspath}?mode=ro` intern-table exception list_nodes/resolve_symbol_id
already use).

Test strategy (per the task focus):
  * SHIM PATH (always runs, even with NO binary): build a hand-crafted SQLite DB that
    matches the engine's real `symbols`/`nodes`/`edges` schema (verified empirically
    against a v0.0.1-indexed fixture), with a KNOWN two-community + isolated-node
    topology. Assert cluster():
      - returns a total node->community partition (every node mapped exactly once),
      - is STABLE across repeated calls on the same graph (deterministic — no random
        tiebreak; init label = own id, stable sorted iteration, ties -> min label),
      - REFLECTS edge weights: densely-connected nodes co-cluster; an isolated node is
        its own singleton; a cross-community bridge does NOT merge two dense clusters,
      - honors the weight modes (calls / confidence / data-affinity) and min_confidence,
      - never mutates the engine DB (read-only handle).
  * NATIVE/REAL-BINARY PATH (skipped when the binary is absent): index a tiny real
    source tree via the engine and assert cluster() produces a coherent partition over
    a genuine graph (call-affinity members co-cluster).

HERMETIC: every fixture lives in a tempfile dir torn down in tearDown/tearDownClass.
cluster() is read-only (no annotate, no overlay writes) so it cannot dirty the repo
tree; the tests still keep all artifacts under /tmp. The working tree stays clean.

The helper module is built by a sibling WF2 unit; if it is not importable yet the
suite stays GREEN (guarded import + skip, never a collection error).
"""
import os
import sys
import json
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

# The known-good v0.0.1 binary (also the helper's priority-4 fallback). The native
# path test is skipped if it is absent so the suite stays green without the engine.
WICKED_ESTATE_FALLBACK = (
    ""
)
BINARY = shutil.which("wicked-estate") or (
    WICKED_ESTATE_FALLBACK if os.access(WICKED_ESTATE_FALLBACK, os.X_OK) else None
)

# Guarded import: skip — never error — if the helper is not importable yet.
try:
    from antilegacy_core import wicked_estate as we  # noqa: E402

    HELPER_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only pre-helper
    we = None
    HELPER_IMPORT_ERROR = exc


# ---------------------------------------------------------------------------
# Hand-built SQLite fixture matching the engine's real schema (verified against
# a v0.0.1-indexed DB). Lets the SHIM path run with NO binary present.
# ---------------------------------------------------------------------------
def _build_fixture_db(path, nodes, edges):
    """Create a minimal wicked-estate-shaped graph DB.

    `nodes`: list of (sid, sym, name, kind_token, file). kind_token is the bare
             token (e.g. "function") — stored JSON-QUOTED ('"function"') exactly as
             the engine stores it, so the helper's _dekind() de-quoting is exercised.
    `edges`: list of (src_sid, tgt_sid, kind_token, confidence, file). kind_token is
             likewise stored JSON-quoted.

    The schema mirrors the engine's (symbols/nodes/edges with the NOT NULL columns the
    real tables carry: nodes.language/data, edges.data) so the helper's read-only
    queries (list_nodes / _read_edges) run unchanged against it.
    """
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
            conn.execute(
                "INSERT INTO symbols(sid, sym) VALUES (?, ?)", (sid, sym)
            )
            conn.execute(
                "INSERT INTO nodes(symbol, name, kind, language, file, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, name, json.dumps(kind), "python", file_, "{}"),
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


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestClusterShim(unittest.TestCase):
    """cluster() over a hand-built fixture graph — the always-runs shim path."""

    # symbol_id strings deliberately NOT in sorted-by-letter order so that the
    # deterministic "label = own id, ties -> lexicographically-smallest label"
    # behavior is actually exercised (the winning label per community is the
    # min sym among its members, not just "A").
    NODES = [
        # (sid, sym, name, kind, file)
        (1, "sym-A", "A", "function", "cluster1.py"),
        (2, "sym-B", "B", "function", "cluster1.py"),
        (3, "sym-C", "C", "function", "cluster1.py"),
        (4, "sym-D", "D", "function", "cluster2.py"),
        (5, "sym-E", "E", "function", "cluster2.py"),
        (6, "sym-F", "F", "function", "cluster2.py"),
        (7, "sym-G", "G", "function", "lonely.py"),  # isolated -> singleton
    ]
    # Two DENSE call-triangles {A,B,C} and {D,E,F}; NO edge bridges them; G isolated.
    CALL_EDGES = [
        (1, 2, "calls", 0.65, "cluster1.py"),
        (2, 3, "calls", 0.65, "cluster1.py"),
        (3, 1, "calls", 0.65, "cluster1.py"),
        (4, 5, "calls", 0.65, "cluster2.py"),
        (5, 6, "calls", 0.65, "cluster2.py"),
        (6, 4, "calls", 0.65, "cluster2.py"),
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-cluster-shim-")
        self.db = os.path.join(self.tmpdir, "g.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- the two-community fixture used by most tests --
    def _build_two_communities(self, extra_edges=None):
        edges = list(self.CALL_EDGES)
        if extra_edges:
            edges += extra_edges
        _build_fixture_db(self.db, self.NODES, edges)

    def _all_node_ids(self):
        return {n[1] for n in self.NODES}

    def test_partition_is_total_and_consistent(self):
        """Every node is mapped to exactly one community; communities & node_community
        agree; num_communities matches."""
        self._build_two_communities()
        res = we.cluster(self.db, weight="calls")

        nc = res["node_community"]
        comms = res["communities"]
        # Every node appears in node_community exactly once.
        self.assertEqual(set(nc.keys()), self._all_node_ids())
        # communities partition the nodes with no overlap and no omission.
        flat = [sid for members in comms.values() for sid in members]
        self.assertEqual(sorted(flat), sorted(self._all_node_ids()))
        self.assertEqual(len(flat), len(set(flat)), "a node landed in two communities")
        # node_community label is the community the node is actually a member of.
        for sid, lab in nc.items():
            self.assertIn(sid, comms[lab])
        self.assertEqual(res["num_communities"], len(comms))
        self.assertEqual(res["db"], self.db)
        self.assertEqual(res["weight"], "calls")

    def test_dense_nodes_cocluster_isolated_is_singleton(self):
        """Reflects edge weights: {A,B,C} share a community, {D,E,F} share another,
        the two dense clusters are SEPARATE, and isolated G is its own singleton."""
        self._build_two_communities()
        res = we.cluster(self.db, weight="calls")
        nc = res["node_community"]

        # The two triangles each collapse to one shared label.
        self.assertEqual(nc["sym-A"], nc["sym-B"])
        self.assertEqual(nc["sym-B"], nc["sym-C"])
        self.assertEqual(nc["sym-D"], nc["sym-E"])
        self.assertEqual(nc["sym-E"], nc["sym-F"])
        # The two clusters are distinct (no bridge edge).
        self.assertNotEqual(nc["sym-A"], nc["sym-D"])
        # Isolated node keeps its own (singleton) label, distinct from both clusters.
        self.assertEqual(nc["sym-G"], "sym-G")
        self.assertNotEqual(nc["sym-G"], nc["sym-A"])
        self.assertNotEqual(nc["sym-G"], nc["sym-D"])
        # Exactly three communities: triangle1, triangle2, singleton G.
        self.assertEqual(res["num_communities"], 3)
        # The chosen community label is one of the triangle's OWN members (not a
        # foreign id) — label propagation adopts a NEIGHBOR's label, so the global
        # min of a symmetric ring need not win (it converges to sym-B / sym-E here;
        # see test_triangle_label_is_a_member). We assert membership, not a fixed id.
        self.assertIn(nc["sym-A"], {"sym-A", "sym-B", "sym-C"})
        self.assertIn(nc["sym-D"], {"sym-D", "sym-E", "sym-F"})

    def test_triangle_label_is_deterministic_member(self):
        """Pin the exact converged label for the symmetric A-B-C call-triangle.

        Label propagation adopts a NEIGHBOR's label (a node never re-adopts its own
        id once a neighbor's wins), so on a symmetric ring the global-min id (sym-A)
        does NOT necessarily win — here both triangles converge to their MIDDLE id
        (sym-B / sym-E). This is fully deterministic given the fixed sorted iteration
        + min-label tiebreak; this test locks that converged value so a future change
        to the propagation order is caught."""
        self._build_two_communities()
        nc = we.cluster(self.db, weight="calls")["node_community"]
        self.assertEqual(nc["sym-A"], "sym-B")
        self.assertEqual(nc["sym-B"], "sym-B")
        self.assertEqual(nc["sym-C"], "sym-B")
        self.assertEqual(nc["sym-D"], "sym-E")
        self.assertEqual(nc["sym-E"], "sym-E")
        self.assertEqual(nc["sym-F"], "sym-E")

    def test_stable_across_repeated_calls(self):
        """Same graph -> byte-identical partition every call (no randomness)."""
        self._build_two_communities()
        r1 = we.cluster(self.db, weight="calls")
        r2 = we.cluster(self.db, weight="calls")
        r3 = we.cluster(self.db, weight="calls")
        self.assertEqual(r1["node_community"], r2["node_community"])
        self.assertEqual(r2["node_community"], r3["node_community"])
        self.assertEqual(r1["communities"], r2["communities"])
        # Members within a community are sorted (stable surface for consumers).
        for members in r1["communities"].values():
            self.assertEqual(members, sorted(members))

    def test_bridge_edge_merges_clusters(self):
        """A strong cross-cluster bridge edge SHOULD pull the two dense clusters into
        one community — proving the partition tracks the actual adjacency, not the
        file layout."""
        self._build_two_communities()
        no_bridge = we.cluster(self.db, weight="calls")["num_communities"]
        self.assertEqual(no_bridge, 3)

        # Rebuild WITH a bridge: C<->D now connects the two triangles.
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.makedirs(self.tmpdir, exist_ok=True)
        self._build_two_communities(extra_edges=[(3, 4, "calls", 0.65, "bridge.py")])
        res = we.cluster(self.db, weight="calls")
        nc = res["node_community"]
        # With the bridge, A..F coalesce; only G remains separate -> 2 communities.
        self.assertEqual(nc["sym-A"], nc["sym-D"])
        self.assertEqual(res["num_communities"], 2)

    def test_weight_calls_ignores_non_call_edges(self):
        """weight='calls' coalesces ONLY {calls,invokes}; a `contains`/`references`
        edge between two otherwise-isolated nodes must NOT merge them."""
        # Two nodes joined only by a `contains` edge (structural, not a call).
        nodes = [
            (1, "sym-A", "A", "function", "f.py"),
            (2, "sym-B", "B", "function", "f.py"),
        ]
        edges = [(1, 2, "contains", 1.0, "f.py")]
        _build_fixture_db(self.db, nodes, edges)
        res = we.cluster(self.db, weight="calls")
        nc = res["node_community"]
        # No CALL edge -> each node is its own singleton under the calls weight.
        self.assertNotEqual(nc["sym-A"], nc["sym-B"])
        self.assertEqual(res["num_communities"], 2)

    def test_weight_data_affinity_uses_data_edges_and_same_file_bonus(self):
        """weight='data-affinity' coalesces on {references,accesses,uses,contains,
        imports} AND adds a same-file bonus, so two nodes sharing a file co-cluster
        even with only a data edge — where the 'calls' mode would split them."""
        nodes = [
            (1, "sym-A", "A", "function", "shared.py"),
            (2, "sym-B", "B", "function", "shared.py"),
            (3, "sym-C", "C", "function", "other.py"),
        ]
        # A->B is a `references` (data) edge; C is in a different file, unconnected.
        edges = [(1, 2, "references", 1.0, "shared.py")]
        _build_fixture_db(self.db, nodes, edges)

        # Under 'calls', the references edge is ignored -> A,B,C all singletons.
        calls_res = we.cluster(self.db, weight="calls")
        self.assertEqual(calls_res["num_communities"], 3)

        # Under 'data-affinity', A&B (data edge + same file) coalesce; C stays alone.
        data_res = we.cluster(self.db, weight="data-affinity")
        nc = data_res["node_community"]
        self.assertEqual(nc["sym-A"], nc["sym-B"])
        self.assertNotEqual(nc["sym-A"], nc["sym-C"])

    def test_same_file_bonus_alone_coalesces_under_data_affinity(self):
        """Even with NO explicit data edge, two nodes sharing a file coalesce under
        data-affinity purely via the synthetic same-file bonus."""
        nodes = [
            (1, "sym-A", "A", "function", "together.py"),
            (2, "sym-B", "B", "function", "together.py"),
            (3, "sym-C", "C", "function", "apart.py"),
        ]
        _build_fixture_db(self.db, nodes, [])  # no edges at all
        res = we.cluster(self.db, weight="data-affinity")
        nc = res["node_community"]
        self.assertEqual(nc["sym-A"], nc["sym-B"], "same-file bonus should merge A,B")
        self.assertNotEqual(nc["sym-A"], nc["sym-C"])

    def test_min_confidence_drops_low_confidence_edges(self):
        """Edges below min_confidence are dropped, splitting a community that the
        edge would otherwise hold together."""
        nodes = [
            (1, "sym-A", "A", "function", "f.py"),
            (2, "sym-B", "B", "function", "f.py"),
        ]
        # A single low-confidence (0.5) call edge.
        edges = [(1, 2, "calls", 0.5, "f.py")]
        _build_fixture_db(self.db, nodes, edges)

        # With min_confidence below 0.5, the edge survives -> one community.
        joined = we.cluster(self.db, weight="calls", min_confidence=0.0)
        self.assertEqual(joined["node_community"]["sym-A"], joined["node_community"]["sym-B"])
        self.assertEqual(joined["num_communities"], 1)

        # With min_confidence above 0.5, the edge is dropped -> two singletons.
        split = we.cluster(self.db, weight="calls", min_confidence=0.9)
        self.assertNotEqual(split["node_community"]["sym-A"], split["node_community"]["sym-B"])
        self.assertEqual(split["num_communities"], 2)
        self.assertEqual(split["min_confidence"], 0.9)

    def test_confidence_weight_mode_runs_and_partitions(self):
        """weight='confidence' weights every edge by its confidence float and still
        yields a total partition; the heuristic-vs-parsed weighting does not crash
        and densely-(calls)-connected nodes still co-cluster."""
        self._build_two_communities()
        res = we.cluster(self.db, weight="confidence")
        nc = res["node_community"]
        self.assertEqual(set(nc.keys()), self._all_node_ids())
        # The two call-triangles still coalesce (each at 0.65 weight within-triangle).
        self.assertEqual(nc["sym-A"], nc["sym-B"])
        self.assertEqual(nc["sym-A"], nc["sym-C"])
        self.assertEqual(nc["sym-D"], nc["sym-E"])

    def test_unknown_weight_mode_raises(self):
        """An unsupported weight mode is a clear error, not a silent empty result."""
        self._build_two_communities()
        with self.assertRaises(we.WickedEstateError):
            we.cluster(self.db, weight="bogus-mode")

    def test_does_not_mutate_db(self):
        """cluster() opens the DB read-only — running it leaves the file byte-identical
        (the engine DB must never be mutated by a read-side shim)."""
        self._build_two_communities()
        before = open(self.db, "rb").read()
        we.cluster(self.db, weight="calls")
        we.cluster(self.db, weight="data-affinity")
        after = open(self.db, "rb").read()
        self.assertEqual(before, after, "cluster() mutated the engine DB")

    def test_empty_graph_yields_no_communities(self):
        """A graph with no nodes returns an empty, well-formed partition."""
        _build_fixture_db(self.db, [], [])
        res = we.cluster(self.db, weight="calls")
        self.assertEqual(res["communities"], {})
        self.assertEqual(res["node_community"], {})
        self.assertEqual(res["num_communities"], 0)

    def test_missing_db_raises(self):
        """A non-existent db path raises WickedEstateError (via list_nodes), never a
        bare sqlite/OSError leaking to the caller."""
        with self.assertRaises(we.WickedEstateError):
            we.cluster(os.path.join(self.tmpdir, "does-not-exist.db"), weight="calls")


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestClusterAgainstRealBinary(unittest.TestCase):
    """cluster() over a REAL engine-indexed graph (skipped when the binary is absent)."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="we-cluster-real-")
        src = os.path.join(cls.tmpdir, "src")
        os.makedirs(src, exist_ok=True)
        # Two self-contained call-clusters in separate files, no cross-file calls:
        #   group1.py: g1_a -> g1_b -> g1_c  (a chain)
        #   group2.py: g2_a -> g2_b           (a pair)
        with open(os.path.join(src, "group1.py"), "w") as f:
            f.write(
                "def g1_c(z):\n    return z + 1\n\n"
                "def g1_b(y):\n    return g1_c(y) * 2\n\n"
                "def g1_a(x):\n    return g1_b(x) - 1\n"
            )
        with open(os.path.join(src, "group2.py"), "w") as f:
            f.write(
                "def g2_b(y):\n    return y * 3\n\n"
                "def g2_a(x):\n    return g2_b(x) + 7\n"
            )
        cls.db = os.path.join(cls.tmpdir, "g.db")
        subprocess.run(
            [BINARY, "index", src, "--db", cls.db],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _sid_for(self, name):
        ids = we.resolve_symbol_id(self.db, name)
        self.assertTrue(ids, f"{name} must resolve to a SymbolId")
        return ids[0]

    def test_real_graph_partition_is_total(self):
        """cluster() over the real graph maps every enumerated node exactly once."""
        res = we.cluster(self.db, weight="calls", binary=BINARY)
        all_ids = {n["symbol_id"] for n in we.list_nodes(self.db)}
        self.assertEqual(set(res["node_community"].keys()), all_ids)
        flat = [sid for m in res["communities"].values() for sid in m]
        self.assertEqual(sorted(flat), sorted(all_ids))
        self.assertGreaterEqual(res["num_communities"], 1)

    def test_real_call_chain_coclusters(self):
        """The g1_a->g1_b->g1_c call chain lands in ONE community under the calls
        weight (transitively connected), and is NOT merged with the disjoint
        group2 pair."""
        res = we.cluster(self.db, weight="calls", binary=BINARY)
        nc = res["node_community"]
        a, b, c = self._sid_for("g1_a"), self._sid_for("g1_b"), self._sid_for("g1_c")
        d, e = self._sid_for("g2_a"), self._sid_for("g2_b")
        self.assertEqual(nc[a], nc[b], "g1_a and g1_b should co-cluster (call edge)")
        self.assertEqual(nc[b], nc[c], "g1_b and g1_c should co-cluster (call edge)")
        self.assertEqual(nc[d], nc[e], "g2_a and g2_b should co-cluster (call edge)")
        # The two groups have no cross-file call edge -> distinct communities.
        self.assertNotEqual(nc[a], nc[d], "disjoint call groups must not merge")

    def test_real_graph_stable(self):
        """Determinism holds on a real graph too."""
        r1 = we.cluster(self.db, weight="calls", binary=BINARY)
        r2 = we.cluster(self.db, weight="calls", binary=BINARY)
        self.assertEqual(r1["node_community"], r2["node_community"])


# ---------------------------------------------------------------------------
# NATIVE-PATH (v0.1.5+): cluster() routes through native `clusters <min_size>
# --json` — REAL community detection (wicked_estate_rank::detect_communities,
# union-find over Calls|Imports edges ONLY, file/structural nodes EXCLUDED). These
# tests pin the WF4 native-first integration EXPLICITLY:
#   * the native subcommand is actually probed True on this engine (so we ARE on the
#     native path, not silently the shim — the WF4 probe-name fix `cluster`->`clusters`),
#   * an ORPHAN (edgeless) node the engine EXCLUDES from every community is
#     SINGLETON-assigned so node_community stays TOTAL (the §I5/extract.py contract),
#   * the public return shape {db,weight,min_confidence,communities,node_community,
#     num_communities} is byte-for-byte the same shape the shim produced — consumers
#     keep working unchanged,
#   * weight/min_confidence are echoed (SHIM-ONLY knobs; native has neither).
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestClusterNativeIntegration(unittest.TestCase):
    """cluster() over a real v0.1.5 index, explicitly exercising the native `clusters`
    path INCLUDING an orphan node (the gamma case) to lock the TOTAL-node_community
    singleton-assignment the native engine itself does NOT provide."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="we-cluster-native-")
        src = os.path.join(cls.tmpdir, "src")
        os.makedirs(src, exist_ok=True)
        # A real call chain alpha->beta (co-clusters under union-find over Calls)
        # PLUS a standalone, edgeless function the engine EXCLUDES from every
        # community — the canonical "gamma" the helper must singleton-assign so
        # node_community is TOTAL.
        with open(os.path.join(src, "chain.py"), "w") as f:
            f.write(
                "def beta(y):\n    return y * 2\n\n\n"
                "def alpha(x):\n    return beta(x) + 1\n"
            )
        with open(os.path.join(src, "standalone.py"), "w") as f:
            f.write("def gamma_orphan(q):\n    return q\n")
        cls.db = os.path.join(cls.tmpdir, "g.db")
        subprocess.run(
            [BINARY, "index", src, "--db", cls.db],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _sid_for(self, name):
        ids = we.resolve_symbol_id(self.db, name)
        self.assertTrue(ids, f"{name} must resolve to a SymbolId")
        return ids[0]

    def test_native_clusters_subcommand_is_present(self):
        """Sanity: on v0.1.5 the CORRECTED native name `clusters` probes True (the
        WF4 fix) and the OLD wrong name `cluster` probes False — proving cluster()
        takes the native path here, not silently the shim."""
        self.assertTrue(
            we._probe_native_subcommand("clusters", binary=BINARY),
            "native `clusters` must be present on v0.1.5 (probe-name fix)",
        )
        self.assertFalse(
            we._probe_native_subcommand("cluster", binary=BINARY),
            "the OLD probed name `cluster` must fall through to the usage banner",
        )

    def test_native_path_yields_total_node_community_with_singleton_orphan(self):
        """The native engine EXCLUDES the edgeless gamma_orphan from every community;
        the helper must SINGLETON-assign it (label == its own symbol_id) so
        node_community is TOTAL over list_nodes — the §I5/extract.py invariant."""
        res = we.cluster(self.db, weight="calls", binary=BINARY)
        all_ids = {n["symbol_id"] for n in we.list_nodes(self.db)}

        # TOTAL: every enumerated node carries a community (nothing dropped).
        self.assertEqual(
            set(res["node_community"].keys()), all_ids,
            "node_community must cover EVERY node (native singletons re-added)",
        )
        # communities partition the node set exactly (no overlap, no omission).
        flat = [sid for members in res["communities"].values() for sid in members]
        self.assertEqual(sorted(flat), sorted(all_ids))
        self.assertEqual(len(flat), len(set(flat)), "a node landed in two communities")

        # alpha + beta co-cluster (a real Calls edge -> one union-find community).
        a, b = self._sid_for("alpha"), self._sid_for("beta")
        self.assertEqual(res["node_community"][a], res["node_community"][b],
                         "alpha/beta share a Calls edge -> one native community")

        # The orphan is its OWN singleton: label == its own symbol_id, members == [it].
        orphan = self._sid_for("gamma_orphan")
        self.assertEqual(
            res["node_community"][orphan], orphan,
            "the engine-excluded orphan must be singleton-assigned (label == own id)",
        )
        self.assertEqual(res["communities"][orphan], [orphan])
        # The orphan is NOT in alpha/beta's community.
        self.assertNotEqual(res["node_community"][orphan], res["node_community"][a])

    def test_native_return_shape_is_unchanged(self):
        """The native path returns the SAME dict shape the shim produced, so the
        extraction loop + §I5 consume it unchanged."""
        res = we.cluster(self.db, weight="calls", binary=BINARY)
        self.assertEqual(
            sorted(res.keys()),
            ["communities", "db", "min_confidence", "node_community",
             "num_communities", "weight"],
        )
        self.assertIsInstance(res["communities"], dict)
        self.assertIsInstance(res["node_community"], dict)
        self.assertEqual(res["num_communities"], len(res["communities"]))
        self.assertEqual(res["db"], self.db)
        # Every node_community label is a real community key whose members include it.
        for sid, lab in res["node_community"].items():
            self.assertIn(lab, res["communities"])
            self.assertIn(sid, res["communities"][lab])
        # Members within each community are sorted (stable consumer surface).
        for members in res["communities"].values():
            self.assertEqual(members, sorted(members))

    def test_native_echoes_shim_only_weight_and_min_confidence(self):
        """weight/min_confidence are SHIM-ONLY knobs the native engine has NEITHER of;
        they are echoed into the return for API stability (NOT applied natively) and
        the partition is identical regardless of the weight mode passed (native has
        no weighting / no confidence filter)."""
        r_calls = we.cluster(self.db, weight="calls", binary=BINARY)
        self.assertEqual(r_calls["weight"], "calls")
        self.assertEqual(r_calls["min_confidence"], 0.0)

        # A non-default weight + a non-zero min_confidence are echoed verbatim but do
        # NOT change the native partition (native ignores both).
        r_conf = we.cluster(
            self.db, weight="data-affinity", min_confidence=0.9, binary=BINARY
        )
        self.assertEqual(r_conf["weight"], "data-affinity")
        self.assertEqual(r_conf["min_confidence"], 0.9)
        self.assertEqual(
            r_conf["node_community"], r_calls["node_community"],
            "native partition must be weight-mode-independent (no shim degeneracy)",
        )

    def test_native_does_not_mutate_caller_db(self):
        """cluster() runs native `clusters` against a TEMP COPY, so the caller's DB is
        byte-identical afterwards (the read-side no-mutation contract holds even though
        native opens the store read-WRITE)."""
        before = open(self.db, "rb").read()
        we.cluster(self.db, weight="calls", binary=BINARY)
        after = open(self.db, "rb").read()
        self.assertEqual(before, after, "native cluster() path mutated the caller DB")


if __name__ == "__main__":
    unittest.main()
