"""Regression (C2): risk_log SURFACES a confidence-less rule instead of silently skipping it.

Before the fix, `mine_low_confidence` did `if not isinstance(c, (int, float)): continue` — a rule
with absent/non-numeric confidence vanished from the risk register. It must now be reported.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts")))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "risk-log", "scripts")))

import risk_log  # noqa: E402


def _graph(confidence):
    rule = {"id": "RULE-001", "statement": "interest = bal * apr"}
    if confidence is not None:
        rule["confidence"] = confidence
    node = {"title": "Compute interest", "legacy_components": ["COBOL/CBINT.cbl"],
            "business_rules": [rule]}
    return {"domains": {"billing": {"requirements": {"REQ-001": node}}}}


class RiskLogC2Test(unittest.TestCase):
    def _categories(self, confidence):
        return [r.category.lower() for r in risk_log.mine_low_confidence(_graph(confidence), 0.75)]

    def test_missing_confidence_is_surfaced_not_skipped(self):
        cats = self._categories(None)
        self.assertTrue(any("missing confidence" in c for c in cats),
                        "a confidence-less rule must be surfaced; got %s" % cats)

    def test_non_numeric_confidence_is_surfaced(self):
        cats = self._categories("high")  # string, not a number
        self.assertTrue(any("missing confidence" in c for c in cats), cats)

    def test_low_confidence_still_flagged(self):
        cats = self._categories(0.40)
        self.assertTrue(any("low-confidence" in c for c in cats), cats)

    def test_high_confidence_not_flagged(self):
        self.assertEqual(self._categories(0.95), [])


if __name__ == "__main__":
    unittest.main()
