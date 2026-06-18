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

    # -- ISS-18: components are emitted in blueprint build_order, NOT dict-insertion order -- #

    def _order_fixture(self):
        """A domain whose components are *declared* C, A, B but whose blueprint build_order
        is A, B, C. A correct forge must emit A→B→C; an insertion-order forge emits C→A→B."""
        J = lambda p, o: json.dump(o, open(os.path.join(self.ws, ".anti-legacy", p), "w"))
        J("config.json", {"project_name": "demo", "target_stack": "java"})

        def comp(cls):
            return {"target_file": "%s.java" % cls, "class_name": cls, "component_type": "service"}
        J("requirements/blueprint.json", {"project": "demo", "target_stack": "java",
          # insertion order C, A, B — deliberately different from build_order
          "domains": {"billing": {"package": "com.demo.billing", "components": {
              "REQ-C": comp("CService"), "REQ-A": comp("AService"), "REQ-B": comp("BService")}}},
          # the dependency-sorted truth the forge must honor
          "build_order": ["REQ-A", "REQ-B", "REQ-C"]})

        def node(rid):
            return {"title": rid, "description": "d", "legacy_components": ["COBOL/%s.cbl" % rid],
                    "data_access": [], "dependencies": [], "status": "active",
                    "business_rules": [{"id": "RULE-%s" % rid, "statement": "s", "confidence": 0.9}]}
        J("requirements/requirements_graph.json", {"metadata": {}, "domains": {"billing": {
            "requirements": {"REQ-C": node("REQ-C"), "REQ-A": node("REQ-A"), "REQ-B": node("REQ-B")},
            "entities": {}}}})

    def test_components_emitted_in_build_order_not_insertion_order(self):
        self._order_fixture()
        skill_forge.generate()
        skill = open(os.path.join(self.ws, ".anti-legacy", "generated-skills",
                                  "build-billing", "SKILL.md"), encoding="utf-8").read()
        # the numbered "Build order" list must read A, B, C — assert by class-name position
        a, b, c = (skill.index("AService"), skill.index("BService"), skill.index("CService"))
        self.assertLess(a, b, "AService must precede BService (build_order), got insertion order")
        self.assertLess(b, c, "BService must precede CService (build_order), got insertion order")
        # the numbered build-order rows specifically (not just first mention) are in order
        self.assertIn("1. `AService`", skill)
        self.assertIn("2. `BService`", skill)
        self.assertIn("3. `CService`", skill)
        # and the per-component detail sections follow the same order
        self.assertLess(skill.index("`AService` —"), skill.index("`BService` —"))
        self.assertLess(skill.index("`BService` —"), skill.index("`CService` —"))

    def test_build_order_absent_falls_back_to_insertion_order(self):
        # no top-level build_order -> stable insertion order preserved (C, A, B as declared)
        self._order_fixture()
        bp_path = os.path.join(self.ws, ".anti-legacy", "requirements", "blueprint.json")
        bp = json.load(open(bp_path))
        del bp["build_order"]
        json.dump(bp, open(bp_path, "w"))
        skill_forge.generate()
        skill = open(os.path.join(self.ws, ".anti-legacy", "generated-skills",
                                  "build-billing", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("1. `CService`", skill)
        self.assertIn("2. `AService`", skill)
        self.assertIn("3. `BService`", skill)

    # -- ISS-19: project_name has no unreachable branch; empty-components domain is handled -- #

    def test_project_name_resolves_from_nested_config_project_name(self):
        # config.project.name path must be reachable (the previously-shadowed middle branch)
        self._fixture()
        cfg_path = os.path.join(self.ws, ".anti-legacy", "config.json")
        json.dump({"project": {"name": "NestedProj"}, "target_stack": "java"},
                  open(cfg_path, "w"))
        skill_forge.generate()
        skill = open(os.path.join(self.ws, ".anti-legacy", "generated-skills",
                                  "build-billing", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("NestedProj", skill)

    def test_project_name_non_dict_project_does_not_crash(self):
        # a non-dict `project` value must not blow up on .get("name"); falls through to blueprint
        self._fixture()
        cfg_path = os.path.join(self.ws, ".anti-legacy", "config.json")
        json.dump({"project": "a-bare-string", "target_stack": "java"}, open(cfg_path, "w"))
        skill_forge.generate()  # must not raise AttributeError
        skill = open(os.path.join(self.ws, ".anti-legacy", "generated-skills",
                                  "build-billing", "SKILL.md"), encoding="utf-8").read()
        # blueprint.project ("demo") is the resolved fallback when config has no usable name
        self.assertIn("demo", skill)

    def test_empty_components_domain_renders_without_crash(self):
        # ISS-19: a domain with no components must not crash; it produces a skill that
        # clearly states there are no components rather than emitting a hollow build list.
        J = lambda p, o: json.dump(o, open(os.path.join(self.ws, ".anti-legacy", p), "w"))
        J("config.json", {"project_name": "demo", "target_stack": "java"})
        J("requirements/blueprint.json", {"project": "demo", "target_stack": "java",
          "domains": {"empty": {"package": "com.demo.empty", "components": {}}}})
        J("requirements/requirements_graph.json", {"metadata": {},
          "domains": {"empty": {"requirements": {}, "entities": {}}}})
        written, index_path = skill_forge.generate()  # must not raise
        self.assertEqual(len(written), 1)
        skill = open(os.path.join(self.ws, ".anti-legacy", "generated-skills",
                                  "build-empty", "SKILL.md"), encoding="utf-8").read()
        self.assertIn('name: "anti-legacy:build-empty"', skill)
        # the chosen behavior: a clear "no components" message, not a fabricated build step
        self.assertIn("no components in blueprint for this domain", skill)


if __name__ == "__main__":
    unittest.main()
