"""Regression for antilegacy_core.capability_graph — the md-as-code capability extractor.

Treats skills/<name>/SKILL.md (frontmatter name:) as BEHAVIOR; reference/*.md + root *.md as
non-behavior docs. Hermetic: builds a tiny fixture repo in tmp.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts")))

from antilegacy_core import capability_graph as cg  # noqa: E402


class CapabilityGraphTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="al-capgraph-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def _seed(self):
        self._write("skills/alpha/SKILL.md",
                    '---\nname: "anti-legacy:alpha"\ndescription: >\n'
                    '  Does the alpha thing thoroughly.\n'
                    '  Use when: "do alpha", "run alpha".\n---\n# anti-legacy:alpha\nbody\n')
        self._write("skills/beta/SKILL.md",
                    '---\nname: "anti-legacy:beta"\ndescription: Beta capability. Use when: "do beta".\n---\nbody\n')
        # a reference doc inside a skill (NOT a capability)
        self._write("skills/alpha/reference/idioms.md", "# idioms\nnot a skill\n")
        # a project doc (NOT a capability)
        self._write("README.md", "# project readme\n")

    def test_frontmatter_parse(self):
        fm = cg.parse_frontmatter('---\nname: "x:y"\ndescription: >\n  multi\n  line.\n---\nbody')
        self.assertEqual(fm["name"], "x:y")
        self.assertIn("multi", fm["description"])

    def test_skills_are_capabilities_refs_and_docs_are_not(self):
        self._seed()
        caps = cg.scan_capabilities(self.root)
        names = sorted(c["name"] for c in caps)
        self.assertEqual(names, ["anti-legacy:alpha", "anti-legacy:beta"])  # only SKILL.md w/ name:
        alpha = next(c for c in caps if c["name"] == "anti-legacy:alpha")
        self.assertIn("alpha thing", alpha["summary"].lower())
        self.assertIn("do alpha", alpha["triggers"])  # Use-when triggers parsed
        census = cg.classify_markdown(self.root)
        self.assertEqual(census["skill_agents"], 2)
        self.assertGreaterEqual(census["reference_docs"], 1)
        self.assertGreaterEqual(census["project_docs"], 1)

    def test_render_site_html(self):
        self._seed()
        page = cg.render_site_html(cg.build_graph(self.root))
        self.assertIn('class="card"', page)
        self.assertIn("anti-legacy:alpha", page)
        self.assertIn('href="/"', page)  # logo links back to homepage
        self.assertIn("<!DOCTYPE html>", page)

    def test_no_skills_yields_no_capabilities(self):
        self._write("README.md", "# nothing here\n")
        self.assertEqual(cg.scan_capabilities(self.root), [])


if __name__ == "__main__":
    unittest.main()
