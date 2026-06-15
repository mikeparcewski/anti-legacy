#!/usr/bin/env python3
"""Hermetic tests for GOTCHA 3 — provenance SOURCE-KIND pins the trust tier.

A fact read from a copybook PIC / level-88 VALUE (a *verifiable DATA DEFINITION*,
SOURCE-KIND ``data-def``) and a fact read from a comment (a *prose CLAIM*,
SOURCE-KIND ``comment``) are NOT the same evidence — yet the real extraction run
folded both into ``trusted_verified``. The fix makes the trust tier reflect the
grounding source:

    verification = trusted_verified  ONLY IF every load-bearing fact is grounded
                   in ``code-body`` and/or ``data-def``.
    A rule resting on a ``comment``/``doc`` claim not confirmed against code is
    ``untrusted_verified`` (RISK-eligible per the trust spectrum).

This convention is documented in:
    * skills/extraction/reference/writing-standard.md  ("SOURCE-KIND drives the trust tier")
    * skills/extraction/reference/ENRICHMENT-PROCEDURE.md  (§5 trust section)
and encoded additively in:
    * schemas/requirements-graph.enriched.schema.json
      (provenance.source_kinds — optional enum array on rule/validation/errorPath).

These tests are HERMETIC: they touch NO real ``.anti-legacy/`` tree, run no
scripts, and read only the committed schema file plus in-memory rule objects.
``scripts/`` is intentionally NOT imported or executed — the SOURCE-KIND → trust
rule is a *written convention/guardrail*, and ``trust_tier_for`` below is the
reference predicate that states it. If a future ``scripts/`` change wants to
compute the tier, it must agree with this predicate.
"""
import os
import json
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCHEMA_PATH = os.path.join(
    REPO_ROOT, "schemas", "requirements-graph.enriched.schema.json"
)
WRITING_STD = os.path.join(
    REPO_ROOT, "skills", "extraction", "reference", "writing-standard.md"
)
ENRICH_PROC = os.path.join(
    REPO_ROOT, "skills", "extraction", "reference", "ENRICHMENT-PROCEDURE.md"
)

try:
    from jsonschema import Draft7Validator

    HAVE_JSONSCHEMA = True
except Exception:  # pragma: no cover
    HAVE_JSONSCHEMA = False


# --- The documented convention, as a reference predicate --------------------
# code-body | data-def are CODE (ground truth); comment | doc are CLAIMS.
CODE_KINDS = frozenset({"code-body", "data-def"})
CLAIM_KINDS = frozenset({"comment", "doc"})
ALL_KINDS = CODE_KINDS | CLAIM_KINDS


def trust_tier_for(source_kinds):
    """Reference predicate for the GOTCHA-3 rule (writing-standard.md).

    Given the SOURCE-KINDs grounding a rule's load-bearing facts, return the
    HIGHEST trust tier the grounding permits:

      * grounded in code-body and/or data-def           -> 'trusted_verified'
      * grounded ONLY in comment/doc (no code fact)      -> 'untrusted_verified'
      * no grounding facts at all                        -> 'unverified'

    A mix (a comment PLUS a confirming code/data fact) is 'trusted_verified' —
    that is exactly the promote-by-confirmation case (the comment is confirmed
    against code).
    """
    kinds = set(source_kinds or [])
    if kinds & CODE_KINDS:
        return "trusted_verified"
    if kinds & CLAIM_KINDS:
        return "untrusted_verified"
    return "unverified"


def make_doc(provenance):
    """Minimal enriched-schema-valid graph carrying one rule whose provenance
    we control. Used to prove the additive schema slot is real and enforced."""
    return {
        "metadata": {"migration_mode": "functional"},
        "domains": {
            "Posting": {
                "entities": {},
                "requirements": {
                    "REQ_POST": {
                        "title": "Post a daily transaction",
                        "description": "Posts a DALYTRAN onto the TRAN record.",
                        "legacy_components": ["2000-POST-TRANSACTION"],
                        "data_access": [],
                        "dependencies": [],
                        "business_rules": [
                            {
                                "id": "RULE-001",
                                "statement": "Copy DALYTRAN fields onto the TRAN record.",
                                "confidence": 0.92,
                                "provenance": provenance,
                            }
                        ],
                        "validations": [],
                        "error_paths": [],
                    }
                },
            }
        },
    }


@unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema not installed")
class TestSourceKindTrustTier(unittest.TestCase):
    """The core GOTCHA-3 assertion: source-kind determines the trust tier."""

    def test_comment_only_grounding_is_untrusted(self):
        # A rule grounded ONLY on a comment is a prose CLAIM -> untrusted_verified.
        self.assertEqual(trust_tier_for(["comment"]), "untrusted_verified")

    def test_doc_only_grounding_is_untrusted(self):
        # README / external doc prose is also a CLAIM, not code.
        self.assertEqual(trust_tier_for(["doc"]), "untrusted_verified")

    def test_code_body_grounding_is_trusted(self):
        # Executable logic read directly is ground truth -> trusted_verified.
        self.assertEqual(trust_tier_for(["code-body"]), "trusted_verified")

    def test_data_def_grounding_is_trusted(self):
        # A copybook PIC / level-88 VALUE is a VERIFIABLE definition -> trusted.
        self.assertEqual(trust_tier_for(["data-def"]), "trusted_verified")

    def test_comment_confirmed_by_code_promotes_to_trusted(self):
        # The promote-by-confirmation case: a comment PLUS a confirming code
        # fact is trusted (the 2800 / 2700-UPDATE-TCATBAL move).
        self.assertEqual(
            trust_tier_for(["comment", "code-body"]), "trusted_verified"
        )

    def test_comment_and_data_def_promotes_to_trusted(self):
        self.assertEqual(
            trust_tier_for(["comment", "data-def"]), "trusted_verified"
        )

    def test_no_grounding_is_unverified(self):
        self.assertEqual(trust_tier_for([]), "unverified")

    def test_the_gotcha_distinction_is_real(self):
        # The whole point of GOTCHA 3: a copybook-PIC fact (data-def) and a
        # comment fact (comment) must NOT collapse to the same trust tier.
        self.assertNotEqual(
            trust_tier_for(["data-def"]),
            trust_tier_for(["comment"]),
            "data-def (verifiable definition) and comment (claim) must carry "
            "different trust tiers — folding both into trusted_verified is the bug",
        )


@unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema not installed")
class TestSchemaSourceKindsSlot(unittest.TestCase):
    """The additive, non-breaking schema slot for provenance.source_kinds."""

    @classmethod
    def setUpClass(cls):
        with open(SCHEMA_PATH) as fh:
            cls.schema = json.load(fh)
        Draft7Validator.check_schema(cls.schema)
        cls.validator = Draft7Validator(cls.schema)

    def _errors(self, doc):
        return list(self.validator.iter_errors(doc))

    def test_source_kinds_present_on_all_three_defs(self):
        # rule, validation, errorPath provenance all gained the slot.
        for name in ("rule", "validation", "errorPath"):
            prov = self.schema["$defs"][name]["properties"]["provenance"]
            self.assertIn(
                "source_kinds",
                prov["properties"],
                f"$defs.{name}.provenance is missing source_kinds",
            )
            enum = prov["properties"]["source_kinds"]["items"]["enum"]
            self.assertEqual(set(enum), ALL_KINDS)

    def test_doc_using_source_kinds_validates(self):
        doc = make_doc(
            {"source_app": "carddemo", "source_kinds": ["code-body", "data-def"]}
        )
        self.assertEqual(self._errors(doc), [])

    def test_doc_without_source_kinds_still_validates(self):
        # Non-breaking: the field is optional. The exact provenance shape the
        # committed requirements_graph.json already uses must remain valid.
        doc = make_doc({"source_app": "carddemo"})
        self.assertEqual(self._errors(doc), [])

    def test_empty_provenance_still_validates(self):
        doc = make_doc({})
        self.assertEqual(self._errors(doc), [])

    def test_out_of_enum_source_kind_is_rejected(self):
        # The enum is enforced — only the four documented kinds are allowed.
        doc = make_doc({"source_kinds": ["hearsay"]})
        errs = self._errors(doc)
        self.assertTrue(
            errs, "an invalid source-kind must be rejected by the schema"
        )

    def test_committed_requirements_graph_unaffected(self):
        # The additive change does not invalidate the real committed graph.
        path = os.path.join(
            REPO_ROOT, ".anti-legacy", "requirements", "requirements_graph.json"
        )
        if not os.path.exists(path):
            self.skipTest("no committed requirements_graph.json to cross-check")
        with open(path) as fh:
            doc = json.load(fh)
        self.assertEqual(
            self._errors(doc),
            [],
            "additive source_kinds slot must not break the committed graph",
        )


class TestConventionDocumented(unittest.TestCase):
    """The written rule must exist where extractors read it (no silent convention)."""

    def test_writing_standard_documents_source_kind_rule(self):
        with open(WRITING_STD) as fh:
            text = fh.read()
        self.assertIn("source_kinds", text)
        self.assertIn("SOURCE-KIND", text)
        # the load-bearing claim: comment/doc-only => untrusted_verified
        self.assertIn("untrusted_verified", text)
        for kind in ALL_KINDS:
            self.assertIn(kind, text, f"writing-standard.md omits SOURCE-KIND '{kind}'")

    def test_enrichment_procedure_trust_section_updated(self):
        with open(ENRICH_PROC) as fh:
            text = fh.read()
        self.assertIn("source_kinds", text)
        self.assertIn("untrusted_verified", text)
        self.assertIn("data-def", text)
        self.assertIn("comment", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
