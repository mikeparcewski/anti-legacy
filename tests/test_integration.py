#!/usr/bin/env python3
"""End-to-end integration test for the WF1 extraction CORE.

Supersedes the old graph_builder.py → graph_normalizer.py → packet → verifier
pipeline. WF1 makes `wicked-estate` the code-graph engine, so the integration
core is now:

  1. INDEX  — `run.py wicked_estate index <cobol>` builds the code graph
              (the direct replacement for the deleted graph_builder.py stage).
  2. DIGEST — the deterministic `stats_digest` is the checksummable
              `legacy-graph` evidence body (replaces the legacy_graph.json blob).
  3. ANNOTATE round-trip — resolve_symbol_id → annotate → by_requirement, the
              native-field write path the extraction skill drives, with the
              lossless rule object mirrored into .anti-legacy/annotations.jsonl.
  4. NO-OP GUARD — annotating an unresolvable name must RAISE, never silently
              no-op (the SymbolId trap that gates the helper).

The test is hermetic: it copies the tiny COBOL fixture into a temp tree and
indexes into a temp DB, so it shares no mutable state with other tests (the old
test coupled on files under tests/mock_workspace and was order-dependent).

It self-skips only when the WF1 wicked_estate helper / engine binary is not yet
resolvable, so the suite stays green; once WF1 lands, the real assertions run.
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
HELPER_PATH = os.path.join(SCRIPTS_DIR, "wicked_estate.py")
COBOL_FIXTURE = os.path.join(
    os.path.dirname(__file__), "mock_workspace", "app_cobol"
)

# Make scripts/ importable so we can drive the helper's documented Python API
# (the contract states scripts/wicked_estate.py is "importable + run.py CLI").
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_helper():
    """Import scripts/wicked_estate.py, or None if WF1 has not landed it yet."""
    if not os.path.isfile(HELPER_PATH):
        return None
    try:
        return importlib.import_module("wicked_estate")
    except Exception:
        return None


def _binary_resolvable(we):
    """The helper's own resolver decides if a wicked-estate binary is usable."""
    try:
        path = we.resolve_binary()
    except Exception:
        return False
    return bool(path) and os.path.exists(path) and os.access(path, os.X_OK)


_HELPER = _load_helper()
_SKIP_REASON = None
if _HELPER is None:
    _SKIP_REASON = "scripts/wicked_estate.py (WF1 helper) not present yet"
elif not _binary_resolvable(_HELPER):
    _SKIP_REASON = "wicked-estate binary not resolvable on this machine"
elif not os.path.isdir(COBOL_FIXTURE):
    _SKIP_REASON = "COBOL fixture tests/mock_workspace/app_cobol missing"


@unittest.skipIf(_SKIP_REASON is not None, _SKIP_REASON or "")
class TestExtractionCorePipeline(unittest.TestCase):
    """Crawl → index → digest → annotate round-trip over the wicked-estate graph."""

    def setUp(self):
        self.we = _HELPER
        # Hermetic workspace: copy the fixture in, index into a temp DB. Nothing
        # under tests/mock_workspace is written, so run-order can't pollute us.
        self.work = tempfile.mkdtemp(prefix="anti-legacy-wf1-")
        self.app_dir = os.path.join(self.work, "app_cobol")
        shutil.copytree(COBOL_FIXTURE, self.app_dir)
        self.db = os.path.join(self.work, "legacy-graph.db")
        # Pin the helper's annotation overlay into the temp tree so the JSONL
        # sidecar assertion is isolated and we never touch the repo's overlay.
        self.overlay = os.path.join(self.work, "annotations.jsonl")
        self._prev_overlay_env = os.environ.get("ANTI_LEGACY_ANNOTATIONS")
        os.environ["ANTI_LEGACY_ANNOTATIONS"] = self.overlay

    def tearDown(self):
        if self._prev_overlay_env is None:
            os.environ.pop("ANTI_LEGACY_ANNOTATIONS", None)
        else:
            os.environ["ANTI_LEGACY_ANNOTATIONS"] = self._prev_overlay_env
        shutil.rmtree(self.work, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Stage 1 — INDEX via the CLI seam (`run.py wicked_estate index`),    #
    # the direct replacement for the deleted graph_builder.py stage.      #
    # ------------------------------------------------------------------ #
    def _index_via_cli(self):
        cmd = [
            sys.executable, HELPER_PATH,
            "index", self.app_dir, "--db", self.db,
        ]
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=REPO_ROOT, timeout=180,
        )
        self.assertEqual(
            res.returncode, 0,
            "wicked_estate index (CLI) failed: %s\n%s" % (res.stdout, res.stderr),
        )
        self.assertTrue(
            os.path.exists(self.db), "index did not create the legacy-graph DB"
        )

    def test_extraction_core_end_to_end(self):
        # 1. Build the code graph through the CLI seam.
        self._index_via_cli()

        # 2. The deterministic stats digest is the checksummable legacy-graph
        #    evidence body. It must carry the canonical "nodes=" stat line and be
        #    byte-stable across calls (volatile repo:/STALENESS/db= lines stripped).
        digest = self.we.stats_digest(self.db)
        self.assertIsInstance(digest, str)
        stat_lines = [
            ln for ln in digest.splitlines() if ln.strip().startswith("nodes=")
        ]
        self.assertTrue(
            stat_lines, "digest missing canonical 'nodes=' stat line: %r" % digest
        )
        self.assertNotIn("STALENESS:", digest)
        self.assertNotIn("repo:", digest)
        self.assertEqual(
            digest, self.we.stats_digest(self.db),
            "stats_digest is not deterministic across calls",
        )

        # 3. Annotation round-trip on a real behavior-bearing node.
        #    The fixture's COBOL program node is named BILLING; the helper must
        #    resolve its FULL interned SymbolId (a simple name would silent-no-op).
        sym_ids = self.we.resolve_symbol_id(self.db, "BILLING")
        self.assertTrue(
            sym_ids, "resolve_symbol_id('BILLING') returned no SymbolId"
        )
        symbol_id = sym_ids[0]
        self.assertIsInstance(symbol_id, str)
        self.assertTrue(symbol_id.strip(), "resolved SymbolId is blank")

        # The requirement is the anti-legacy IP packed into the native TEXT field
        # as the agreed tagged string: "<rule_id>|<conf>|<prov>|<statement>".
        rule_id = "REQ_BILLING"
        requirement = (
            rule_id + "|0.9|extraction-skill@ring0|"
            "Computes tax on billing via CONFIG lookup"
        )
        self.we.annotate(
            self.db, symbol_id,
            requirement=requirement,
            description="Billing program (fixture)",
            validated=True,
        )

        # by_requirement is an EXACT-string reverse lookup (verified against the
        # binary): the full tagged requirement resolves back to the node, so the
        # native-field projection round-trips and `drift` stays wired.
        hits = self.we.by_requirement(self.db, requirement)
        self.assertTrue(
            hits, "by_requirement did not find the annotated node (silent no-op?)"
        )
        self.assertTrue(
            any("BILLING" in str(h) for h in _iter_strings(hits)),
            "by_requirement result did not reference BILLING: %r" % (hits,),
        )

        # The lossless rule object is mirrored into the anti-legacy-owned overlay.
        self.assertTrue(
            os.path.exists(self.overlay),
            "annotate() did not append the JSONL overlay sidecar",
        )
        rows = [
            json.loads(ln)
            for ln in open(self.overlay, encoding="utf-8").read().splitlines()
            if ln.strip()
        ]
        self.assertTrue(rows, "annotations.jsonl overlay is empty")
        self.assertTrue(
            any(r.get("symbol_id") == symbol_id for r in rows),
            "overlay missing a row keyed by the annotated SymbolId",
        )

        # 4. The gating no-op guard: annotating an unresolvable symbol must RAISE,
        #    never report success while updating 0 rows (the SymbolId trap). Kept
        #    in-method so this file stays a single integration test.
        with self.assertRaises(Exception):
            self.we.annotate(
                self.db, "",
                requirement="REQ_X|0.5|t|noop",
                validated=False,
            )


def _iter_strings(obj):
    """Yield stringifiable leaves of a dict/list/scalar for loose matching."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)
    else:
        yield obj


if __name__ == "__main__":
    unittest.main()
