#!/usr/bin/env python3
"""Tests for templates/run.py dispatcher path-confinement and exit codes.

The template carries a __PLUGIN_ROOT__ placeholder that anti-legacy:setup
substitutes at init time. These tests materialize a real run.py with the
placeholder pointed at a temp plugin root (containing a scripts/ dir), then
exercise the dispatcher as a subprocess to assert the path-traversal guard
and the established exit-code contract.
"""
import unittest
import tempfile
import shutil
import os
import sys
import subprocess

TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "skills", "setup", "assets", "run.py.tmpl"
)


class TestRunDispatcher(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.plugin_root = os.path.join(self.test_dir, "plugin")
        self.scripts_dir = os.path.join(self.plugin_root, "scripts")
        os.makedirs(self.scripts_dir, exist_ok=True)

        # A real, runnable target script under scripts/.
        self.ok_script = os.path.join(self.scripts_dir, "ok.py")
        with open(self.ok_script, "w") as f:
            f.write("import sys\n")
            f.write("sys.stdout.write('ran:' + ' '.join(sys.argv[1:]))\n")
            f.write("sys.exit(0)\n")

        # A sibling file OUTSIDE scripts/ that a '../' stem would target,
        # to prove traversal is rejected before execution.
        self.outside = os.path.join(self.plugin_root, "outside.py")
        with open(self.outside, "w") as f:
            f.write("import sys\n")
            f.write("sys.stdout.write('ESCAPED')\n")
            f.write("sys.exit(0)\n")

        # Materialize run.py with the placeholder substituted.
        with open(TEMPLATE) as f:
            src = f.read()
        src = src.replace("__PLUGIN_ROOT__", self.plugin_root)
        self.run_py = os.path.join(self.test_dir, "run.py")
        with open(self.run_py, "w") as f:
            f.write(src)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, self.run_py, *args],
            capture_output=True, text=True,
        )

    def test_valid_stem_executes(self):
        r = self._run("ok", "alpha", "beta")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "ran:alpha beta")

    def test_no_arg_usage_exit_2(self):
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr.lower())

    def test_unknown_stem_exit_2(self):
        r = self._run("does_not_exist")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such script", r.stderr.lower())

    def test_parent_traversal_stem_rejected_exit_2(self):
        # '../outside' would resolve to plugin/outside.py and escape scripts/.
        r = self._run("../outside")
        self.assertEqual(r.returncode, 2)
        self.assertIn("illegal script stem", r.stderr.lower())
        self.assertNotIn("ESCAPED", r.stdout)

    def test_separator_stem_rejected_exit_2(self):
        # A nested separator stem must be rejected even if it would resolve
        # inside scripts/ (no subdirectory dispatch is permitted).
        r = self._run("sub/ok")
        self.assertEqual(r.returncode, 2)
        self.assertIn("illegal script stem", r.stderr.lower())

    def test_dotdot_only_stem_rejected_exit_2(self):
        r = self._run("..")
        self.assertEqual(r.returncode, 2)
        self.assertIn("illegal script stem", r.stderr.lower())


if __name__ == "__main__":
    unittest.main()
