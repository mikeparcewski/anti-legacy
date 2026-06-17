"""Regression for anti-legacy:skill-forge — generates per-domain build SKILL.md files from the
blueprint + requirements graph, with component specs, embedded rules, and the §2 legacy trace.

Hermetic: chdir to a tmp workspace with fixture blueprint/graph/config; skill_forge's loaders are
cwd-anchored. Imported directly (conftest puts skills/*/scripts on sys.path).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts")))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "skill-forge", "scripts")))

import skill_forge  # noqa: E402


class SkillForgeTest(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="anti-legacy-forge-"))
        self._cwd = os.getcwd()
        os.chdir(self.ws)
        os.makedirs(os.path.join(self.ws, ".anti-legacy", "requirements"))

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.ws, ignore_errors=True)

    def _fixture(self):
        J = lambda p, o: json.dump(o, open(os.path.join(self.ws, ".anti-legacy", p), "w"))
        J("config.json", {"project_name": "demo", "target_stack": "java"})
        J("requirements/blueprint.json", {"project": "demo", "target_stack": "java", "style": "hexagonal",
          "domains": {"billing": {"package": "com.demo.billing", "components": {
              "REQ-001": {"target_file": "InterestService.java", "class_name": "InterestService",
                          "component_type": "service", "api": {"method": "POST", "path": "/interest"},
                          "methods": [{"name": "compute", "signature": "Money compute(Req r)"}]}},
              "entities": {"Account": {"table_name": "account", "columns": [
                  {"name": "balance", "type": "DECIMAL(11,2)", "source_type": "COMP-3 PIC 9(9)V99"}]}}}}})
        J("requirements/requirements_graph.json", {"metadata": {}, "domains": {"billing": {"requirements": {
            "REQ-001": {"title": "Interest", "description": "d", "legacy_components": ["COBOL/CBINT.cbl"],
                        "data_access": ["Account"], "dependencies": [], "status": "active",
                        "business_rules": [{"id": "RULE-001", "statement": "interest = bal*apr/365", "confidence": 0.9}],
                        "validations": [{"id": "VAL-001", "statement": "apr>0", "field": "apr"}],
                        "error_paths": [{"id": "ERR-001", "statement": "reject apr<=0", "code": "ERR-APR"}]}},
            "entities": {}}}})

    def test_generates_per_domain_build_skill(self):
        self._fixture()
        written, index_path = skill_forge.generate()
        self.assertEqual(len(written), 1)
        self.assertTrue(os.path.isfile(index_path))
        skill = open(os.path.join(self.ws, ".anti-legacy", "generated-skills",
                                  "build-billing", "SKILL.md"), encoding="utf-8").read()
        # it's a real, agent-followable skill (frontmatter name)
        self.assertIn('name: "anti-legacy:build-billing"', skill)
        # blueprint component spec baked in
        self.assertIn("InterestService", skill)
        self.assertIn("/interest", skill)
        # requirement behavior baked in (rule + validation + error path)
        self.assertIn("RULE-001", skill)
        self.assertIn("VAL-001", skill)
        self.assertIn("ERR-001", skill)
        self.assertIn("@ImplementsRule", skill)
        # §2 traceability: legacy provenance present
        self.assertIn("COBOL/CBINT.cbl", skill)
        # parity grounding: COMP-3 source type surfaced
        self.assertIn("COMP-3", skill)

    def test_no_blueprint_exits_nonzero(self):
        # no blueprint fixture written -> refuse (don't generate hollow skills)
        with self.assertRaises(SystemExit) as cm:
            skill_forge.generate()
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
