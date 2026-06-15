#!/usr/bin/env python3
"""Unit tests for scripts/generate_target_graph.py — the target-state graph
scanner that extracts rule-implementation evidence (annotation / marker-comment
anchors) from the modernized codebase.

FOCUS (FIX #4): the per-language annotation regex must tolerate a FULLY-QUALIFIED
annotation/attribute name. A developer may import the annotation and write it
bare (`@ImplementsRule("RULE-001")`) OR reference it fully-qualified
(`@com.carddemo.util.ImplementsRule("RULE-001")`). The original regex
(`@(?:ImplementsRule|SatisfiesRule)\\(...`) only matched the bare form, so a
fully-qualified annotation silently produced ZERO rule evidence and the
round-trip done-check (compare_graphs.py) reported the rule as uncovered. The
generalized regex (`@(?:[A-Za-z0-9_.]+\\.)?(?:ImplementsRule|SatisfiesRule)\\(...`)
must match BOTH forms while still rejecting near-miss names
(`@ImplementsRuleFoo(...)`, `@NotARule(...)`).

Hermetic: every test builds a tiny source tree under a temp dir and writes the
target graph under tmp; nothing under .anti-legacy/ is read or mutated.
"""
import json
import os
import shutil
import tempfile
import unittest

from scripts.generate_target_graph import (
    TargetGraphGenerator,
    _LANG_CONFIG,
    _PKG_QUALIFIER_RE,
)


class _GenBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gtg-test-")
        self.out = os.path.join(self.tmp, "target_graph.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.tmp, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return full

    def _generate(self, stack="java"):
        gen = TargetGraphGenerator(self.tmp, stack)
        ok = gen.generate(self.out)
        self.assertTrue(ok, "generator failed for stack %r" % stack)
        with open(self.out, encoding="utf-8") as fh:
            return json.load(fh)

    def _all_rule_ids(self, graph):
        """Collect every implemented rule_id across all domains/components."""
        ids = set()
        for dom in graph.get("domains", {}).values():
            for comp in dom.get("components", {}).values():
                for ev in comp.get("implemented_rules", []) or []:
                    ids.add(ev.get("rule_id"))
            for ent in dom.get("entities", {}).values():
                for ev in ent.get("implemented_rules", []) or []:
                    ids.add(ev.get("rule_id"))
        return ids

    def _evidence_sources(self, graph):
        out = {}
        for dom in graph.get("domains", {}).values():
            for comp in dom.get("components", {}).values():
                for ev in comp.get("implemented_rules", []) or []:
                    out.setdefault(ev.get("rule_id"), set()).add(ev.get("source"))
        return out


# ---------------------------------------------------------------------------
# Direct regex behavior (the unit under FIX #4).
# ---------------------------------------------------------------------------
class TestAnnotationRegexQualifier(unittest.TestCase):
    def test_java_regex_matches_bare_and_fully_qualified(self):
        ann = _LANG_CONFIG["java"]["annotation"]
        # bare form (the only one the old regex matched)
        m = ann.search('@ImplementsRule("RULE-001")')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "RULE-001")
        # fully-qualified form (the FIX #4 case)
        m = ann.search('@com.carddemo.util.ImplementsRule("RULE-002")')
        self.assertIsNotNone(m, "FQ-qualified annotation must match")
        self.assertEqual(m.group(1), "RULE-002")
        # SatisfiesRule, qualified, with internal whitespace
        m = ann.search('@org.example.SatisfiesRule( "VAL-009" )')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "VAL-009")
        # deeply nested package
        m = ann.search('@a.b.c.d.e.ImplementsRule("ERR-042")')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "ERR-042")

    def test_java_regex_rejects_near_miss_names(self):
        ann = _LANG_CONFIG["java"]["annotation"]
        # a longer name that merely STARTS with the anchor must not match
        self.assertIsNone(ann.search('@ImplementsRuleFoo("RULE-001")'))
        # an unrelated annotation must not match
        self.assertIsNone(ann.search('@NotARule("RULE-001")'))
        # a name that ENDS with the anchor but has no qualifying dot must not match
        self.assertIsNone(ann.search('@FooImplementsRule("RULE-001")'))

    def test_kotlin_regex_matches_fully_qualified(self):
        ann = _LANG_CONFIG["kotlin"]["annotation"]
        m = ann.search('@com.carddemo.rules.SatisfiesRule("RULE-007")')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "RULE-007")

    def test_csharp_attribute_matches_namespace_qualified(self):
        ann = _LANG_CONFIG["csharp"]["annotation"]
        m = ann.search('[ImplementsRule("RULE-001")]')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "RULE-001")
        m = ann.search('[CardDemo.Util.SatisfiesRule("VAL-003")]')
        self.assertIsNotNone(m, "namespace-qualified attribute must match")
        self.assertEqual(m.group(1), "VAL-003")

    def test_qualifier_fragment_is_optional(self):
        # The qualifier matches zero-or-more dotted prefixes, so the empty prefix
        # (bare annotation) is still accepted — i.e. the fix is purely additive.
        import re
        self.assertIsNotNone(re.fullmatch(_PKG_QUALIFIER_RE, ""))
        self.assertIsNotNone(re.fullmatch(_PKG_QUALIFIER_RE, "com.carddemo.util."))


# ---------------------------------------------------------------------------
# End-to-end through the generator: a FQ-qualified annotation in a real Java
# source tree must surface rule evidence (the bug: it produced none).
# ---------------------------------------------------------------------------
class TestFullyQualifiedAnnotationEndToEnd(_GenBase):
    def test_fq_annotation_yields_rule_evidence(self):
        self._write(
            "src/main/java/com/carddemo/billing/InterestService.java",
            "package com.carddemo.billing;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service\n"
            "public class InterestService {\n"
            "    @com.carddemo.util.ImplementsRule(\"RULE-001\")\n"
            "    public BigDecimal accrue() { return null; }\n"
            "    @com.carddemo.util.SatisfiesRule(\"VAL-002\")\n"
            "    public void validate() {}\n"
            "}\n",
        )
        graph = self._generate("java")
        ids = self._all_rule_ids(graph)
        self.assertIn("RULE-001", ids,
                      "fully-qualified @ImplementsRule must yield rule evidence")
        self.assertIn("VAL-002", ids,
                      "fully-qualified @SatisfiesRule must yield rule evidence")
        # The evidence is recorded with the annotation source (weak structural tier).
        sources = self._evidence_sources(graph)
        self.assertEqual(sources.get("RULE-001"), {"annotation"})
        self.assertEqual(sources.get("VAL-002"), {"annotation"})

    def test_bare_and_fq_annotations_coexist(self):
        """A file mixing the bare and the fully-qualified form yields BOTH rules
        — the fix is additive, never regressing the bare case."""
        self._write(
            "src/main/java/com/carddemo/account/AccountService.java",
            "package com.carddemo.account;\n"
            "import com.carddemo.util.ImplementsRule;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service\n"
            "public class AccountService {\n"
            "    @ImplementsRule(\"RULE-010\")\n"
            "    public void post() {}\n"
            "    @com.carddemo.util.ImplementsRule(\"RULE-011\")\n"
            "    public void reverse() {}\n"
            "}\n",
        )
        graph = self._generate("java")
        ids = self._all_rule_ids(graph)
        self.assertIn("RULE-010", ids, "bare annotation must still match")
        self.assertIn("RULE-011", ids, "fully-qualified annotation must match")

    def test_near_miss_annotation_yields_no_evidence(self):
        """A look-alike annotation (`@ImplementsRuleX`) must NOT be mistaken for
        the real anchor — no rule evidence is produced for it."""
        self._write(
            "src/main/java/com/carddemo/misc/Decoy.java",
            "package com.carddemo.misc;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service\n"
            "public class Decoy {\n"
            "    @com.carddemo.util.ImplementsRuleX(\"RULE-999\")\n"
            "    public void noop() {}\n"
            "}\n",
        )
        graph = self._generate("java")
        self.assertNotIn("RULE-999", self._all_rule_ids(graph),
                         "a near-miss annotation name must not match the anchor")


if __name__ == "__main__":
    unittest.main()
