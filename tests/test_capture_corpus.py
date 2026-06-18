"""Regression for antilegacy_core.capture_corpus — assemble a golden corpus from what's available.

Covers: contracts' success scenarios -> contract-expected goldens (error scenarios skipped);
higher-confidence overlay precedence (captured > oracle > contract); and the provenance report
(confidence = weakest tier present; warnings explain why the data could be incorrect). Hermetic.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts")))

from antilegacy_core import capture_corpus as cc  # noqa: E402

_ATTESTATION = {"method": "replay", "source": "PROD-LPAR1", "captured_at": "2026-01-01T00:00:00Z"}


class ValidateAttestationTest(unittest.TestCase):
    """ISS-24: capture-corpus only stamps captured-legacy on entries with a valid capture
    attestation; validate_attestation is the gate, returning (ok, reason)."""

    def test_valid_attestation_ok(self):
        ok, reason = cc.validate_attestation({"capture": dict(_ATTESTATION)})
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_no_capture_block_reason(self):
        ok, reason = cc.validate_attestation({"golden_output": {}})
        self.assertFalse(ok)
        self.assertIn("no `capture`", reason)

    def test_missing_key_named_in_reason(self):
        ok, reason = cc.validate_attestation(
            {"capture": {"method": "replay", "source": "PROD"}})  # captured_at missing
        self.assertFalse(ok)
        self.assertIn("captured_at", reason)

    def test_blank_value_rejected(self):
        ok, _reason = cc.validate_attestation(
            {"capture": {"method": "", "source": "PROD", "captured_at": "2026"}})
        self.assertFalse(ok)


class StampCapturedTest(unittest.TestCase):
    """_stamp_captured splits attested (captured-legacy) from unattested
    (captured-legacy-unverified) and returns the demoted scenario_ids."""

    def test_attested_stamped_high_unattested_demoted(self):
        entries = [
            {"scenario_id": "a", "golden_output": {}, "capture": dict(_ATTESTATION)},
            {"scenario_id": "b", "golden_output": {}},  # no attestation
        ]
        stamped, unverified = cc._stamp_captured(entries)
        by_sid = {e["scenario_id"]: e for e in stamped}
        self.assertEqual(by_sid["a"]["provenance"], "captured-legacy")
        self.assertEqual(by_sid["b"]["provenance"], "captured-legacy-unverified")
        self.assertEqual(unverified, ["b"])

    def test_entry_without_scenario_id_skipped(self):
        stamped, _ = cc._stamp_captured([{"golden_output": {}, "capture": dict(_ATTESTATION)}])
        self.assertEqual(stamped, [])


class FromContractsTest(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="al-capcorpus-"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _contract(self, name, payload):
        dom = os.path.join(self.d, "billing")
        os.makedirs(dom, exist_ok=True)
        with open(os.path.join(dom, name), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_success_scenarios_become_contract_expected_goldens(self):
        self._contract("REQ-1.contract.json", {
            "req_id": "REQ-1",
            "scenarios": [
                {"id": "happy", "type": "success", "inputs": {"A": 1},
                 "expected_output": {"OUT": "1.00"}, "expected_error": None},
                {"id": "bad", "type": "error", "inputs": {"A": -1},
                 "expected_output": {}, "expected_error": "ERR-X"},  # skipped: error case
            ]})
        entries = cc.from_contracts(self.d)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["req_id"], "REQ-1")
        self.assertEqual(e["scenario_id"], "REQ-1::happy")
        self.assertEqual(e["golden_output"], {"OUT": "1.00"})
        self.assertEqual(e["provenance"], "contract-expected")

    def test_absent_contracts_dir(self):
        self.assertEqual(cc.from_contracts(os.path.join(self.d, "nope")), [])


class AssembleTest(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="al-capcorpus-asm-"))
        dom = os.path.join(self.d, "billing")
        os.makedirs(dom)
        with open(os.path.join(dom, "REQ-1.contract.json"), "w", encoding="utf-8") as f:
            json.dump({"req_id": "REQ-1", "scenarios": [
                {"id": "s1", "type": "success", "inputs": {}, "expected_output": {"OUT": "1.00"}}]}, f)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_contracts_only_is_low_confidence(self):
        corpus, report = cc.assemble(self.d)
        self.assertEqual(report["scenarios"], 1)
        self.assertEqual(report["golden_confidence"], "low")
        self.assertTrue(report["warnings"])  # explains it's assumed behavior, not captured

    def test_attested_captured_overlays_contract_and_raises_confidence(self):
        # ISS-24: a captured-legacy entry reaches high confidence ONLY with a capture attestation.
        captured = [{"scenario_id": "REQ-1::s1", "req_id": "REQ-1",
                     "golden_output": {"OUT": "1.00"},
                     "capture": dict(_ATTESTATION)}]  # attested -> stamped captured-legacy (high)
        corpus, report = cc.assemble(self.d, captured=captured)
        self.assertEqual(report["scenarios"], 1)  # same scenario_id -> overlaid, not duplicated
        self.assertEqual(report["golden_confidence"], "high")
        self.assertEqual(corpus[0]["provenance"], "captured-legacy")
        self.assertEqual(report["warnings"], [])  # attested captured-legacy -> no trust warning

    def test_unattested_captured_is_demoted_and_warns(self):
        # ISS-24: --captured WITHOUT a capture attestation must NOT be stamped captured-legacy.
        captured = [{"scenario_id": "REQ-1::s1", "req_id": "REQ-1",
                     "golden_output": {"OUT": "1.00"}}]  # NO capture block
        corpus, report = cc.assemble(self.d, captured=captured)
        self.assertEqual(corpus[0]["provenance"], "captured-legacy-unverified")
        self.assertEqual(report["golden_confidence"], "low")     # demoted, not high
        self.assertTrue(any("attestation" in w.lower() for w in report["warnings"]))
        self.assertTrue(any("REQ-1::s1" in w for w in report["warnings"]))  # names the demoted sid

    def test_explicit_provenance_on_captured_entry_is_respected(self):
        # An entry that already declares a provenance keeps it (no re-stamping / no demotion churn).
        captured = [{"scenario_id": "REQ-1::s1", "req_id": "REQ-1",
                     "golden_output": {"OUT": "1.00"}, "provenance": "source-oracle"}]
        corpus, report = cc.assemble(self.d, captured=captured)
        self.assertEqual(corpus[0]["provenance"], "source-oracle")
        self.assertEqual(report["golden_confidence"], "medium")

    def test_oracle_is_medium_and_overlays_contract(self):
        oracle = [{"scenario_id": "REQ-1::s1", "req_id": "REQ-1", "golden_output": {"OUT": "1.00"}}]
        corpus, report = cc.assemble(self.d, oracle=oracle)
        self.assertEqual(corpus[0]["provenance"], "source-oracle")
        self.assertEqual(report["golden_confidence"], "medium")

    def test_empty_everything_is_none(self):
        corpus, report = cc.assemble(os.path.join(self.d, "nope"))
        self.assertEqual(corpus, [])
        self.assertEqual(report["golden_confidence"], "none")
        self.assertTrue(report["warnings"])


if __name__ == "__main__":
    unittest.main()
