#!/usr/bin/env python3
"""
Contract regression test for the anti-legacy:extraction skill (WF1 §I1/§I2).

The extraction SKILL.md is the agent-facing recipe for the adaptive ring-expansion
crawl. It has several load-bearing invariants that, if silently dropped from the
prose, would break the extraction loop or re-introduce a deleted intermediate.
These are mechanically-checkable text assertions over the file this unit owns —
deterministic, no network, no dependency on the sibling wicked_estate.py / coverage.py
units. They pin the *post-WF1* flow so the skill can't regress to the old
graph-translator behavior.
"""
import os
import re
import unittest


def _skill_text():
    here = os.path.dirname(__file__)
    path = os.path.normpath(os.path.join(here, "..", "SKILL.md"))
    with open(path, encoding="utf-8") as fh:
        return path, fh.read()


class TestExtractionSkillContract(unittest.TestCase):
    def setUp(self):
        self.path, self.text = _skill_text()
        self.lower = self.text.lower()

    def test_skill_file_exists_and_has_frontmatter(self):
        self.assertTrue(os.path.isfile(self.path))
        self.assertTrue(
            self.text.lstrip().startswith("---"),
            "extraction SKILL.md must open with YAML frontmatter",
        )
        self.assertIn(
            'name: "anti-legacy:extraction"',
            self.text,
            "frontmatter must declare the anti-legacy:extraction skill name",
        )

    def test_does_not_resurrect_deleted_legacy_graph_json(self):
        # §H deletes the legacy_graph.json intermediate; the extraction skill must
        # NOT read it. (It may mention the digest evidence 'legacy-graph.digest.txt'
        # and the manifest artifact id 'legacy-graph', which are not the JSON blob.)
        self.assertNotIn(
            "legacy_graph.json",
            self.text,
            "extraction must not read the deleted legacy_graph.json intermediate",
        )
        self.assertNotIn(
            "graph_builder",
            self.text,
            "extraction must not invoke the deleted graph_builder.py",
        )

    def test_drives_wicked_estate_helper_not_raw_sqlite(self):
        # Structure comes from the helper via run.py, never raw SQLite in the skill.
        self.assertIn("wicked_estate", self.lower)
        for cmd in ("blast-radius", "source", "query", "rank"):
            self.assertIn(
                cmd,
                self.lower,
                f"crawl recipe must use the helper '{cmd}' command",
            )
        # No raw sqlite invocation in the agent-facing recipe.
        self.assertNotIn("sqlite3 ", self.lower)
        self.assertNotIn("select ", self.lower)

    def test_symbolid_gotcha_is_documented(self):
        # The silent-no-op trap: writes key on the FULL interned SymbolId, never the
        # simple name; resolve-first, and empty resolution must NOT trigger a write.
        self.assertIn("symbolid", self.lower)
        # The helper API is `we.resolve_symbol_id`; the dispatcher subcommand uses the
        # same stem. Accept either the hyphen or underscore spelling of the name.
        self.assertTrue(
            "resolve_symbol_id" in self.lower or "resolve-symbol-id" in self.lower,
            "must name the resolve_symbol_id helper / subcommand",
        )
        self.assertTrue(
            "silent no-op" in self.lower or "silent-no-op" in self.lower,
            "must warn about the silent no-op trap on name-keyed writes",
        )
        self.assertTrue(
            "empty" in self.lower,
            "must instruct not to write when resolve-symbol-id returns empty",
        )

    def test_annotation_value_contract_packing(self):
        # requirement = "<rule_id>|<confidence>|<provenance>|<statement>"
        self.assertIn("<rule_id>|<confidence>|<provenance>|<statement>", self.text)
        # native fields + lossless JSONL overlay are BOTH written.
        self.assertIn("requirement_validated", self.text)
        self.assertIn(".anti-legacy/annotations.jsonl", self.text)

    def test_three_terminal_stop_conditions_present(self):
        for token in ("RESOLVE", "EXPAND", "RISK"):
            self.assertIn(
                token,
                self.text,
                f"crawl stop-condition must document the {token} branch",
            )
        # Termination guarantee: only RESOLVE/RISK are terminal; EXPAND consumes budget.
        self.assertIn("max_rings", self.text)
        self.assertIn("context_budget_chars", self.text)
        self.assertIn("resolve_threshold", self.text)

    def test_ring_expansion_is_one_hop_both_directions(self):
        # 1 down (dependencies / calls-uses) + 1 up (dependents / blast-radius).
        self.assertTrue(
            re.search(r"1\s*down", self.lower) is not None,
            "must describe the 1-DOWN dependency ring",
        )
        self.assertTrue(
            re.search(r"1\s*up", self.lower) is not None,
            "must describe the 1-UP dependent ring",
        )
        # Bounded traversal: widen seeds by one hop, not re-query deeper.
        self.assertTrue(
            "bounded" in self.lower and "one hop" in self.lower,
            "must honor the bounded-traversal one-hop-per-ring rule",
        )

    def test_coverage_is_the_terminal_and_gate(self):
        self.assertIn("coverage", self.lower)
        # resolved-or-flagged metric; 1.0 is the provable terminal / done-gate.
        self.assertTrue(
            "resolved" in self.lower and "risk" in self.lower,
            "coverage must be resolved-or-flagged",
        )
        self.assertTrue(
            "1.0" in self.text,
            "done-gate is coverage == 1.0 (zero unaccounted)",
        )
        self.assertIn("coverage-report.json", self.text)

    def test_worklist_is_rank_ordered_and_idempotent(self):
        self.assertIn("rank", self.lower)  # PageRank worklist order
        self.assertTrue(
            "idempot" in self.lower or "resumable" in self.lower,
            "crawl must be idempotent/resumable (skip settled nodes)",
        )

    def test_registers_coverage_evidence_via_manifest(self):
        self.assertIn("manifest register coverage-report", self.text)
        self.assertIn("anti-legacy:extraction", self.text)
        # `extraction` is NOT a legal manifest phase enum (manifest.py PHASES has
        # `graph-translate`, no `extraction`) — advancing into `extraction` would exit
        # 2. The extraction skill occupies the graph-translate phase slot (it replaces
        # the old graph-translator enrich flow), so it must advance into graph-translate.
        self.assertIn("manifest advance graph-translate", self.text)
        self.assertNotIn(
            "manifest advance extraction",
            self.text,
            "must not advance into the nonexistent 'extraction' phase (exit 2)",
        )


if __name__ == "__main__":
    unittest.main()
