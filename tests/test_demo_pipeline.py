#!/usr/bin/env python3
"""
End-to-end pipeline demo — runs the first 5 phases of the anti-legacy
pipeline against the demo COBOL programs and verifies each output.

Phases exercised:
  1. Setup    → manifest.json
  2. Survey   → wicked-estate index → legacy-graph.digest.txt (checksummed seam)
  3. Analyze  → read the wicked-estate code graph via the helper/CLI, then
                graph_normalizer → draft requirements scaffold
  4. Generate → packet_generator → review_packet.md
  5. Verify   → demo/target_verifier (on a trivial Python target)

WF1 (§H/§I) rewire: the code-graph engine is now `wicked-estate`.
graph_builder.py and the `legacy_graph.json` *survey output* are gone.

  * test_02 (survey) drives `wicked-estate index` and registers the
    DETERMINISTIC stats digest as the checksummed `legacy-graph` evidence
    (replaces graph_builder.py → legacy_graph.json → register json).
  * test_03 (analyze) READS the code graph via the wicked-estate helper/CLI
    (replaces reading legacy_graph.json directly), then still drives the
    unchanged graph_normalizer to produce requirements_graph.json for the
    downstream packet test.
  * test_08 asserts the digest seam is DETERMINISTIC (re-index → byte-identical
    canonical block → stable SHA-256), the structural property §H/§I6 drift
    detection relies on (replaces the old structural-vs-functional comparison).

graph_normalizer is NOT deleted in WF1 (the §I5 re-think is a later WF); it still
consumes a code-graph JSON. graph_builder, its former producer, IS deleted, so
that normalizer input is materialized inline here as DEMO_LEGACY_GRAPH — the
stable replacement seam for the deleted producer.

The graph tests gate on the wicked-estate binary being resolvable (the helper's
resolve_binary() or the documented resolution chain) and SKIP cleanly where the
binary is not installed, so the suite stays green in CI without the engine.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


# ---------------------------------------------------------------------------
# wicked-estate binary resolution + skip gate.
#
# Mirrors scripts/wicked_estate.py resolve_binary() priority (config
# wicked_estate_path → WICKED_ESTATE_PATH env → PATH → wicked-estate fallback).
# Prefers the helper's own resolver when the WF1 helper module is importable so
# the test and production code agree on the binary; otherwise falls back to the
# same documented chain inline. Returns (binary_path_or_None, helper_or_None).
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WICKED_ESTATE_FALLBACK = (
    ""
)

# Volatile stats lines stripped to make the digest deterministic / checksummable.
_VOLATILE_PREFIXES = ("repo:", "STALENESS:", "db=")


def _resolve_via_helper():
    """Use scripts.wicked_estate.resolve_binary() if the WF1 helper has landed."""
    try:
        if PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        from scripts import wicked_estate as we  # type: ignore

        binary = we.resolve_binary()
        if binary and os.path.exists(binary) and os.access(binary, os.X_OK):
            return binary, we
    except Exception:
        pass
    return None, None


def _resolve_inline(config_path=None):
    """Documented resolution chain, used when the helper module is absent."""
    # (1) config.json wicked_estate_path
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            cand = cfg.get("wicked_estate_path")
            if cand and os.path.exists(cand) and os.access(cand, os.X_OK):
                return cand
        except Exception:
            pass
    # (2) WICKED_ESTATE_PATH env
    env_cand = os.environ.get("WICKED_ESTATE_PATH")
    if env_cand and os.path.exists(env_cand) and os.access(env_cand, os.X_OK):
        return env_cand
    # (3) PATH
    which = shutil.which("wicked-estate")
    if which:
        return which
    # (4) wicked-estate fallback (known-good v0.0.1 used by the spike)
    if os.path.exists(WICKED_ESTATE_FALLBACK) and os.access(WICKED_ESTATE_FALLBACK, os.X_OK):
        return WICKED_ESTATE_FALLBACK
    return None


def resolve_wicked_estate(config_path=None):
    """Return (binary_path_or_None, helper_module_or_None)."""
    binary, helper = _resolve_via_helper()
    if binary:
        return binary, helper
    return _resolve_inline(config_path), None


WICKED_ESTATE_BIN, WICKED_ESTATE_HELPER = resolve_wicked_estate()
HAVE_WICKED_ESTATE = WICKED_ESTATE_BIN is not None
SKIP_REASON = (
    "wicked-estate binary not resolvable "
    "(set wicked_estate_path / WICKED_ESTATE_PATH or install wicked-estate)"
)


def _canonical_digest(stats_text):
    """Strip the volatile lines so the digest is deterministic / checksummable."""
    out = []
    for raw in stats_text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if any(stripped.startswith(p) for p in _VOLATILE_PREFIXES):
            continue
        out.append(raw.rstrip())
    return "\n".join(out) + "\n"


class TestEndToEndPipeline(unittest.TestCase):
    """Run the pipeline against the demo COBOL programs."""

    # graph_normalizer is NOT deleted in WF1 and still consumes a code-graph
    # JSON; graph_builder (its former producer) IS deleted, so the normalizer
    # input is materialized inline here. This is a FIXTURE for the deleted
    # producer, NOT the survey output (the survey output is the wicked-estate
    # DB + the digest seam, see test_02). The structural call/copy/sql graph of
    # the demo COBOL is fixed and deterministic.
    DEMO_LEGACY_GRAPH = {
        "applications": {
            "billing-system": {
                "path": "demo/legacy-src",
                "nodes": {
                    "BILLING": {
                        "type": "program", "name": "BILLING",
                        "file_path": "BILLING.cbl",
                        "details": {"language": "cobol"},
                    },
                    "TAXRATES": {
                        "type": "copybook", "name": "TAXRATES",
                        "file_path": "shared/TAXRATES",
                        "details": {"language": "cobol"},
                    },
                    "TAX_CONFIG": {
                        "type": "table", "name": "TAX_CONFIG",
                        "file_path": "database", "details": {},
                    },
                    "CUSTMGR": {
                        "type": "program", "name": "CUSTMGR",
                        "file_path": "CUSTMGR.cbl",
                        "details": {"language": "cobol"},
                    },
                    "PAY-GATE": {
                        "type": "program", "name": "PAY-GATE",
                        "file_path": "PAY-GATE.cbl",
                        "details": {"language": "cobol"},
                    },
                },
                "edges": [
                    {"source": "BILLING", "target": "PAY-GATE", "type": "call"},
                    {"source": "BILLING", "target": "TAXRATES", "type": "copy"},
                    {"source": "BILLING", "target": "TAX_CONFIG", "type": "sql_access"},
                ],
            }
        }
    }

    @classmethod
    def setUpClass(cls):
        cls.project_root = PROJECT_ROOT
        cls.scripts_dir = os.path.join(cls.project_root, "scripts")
        cls.demo_dir = os.path.join(cls.project_root, "demo")
        cls.demo_src = os.path.join(cls.project_root, "demo", "legacy-src")

        # Create temp workspace
        cls.workspace = tempfile.mkdtemp(prefix="anti-legacy-demo-")

        # Init a git repo (required for git-brain)
        cls._git("init")
        cls._git("config", "user.email", "demo@test.com")
        cls._git("config", "user.name", "Demo")
        # Initial commit so branches work
        readme = os.path.join(cls.workspace, "README.md")
        with open(readme, "w") as f:
            f.write("# Demo workspace\n")
        cls._git("add", ".")
        cls._git("commit", "-m", "init")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workspace, ignore_errors=True)

    @classmethod
    def _git(cls, *args):
        subprocess.run(
            ["git"] + list(args),
            cwd=cls.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def _run_script(self, script, *args):
        """Run a script and return (returncode, stdout, stderr)."""
        _legacy = os.path.join(self.scripts_dir, script)
        if os.path.isfile(_legacy):
            cmd = [sys.executable, _legacy] + list(args)
        else:  # migrated — leaf (skills/*/scripts) run as bare module, else core
            import glob as _glob
            _stem = script[:-3] if script.endswith('.py') else script
            _leaf = _glob.glob(os.path.join(os.path.dirname(self.scripts_dir), 'skills', '*', 'scripts', _stem + '.py'))
            cmd = [sys.executable, '-m', (_stem if _leaf else 'antilegacy_core.' + _stem)] + list(args)
        result = subprocess.run(
            cmd,
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result

    def _run_demo_script(self, script, *args):
        """Run a script from the demo/ directory and return the result."""
        cmd = [sys.executable, os.path.join(self.demo_dir, script)] + list(args)
        result = subprocess.run(
            cmd,
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result

    # ------------------------------------------------------------------
    # wicked-estate CLI helpers (the read-side STRUCTURE always comes from
    # CLI index/stats/query — never raw SQLite — per BACKLOG §H).
    # ------------------------------------------------------------------
    def _we(self, *args, check=True):
        cmd = [WICKED_ESTATE_BIN] + list(args)
        result = subprocess.run(
            cmd,
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
        if check:
            self.assertEqual(
                result.returncode, 0,
                f"wicked-estate {' '.join(args)} failed: {result.stderr}",
            )
        return result

    def _graph_db_path(self):
        return os.path.join(
            self.workspace, ".anti-legacy", "graphs", "billing-system.db"
        )

    def _index_demo(self, db=None):
        """Index the demo COBOL into a per-app DB (idempotent) and return its path."""
        if db is None:
            db = self._graph_db_path()
        os.makedirs(os.path.dirname(db), exist_ok=True)
        if WICKED_ESTATE_HELPER is not None and hasattr(WICKED_ESTATE_HELPER, "index"):
            # Exercise the WF1 helper's index() when it is available.
            WICKED_ESTATE_HELPER.index([("billing-system", self.demo_src)], db)
        else:
            self._we("index", self.demo_src, "--db", db)
        self.assertTrue(os.path.exists(db), "wicked-estate DB not created")
        return db

    def _digest_for(self, db):
        """Deterministic, checksummable stats digest for a DB."""
        if WICKED_ESTATE_HELPER is not None and hasattr(WICKED_ESTATE_HELPER, "stats_digest"):
            blk = WICKED_ESTATE_HELPER.stats_digest(db)
            return blk if blk.endswith("\n") else blk + "\n"
        return _canonical_digest(self._we("stats", "--db", db).stdout)

    # ------------------------------------------------------------------
    # Phase 1: Setup (manifest init)
    # ------------------------------------------------------------------
    def test_01_setup_initializes_workspace(self):
        """Phase 1: manifest init creates the workspace structure."""
        result = self._run_script(
            "manifest.py", "init",
            "--name", "demo-billing",
            "--target-stack", "java",
        )
        self.assertEqual(result.returncode, 0, f"Init failed: {result.stderr}")

        manifest_path = os.path.join(self.workspace, ".anti-legacy", "manifest.json")
        self.assertTrue(os.path.exists(manifest_path), "manifest.json not created")

        with open(manifest_path) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["phase"]["current"], "uninitialized",
                         "Init starts as uninitialized")
        self.assertEqual(manifest["project"]["target_stack"], "java",
                         "Target stack should be set")

        # Advance to survey
        adv = self._run_script("manifest.py", "advance", "survey")
        self.assertEqual(adv.returncode, 0, f"Advance failed: {adv.stderr}")

    # ------------------------------------------------------------------
    # Phase 2: Survey — wicked-estate index → deterministic digest evidence
    # ------------------------------------------------------------------
    @unittest.skipUnless(HAVE_WICKED_ESTATE, SKIP_REASON)
    def test_02_survey_indexes_with_wicked_estate(self):
        """Phase 2: survey indexes the COBOL source with `wicked-estate index`
        and registers the deterministic stats digest as the checksummed
        legacy-graph evidence (replaces graph_builder.py → legacy_graph.json)."""
        db = self._index_demo()

        # The indexed graph must contain our demo programs (STRUCTURE from CLI).
        custmgr = self._we("query", "CUSTMGR", "--db", db)
        self.assertIn("CUSTMGR", custmgr.stdout, "CUSTMGR not found in graph")
        self.assertIn("CUSTMGR.cbl", custmgr.stdout,
                      "CUSTMGR missing file provenance in graph")
        self.assertIn("BILLING", self._we("query", "BILLING", "--db", db).stdout,
                      "BILLING not found in graph")
        self.assertIn("PAY-GATE", self._we("query", "PAY-GATE", "--db", db).stdout,
                      "PAY-GATE not found in graph")

        # BILLING -> PAY-GATE call relationship is captured: PAY-GATE's
        # dependents (blast-radius) include the BILLING caller.
        pg_blast = self._we("blast-radius", "PAY-GATE", "--db", db)
        self.assertIn("BILLING", pg_blast.stdout,
                      f"BILLING->PAY-GATE call edge missing. Blast: {pg_blast.stdout}")

        # Build the deterministic, checksummable digest (volatile lines stripped).
        digest_block = self._digest_for(db)
        self.assertIn("nodes=", digest_block, "Digest missing node count")
        self.assertNotIn("repo:", digest_block, "Digest leaks volatile repo line")
        self.assertNotIn("STALENESS:", digest_block, "Digest leaks STALENESS line")

        # Write the thin-seam evidence: .anti-legacy/legacy-graph.digest.txt,
        # one "# app:" header block per source repo (the survey convention).
        digest_path = os.path.join(
            self.workspace, ".anti-legacy", "legacy-graph.digest.txt"
        )
        with open(digest_path, "w") as f:
            f.write("# app: billing-system\n")
            f.write(digest_block)
        self.assertTrue(os.path.exists(digest_path), "digest evidence not written")

        # Register the digest as the checksummed legacy-graph evidence
        # (text format, NO schema — the JSON blob + code-graph.schema.json are gone).
        reg = self._run_script(
            "manifest.py", "register", "legacy-graph",
            "--path", "legacy-graph.digest.txt",
            "--format", "text",
            "--produced-by", "anti-legacy:survey",
            "--status", "final",
        )
        self.assertEqual(reg.returncode, 0, f"Register failed: {reg.stderr}")

        # The registered artifact must carry a SHA-256 checksum (the gate seam),
        # and must NOT be the deleted json blob.
        with open(os.path.join(self.workspace, ".anti-legacy", "manifest.json")) as f:
            manifest = json.load(f)
        artifact = manifest.get("artifacts", {}).get("legacy-graph", {})
        self.assertEqual(artifact.get("format"), "text",
                         "legacy-graph evidence should be the text digest, not json")
        self.assertTrue(artifact.get("checksum"),
                        "legacy-graph digest must be checksummed (the gate/audit seam)")
        # The deleted intermediate must NOT be produced by survey.
        self.assertFalse(
            os.path.exists(os.path.join(self.workspace, "legacy_graph.json")),
            "survey must not emit the deleted legacy_graph.json intermediate",
        )

    # ------------------------------------------------------------------
    # Phase 3: Analyze — read the wicked-estate graph, then normalize
    # ------------------------------------------------------------------
    @unittest.skipUnless(HAVE_WICKED_ESTATE, SKIP_REASON)
    def test_03_analyze_reads_graph_and_normalizes(self):
        """Phase 3: analyze READS the code graph via the wicked-estate
        helper/CLI (replaces reading legacy_graph.json directly), confirming
        every demo program is a findable node with file provenance — then runs
        the unchanged graph_normalizer to draft the requirements scaffold."""
        db = self._index_demo()

        # READ the graph via the helper (preferred) or the CLI — the repoint off
        # legacy_graph.json. Every demo program must be findable with provenance.
        for prog, src_file in [
            ("CUSTMGR", "CUSTMGR.cbl"),
            ("BILLING", "BILLING.cbl"),
            ("PAY-GATE", "PAY-GATE.cbl"),
        ]:
            if WICKED_ESTATE_HELPER is not None and hasattr(WICKED_ESTATE_HELPER, "query"):
                blob = json.dumps(WICKED_ESTATE_HELPER.query(db, prog))
            else:
                blob = self._we("query", prog, "--db", db).stdout
            self.assertIn(prog, blob, f"{prog} not found via the graph read path")
            self.assertIn(src_file, blob,
                          f"{prog} missing file provenance — traceability invariant")

        # Dependency structure is reachable from the graph: BILLING depends on
        # PAY-GATE (the call edge), surfaced by blast-radius on the callee.
        self.assertIn("BILLING", self._we("blast-radius", "PAY-GATE", "--db", db).stdout,
                      "BILLING dependency on PAY-GATE not reachable via the graph")

        # graph_normalizer is unchanged in WF1 and still consumes a code-graph
        # JSON; its former producer (graph_builder) is deleted, so feed it the
        # inline fixture. This produces requirements_graph.json for phase 4.
        graph_path = os.path.join(self.workspace, "legacy_graph.json")
        with open(graph_path, "w") as f:
            json.dump(self.DEMO_LEGACY_GRAPH, f, indent=2)
        req_path = os.path.join(self.workspace, "requirements_graph.json")

        result = self._run_script(
            "graph_normalizer.py", "--input", graph_path, "--output", req_path,
        )
        self.assertEqual(result.returncode, 0, f"Normalizer failed: {result.stderr}")
        self.assertTrue(os.path.exists(req_path), "requirements_graph.json not created")

        with open(req_path) as f:
            reqs = json.load(f)
        domains = reqs.get("domains", {})
        self.assertTrue(len(domains) > 0, "No domains generated")

        all_req_ids = []
        for domain_data in domains.values():
            all_req_ids += list(domain_data.get("requirements", {}).keys())
        self.assertTrue(any("CUSTMGR" in r for r in all_req_ids),
                        f"No requirement for CUSTMGR. Got: {all_req_ids}")
        self.assertTrue(any("BILLING" in r for r in all_req_ids),
                        f"No requirement for BILLING. Got: {all_req_ids}")

        # Each requirement carries legacy_components traceability.
        for domain_data in domains.values():
            for req_id, req in domain_data.get("requirements", {}).items():
                self.assertTrue(req.get("legacy_components"),
                                f"Requirement {req_id} missing legacy_components")

    # ------------------------------------------------------------------
    # Phase 4: Review Packet (packet_generator) — UNCHANGED
    # ------------------------------------------------------------------
    def test_04_review_packet_generates_markdown(self):
        """Phase 4: packet_generator compiles a Markdown review packet."""
        req_path = os.path.join(self.workspace, "requirements_graph.json")
        packet_path = os.path.join(self.workspace, "review_packet.md")

        # When the graph phases were skipped (no wicked-estate binary), the
        # requirements graph may not exist — normalize the fixture so this
        # unchanged phase still has its input regardless of the skip gate.
        if not os.path.exists(req_path):
            graph_path = os.path.join(self.workspace, "legacy_graph.json")
            with open(graph_path, "w") as f:
                json.dump(self.DEMO_LEGACY_GRAPH, f, indent=2)
            norm = self._run_script(
                "graph_normalizer.py", "--input", graph_path, "--output", req_path,
            )
            self.assertEqual(norm.returncode, 0, f"Normalizer failed: {norm.stderr}")

        result = self._run_script(
            "packet_generator.py",
            "--input", req_path,
            "--output", packet_path,
        )
        self.assertEqual(result.returncode, 0, f"Packet gen failed: {result.stderr}")
        self.assertTrue(os.path.exists(packet_path), "review_packet.md not created")

        with open(packet_path) as f:
            packet = f.read()

        # Must have key sections
        self.assertIn("# Digital Review Packet", packet, "Missing title")
        self.assertIn("Architecture Overview", packet, "Missing arch overview")
        self.assertIn("```mermaid", packet, "Missing Mermaid diagram")
        self.assertIn("Rigid Sign-off Gate Checklist", packet, "Missing gate checklist")
        self.assertIn("GATE_1_DESIGN", packet, "Missing GATE_1")
        self.assertIn("GATE_4_UAT", packet, "Missing GATE_4")

        # Must reference our requirements
        self.assertIn("Domain", packet, "No domain sections found")

        # Mermaid should have our programs
        self.assertIn("CUSTMGR", packet, "CUSTMGR not in review packet")
        self.assertIn("BILLING", packet, "BILLING not in review packet")

    # ------------------------------------------------------------------
    # Phase 5: Target Verifier (on a trivial Python target)
    # ------------------------------------------------------------------
    def test_05_target_verifier_passes_valid_code(self):
        """Phase 5: target_verifier compiles valid Python and records PASS evidence."""
        target_dir = os.path.join(self.workspace, "target")
        os.makedirs(target_dir, exist_ok=True)

        # Write a valid Python file
        with open(os.path.join(target_dir, "billing_service.py"), "w") as f:
            f.write("class BillingService:\n")
            f.write("    def calculate_tax(self, amount: float, rate: float) -> float:\n")
            f.write("        return round(amount * rate, 2)\n")

        evidence_path = os.path.join(self.workspace, "evidence", "build-integrity.json")

        result = self._run_demo_script(
            "target_verifier.py",
            "--workspace", target_dir,
            "--stack", "python",
            "--evidence", evidence_path,
        )
        self.assertEqual(result.returncode, 0, f"Verifier failed: {result.stderr}")

        with open(evidence_path) as f:
            evidence = json.load(f)

        self.assertEqual(evidence["status"], "PASS", "Expected PASS verdict")
        self.assertEqual(evidence["scope"], "build")
        self.assertEqual(evidence["phase"], "compilation")

    def test_06_target_verifier_fails_invalid_code(self):
        """Phase 5b: target_verifier detects syntax errors and records FAIL."""
        bad_dir = os.path.join(self.workspace, "target-bad")
        os.makedirs(bad_dir, exist_ok=True)

        with open(os.path.join(bad_dir, "broken.py"), "w") as f:
            f.write("def broken(\n")  # Syntax error
            f.write("    return 42\n")

        evidence_path = os.path.join(self.workspace, "evidence", "build-fail.json")

        result = self._run_demo_script(
            "target_verifier.py",
            "--workspace", bad_dir,
            "--stack", "python",
            "--evidence", evidence_path,
        )
        # Should exit non-zero for failed build
        self.assertNotEqual(result.returncode, 0, "Should fail on invalid code")

        with open(evidence_path) as f:
            evidence = json.load(f)

        self.assertEqual(evidence["status"], "FAIL", "Expected FAIL verdict")

    # ------------------------------------------------------------------
    # Digest seam determinism: re-index → byte-identical canonical digest.
    # Replaces the old structural-vs-functional graph_normalizer comparison;
    # the WF1 structural property is that the checksummed seam is STABLE so the
    # §H/§I6 drift gate is meaningful (a re-index of unchanged source must NOT
    # change the legacy-graph evidence checksum).
    # ------------------------------------------------------------------
    @unittest.skipUnless(HAVE_WICKED_ESTATE, SKIP_REASON)
    def test_08_stats_digest_is_deterministic_across_reindex(self):
        """The deterministic stats digest is byte-stable across a re-index into
        a fresh DB (volatile repo/STALENESS/db lines stripped), so its SHA-256
        is a stable checksum for the legacy-graph evidence seam."""
        db1 = self._index_demo()
        first = self._digest_for(db1)

        # Re-index the SAME source into a SECOND, fresh DB.
        db2 = os.path.join(
            self.workspace, ".anti-legacy", "graphs", "billing-system-2.db"
        )
        self._index_demo(db=db2)
        second = self._digest_for(db2)

        self.assertEqual(
            first, second,
            "stats digest must be deterministic across re-index "
            f"(the seam checksum would be unstable).\n--- first ---\n{first}\n"
            f"--- second ---\n{second}",
        )

        # The digest carries real structure (the demo graph is non-empty).
        self.assertIn("nodes=", first, "digest missing node count")
        self.assertRegex(first, r"nodes=\d+", "digest node count not numeric")

        # A second digest of the SAME db is also identical (idempotent read),
        # which is what makes the registered SHA-256 a stable gate predicate.
        self.assertEqual(first, self._digest_for(db1),
                         "stats digest not idempotent on the same DB")


if __name__ == "__main__":
    unittest.main()
