#!/usr/bin/env python3
import unittest
import tempfile
import shutil
import os
import sys
import subprocess

class TestAuditReport(unittest.TestCase):
    def setUp(self):
        # Create a temp directory for the workspace
        self.workspace = tempfile.mkdtemp(prefix="anti-legacy-audit-test-")
        
        self.project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        self.scripts_dir = os.path.join(self.project_root, "scripts")

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _run_script(self, script, *args):
        """Run a script and return the subprocess result."""
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
            text=True
        )
        return result

    def test_audit_report_generation(self):
        # 1. Initialize manifest
        init_res = self._run_script(
            "manifest.py", "init",
            "--name", "audit-test-project",
            "--target-stack", "go"
        )
        self.assertEqual(init_res.returncode, 0, f"Init failed: {init_res.stderr}")

        # 2. Advance phase to survey
        adv_res = self._run_script("manifest.py", "advance", "survey")
        self.assertEqual(adv_res.returncode, 0)

        # 3. Register a dummy legacy-graph artifact. It must live where the manifest
        # resolves a relative --path: anchored under .anti-legacy/. Writing it there
        # means register captures a checksum and the gate's content-verify (file must
        # exist + checksum must match) passes against real evidence.
        #
        # WF1: the survey step now produces a deterministic wicked-estate `stats`
        # DIGEST (a checksummable TEXT artifact) as the `legacy-graph` evidence,
        # replacing the old `legacy_graph.json` intermediate. manifest register is
        # filename/format-agnostic — the audit trail just echoes whatever path/format
        # was registered — so this test exercises the new digest convention.
        dummy_file = os.path.join(self.workspace, ".anti-legacy", "legacy-graph.digest.txt")
        with open(dummy_file, "w") as f:
            f.write("nodes: 0\nedges: 0\n")

        reg_res = self._run_script(
            "manifest.py", "register", "legacy-graph",
            "--path", "legacy-graph.digest.txt",
            "--format", "text",
            "--produced-by", "anti-legacy:survey"
        )
        self.assertEqual(reg_res.returncode, 0, f"Register failed: {reg_res.stderr}")

        # 4. Sign off design gate (GATE_1_DESIGN), citing the registered evidence
        gate_res = self._run_script(
            "manifest.py", "gate", "GATE_1_DESIGN",
            "--opinion", "passed",
            "--evaluator", "compliance-lead",
            "--rationale", "Verified security boundaries",
            "--evidence", "legacy-graph"
        )
        self.assertEqual(gate_res.returncode, 0, f"Gate failed: {gate_res.stderr}")

        # 5. Compile the audit report
        report_res = self._run_script("manifest.py", "audit-report")
        self.assertEqual(report_res.returncode, 0, f"Audit report failed: {report_res.stderr}")

        # 6. Verify report file exists and has correct content
        report_path = os.path.join(self.workspace, ".anti-legacy", "audit_report.md")
        self.assertTrue(os.path.exists(report_path), "audit_report.md was not created")

        with open(report_path, "r") as f:
            content = f.read()

        self.assertIn("# Compliance Audit Report — audit-test-project", content)
        self.assertIn("Chronological Audit Trail", content)
        
        # Verify event details
        self.assertIn("Phase advanced from `uninitialized` to `survey`", content)
        self.assertIn("Artifact `legacy-graph` registered at path `legacy-graph.digest.txt`", content)
        self.assertIn("Gate **GATE_1_DESIGN** signed off as **PASSED**", content)
        self.assertIn("Rationale: *Verified security boundaries*", content)
        self.assertIn("compliance-lead", content)

if __name__ == "__main__":
    unittest.main()
