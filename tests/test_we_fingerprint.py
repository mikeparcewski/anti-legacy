#!/usr/bin/env python3
"""Hermetic unit tests for fingerprint()/changed() (interface D) in
scripts/wicked_estate.py — the CODE-node drift primitive (orthogonal to the
estate `drift` IaC axis).

Two test surfaces:

  * SHIM-LEVEL (always runs, no binary): the pure-stdlib core — _normalize_body,
    _hash_body, _body_is_unhashable — plus changed()'s map-diff, exercised by
    monkeypatching fingerprint()/list_nodes() so the diff logic runs on a
    hand-built input even without the engine. This pins the algorithm:
      - same normalized slice -> identical sha256 (idempotent)
      - editing the slice -> different sha256
      - changed() reports EXACTLY the moved/added/removed symbol_ids vs a
        baseline, and is empty when nothing changed.

  * NATIVE-LEVEL (skipped when the engine binary is absent): build a tiny real
    indexed fixture DB with the engine (mirroring tests/test_wicked_estate.py's
    setUpClass), fingerprint a node, re-index after editing its source, and
    confirm the hash flips; then round-trip changed() against a persisted
    baseline JSON.

All fixtures live in tempfile dirs and are torn down — the working tree stays
clean (the shims are read-only; no annotate(), no overlay writes).
"""
import os
import sys
import json
import shutil
import unittest
import tempfile
import subprocess

SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

WICKED_ESTATE_FALLBACK = (
    ""
)
BINARY = shutil.which("wicked-estate") or (
    WICKED_ESTATE_FALLBACK if os.access(WICKED_ESTATE_FALLBACK, os.X_OK) else None
)

try:
    import wicked_estate as we  # noqa: E402

    HELPER_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - exercised only pre-helper
    we = None
    HELPER_IMPORT_ERROR = exc


# ---------------------------------------------------------------------------
# SHIM-LEVEL: the pure-stdlib hashing/normalization core. No binary required.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestFingerprintNormalizationCore(unittest.TestCase):
    """_normalize_body / _hash_body / _body_is_unhashable — stdlib, deterministic."""

    def test_hash_is_idempotent_for_identical_body(self):
        """Same slice text -> identical sha256 (the idempotence the drift gate relies on)."""
        body = "def alpha(x):\n    return beta(x) + 1\n"
        self.assertEqual(we._hash_body(body), we._hash_body(body))

    def test_hash_differs_when_body_edited(self):
        """A real content edit flips the hash (drift IS detected)."""
        before = "def alpha(x):\n    return beta(x) + 1\n"
        after = "def alpha(x):\n    return beta(x) + 99\n"
        self.assertNotEqual(we._hash_body(before), we._hash_body(after))

    def test_hash_is_sha256_hex(self):
        """Fingerprint is a 64-char lowercase hex sha256 digest."""
        h = we._hash_body("anything")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_trailing_whitespace_does_not_change_hash(self):
        """Per-line trailing-whitespace churn is normalized away (no false drift)."""
        clean = "def f():\n    return 1\n"
        trailing = "def f():   \n    return 1\t\n"
        self.assertEqual(we._hash_body(clean), we._hash_body(trailing))

    def test_crlf_vs_lf_does_not_change_hash(self):
        """Windows CRLF vs Unix LF checkouts must NOT report every node moved."""
        lf = "def f():\n    return 1\n"
        crlf = "def f():\r\n    return 1\r\n"
        cr = "def f():\r    return 1\r"
        self.assertEqual(we._hash_body(lf), we._hash_body(crlf))
        self.assertEqual(we._hash_body(lf), we._hash_body(cr))

    def test_leading_whitespace_is_significant(self):
        """Indentation (leading whitespace) is semantic — only TRAILING ws is stripped."""
        a = "def f():\n    return 1\n"
        b = "def f():\n        return 1\n"  # different indent
        self.assertNotEqual(we._hash_body(a), we._hash_body(b))

    def test_unhashable_markers_detected(self):
        """'(source not stored)' / empty bodies are flagged unhashable."""
        self.assertTrue(we._body_is_unhashable(""))
        self.assertTrue(we._body_is_unhashable("   \n  "))
        self.assertTrue(we._body_is_unhashable("(source not stored — re-run index)"))
        self.assertTrue(we._body_is_unhashable("source not stored"))
        self.assertFalse(we._body_is_unhashable("def f(): return 1"))


# ---------------------------------------------------------------------------
# SHIM-LEVEL: changed() map-diff. Monkeypatch fingerprint()/list_nodes() so the
# diff algorithm runs against a hand-built map with no engine present.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestChangedMapDiff(unittest.TestCase):
    """changed() reports exactly added/removed/moved vs a persisted baseline."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-changed-shim-")
        # Saved originals so we can restore the real functions in tearDown.
        self._orig_fingerprint = we.fingerprint
        self._orig_list_nodes = we.list_nodes

    def tearDown(self):
        we.fingerprint = self._orig_fingerprint
        we.list_nodes = self._orig_list_nodes
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _install_current_map(self, current_map, names):
        """Stub fingerprint(node=None) -> the given current map, and list_nodes()
        -> name lookup. changed() calls both; nothing touches the engine."""
        def fake_fingerprint(db, node=None, *, file=None, kind=None, binary=None):
            self.assertIsNone(node, "changed() must call fingerprint in baseline (node=None) mode")
            return {"db": db, "fingerprints": dict(current_map), "count": len(current_map)}

        def fake_list_nodes(db, kinds=None):
            return [{"symbol_id": sid, "name": names.get(sid, "")} for sid in current_map]

        we.fingerprint = fake_fingerprint
        we.list_nodes = fake_list_nodes

    def _write_baseline(self, baseline_map, wrap=False):
        path = os.path.join(self.tmpdir, "baseline.json")
        payload = (
            {"db": "x", "fingerprints": baseline_map, "count": len(baseline_map)}
            if wrap
            else baseline_map
        )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return path

    def test_detects_exactly_the_moved_node(self):
        """One symbol's content changed -> it (and only it) appears in `moved`."""
        baseline = {"ts-a@alpha": "h_alpha_v1", "ts-a@beta": "h_beta"}
        names = {"ts-a@alpha": "alpha", "ts-a@beta": "beta"}
        current = {"ts-a@alpha": "h_alpha_v2", "ts-a@beta": "h_beta"}  # alpha changed
        self._install_current_map(current, names)
        base_path = self._write_baseline(baseline)

        result = we.changed("ignored.db", base_path)

        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual([m["symbol_id"] for m in result["moved"]], ["ts-a@alpha"])
        moved = result["moved"][0]
        self.assertEqual(moved["name"], "alpha")
        self.assertEqual(moved["old"], "h_alpha_v1")
        self.assertEqual(moved["new"], "h_alpha_v2")
        self.assertEqual(result["unchanged_count"], 1)  # beta unchanged

    def test_empty_when_nothing_changed(self):
        """Identical baseline and current -> no added/removed/moved, all unchanged."""
        m = {"ts-a@alpha": "h1", "ts-a@beta": "h2", "ts-a@gamma": "h3"}
        names = {"ts-a@alpha": "alpha", "ts-a@beta": "beta", "ts-a@gamma": "gamma"}
        self._install_current_map(dict(m), names)
        base_path = self._write_baseline(dict(m))

        result = we.changed("ignored.db", base_path)

        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["moved"], [])
        self.assertEqual(result["unchanged_count"], 3)

    def test_added_and_removed_symbols(self):
        """New symbol -> added; vanished symbol -> removed; neither is `moved`."""
        baseline = {"ts-a@alpha": "h1", "ts-a@gone": "h_gone"}
        current = {"ts-a@alpha": "h1", "ts-a@fresh": "h_fresh"}
        names = {"ts-a@alpha": "alpha", "ts-a@fresh": "fresh"}
        self._install_current_map(current, names)
        base_path = self._write_baseline(baseline)

        result = we.changed("ignored.db", base_path)

        self.assertEqual(result["added"], ["ts-a@fresh"])
        self.assertEqual(result["removed"], ["ts-a@gone"])
        self.assertEqual(result["moved"], [])
        self.assertEqual(result["unchanged_count"], 1)

    def test_accepts_full_baseline_dict_shape(self):
        """A baseline persisted as the full fingerprint() dict (wrapped in
        'fingerprints') diffs identically to a raw {sid->hash} map."""
        baseline = {"ts-a@alpha": "h_alpha_v1"}
        current = {"ts-a@alpha": "h_alpha_v2"}
        names = {"ts-a@alpha": "alpha"}
        self._install_current_map(current, names)
        base_path = self._write_baseline(baseline, wrap=True)

        result = we.changed("ignored.db", base_path)
        self.assertEqual([m["symbol_id"] for m in result["moved"]], ["ts-a@alpha"])
        self.assertEqual(result["unchanged_count"], 0)

    def test_outputs_are_deterministically_sorted(self):
        """added/removed are sorted lists; moved iterates in sorted symbol_id order
        (no random ordering across runs)."""
        baseline = {"ts-z": "1", "ts-a": "1", "ts-m_old": "x"}
        current = {"ts-y": "2", "ts-b": "2", "ts-m_old": "y"}  # m_old moved
        names = {"ts-y": "y", "ts-b": "b", "ts-m_old": "m"}
        self._install_current_map(current, names)
        base_path = self._write_baseline(baseline)

        result = we.changed("ignored.db", base_path)
        self.assertEqual(result["added"], sorted(result["added"]))
        self.assertEqual(result["removed"], sorted(result["removed"]))
        self.assertEqual(result["added"], ["ts-b", "ts-y"])
        self.assertEqual(result["removed"], ["ts-a", "ts-z"])

    def test_missing_baseline_raises(self):
        """A non-existent baseline path is a clear error, not a silent empty diff."""
        with self.assertRaises(we.WickedEstateError):
            we.changed("ignored.db", os.path.join(self.tmpdir, "nope.json"))

    def test_garbage_baseline_raises(self):
        """A baseline that is not a dict (e.g. a JSON list) raises rather than
        producing a meaningless diff."""
        bad = os.path.join(self.tmpdir, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(["not", "a", "map"], f)
        with self.assertRaises(we.WickedEstateError):
            we.changed("ignored.db", bad)


# ---------------------------------------------------------------------------
# NATIVE-LEVEL: real engine fixture. Skipped when the binary is absent.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestFingerprintAgainstRealBinary(unittest.TestCase):
    """fingerprint()/changed() round-trip against a tiny real indexed DB."""

    ALPHA_V1 = "def alpha(x):\n    return beta(x) + 1\n\n\ndef beta(y):\n    return y * 2\n"
    # Edit alpha's body only; beta is byte-identical between versions.
    ALPHA_V2 = "def alpha(x):\n    return beta(x) + 99\n\n\ndef beta(y):\n    return y * 2\n"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-fp-fixture-")
        self.src = os.path.join(self.tmpdir, "src")
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _index(self, body, db_name):
        """Write main.py with `body`, index a fresh DB, return its path."""
        with open(os.path.join(self.src, "main.py"), "w") as f:
            f.write(body)
        db = os.path.join(self.tmpdir, db_name)
        subprocess.run(
            [BINARY, "index", self.src, "--db", db],
            capture_output=True, text=True, check=True,
        )
        return db

    def test_single_node_fingerprint_is_idempotent(self):
        """fingerprint(node) twice on an unchanged DB -> identical hash + identity."""
        db = self._index(self.ALPHA_V1, "v1.db")
        fp1 = we.fingerprint(db, "alpha", binary=BINARY)
        fp2 = we.fingerprint(db, "alpha", binary=BINARY)
        self.assertEqual(fp1["fingerprint"], fp2["fingerprint"])
        self.assertEqual(len(fp1["fingerprint"]), 64)  # sha256 hex
        self.assertFalse(fp1["unhashable"])
        self.assertEqual(fp1["name"], "alpha")
        # Keyed by the full interned SymbolId, never the bare name.
        self.assertNotEqual(fp1["symbol_id"], "alpha")
        self.assertIn("alpha", fp1["symbol_id"])

    def test_editing_node_source_changes_fingerprint(self):
        """Same node, edited source slice -> different sha256."""
        db_v1 = self._index(self.ALPHA_V1, "v1.db")
        fp_before = we.fingerprint(db_v1, "alpha", binary=BINARY)
        db_v2 = self._index(self.ALPHA_V2, "v2.db")
        fp_after = we.fingerprint(db_v2, "alpha", binary=BINARY)
        self.assertNotEqual(
            fp_before["fingerprint"], fp_after["fingerprint"],
            "editing alpha's body must change its fingerprint",
        )

    def test_baseline_map_covers_all_nodes(self):
        """fingerprint(node=None) returns {symbol_id -> sha256} over every node."""
        db = self._index(self.ALPHA_V1, "v1.db")
        base = we.fingerprint(db, node=None, binary=BINARY)
        self.assertIn("fingerprints", base)
        self.assertEqual(base["count"], len(base["fingerprints"]))
        self.assertGreaterEqual(base["count"], 2, "expect at least alpha + beta")
        # Keys are interned SymbolIds; the alpha node's hash matches the single
        # node lookup (same normalized slice text).
        alpha_sid = we.resolve_symbol_id(db, "alpha")[0]
        self.assertIn(alpha_sid, base["fingerprints"])
        single = we.fingerprint(db, "alpha", binary=BINARY)
        self.assertEqual(base["fingerprints"][alpha_sid], single["fingerprint"])

    def test_changed_detects_exactly_the_edited_node(self):
        """changed(): persist v1 baseline, edit alpha, re-index, diff -> alpha moved,
        beta unchanged, nothing added/removed (same node set across versions)."""
        db_v1 = self._index(self.ALPHA_V1, "v1.db")
        base_map = we.fingerprint(db_v1, node=None, binary=BINARY)
        baseline_path = os.path.join(self.tmpdir, "fingerprint-baseline.json")
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(base_map, f)

        # Re-index the EDITED source into a fresh DB; the interned SymbolIds are
        # stable identity (ADR-002) so alpha/beta keys carry over.
        db_v2 = self._index(self.ALPHA_V2, "v2.db")
        result = we.changed(db_v2, baseline_path, binary=BINARY)

        moved_names = {m["name"] for m in result["moved"]}
        self.assertIn("alpha", moved_names, f"alpha should be moved; got {result!r}")
        self.assertNotIn("beta", moved_names, "beta's body was unchanged")
        self.assertEqual(result["added"], [], f"no new symbols expected; got {result['added']}")
        self.assertEqual(result["removed"], [], f"no symbols removed; got {result['removed']}")
        self.assertGreaterEqual(result["unchanged_count"], 1, "beta (>=1 node) unchanged")
        # The moved record carries the old/new hashes that differ.
        amoved = next(m for m in result["moved"] if m["name"] == "alpha")
        self.assertNotEqual(amoved["old"], amoved["new"])

    def test_changed_empty_when_reindex_unchanged(self):
        """changed() against a baseline taken from identical source -> nothing moved."""
        db_v1 = self._index(self.ALPHA_V1, "v1.db")
        base_map = we.fingerprint(db_v1, node=None, binary=BINARY)
        baseline_path = os.path.join(self.tmpdir, "baseline.json")
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(base_map, f)

        # Re-index the SAME source into a fresh DB.
        db_v1b = self._index(self.ALPHA_V1, "v1b.db")
        result = we.changed(db_v1b, baseline_path, binary=BINARY)
        self.assertEqual(result["moved"], [], f"identical source must not move; got {result!r}")
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertGreaterEqual(result["unchanged_count"], 2)

    def test_disambiguation_required_for_colliding_name(self):
        """When a name resolves to >1 symbol and no file/kind is given, fingerprint
        must REFUSE (raise) rather than silently hash an arbitrary collision."""
        # main.py 'alpha' is unique here, so synthesize a collision: a second file
        # also defining alpha. (Names are NOT unique across the estate.)
        with open(os.path.join(self.src, "other.py"), "w") as f:
            f.write("def alpha(z):\n    return z\n")
        with open(os.path.join(self.src, "main.py"), "w") as f:
            f.write(self.ALPHA_V1)
        db = os.path.join(self.tmpdir, "collide.db")
        subprocess.run(
            [BINARY, "index", self.src, "--db", db],
            capture_output=True, text=True, check=True,
        )
        sids = we.resolve_symbol_id(db, "alpha")
        if len(sids) < 2:
            self.skipTest(f"engine did not produce a name collision for alpha: {sids!r}")
        with self.assertRaises(we.WickedEstateError):
            we.fingerprint(db, "alpha", binary=BINARY)
        # Disambiguated by file -> succeeds and hashes the right slice.
        fp = we.fingerprint(db, "alpha", file="other.py", binary=BINARY)
        self.assertFalse(fp["unhashable"])
        self.assertEqual(len(fp["fingerprint"]), 64)


# ---------------------------------------------------------------------------
# NATIVE ADJUNCT (v0.1.5+): native `fingerprint <name>` is an IDENTITY hash over
# id+name+kind+file+SIGNATURE (NOT body) — a body edit does NOT move it. The CI
# body-drift gate therefore KEEPS the sha256 body-hash as fingerprint()'s primary;
# native identity-hash is exposed via fingerprint_native() + an additive
# `identity_fingerprint` field. These tests pin:
#   * the native `fingerprint` subcommand probes True on v0.1.5 (so the OLD
#     `if probe: raise` would have broken fingerprint() — the WF4 fix removes it),
#   * fingerprint_native() returns the 16-hex identity hash for a single resolved
#     SymbolId (collision-guarded, NO name fan-out),
#   * EMPIRICALLY CONFIRMED divergence: editing a body (signature unchanged) MOVES
#     the shim body-sha256 but LEAVES the native identity hash unchanged,
#   * the additive identity_fingerprint field rides alongside (never replacing) the
#     body hash on single-node fingerprint(), so the body-drift contract is intact.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestFingerprintNativeAdjunct(unittest.TestCase):
    """fingerprint_native() identity hash + the additive identity_fingerprint field."""

    # Same signature (def beta(y)), file, name, line — ONLY the body literal moves.
    BETA_V1 = "def beta(y):\n    return y * 42\n"
    BETA_V2 = "def beta(y):\n    return y * 777\n"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-fp-native-")
        self.src = os.path.join(self.tmpdir, "src")
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _index(self, body, db_name):
        with open(os.path.join(self.src, "chain.py"), "w") as f:
            f.write(body)
        db = os.path.join(self.tmpdir, db_name)
        subprocess.run(
            [BINARY, "index", self.src, "--db", db],
            capture_output=True, text=True, check=True,
        )
        return db

    def test_native_fingerprint_subcommand_is_present(self):
        """v0.1.5 ships native `fingerprint` (probe True) — this is exactly why the
        OLD unguarded `if probe: raise` broke fingerprint(); the WF4 fix runs the shim
        body-hash unconditionally and exposes native as an adjunct."""
        db = self._index(self.BETA_V1, "v1.db")
        self.assertTrue(we._probe_native_subcommand("fingerprint", binary=BINARY))

    def test_fingerprint_native_returns_identity_hash_for_single_symbol(self):
        """fingerprint_native() resolves to ONE SymbolId and returns its 16-hex
        identity hash tagged kind_of='identity' — no name fan-out across collisions."""
        db = self._index(self.BETA_V1, "v1.db")
        nat = we.fingerprint_native(db, "beta", binary=BINARY)
        self.assertEqual(nat["name"], "beta")
        self.assertEqual(nat["kind_of"], "identity")
        # 16-hex identity hash (distinct from the 64-hex sha256 body hash).
        self.assertEqual(len(nat["fingerprint"]), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in nat["fingerprint"].lower()))
        # Keyed by the full interned SymbolId, never the bare name.
        self.assertNotEqual(nat["symbol_id"], "beta")
        self.assertIn("beta", nat["symbol_id"])

    def test_body_edit_moves_shim_hash_but_not_native_identity(self):
        """THE reconciliation evidence: a body edit (42 -> 777, signature unchanged)
        MOVES the shim's sha256 body-hash (drift IS detected) but LEAVES the native
        identity hash UNCHANGED (it hashes signature, not body). This is why the body
        hash — not native — is the drift primitive."""
        db_v1 = self._index(self.BETA_V1, "v1.db")
        body_v1 = we.fingerprint(db_v1, "beta", binary=BINARY)["fingerprint"]
        nat_v1 = we.fingerprint_native(db_v1, "beta", binary=BINARY)["fingerprint"]

        db_v2 = self._index(self.BETA_V2, "v2.db")
        body_v2 = we.fingerprint(db_v2, "beta", binary=BINARY)["fingerprint"]
        nat_v2 = we.fingerprint_native(db_v2, "beta", binary=BINARY)["fingerprint"]

        # Shim body-hash MOVES (drift detected) — the gate's whole purpose.
        self.assertNotEqual(body_v1, body_v2,
                            "shim body-sha256 must move on a body edit (drift)")
        # Native identity hash is UNCHANGED (it does not see the body literal).
        self.assertEqual(nat_v1, nat_v2,
                         "native identity hash must NOT move on a body-only edit")

    def test_single_node_fingerprint_carries_additive_identity_field(self):
        """When native `fingerprint` is present, single-node fingerprint() ADDS an
        `identity_fingerprint` (the native 16-hex) ALONGSIDE — never replacing — the
        64-hex body `fingerprint`."""
        db = self._index(self.BETA_V1, "v1.db")
        fp = we.fingerprint(db, "beta", binary=BINARY)
        # Body hash is still the sha256 (64-hex) primary key.
        self.assertEqual(len(fp["fingerprint"]), 64)
        self.assertFalse(fp["unhashable"])
        # Additive identity field present and == the native adjunct's hash.
        self.assertIn("identity_fingerprint", fp)
        self.assertEqual(len(fp["identity_fingerprint"]), 16)
        nat = we.fingerprint_native(db, "beta", binary=BINARY)
        self.assertEqual(fp["identity_fingerprint"], nat["fingerprint"])

    def test_fingerprint_native_refuses_ambiguous_name(self):
        """Native `fingerprint <name>` prints a line PER search hit; fingerprint_native
        resolves to a SymbolId first and REFUSES an ambiguous name (no disambiguator)
        rather than smearing across collisions — then succeeds when file-scoped."""
        # Two files both defining beta -> a real name collision.
        with open(os.path.join(self.src, "chain.py"), "w") as f:
            f.write(self.BETA_V1)
        with open(os.path.join(self.src, "other.py"), "w") as f:
            f.write("def beta(y):\n    return y - 9\n")
        db = os.path.join(self.tmpdir, "collide.db")
        subprocess.run(
            [BINARY, "index", self.src, "--db", db],
            capture_output=True, text=True, check=True,
        )
        sids = we.resolve_symbol_id(db, "beta")
        if len(sids) < 2:
            self.skipTest(f"engine did not produce a beta collision: {sids!r}")
        with self.assertRaises(we.WickedEstateError):
            we.fingerprint_native(db, "beta", binary=BINARY)
        # File-disambiguated -> resolves to one symbol and returns its identity hash.
        nat = we.fingerprint_native(db, "beta", file="other.py", binary=BINARY)
        self.assertEqual(len(nat["fingerprint"]), 16)
        self.assertEqual(os.path.basename(nat["file"]), "other.py")


# ---------------------------------------------------------------------------
# NATIVE ADJUNCT (v0.1.5+): native `changed-since <git-sha> --json` is a GIT-diff of
# FILE paths (file-granular, over-reports every symbol in a touched file, needs a git
# repo + a SHA). It is a DIFFERENT mechanism than changed()'s fingerprint-baseline
# diff — exposed as the SEPARATE adjunct changed_since(), which NEVER replaces
# changed(). Requires a throwaway git tree; the binary runs `git diff` from the
# PROCESS cwd, so these tests chdir into the repo (and restore cwd in tearDown).
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestChangedSinceNativeAdjunct(unittest.TestCase):
    """changed_since() over a throwaway git repo — native git-diff symbol list."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-changed-since-")
        self._cwd = os.getcwd()
        # Index root == git root so native `nodes_in_file` keys match git's paths.
        with open(os.path.join(self.tmpdir, "chain.py"), "w") as f:
            f.write("def alpha(x):\n    return beta(x)\n\n\ndef beta(y):\n    return y * 2\n")
        self._git("init", "-q")
        self._git("config", "user.email", "t@t.t")
        self._git("config", "user.name", "t")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "init")
        self.base_sha = self._git("rev-parse", "HEAD").strip()
        # Edit alpha's body and re-commit so changed-since has a file delta.
        with open(os.path.join(self.tmpdir, "chain.py"), "w") as f:
            f.write("def alpha(x):\n    return beta(x) + 1\n\n\ndef beta(y):\n    return y * 2\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "edit alpha")
        self.db = os.path.join(self.tmpdir, "g.db")
        subprocess.run(
            [BINARY, "index", ".", "--db", self.db],
            cwd=self.tmpdir, capture_output=True, text=True, check=True,
        )
        # The native command runs `git diff` from the process cwd -> chdir into repo.
        os.chdir(self.tmpdir)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", self.tmpdir, *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def test_native_changed_since_present_and_changed_name_is_not(self):
        """v0.1.5 ships `changed-since` (probe True) under the CORRECTED name; the OLD
        probed name `changed` falls through to the banner (probe False) — which is why
        changed() correctly stays the shim baseline-diff."""
        self.assertTrue(we._probe_native_subcommand("changed-since", binary=BINARY))
        self.assertFalse(we._probe_native_subcommand("changed", binary=BINARY))

    def test_changed_since_reports_symbols_in_the_changed_file(self):
        """changed_since() returns {since_sha, symbols, count} over EVERY symbol in any
        git-changed file (file-granular: even beta, whose body did not change, because
        it shares the touched chain.py — the over-report the docstring warns of)."""
        res = we.changed_since(self.db, self.base_sha, binary=BINARY)
        self.assertEqual(res["since_sha"], self.base_sha)
        self.assertEqual(res["count"], len(res["symbols"]))
        names = {s["name"] for s in res["symbols"]}
        # alpha's file changed -> alpha reported; beta is in the same file -> ALSO
        # reported (file-granular over-report), proving the git-diff mechanism.
        self.assertIn("alpha", names)
        self.assertIn("beta", names)
        for s in res["symbols"]:
            self.assertEqual(sorted(s.keys()), ["file", "kind", "line", "name"])

    def test_changed_since_is_separate_from_changed(self):
        """changed_since() (git SHA, native) and changed() (baseline JSON path, shim)
        are DISTINCT functions with different signatures — changed_since does NOT
        accept a baseline path and changed() does NOT accept a SHA. Both coexist."""
        # changed() still works as the fingerprint-baseline diff (separate mechanism).
        base_map = we.fingerprint(self.db, node=None, binary=BINARY)
        baseline_path = os.path.join(self.tmpdir, "baseline.json")
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(base_map, f)
        diff = we.changed(self.db, baseline_path, binary=BINARY)
        # Same DB vs its own fresh baseline -> nothing moved (content-identical).
        self.assertEqual(diff["moved"], [])
        # And the native git path returns its own (file-granular) shape, unaffected.
        res = we.changed_since(self.db, self.base_sha, binary=BINARY)
        self.assertIn("since_sha", res)
        self.assertNotIn("moved", res)  # different shape than changed()

    def test_changed_since_no_delta_returns_empty(self):
        """v0.1.5: with NO files changed, native `changed-since <sha> --json` emits `[]`
        (not the old human 'no files changed' sentinel), so changed_since() returns a clean
        empty result instead of raising. (The WF4 KNOWN-BUG is fixed in the engine.)"""
        head = self._git("rev-parse", "HEAD").strip()
        res = we.changed_since(self.db, head, binary=BINARY)
        self.assertEqual(res["since_sha"], head)
        self.assertEqual(res["symbols"], [])
        self.assertEqual(res["count"], 0)


# ---------------------------------------------------------------------------
# ISS-20 (name-collision fingerprint): the body-hash shim previously keyed the
# baseline body cache by NAME via source(db, name), which CONCATENATES every
# match into one blob — so the 21 carddemo MAIN-PARA nodes all shared ONE
# fingerprint (one edit moved ALL of them). The fix splits source() into PER-MATCH
# bodies (source_by_match) keyed by file, so each colliding symbol_id gets its OWN
# distinct fingerprint.
#
# SHIM-LEVEL: monkeypatch we._run to return a canned multi-match `source` blob
# mirroring the engine's `[Kind] name @ file:line` header format — no binary.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
class TestSourceByMatchSplit(unittest.TestCase):
    """source_by_match() splits the concatenated `source` blob into per-node bodies."""

    # Two files define MAIN-PARA with DIFFERENT bodies, a third repeats file A's body.
    # The count banner + per-match headers mirror the real engine output exactly.
    CANNED = (
        "3 match(es) for 'MAIN-PARA':\n"
        "  [Function] MAIN-PARA @ app/cbl/COUSR00C.cbl:98\n"
        "MAIN-PARA.\n"
        "    MOVE 1 TO WS-A\n"
        "\n"
        "  [Function] MAIN-PARA @ app/cbl/COUSR01C.cbl:71\n"
        "MAIN-PARA.\n"
        "    MOVE 2 TO WS-B\n"
        "\n"
        "  [Function] MAIN-PARA @ app/cbl/COSGN00C.cbl:73\n"
        "MAIN-PARA.\n"
        "    MOVE 1 TO WS-A\n"
    )

    def setUp(self):
        self._orig_run = we._run

    def tearDown(self):
        we._run = self._orig_run

    def _install(self, canned):
        def fake_run(args, *, timeout, cwd=".", binary=None):
            # source_by_match shells `source <name> --db <db>`.
            self.assertEqual(args[0], "source")
            return canned
        we._run = fake_run

    def test_splits_into_one_record_per_match(self):
        """A 3-match blob -> 3 records, each carrying its OWN file + body."""
        self._install(self.CANNED)
        recs = we.source_by_match("ignored.db", "MAIN-PARA")
        self.assertEqual(len(recs), 3)
        files = [os.path.basename(r["file"]) for r in recs]
        self.assertEqual(files, ["COUSR00C.cbl", "COUSR01C.cbl", "COSGN00C.cbl"])
        # The body BELOW each header is attributed to that match (not concatenated).
        self.assertIn("MOVE 1 TO WS-A", recs[0]["body"])
        self.assertNotIn("MOVE 2 TO WS-B", recs[0]["body"])
        self.assertIn("MOVE 2 TO WS-B", recs[1]["body"])
        self.assertNotIn("MOVE 1 TO WS-A", recs[1]["body"])

    def test_distinct_bodies_get_distinct_hashes_identical_bodies_collide(self):
        """Hashing the per-match bodies: the two byte-identical bodies (file A & C)
        share a hash; the different body (file B) does NOT — proving per-node
        attribution, not one-blob aliasing."""
        self._install(self.CANNED)
        recs = we.source_by_match("ignored.db", "MAIN-PARA")
        h = [we._hash_body(r["body"]) for r in recs]
        self.assertEqual(h[0], h[2], "byte-identical paragraph bodies hash equal")
        self.assertNotEqual(h[0], h[1], "a genuinely different body must hash differently")
        # The PRIOR (buggy) behavior would give ALL THREE the same concatenated hash.
        self.assertEqual(len(set(h)), 2, "expected exactly 2 distinct hashes, not 1 (aliased) or 3")

    def test_header_parser_extracts_file_and_line(self):
        """_parse_source_match_header reads the `[Kind] name @ file:line` disambiguator."""
        hdr = we._parse_source_match_header("  [Function] MAIN-PARA @ app/cbl/COUSR00C.cbl:98")
        self.assertEqual(hdr["kind"], "Function")
        self.assertEqual(hdr["name"], "MAIN-PARA")
        self.assertEqual(hdr["file"], "app/cbl/COUSR00C.cbl")
        self.assertEqual(hdr["line"], 98)
        # Non-header lines (banner, body) return None.
        self.assertIsNone(we._parse_source_match_header("3 match(es) for 'MAIN-PARA':"))
        self.assertIsNone(we._parse_source_match_header("MAIN-PARA."))

    def test_single_match_returns_one_record(self):
        """A name with one match (single header) -> a one-element list."""
        self._install(
            "1 match(es) for 'beta':\n"
            "  [Function] beta @ chain.py:4\n"
            "    return y * 2\n"
        )
        recs = we.source_by_match("ignored.db", "beta")
        self.assertEqual(len(recs), 1)
        self.assertEqual(os.path.basename(recs[0]["file"]), "chain.py")
        self.assertIn("return y * 2", recs[0]["body"])


# ---------------------------------------------------------------------------
# ISS-20 NATIVE: real engine fixture with a genuine name collision (two files
# defining `beta` with DIFFERENT bodies). The baseline fingerprint() map must give
# each colliding symbol_id its OWN fingerprint — the regression the bug would fail.
# ---------------------------------------------------------------------------
@unittest.skipIf(we is None, f"scripts/wicked_estate.py not importable yet: {HELPER_IMPORT_ERROR}")
@unittest.skipIf(BINARY is None, "wicked-estate binary not available")
class TestFingerprintCollisionFreeBaseline(unittest.TestCase):
    """Baseline fingerprint() gives name-colliding nodes DISTINCT fingerprints (ISS-20)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="we-collide-baseline-")
        self.src = os.path.join(self.tmpdir, "src")
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _index(self, files):
        for fname, body in files.items():
            with open(os.path.join(self.src, fname), "w") as f:
                f.write(body)
        db = os.path.join(self.tmpdir, "g.db")
        subprocess.run(
            [BINARY, "index", self.src, "--db", db],
            capture_output=True, text=True, check=True,
        )
        return db

    def test_colliding_names_get_distinct_fingerprints(self):
        """Two files defining `beta` with DIFFERENT bodies -> two symbol_ids, two
        DISTINCT fingerprints in the baseline map (NOT one aliased hash)."""
        db = self._index({
            "a.py": "def beta(y):\n    return y * 2\n",
            "b.py": "def beta(y):\n    return y - 9\n",   # different body
        })
        sids = we.resolve_symbol_id(db, "beta")
        if len(sids) < 2:
            self.skipTest(f"engine did not produce a beta collision: {sids!r}")
        base = we.fingerprint(db, node=None, binary=BINARY)
        fps = base["fingerprints"]
        beta_fps = {sid: fps.get(sid) for sid in sids}
        for sid in sids:
            self.assertIn(sid, fps, f"every colliding symbol_id must be in the baseline: {sid}")
            self.assertTrue(beta_fps[sid], f"{sid} should have a non-empty fingerprint")
        # THE ISS-20 ASSERTION: the two different-bodied betas hash DIFFERENTLY.
        distinct = set(beta_fps.values())
        self.assertEqual(
            len(distinct), 2,
            f"name-colliding nodes with different bodies must get distinct "
            f"fingerprints (ISS-20); got {beta_fps!r}",
        )
        # Each baseline fingerprint matches the exact single-node (file-scoped) hash.
        for fname in ("a.py", "b.py"):
            single = we.fingerprint(db, "beta", file=fname, binary=BINARY)
            self.assertIn(single["fingerprint"], distinct)

    def test_identical_bodies_collision_still_per_node_keyed(self):
        """Two files with the SAME `beta` body share a fingerprint (correct — same
        content), but each symbol_id is still INDEPENDENTLY keyed in the map (so an
        edit to ONE later moves only that one)."""
        db = self._index({
            "a.py": "def beta(y):\n    return y * 2\n",
            "b.py": "def beta(y):\n    return y * 2\n",   # identical body
        })
        sids = we.resolve_symbol_id(db, "beta")
        if len(sids) < 2:
            self.skipTest(f"engine did not produce a beta collision: {sids!r}")
        base = we.fingerprint(db, node=None, binary=BINARY)
        fps = base["fingerprints"]
        # Both present and keyed by their OWN symbol_id (not one shared key).
        self.assertEqual(len({s for s in sids}), len(sids))
        for sid in sids:
            self.assertIn(sid, fps)
            self.assertTrue(fps[sid])
        # Same body -> same hash is correct here (content equality, not aliasing).
        self.assertEqual(len({fps[sid] for sid in sids}), 1)


if __name__ == "__main__":
    unittest.main()
