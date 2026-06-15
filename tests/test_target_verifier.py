#!/usr/bin/env python3
import unittest
import sys
import os
import json
import tempfile
import shutil
import subprocess

# Adjust path to find target_verifier (moved to demo/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../demo')))

from target_verifier import TargetVerifier


class TestTargetVerifierPythonPass(unittest.TestCase):
    """Python stack: valid Python file → PASS evidence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a valid Python file in the workspace
        with open(os.path.join(self.tmpdir, "valid_module.py"), 'w') as f:
            f.write("def hello():\n    return 'world'\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_compile_returns_zero(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        result = verifier.detect_and_compile()
        self.assertEqual(result["exit_code"], 0,
                         f"Valid Python should compile cleanly, stderr: {result['stderr']}")

    def test_evidence_pass(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        build_result = verifier.detect_and_compile()
        evidence_path = os.path.join(self.tmpdir, "evidence.json")
        success = verifier.record_evidence(evidence_path, build_result)
        self.assertTrue(success, "record_evidence should return True for passing build")

        with open(evidence_path, 'r') as f:
            evidence = json.load(f)
        self.assertEqual(evidence["status"], "PASS",
                         "Evidence status should be PASS for valid code")


class TestTargetVerifierPythonFail(unittest.TestCase):
    """Python stack: file with syntax errors → FAIL evidence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a Python file with syntax errors
        with open(os.path.join(self.tmpdir, "broken_module.py"), 'w') as f:
            f.write("def broken(\n    this is not valid python\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_compile_returns_nonzero(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        result = verifier.detect_and_compile()
        self.assertNotEqual(result["exit_code"], 0,
                            "Broken Python should fail compilation")

    def test_evidence_fail(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        build_result = verifier.detect_and_compile()
        evidence_path = os.path.join(self.tmpdir, "evidence.json")
        success = verifier.record_evidence(evidence_path, build_result)
        self.assertFalse(success, "record_evidence should return False for failing build")

        with open(evidence_path, 'r') as f:
            evidence = json.load(f)
        self.assertEqual(evidence["status"], "FAIL",
                         "Evidence status should be FAIL for broken code")


class TestTargetVerifierEvidenceStructure(unittest.TestCase):
    """Evidence JSON has correct structure (scope, phase, claim, status, evidence)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(self.tmpdir, "ok.py"), 'w') as f:
            f.write("x = 1\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_evidence_has_required_keys(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        build_result = verifier.detect_and_compile()
        evidence_path = os.path.join(self.tmpdir, "evidence.json")
        verifier.record_evidence(evidence_path, build_result)

        with open(evidence_path, 'r') as f:
            evidence = json.load(f)

        self.assertIn("scope", evidence, "Evidence must contain 'scope'")
        self.assertIn("phase", evidence, "Evidence must contain 'phase'")
        self.assertIn("claim", evidence, "Evidence must contain 'claim'")
        self.assertIn("status", evidence, "Evidence must contain 'status'")
        self.assertIn("evidence", evidence, "Evidence must contain 'evidence' block")

    def test_evidence_field_values(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        build_result = verifier.detect_and_compile()
        evidence_path = os.path.join(self.tmpdir, "evidence.json")
        verifier.record_evidence(evidence_path, build_result)

        with open(evidence_path, 'r') as f:
            evidence = json.load(f)

        self.assertEqual(evidence["scope"], "build", "scope should be 'build'")
        self.assertEqual(evidence["phase"], "compilation", "phase should be 'compilation'")
        self.assertEqual(evidence["claim"], "target-compiles", "claim should be 'target-compiles'")

    def test_evidence_nested_block(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        build_result = verifier.detect_and_compile()
        evidence_path = os.path.join(self.tmpdir, "evidence.json")
        verifier.record_evidence(evidence_path, build_result)

        with open(evidence_path, 'r') as f:
            evidence = json.load(f)

        nested = evidence["evidence"]
        self.assertIn("command", nested, "Nested evidence must have 'command'")
        self.assertIn("exit_code", nested, "Nested evidence must have 'exit_code'")
        self.assertIn("stdout_snippet", nested, "Nested evidence must have 'stdout_snippet'")
        self.assertIn("stderr_snippet", nested, "Nested evidence must have 'stderr_snippet'")

    def test_evidence_creates_parent_dirs(self):
        verifier = TargetVerifier(self.tmpdir, "python")
        build_result = verifier.detect_and_compile()
        evidence_path = os.path.join(self.tmpdir, "deep", "nested", "evidence.json")
        verifier.record_evidence(evidence_path, build_result)
        self.assertTrue(os.path.exists(evidence_path),
                        "record_evidence should create parent directories")


class TestTargetVerifierUnknownStack(unittest.TestCase):
    """Unknown stack exits gracefully."""

    def test_unknown_stack_exits_zero(self):
        demo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../demo'))
        tmpdir = tempfile.mkdtemp()
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(demo_dir, 'target_verifier.py'),
                 '--workspace', tmpdir,
                 '--stack', 'brainfuck',
                 '--evidence', os.path.join(tmpdir, 'evidence.json')],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            self.assertEqual(result.returncode, 0,
                             "Unknown stack should exit with code 0 (graceful)")
            self.assertIn("Unknown target stack", result.stderr,
                          "Should print warning about unknown stack")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestTargetVerifierCLI(unittest.TestCase):
    """CLI args work (--workspace, --stack, --evidence)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.demo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../demo'))
        # Create a valid Python file
        with open(os.path.join(self.tmpdir, "hello.py"), 'w') as f:
            f.write("print('hello')\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cli_pass_flow(self):
        evidence_path = os.path.join(self.tmpdir, "out", "evidence.json")
        result = subprocess.run(
            [sys.executable, os.path.join(self.demo_dir, 'target_verifier.py'),
             '--workspace', self.tmpdir,
             '--stack', 'python',
             '--evidence', evidence_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"CLI should exit 0 for valid Python, stderr: {result.stderr}")
        self.assertTrue(os.path.exists(evidence_path),
                        "Evidence file should be created by CLI")

    def test_cli_fail_flow(self):
        # Put a broken file in workspace
        with open(os.path.join(self.tmpdir, "bad.py"), 'w') as f:
            f.write("def (:\n")
        evidence_path = os.path.join(self.tmpdir, "fail_evidence.json")
        result = subprocess.run(
            [sys.executable, os.path.join(self.demo_dir, 'target_verifier.py'),
             '--workspace', self.tmpdir,
             '--stack', 'python',
             '--evidence', evidence_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertNotEqual(result.returncode, 0,
                            "CLI should exit non-zero for broken Python")
        self.assertTrue(os.path.exists(evidence_path),
                        "Evidence file should still be created even on failure")
        with open(evidence_path, 'r') as f:
            evidence = json.load(f)
        self.assertEqual(evidence["status"], "FAIL",
                         "Evidence should record FAIL status")

    def test_cli_missing_args(self):
        result = subprocess.run(
            [sys.executable, os.path.join(self.demo_dir, 'target_verifier.py')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertNotEqual(result.returncode, 0,
                            "CLI should fail when required args are missing")


if __name__ == '__main__':
    unittest.main()
