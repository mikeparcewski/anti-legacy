"""Functional regression tests for the deliverable_review_worklist leaf script — the
per-deliverable adversarial-critic worklist assembler.

Hermetic (mirrors tests/test_precheck.py): each test runs in a fresh tempfile.mkdtemp
workspace with cwd=that dir and PYTHONPATH=the antilegacy_core parent, invoking the leaf
script as a subprocess by its absolute file path (the dispatcher resolves stems to that
path at runtime; here we exercise the script directly, same as the contract's §8 manual
test). cwd=tmpdir keeps the script immune to any ambient .anti-legacy/config.json.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


class DeliverableReviewWorklistTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="anti-legacy-dlvreview-")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.core_parent = os.path.join(repo, "skills", "anti-legacy-expert", "scripts")
        self.script = os.path.join(repo, "skills", "adversarial-review", "scripts",
                                   "deliverable_review_worklist.py")
        self.env = dict(os.environ, PYTHONPATH=self.core_parent)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def _run(self, *args):
        return subprocess.run([sys.executable, self.script, *args], cwd=self.ws, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _manifest(self, *args):
        r = subprocess.run([sys.executable, "-m", "antilegacy_core.manifest", *args],
                           cwd=self.ws, env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(r.returncode, 0, "manifest %s failed: %s" % (args, r.stderr))
        return r

    def _write(self, rel, obj):
        path = os.path.join(self.ws, ".anti-legacy", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def _graph(self):
        node = {"title": "Compute interest", "legacy_components": ["COBOL/CBINT.cbl"],
                "status": "active",
                "business_rules": [{"id": "RULE-001", "statement": "i = b*r", "confidence": 0.9}]}
        return {"metadata": {"migration_mode": "functional"},
                "domains": {"billing": {"requirements": {"REQ-001": node}, "entities": {}}}}

    def _init(self):
        self._manifest("init", "--name", "t", "--target-stack", "java", "--target-path", "./t")
        self._write(os.path.join("requirements", "requirements_graph.json"), self._graph())

    def _render_and_register(self, art_id, relname, produced_by, body="# stub\n"):
        """Write a deliverable file under deliverables/ and register it in the manifest,
        reusing the same registrar the real renders use."""
        self._write(os.path.join("deliverables", relname), body)
        code = (
            "from antilegacy_core import deliverables as D;"
            "D.register_deliverable(%r, D._abs('.anti-legacy/deliverables/%s'), %r)"
            % (art_id, relname, produced_by)
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=self.ws, env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(r.returncode, 0, "register failed: %s" % r.stderr)

    # -- tests -----------------------------------------------------------------
    def test_no_manifest_exits_2(self):
        r = self._run("--json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("manifest", (r.stdout + r.stderr).lower())

    def test_no_rendered_deliverable_exits_1(self):
        """A workspace with a manifest but zero rendered deliverables has nothing to review."""
        self._init()
        r = self._run("--json")
        self.assertEqual(r.returncode, 1)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["count"], 0)
        # all nine canonical deliverables are still listed (named, not silently absent — §6)
        self.assertEqual(len(payload["items"]), 9)
        self.assertTrue(all(not it["present"] for it in payload["items"]))

    def test_rendered_deliverable_is_reviewable(self):
        self._init()
        self._render_and_register("deliverable-prd", "product-requirements.md", "anti-legacy:prd")
        r = self._run("--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["count"], 1)
        prd = next(it for it in payload["items"] if it["artifact_id"] == "deliverable-prd")
        self.assertTrue(prd["present"])
        self.assertTrue(prd["registered"])
        self.assertEqual(prd["rendered_path"], ".anti-legacy/deliverables/product-requirements.md")
        self.assertEqual(prd["producing_skill"], "anti-legacy:prd")
        # the §2 traceability spine is ALWAYS a critic source for a requirement deliverable
        self.assertIn(".anti-legacy/requirements/requirements_graph.json", prd["source_data"])

    def test_source_pointers_only_include_existing_files(self):
        """A critic must never be sent a dead path — absent source data is filtered out."""
        self._init()
        self._render_and_register("deliverable-prd", "product-requirements.md", "anti-legacy:prd")
        # PRD declares graph + coverage + annotations; only the graph exists here.
        r = self._run("--json")
        prd = next(it for it in json.loads(r.stdout)["items"]
                   if it["artifact_id"] == "deliverable-prd")
        self.assertEqual(prd["source_data"], [".anti-legacy/requirements/requirements_graph.json"])
        self.assertNotIn(".anti-legacy/coverage-report.json", prd["source_data"])
        # now add the coverage report → it appears as a critic source
        self._write("coverage-report.json", {"coverage": 1.0})
        r2 = self._run("--json")
        prd2 = next(it for it in json.loads(r2.stdout)["items"]
                    if it["artifact_id"] == "deliverable-prd")
        self.assertIn(".anti-legacy/coverage-report.json", prd2["source_data"])

    def test_registered_but_missing_file_is_flagged_not_reviewable(self):
        """ROOT-B-style gap: registered in the manifest, file gone from disk → present=False."""
        self._init()
        self._render_and_register("deliverable-prd", "product-requirements.md", "anti-legacy:prd")
        os.remove(os.path.join(self.ws, ".anti-legacy", "deliverables", "product-requirements.md"))
        r = self._run("--json")
        self.assertEqual(r.returncode, 1, "a vanished file leaves nothing to review")
        prd = next(it for it in json.loads(r.stdout)["items"]
                   if it["artifact_id"] == "deliverable-prd")
        self.assertTrue(prd["registered"])
        self.assertFalse(prd["present"])
        # text report names it as a MISSING FILE rather than silently dropping it
        rt = self._run()
        self.assertIn("MISSING FILE", rt.stdout)

    def test_deliverable_filter_scopes_to_one(self):
        self._init()
        self._render_and_register("deliverable-prd", "product-requirements.md", "anti-legacy:prd")
        self._render_and_register("deliverable-risk-log", "risk-log.md", "anti-legacy:risk-log")
        r = self._run("--deliverable", "deliverable-risk-log", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["artifact_id"], "deliverable-risk-log")
        self.assertTrue(payload["items"][0]["living"])

    def test_living_flag_marks_living_deliverables(self):
        self._init()
        self._render_and_register("deliverable-evidence-log", "evidence-log.md",
                                  "anti-legacy:evidence-log")
        r = self._run("--json")
        items = {it["artifact_id"]: it for it in json.loads(r.stdout)["items"]}
        self.assertTrue(items["deliverable-evidence-log"]["living"])
        self.assertFalse(items["deliverable-prd"]["living"])

    def test_suite_matches_deliverables_index(self):
        """Guard the deliberate mirror: the worklist SUITE ids must equal the index SUITE ids,
        so a new deliverable cannot be added to the package yet silently skip adversarial review."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(repo, "skills", "adversarial-review", "scripts"))
        sys.path.insert(0, os.path.join(repo, "skills", "deliverables", "scripts"))
        sys.path.insert(0, self.core_parent)
        try:
            import deliverable_review_worklist as wl
            import deliverables_index as idx
            self.assertEqual([a for a, _, _ in wl.SUITE], [a for a, _, _ in idx.SUITE])
            # every reviewed deliverable has a declared source-data pointer set
            for art_id, _, _ in wl.SUITE:
                self.assertIn(art_id, wl._SOURCES)
        finally:
            for p in (self.core_parent, os.path.join(repo, "skills", "deliverables", "scripts"),
                      os.path.join(repo, "skills", "adversarial-review", "scripts")):
                if p in sys.path:
                    sys.path.remove(p)


if __name__ == "__main__":
    unittest.main()
