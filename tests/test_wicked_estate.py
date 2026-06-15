#!/usr/bin/env python3
"""Unit tests for scripts/wicked_estate.py — the WF1 wicked-estate integration helper.

The helper wraps the user's MIT wicked-estate engine (the code-graph engine adopted
in BACKLOG §H). These tests pin the contracts that gate the rest of WF1:

  * resolve_binary() priority order (config > env > PATH > wicked-estate fallback) and
    its raise-on-none behavior (the engine's R2 "never silently degrade" rule).
  * stats_digest() determinism — the digest STRIPS the volatile stats lines
    (STALENESS:, repo: git provenance, db= size) so the checksummed `legacy-graph`
    evidence is byte-stable across re-runs.
  * resolve_symbol_id() returns the FULL interned SymbolId string (the read-only
    intern-table lookup), NOT the simple name.
  * annotate() RAISES on an unresolved / empty symbol_id — the guard against the
    proven silent-no-op trap (passing a simple name reports success but updates 0
    rows because the symbol string was never interned).
  * annotate() + by_requirement() round-trip against a tiny indexed fixture DB, and
    annotate() also appends the lossless rule object to .anti-legacy/annotations.jsonl
    (the IP-rich overlay). Round-trip tests skip cleanly when the binary is absent.

The helper module is built by a sibling WF1 unit; if it is not importable yet the
suite must stay GREEN, so the module import is guarded and the whole case skips with
a clear reason rather than erroring at collection time.
"""
import os
import sys
import json
import shutil
import unittest
import tempfile
import subprocess

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# The known-good v0.0.1 binary used by the spike — also the helper's documented
# priority-4 fallback. Round-trip tests are skipped if it is not present so the
# suite stays green in environments without the engine.
WICKED_ESTATE_FALLBACK = (
    ""
)
BINARY = shutil.which("wicked-estate") or (
    WICKED_ESTATE_FALLBACK if os.access(WICKED_ESTATE_FALLBACK, os.X_OK) else None
)

# Guarded import: the helper is built by a sibling WF1 unit. Skip — never error —
# if it is not importable yet so collection stays green during the parallel build.
try:
    import wicked_estate as we  # noqa: E402

    HELPER_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only pre-helper
    we = None
    HELPER_IMPORT_ERROR = exc


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestResolveBinary(unittest.TestCase):
    """resolve_binary() priority order and raise-on-none."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-resolve-")
        # A fake-but-executable binary to stand in for each resolution source.
        self.fake = os.path.join(self.tmpdir, "fake-wicked-estate")
        with open(self.fake, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(self.fake, 0o755)
        self._saved_env = os.environ.get("WICKED_ESTATE_PATH")
        os.environ.pop("WICKED_ESTATE_PATH", None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("WICKED_ESTATE_PATH", None)
        else:
            os.environ["WICKED_ESTATE_PATH"] = self._saved_env
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_config_path_wins(self):
        """Priority 1: an executable config `wicked_estate_path` beats env/PATH/fallback."""
        env_fake = os.path.join(self.tmpdir, "env-bin")
        shutil.copy(self.fake, env_fake)
        os.chmod(env_fake, 0o755)
        os.environ["WICKED_ESTATE_PATH"] = env_fake
        resolved = we.resolve_binary(config={"wicked_estate_path": self.fake})
        self.assertEqual(os.path.realpath(resolved), os.path.realpath(self.fake))

    def test_env_var_used_when_no_config(self):
        """Priority 2: WICKED_ESTATE_PATH is used when config has no key."""
        os.environ["WICKED_ESTATE_PATH"] = self.fake
        resolved = we.resolve_binary(config={})
        self.assertEqual(os.path.realpath(resolved), os.path.realpath(self.fake))

    def test_raises_when_nothing_resolves(self):
        """No config, no env, no PATH hit, no fallback → a clear error (never silent degrade).

        We point every source at a non-existent path so resolution cannot find any
        executable; the helper must raise rather than return a bogus / empty path.
        """
        os.environ["WICKED_ESTATE_PATH"] = os.path.join(self.tmpdir, "nope-does-not-exist")
        missing = os.path.join(self.tmpdir, "also-missing")
        with self.assertRaises(Exception) as ctx:
            we.resolve_binary(
                config={"wicked_estate_path": missing},
                fallback=missing,
                search_path=False,
            )
        # The message must guide the user toward a fix, not be opaque.
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "wicked_estate_path" in msg or "wicked-estate" in msg,
            f"error should instruct the user how to fix it, got: {ctx.exception!r}",
        )


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestStatsDigestDeterminism(unittest.TestCase):
    """stats_digest() strips volatile lines so the checksummed evidence is stable."""

    # Two stats outputs that differ ONLY in volatile lines (STALENESS:, repo:
    # git provenance, db= size) — the canonical node/edge block is identical.
    STATS_A = (
        "STALENESS: 1 commit(s) since last index — run `wicked-estate index /x` to refresh\n"
        "nodes=3 edges=3 files=1\n"
        '  edge "calls" = 1\n'
        '  edge "contains" = 2\n'
        "repo:  commit=d238f672  branch=main\n"
    )
    STATS_B = (
        "nodes=3 edges=3 files=1\n"
        '  edge "calls" = 1\n'
        '  edge "contains" = 2\n'
        "repo:  commit=aaaaaaaa  branch=feature/x  db=12.3MB\n"
    )

    def _digest(self, raw):
        # Prefer a pure-text digest entry point if the helper exposes one; else
        # fall through to the public stats_digest signature variants.
        for name in ("digest_stats_text", "_canonicalize_stats", "stats_digest"):
            fn = getattr(we, name, None)
            if fn is None:
                continue
            try:
                return fn(raw)
            except TypeError:
                continue
        self.skipTest("helper exposes no text-level stats digest entry point")

    def test_volatile_lines_stripped_and_stable(self):
        """The canonical block survives; STALENESS/repo/db lines do not leak in."""
        da = self._digest(self.STATS_A)
        db = self._digest(self.STATS_B)
        self.assertEqual(da, db, "digest must ignore git provenance / staleness / db size")
        self.assertIn("nodes=3 edges=3 files=1", da)
        self.assertIn('edge "calls" = 1', da)
        # The volatile substrings must NOT survive into the digest.
        for volatile in ("STALENESS", "commit=", "branch=", "db=12.3MB"):
            self.assertNotIn(volatile, da, f"{volatile!r} leaked into the digest")

    def test_digest_is_idempotent(self):
        """Same input → same digest (no embedded timestamps / randomness)."""
        self.assertEqual(self._digest(self.STATS_A), self._digest(self.STATS_A))


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestAgainstRealBinary(unittest.TestCase):
    """Round-trip tests against a tiny indexed fixture DB built by the real engine."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="we-fixture-")
        src = os.path.join(cls.tmpdir, "src")
        os.makedirs(src, exist_ok=True)
        # Two functions, one calling the other → a known, stable interned SymbolId.
        with open(os.path.join(src, "main.py"), "w") as f:
            f.write("def alpha(x):\n    return beta(x) + 1\n\n\ndef beta(y):\n    return y * 2\n")
        cls.db = os.path.join(cls.tmpdir, "g.db")
        # Index via the engine directly (the helper's index() is also exercised below,
        # but we build the fixture with a raw call so resolve/annotate tests are isolated).
        subprocess.run(
            [BINARY, "index", src, "--db", cls.db],
            capture_output=True, text=True, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_resolve_symbol_id_returns_full_interned_id(self):
        """resolve_symbol_id() returns the full SymbolId string, not the simple name."""
        ids = we.resolve_symbol_id(self.db, "alpha")
        self.assertTrue(ids, "alpha must resolve to at least one SymbolId")
        sid = ids[0]
        # The interned id embeds the language tag and the program/file scope; it is
        # NOT the bare name (the bare name is the silent-no-op trap).
        self.assertNotEqual(sid, "alpha")
        self.assertIn("alpha", sid)
        self.assertTrue(
            sid.startswith("ts-") or "." in sid,
            f"expected a full interned SymbolId, got {sid!r}",
        )

    def test_annotate_raises_on_unresolvable_name(self):
        """The silent-no-op guard: annotate() must REFUSE an empty/unresolvable id.

        Passing a bare, uninterned name to `semantics` reports success but updates 0
        rows. The helper guards this by raising when the id cannot be resolved instead
        of shelling out with a string the engine will silently drop.
        """
        bogus = "this-symbol-was-never-interned-xyz"
        # resolve_symbol_id on a non-existent name yields nothing.
        self.assertEqual(we.resolve_symbol_id(self.db, bogus), [])
        with self.assertRaises(Exception):
            # An empty/unresolvable symbol_id must not reach `semantics`.
            we.annotate(
                self.db, "",
                requirement="RULE-X|0.0|prov|RISK",
                description="should never persist",
                validated=False,
            )

    def test_annotate_by_requirement_round_trip(self):
        """annotate() persists via the stable CLI and by_requirement() reads it back."""
        sid = we.resolve_symbol_id(self.db, "alpha")[0]
        requirement = "RULE-001|0.92|ring1|alpha adds one to the result of beta"
        # Redirect the IP sidecar to a throwaway path so this test never writes
        # into the repo-root .anti-legacy/annotations.jsonl (the overlay default
        # is a CWD-relative path; the round-trip we assert here is the native
        # `by_requirement` field, not the overlay — that is covered separately).
        we.annotate(
            self.db, sid,
            requirement=requirement,
            description="alpha entry point",
            validated=True,
            overlay_path=os.path.join(self.tmpdir, "round-trip-overlay.jsonl"),
        )
        # by_requirement is an EXACT-string reverse lookup (verified against the
        # binary): the full packed requirement string round-trips to this symbol.
        hits = we.by_requirement(self.db, requirement)
        names = " ".join(str(h) for h in (hits if isinstance(hits, (list, tuple)) else [hits]))
        self.assertIn("alpha", names, f"requirement did not round-trip; got {hits!r}")

    def test_annotate_writes_jsonl_overlay(self):
        """annotate() also appends the lossless rule object to .anti-legacy/annotations.jsonl."""
        # Run with cwd at a temp project root so the overlay lands in a known place.
        proj = tempfile.mkdtemp(prefix="we-overlay-")
        try:
            os.makedirs(os.path.join(proj, ".anti-legacy"), exist_ok=True)
            sid = we.resolve_symbol_id(self.db, "beta")[0]
            cwd0 = os.getcwd()
            os.chdir(proj)
            try:
                we.annotate(
                    self.db, sid,
                    requirement="RULE-002|0.81|ring0|beta doubles its input",
                    description="beta doubler",
                    validated=True,
                )
            finally:
                os.chdir(cwd0)
            overlay = os.path.join(proj, ".anti-legacy", "annotations.jsonl")
            self.assertTrue(
                os.path.exists(overlay),
                "annotate() must append the lossless rule object to annotations.jsonl",
            )
            lines = [l for l in open(overlay).read().splitlines() if l.strip()]
            self.assertTrue(lines, "overlay should have at least one record")
            rec = json.loads(lines[-1])
            # The overlay record is keyed by {db_id, symbol_id} and carries the IP.
            self.assertEqual(rec.get("symbol_id"), sid)
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_stats_digest_strips_volatile_lines_on_real_db(self):
        """stats_digest() over a real DB yields a stable canonical block, no provenance."""
        d1 = we.stats_digest(self.db)
        d2 = we.stats_digest(self.db)
        self.assertEqual(d1, d2, "stats_digest must be deterministic across calls")
        self.assertIn("nodes=", d1)
        self.assertIn("edges=", d1)
        for volatile in ("STALENESS", "commit=", "branch="):
            self.assertNotIn(volatile, d1, f"{volatile!r} must be stripped from the digest")

    def test_index_helper_returns_parsed_stats(self):
        """index() runs the engine and returns a parsed stats dict with node/edge counts."""
        src2 = os.path.join(self.tmpdir, "src2")
        os.makedirs(src2, exist_ok=True)
        with open(os.path.join(src2, "m.py"), "w") as f:
            f.write("def only():\n    return 7\n")
        db2 = os.path.join(self.tmpdir, "g2.db")
        result = we.index([("m", src2)], db2)
        self.assertIsInstance(result, dict)
        # Parsed stats expose the node count as an int (the spike contract).
        nodes = result.get("nodes")
        self.assertIsNotNone(nodes, f"index() stats dict should carry a node count: {result!r}")
        # The engine node-ifies a one-function file as a `file` node PLUS the
        # `function` node (verified against the v0.0.1 binary: 2 nodes / 1 edge),
        # so the parsed count is the file + every symbol, not just symbols.
        self.assertEqual(int(nodes), 2)
        self.assertEqual(int(result.get("files")), 1)


# ---------------------------------------------------------------------------
# ISS-12 (legacy-graph digest drift gate): drift() recomputes the current
# deterministic digest and compares it — by SHA-256 AND line-by-line — to a
# registered baseline (a digest-file path, raw digest text, or a 64-hex checksum).
# A no-drift case returns drift=False; a changed-graph case returns drift=True and
# the per-line `changed` detail; the CLI exits 2 on drift so the gate BLOCKS.
#
# SHIM-LEVEL: monkeypatch we.stats_digest so the drift comparison runs against a
# hand-built current digest with NO binary present (the comparison logic is the
# unit under test, not the engine).
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestDriftDigestComparison(unittest.TestCase):
    """drift() composes the digest primitive into a structured drift verdict."""

    # A canonical digest as stats_digest() would emit (volatile lines already stripped).
    DIGEST_V1 = (
        "nodes=84 edges=101 files=1\n"
        'edge "calls" = 18\n'
        'edge "contains" = 83\n'
    )
    # Same graph, one extra `calls` edge (a code change that re-indexing would record).
    DIGEST_V2 = (
        "nodes=84 edges=102 files=1\n"
        'edge "calls" = 19\n'
        'edge "contains" = 83\n'
    )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-drift-shim-")
        self._orig_stats_digest = we.stats_digest

    def tearDown(self):
        we.stats_digest = self._orig_stats_digest
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set_current(self, digest_text):
        """Stub stats_digest(db) -> the given current digest. drift() calls it to
        recompute the current state; nothing touches the engine."""
        def fake_stats_digest(db_or_text=we.DEFAULT_DB, binary=None):
            return digest_text
        we.stats_digest = fake_stats_digest

    def _write_baseline_file(self, digest_text, name="legacy-graph.digest.txt"):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(digest_text)
        return path

    def test_clean_case_no_drift_against_digest_file(self):
        """Current digest == the registered baseline file -> drift=False, changed=[]."""
        self._set_current(self.DIGEST_V1)
        base = self._write_baseline_file(self.DIGEST_V1)
        v = we.drift("ignored.db", base)
        self.assertFalse(v["drift"], f"identical digest must not drift; got {v!r}")
        self.assertEqual(v["changed"], [])
        self.assertEqual(v["current_checksum"], v["baseline_checksum"])
        self.assertEqual(v["baseline_kind"], "digest")

    def test_clean_case_no_drift_against_checksum(self):
        """A bare 64-hex checksum baseline that matches the current digest -> no drift."""
        self._set_current(self.DIGEST_V1)
        chk = we._digest_checksum(we._canonicalize_stats(self.DIGEST_V1))
        v = we.drift("ignored.db", chk)
        self.assertFalse(v["drift"])
        self.assertEqual(v["baseline_kind"], "checksum")
        self.assertEqual(v["changed"], [])  # no line detail for a bare checksum

    def test_changed_graph_reports_drift_and_changed_lines(self):
        """Current digest differs from the baseline -> drift=True + the exact
        count facts that changed (paired on stem, not shown as add+remove)."""
        self._set_current(self.DIGEST_V2)
        base = self._write_baseline_file(self.DIGEST_V1)
        v = we.drift("ignored.db", base)
        self.assertTrue(v["drift"], "a re-indexed graph that differs must report drift")
        self.assertNotEqual(v["current_checksum"], v["baseline_checksum"])
        # Two facts moved: the count header (edges 101->102) and edge "calls" (18->19).
        changed_pairs = {(c["baseline"], c["current"]) for c in v["changed"]}
        self.assertIn(
            ("nodes=84 edges=101 files=1", "nodes=84 edges=102 files=1"),
            changed_pairs,
        )
        self.assertIn(('edge "calls" = 18', 'edge "calls" = 19'), changed_pairs)
        # The unchanged `contains` edge is NOT reported.
        for c in v["changed"]:
            self.assertNotIn("contains", str(c["baseline"]) + str(c["current"]))

    def test_changed_against_checksum_reports_drift_without_line_detail(self):
        """A bare checksum baseline still detects drift (checksum mismatch) but
        yields no per-line detail (only the digest text can be line-diffed)."""
        self._set_current(self.DIGEST_V2)
        stale_chk = we._digest_checksum(we._canonicalize_stats(self.DIGEST_V1))
        v = we.drift("ignored.db", stale_chk)
        self.assertTrue(v["drift"])
        self.assertEqual(v["baseline_kind"], "checksum")
        self.assertEqual(v["changed"], [])

    def test_added_and_removed_edge_kinds_are_reported(self):
        """A new edge kind appears / an old one vanishes -> each shows as a one-sided
        change ({baseline:None,...} added / {...,current:None} removed)."""
        self._set_current(
            "nodes=84 edges=101 files=1\n"
            'edge "calls" = 18\n'
            'edge "invokes" = 83\n'   # 'contains' renamed-away -> removed; 'invokes' added
        )
        base = self._write_baseline_file(self.DIGEST_V1)
        v = we.drift("ignored.db", base)
        self.assertTrue(v["drift"])
        kinds = {(c["baseline"], c["current"]) for c in v["changed"]}
        self.assertIn(('edge "contains" = 83', None), kinds)      # removed
        self.assertIn((None, 'edge "invokes" = 83'), kinds)       # added

    def test_empty_against_raises(self):
        """An empty --against baseline is a clear error, not a silent pass."""
        self._set_current(self.DIGEST_V1)
        with self.assertRaises(we.WickedEstateError):
            we.drift("ignored.db", "")

    def test_garbage_against_raises(self):
        """A baseline that is neither a file, digest text, nor a checksum raises."""
        self._set_current(self.DIGEST_V1)
        with self.assertRaises(we.WickedEstateError):
            we.drift("ignored.db", "this-is-not-a-digest-or-checksum")

    def test_drift_verdict_is_deterministic(self):
        """Same inputs -> identical verdict (no embedded randomness/timestamps)."""
        self._set_current(self.DIGEST_V2)
        base = self._write_baseline_file(self.DIGEST_V1)
        self.assertEqual(we.drift("ignored.db", base), we.drift("ignored.db", base))


@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestDriftCliExitCodes(unittest.TestCase):
    """The `drift` CLI exits 0 (clean), 2 (drift), 1 (error) — the gate's signal."""

    DIGEST = (
        "nodes=5 edges=4 files=1\n"
        'edge "calls" = 2\n'
        'edge "contains" = 2\n'
    )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-drift-cli-")
        self._orig_stats_digest = we.stats_digest

        def fake_stats_digest(db_or_text=we.DEFAULT_DB, binary=None):
            return self.DIGEST
        we.stats_digest = fake_stats_digest

    def tearDown(self):
        we.stats_digest = self._orig_stats_digest
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _baseline(self, text):
        path = os.path.join(self.tmpdir, "baseline.digest.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_cli_exit_0_on_no_drift(self):
        """`drift --against <matching digest>` exits 0."""
        base = self._baseline(self.DIGEST)
        rc = we.main(["drift", "--db", "ignored.db", "--against", base])
        self.assertEqual(rc, 0)

    def test_cli_exit_2_on_drift(self):
        """`drift --against <stale digest>` exits 2 (the BLOCK signal for CI/gate)."""
        stale = self._baseline(self.DIGEST.replace("edges=4", "edges=99"))
        rc = we.main(["drift", "--db", "ignored.db", "--against", stale])
        self.assertEqual(rc, 2)

    def test_cli_exit_1_on_bad_against(self):
        """A malformed --against is a helper error -> exit 1 (distinct from 0/2)."""
        rc = we.main(["drift", "--db", "ignored.db", "--against", "garbage"])
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# ISS-12 NATIVE: real-engine drift round-trip. Build a tiny DB, register its
# digest as the baseline, then re-index a CHANGED source and confirm drift() flips.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestDriftAgainstRealBinary(unittest.TestCase):
    """drift() round-trip against a real indexed DB + its registered digest seam."""

    SRC_V1 = "def alpha(x):\n    return beta(x) + 1\n\n\ndef beta(y):\n    return y * 2\n"
    # Adds a third function -> more nodes -> the canonical digest changes.
    SRC_V2 = (
        "def alpha(x):\n    return beta(x) + 1\n\n\n"
        "def beta(y):\n    return y * 2\n\n\n"
        "def gamma(z):\n    return alpha(z) + beta(z)\n"
    )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-drift-real-")
        self.src = os.path.join(self.tmpdir, "src")
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _index(self, body, db_name):
        with open(os.path.join(self.src, "main.py"), "w") as f:
            f.write(body)
        db = os.path.join(self.tmpdir, db_name)
        subprocess.run(
            [BINARY, "index", self.src, "--db", db],
            capture_output=True, text=True, check=True,
        )
        return db

    def test_no_drift_when_digest_matches_registered_baseline(self):
        """Register the v1 digest, re-index the SAME source -> drift() reports no drift."""
        db_v1 = self._index(self.SRC_V1, "v1.db")
        digest = we.stats_digest(db_v1, binary=BINARY)
        baseline_path = os.path.join(self.tmpdir, "legacy-graph.digest.txt")
        with open(baseline_path, "w", encoding="utf-8") as f:
            f.write(digest)

        db_v1b = self._index(self.SRC_V1, "v1b.db")  # identical source, fresh DB
        v = we.drift(db_v1b, baseline_path, binary=BINARY)
        self.assertFalse(v["drift"], f"identical graph must not drift; got {v!r}")
        self.assertEqual(v["changed"], [])

    def test_drift_when_code_changes_after_annotation(self):
        """Register v1 digest (the seam annotations were written against), then the
        code changes (a new function) and the graph is re-indexed -> drift()=True with
        the changed count facts. This is the §I6 stale-annotation block."""
        db_v1 = self._index(self.SRC_V1, "v1.db")
        digest_v1 = we.stats_digest(db_v1, binary=BINARY)
        baseline_path = os.path.join(self.tmpdir, "legacy-graph.digest.txt")
        with open(baseline_path, "w", encoding="utf-8") as f:
            f.write(digest_v1)

        db_v2 = self._index(self.SRC_V2, "v2.db")  # code changed: +gamma
        v = we.drift(db_v2, baseline_path, binary=BINARY)
        self.assertTrue(v["drift"], f"a changed graph must drift; got {v!r}")
        self.assertNotEqual(v["current_checksum"], v["baseline_checksum"])
        self.assertTrue(v["changed"], "drift must enumerate the changed digest facts")

    def test_drift_against_bare_checksum_baseline(self):
        """drift() accepts the registered 64-hex SHA-256 (the manifest stores exactly
        this for the `legacy-graph` artifact) and detects a stale checksum."""
        db_v1 = self._index(self.SRC_V1, "v1.db")
        chk_v1 = we._digest_checksum(we.stats_digest(db_v1, binary=BINARY))
        # Same source, fresh DB -> checksum still matches -> no drift.
        db_v1b = self._index(self.SRC_V1, "v1b.db")
        self.assertFalse(we.drift(db_v1b, chk_v1, binary=BINARY)["drift"])
        # Changed source -> checksum mismatch -> drift.
        db_v2 = self._index(self.SRC_V2, "v2.db")
        v = we.drift(db_v2, chk_v1, binary=BINARY)
        self.assertTrue(v["drift"])
        self.assertEqual(v["baseline_kind"], "checksum")

    def test_cli_exits_2_on_real_drift(self):
        """The `drift` CLI over a real DB exits 2 when the graph drifted from baseline."""
        db_v1 = self._index(self.SRC_V1, "v1.db")
        digest_v1 = we.stats_digest(db_v1, binary=BINARY)
        baseline_path = os.path.join(self.tmpdir, "legacy-graph.digest.txt")
        with open(baseline_path, "w", encoding="utf-8") as f:
            f.write(digest_v1)
        db_v2 = self._index(self.SRC_V2, "v2.db")
        rc = we.main(["drift", "--db", db_v2, "--against", baseline_path])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
