"""Functional regression tests for antilegacy_core.precheck — the execution-time readiness gate.

Hermetic (mirrors tests/test_manifest.py): each test runs in a fresh tempfile.mkdtemp workspace
with cwd=that dir and PYTHONPATH=the antilegacy_core parent, invoking the CLI via
`python -m antilegacy_core.precheck`. cwd=tmpdir keeps the CLI immune to any ambient
.anti-legacy/config.json (the graph_normalizer hermetic lesson).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


class PrecheckCLITest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="anti-legacy-precheck-")
        self.core_parent = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts"))
        self.env = dict(os.environ, PYTHONPATH=self.core_parent)
        # ISS-22 review: don't inherit an ambient PRECHECK_STRICT — it would flip the lenient
        # tests into strict mode. Strict tests set it (or --strict) explicitly off this base.
        self.env.pop("PRECHECK_STRICT", None)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def _run(self, module, *args):
        return subprocess.run([sys.executable, "-m", module, *args], cwd=self.ws, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _precheck(self, *args):
        return self._run("antilegacy_core.precheck", *args)

    def _manifest(self, *args):
        r = self._run("antilegacy_core.manifest", *args)
        self.assertEqual(r.returncode, 0, "manifest %s failed: %s" % (args, r.stderr))
        return r

    def _write(self, rel, obj):
        path = os.path.join(self.ws, ".anti-legacy", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(obj, str):
                f.write(obj)
            else:
                json.dump(obj, f)
        return path

    def _graph(self, confidence=0.9, with_validations=True):
        rule = {"id": "RULE-001", "statement": "interest = bal * apr / 365"}
        if confidence is not None:
            rule["confidence"] = confidence
        node = {"title": "Compute interest", "description": "daily interest",
                "legacy_components": ["COBOL/CBINT.cbl"], "data_access": [], "dependencies": [],
                "status": "active", "business_rules": [rule],
                "validations": [{"id": "VAL-001", "statement": "apr>0"}] if with_validations else [],
                "error_paths": []}
        return {"metadata": {"migration_mode": "functional"},
                "domains": {"billing": {"requirements": {"REQ-001": node}, "entities": {}}}}

    def _ready_workspace(self, confidence=0.9):
        """A workspace where `precheck deliverables` should be READY."""
        self._manifest("init", "--name", "t", "--target-stack", "java", "--target-path", "./t")
        self._write("legacy-graph.digest.txt", "nodes=10 edges=20\n")
        self._write("requirements/requirements_graph.json", self._graph(confidence=confidence))
        self._write("coverage-report.json", {"coverage": 1.0, "behavior_bearing": 1, "resolved": 1,
                                             "risk_flagged": 0, "unaccounted": 0})
        # register the evidence spine + the derived graph (graph depends_on the spine → ROOT B link)
        self._manifest("register", "legacy-graph", "--path", "legacy-graph.digest.txt",
                       "--format", "text", "--produced-by", "anti-legacy:survey")
        self._manifest("register", "requirements-graph", "--path",
                       "requirements/requirements_graph.json", "--format", "json",
                       "--produced-by", "anti-legacy:graph-translator", "--depends-on", "legacy-graph")

    # -- tests -----------------------------------------------------------------
    def test_blocks_when_no_manifest(self):
        r = self._precheck("deliverables")
        self.assertEqual(r.returncode, 1)
        self.assertIn("manifest", (r.stdout + r.stderr).lower())

    def test_blocks_when_no_requirements_graph(self):
        self._manifest("init", "--name", "t", "--target-stack", "java", "--target-path", "./t")
        r = self._precheck("deliverables")
        self.assertEqual(r.returncode, 1)
        self.assertIn("requirements-graph", (r.stdout + r.stderr))

    def test_ready_passes(self):
        self._ready_workspace()
        r = self._precheck("deliverables")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("READY", r.stdout)

    def test_root_b_reconcile_detects_orphaned_graph(self):
        """The exact GATING_REVIEW ROOT B scenario: derived graph outlives its evidence spine."""
        self._ready_workspace()
        os.remove(os.path.join(self.ws, ".anti-legacy", "legacy-graph.digest.txt"))
        r = self._precheck("deliverables")
        self.assertEqual(r.returncode, 1, "should block when the source evidence vanished")
        self.assertIn("reconcile", (r.stdout + r.stderr).lower())

    def test_root_b_reconcile_detects_drifted_source(self):
        self._ready_workspace()
        # mutate the registered source after its checksum was recorded
        self._write("legacy-graph.digest.txt", "nodes=99 edges=99 TAMPERED\n")
        r = self._precheck("deliverables")
        self.assertEqual(r.returncode, 1)
        self.assertIn("reconcile", (r.stdout + r.stderr).lower())

    def test_c2_blocks_rule_without_confidence(self):
        self._ready_workspace(confidence=None)  # rule carries no confidence
        r = self._precheck("deliverables")
        self.assertEqual(r.returncode, 1, "a confidence-less rule must block, not silently pass")
        self.assertIn("confidence", (r.stdout + r.stderr).lower())

    def test_advisory_never_gates(self):
        self._manifest("init", "--name", "t", "--target-stack", "java", "--target-path", "./t")
        r = self._precheck("deliverables", "--advisory")  # would block, but advisory
        self.assertEqual(r.returncode, 0)
        self.assertIn("NOT READY", r.stdout)

    def test_json_report_is_parseable(self):
        self._ready_workspace()
        r = self._precheck("deliverables", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["phase"], "deliverables")
        self.assertTrue(all("severity" in p and "category" in p for p in payload["probes"]))

    # -- ISS-22: strict mode for unlisted phases -------------------------------
    def _init_only(self):
        """A bare initialized workspace (manifest present, no artifacts)."""
        self._manifest("init", "--name", "t", "--target-stack", "java", "--target-path", "./t")

    def test_unlisted_phase_lenient_warn_passes(self):
        """Default (lenient): an unlisted phase warn-passes (exit 0, READY)."""
        self._init_only()
        r = self._precheck("some-brand-new-phase")  # no PHASE_READINESS profile
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("READY", r.stdout)
        self.assertIn("no readiness profile", (r.stdout + r.stderr).lower())

    def test_unlisted_phase_strict_flag_blocks(self):
        """--strict turns the unlisted-phase warn-pass into a hard BLOCK (exit 1)."""
        self._init_only()
        r = self._precheck("some-brand-new-phase", "--strict")
        self.assertEqual(r.returncode, 1, "an unlisted phase must BLOCK under --strict")
        out = (r.stdout + r.stderr).lower()
        self.assertIn("strict", out)
        self.assertIn("no phase_readiness profile", out)

    def test_unlisted_phase_strict_env_blocks(self):
        """PRECHECK_STRICT env var enables strict mode without the flag."""
        self._init_only()
        env = dict(self.env, PRECHECK_STRICT="1")
        r = subprocess.run([sys.executable, "-m", "antilegacy_core.precheck", "some-brand-new-phase"],
                           cwd=self.ws, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(r.returncode, 1, "PRECHECK_STRICT=1 must BLOCK an unlisted phase")
        self.assertIn("strict", (r.stdout + r.stderr).lower())

    def test_unlisted_phase_strict_advisory_still_exits_zero(self):
        """--strict --advisory: strict marks the BLOCK, but --advisory keeps exit 0
        (advisory always reports rather than gating)."""
        self._init_only()
        r = self._precheck("some-brand-new-phase", "--strict", "--advisory")
        self.assertEqual(r.returncode, 0, "advisory always exits 0 even under strict")
        self.assertIn("NOT READY", r.stdout)

    def test_listed_phase_unaffected_by_strict(self):
        """Strict only changes the UNLISTED-phase case — a listed, ready phase
        still passes under --strict (backward-compatible)."""
        self._ready_workspace()
        r = self._precheck("deliverables", "--strict")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("READY", r.stdout)


if __name__ == "__main__":
    unittest.main()
