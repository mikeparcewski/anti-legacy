"""Regression for PR #2 Part A: domain_graph projects overlay validations/error_paths
into the requirement (they were hardcoded to [] before), producing schema-legal items.

The companion producer (anti-legacy:negative-extraction) writes validations/error_paths
into the .anti-legacy/annotations.jsonl overlay; this proves the projection turns them into
schema-legal VAL-/ERR- items (with provenance + source-kind trust grounding) instead of
dropping them.
"""
import json
import os
import sys
import unittest

_CORE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts"))
sys.path.insert(0, _CORE)

from antilegacy_core import domain_graph as dg  # noqa: E402

_ENRICHED = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "schemas", "requirements-graph.enriched.schema.json"))


class NegativeExtractionProjectionTest(unittest.TestCase):
    def test_validation_builder_structure_and_provenance(self):
        v = dg._validation_object(
            {"statement": "apr must be > 0", "field": "apr", "error_ref": "ERR-001",
             "confidence": 0.9, "source_kinds": ["code-body"]}, "billing", 1)
        self.assertEqual(v["id"], "VAL-001")
        self.assertEqual(v["field"], "apr")
        self.assertEqual(v["error_ref"], "ERR-001")
        self.assertEqual(v["confidence"], 0.9)
        self.assertEqual(v["provenance"], {"source_app": "billing", "source_kinds": ["code-body"]})

    def test_error_path_builder(self):
        e = dg._error_path_object(
            {"statement": "reject non-positive apr", "code": "ERR-APR",
             "source_kinds": ["code-body"]}, "billing", 3)
        self.assertEqual(e["id"], "ERR-003")
        self.assertEqual(e["code"], "ERR-APR")
        self.assertTrue(e["statement"])

    def test_preserves_authored_id_for_crossref(self):
        # a well-formed authored id is preserved (keeps validation.error_ref links intact)
        self.assertEqual(dg._validation_object({"id": "VAL-042", "statement": "x"}, "a", 9)["id"], "VAL-042")
        # a malformed id is replaced by the generated sequential id
        self.assertEqual(dg._error_path_object({"id": "bogus", "statement": "x"}, "a", 5)["id"], "ERR-005")

    def test_out_of_enum_source_kinds_dropped(self):
        v = dg._validation_object({"statement": "x", "source_kinds": ["hearsay"]}, "a", 1)
        self.assertNotIn("source_kinds", v["provenance"])  # not a real grounding kind -> omitted

    def test_built_items_validate_against_enriched_schema(self):
        try:
            import jsonschema  # noqa: F401
            from jsonschema import Draft7Validator, RefResolver
        except Exception:
            self.skipTest("jsonschema not installed")
        enriched = json.load(open(_ENRICHED, encoding="utf-8"))
        defs = enriched.get("$defs", {})
        store = {enriched.get("$id", ""): enriched}
        v = dg._validation_object(
            {"statement": "apr > 0", "field": "apr", "confidence": 0.9,
             "source_kinds": ["code-body"]}, "billing", 1)
        e = dg._error_path_object(
            {"statement": "reject apr<=0", "code": "E1", "confidence": 0.8,
             "source_kinds": ["code-body"]}, "billing", 1)
        for name, obj in (("validation", v), ("errorPath", e)):
            schema = defs.get(name)
            self.assertIsNotNone(schema, "enriched schema missing $defs[%r]" % name)
            resolver = RefResolver(base_uri="", referrer=enriched, store=store)
            errs = sorted(Draft7Validator(schema, resolver=resolver).iter_errors(obj), key=str)
            self.assertEqual([x.message for x in errs], [],
                             "%s item not schema-legal: %s" % (name, [x.message for x in errs]))


if __name__ == "__main__":
    unittest.main()
