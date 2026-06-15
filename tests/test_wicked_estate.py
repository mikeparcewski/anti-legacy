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


if __name__ == "__main__":
    unittest.main()
