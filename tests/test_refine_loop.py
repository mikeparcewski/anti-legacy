"""Regression for antilegacy_core.refine_loop — bounded make->review->refine (ISS-8/ISS-12).

Two halves: loop_decision (the §7-capped verdict->action decision) and build_descriptor
(the GENERIC single-artifact critic target, resolved from the manifest). Hermetic.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts")))

from antilegacy_core import refine_loop as rl  # noqa: E402
from antilegacy_core import deliverables as D  # noqa: E402


class LoopDecisionTest(unittest.TestCase):
    def test_pass_stops_converged(self):
        d = rl.loop_decision("PASS", 1)
        self.assertEqual(d["action"], "stop")
        self.assertEqual(d["terminal"], "converged")
        self.assertEqual(d["exit_code"], rl.EXIT_STOP)

    def test_revise_under_cap_refines(self):
        d = rl.loop_decision("REVISE", 1, cap=3)
        self.assertEqual(d["action"], "refine")
        self.assertEqual(d["exit_code"], rl.EXIT_REFINE)
        self.assertFalse(d["recommend_recon"])

    def test_block_at_cap_stops_with_recon(self):
        d = rl.loop_decision("BLOCK", 3, cap=3)
        self.assertEqual(d["action"], "stop")
        self.assertEqual(d["terminal"], "cap-reached")
        self.assertTrue(d["recommend_recon"])
        self.assertEqual(d["exit_code"], rl.EXIT_CAP)

    def test_forced_stops_past_nonpass_loudly(self):
        d = rl.loop_decision("BLOCK", 1, forced=True)
        self.assertEqual(d["action"], "stop")
        self.assertEqual(d["terminal"], "forced")
        self.assertEqual(d["exit_code"], rl.EXIT_STOP)
        self.assertIn("state", d["reason"].lower())  # must say "state it loudly"

    def test_default_cap_is_three_sec7(self):
        # §7: three attempts, then recon. attempt 2 still refines; attempt 3 stops + recon.
        self.assertEqual(rl.loop_decision("REVISE", 2)["action"], "refine")
        self.assertEqual(rl.loop_decision("REVISE", 3)["action"], "stop")
        self.assertTrue(rl.loop_decision("REVISE", 3)["recommend_recon"])

    def test_invalid_verdict_raises(self):
        with self.assertRaises(ValueError):
            rl.loop_decision("MAYBE", 1)

    def test_attempt_floor_raises(self):
        with self.assertRaises(ValueError):
            rl.loop_decision("PASS", 0)

    def test_status_report_has_three_lenses(self):
        rep = rl.status_report("requirements-graph", rl.loop_decision("REVISE", 1))
        for lens in ("TRUE:", "NOT YET:", "NEXT:"):
            self.assertIn(lens, rep)


class DescriptorTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="al-refineloop-"))
        os.chdir(self.ws)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, rel_to_ws, text="{}"):
        p = os.path.join(self.ws, rel_to_ws)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def _manifest(self, artifacts):
        self._write(".anti-legacy/manifest.json", json.dumps({"artifacts": artifacts}))

    def test_descriptor_resolves_rendered_skill_and_spine_sources(self):
        self._write(".anti-legacy/requirements/requirements_graph.json", '{"domains":{}}')
        self._write(".anti-legacy/requirements/blueprint.json", "{}")
        self._manifest({
            "requirements-graph": {"path": "requirements/requirements_graph.json",
                                   "produced_by": "anti-legacy:graph-translator", "depends_on": []},
            "blueprint-json": {"path": "requirements/blueprint.json",
                               "produced_by": "anti-legacy:blueprint",
                               "depends_on": ["requirements-graph"]},
        })
        desc = rl.build_descriptor(D.load_manifest(), "blueprint-json")
        self.assertTrue(desc["present"])
        self.assertEqual(desc["producing_skill"], "anti-legacy:blueprint")
        self.assertEqual(desc["rendered_path"], ".anti-legacy/requirements/blueprint.json")
        # source_data carries the §2 spine (requirements graph), de-duplicated.
        self.assertIn(".anti-legacy/requirements/requirements_graph.json", desc["source_data"])
        self.assertEqual(len(desc["source_data"]), len(set(desc["source_data"])))

    def test_registered_but_missing_file_is_not_present(self):
        self._manifest({"task-plan": {"path": "task.md", "produced_by": "anti-legacy:planner",
                                      "depends_on": []}})
        desc = rl.build_descriptor(D.load_manifest(), "task-plan")
        self.assertTrue(desc["registered"])
        self.assertFalse(desc["present"])

    def test_unregistered_artifact(self):
        self._manifest({})
        desc = rl.build_descriptor(D.load_manifest(), "nope")
        self.assertFalse(desc["registered"])
        self.assertFalse(desc["present"])


if __name__ == "__main__":
    unittest.main()
