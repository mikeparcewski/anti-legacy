"""Regression for antilegacy_core.differential_equivalence — executed parity gate (ISS-7).

Covers the precision-aware comparator (COMP-3 decimal parity is the catastrophic case),
record comparison (missing field = violation), and the vacuous-safe harness
(empty corpus -> NOT_APPLICABLE so a corpus-less pipeline is never blocked). Hermetic.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts")))

from antilegacy_core import differential_equivalence as de  # noqa: E402


class CompareValueTest(unittest.TestCase):
    def test_exact_match_and_mismatch(self):
        self.assertTrue(de.compare_value("AB", "AB", "exact")[0])
        self.assertFalse(de.compare_value("AB", "AC", "exact")[0])

    def test_numeric_parity_to_precision(self):
        # equal to 2 decimal places
        self.assertTrue(de.compare_value(100.00, 100.004, 2)[0])     # rounds to 100.00 both
        # COMP-3 parity LOSS: differs at the 2nd decimal
        ok, detail = de.compare_value("100.00", "100.01", 2)
        self.assertFalse(ok)
        self.assertIn("PARITY LOSS", detail)

    def test_numeric_strings_coerced(self):
        # COBOL outputs are often strings; they must compare numerically.
        self.assertTrue(de.compare_value("042.50", 42.5, 2)[0])

    def test_non_numeric_where_numeric_declared_is_violation(self):
        ok, detail = de.compare_value("N/A", "100.00", 2)
        self.assertFalse(ok)
        self.assertIn("non-numeric", detail)

    def test_bool_is_never_a_money_value(self):
        self.assertFalse(de.compare_value(True, "1.00", 2)[0])


class CompareRecordTest(unittest.TestCase):
    RULES = [{"field": "GROSS", "precision": 2, "source_type": "COMP-3 PIC 9(9)V99"},
             {"field": "CODE", "precision": "exact", "source_type": "PIC X(2)"}]

    def test_all_fields_match(self):
        rec = de.compare_record({"GROSS": "10.00", "CODE": "OK"},
                                {"GROSS": "10.004", "CODE": "OK"}, self.RULES)
        self.assertEqual(rec["status"], de.PASS)
        self.assertEqual(rec["violations"], 0)

    def test_missing_field_is_violation(self):
        rec = de.compare_record({"GROSS": "10.00", "CODE": "OK"},
                                {"GROSS": "10.00"}, self.RULES)  # CODE dropped
        self.assertEqual(rec["status"], de.FAIL)
        self.assertTrue(any("MISSING" in f["detail"] for f in rec["fields"]))

    def test_precision_loss_fails_record(self):
        rec = de.compare_record({"GROSS": "10.00", "CODE": "OK"},
                                {"GROSS": "10.99", "CODE": "OK"}, self.RULES)
        self.assertEqual(rec["status"], de.FAIL)


class HarnessTest(unittest.TestCase):
    PARITY = {"REQ-1": [{"field": "AMT", "precision": 2}]}

    def test_empty_corpus_is_not_applicable_vacuous_safe(self):
        rep = de.run_harness([], {}, self.PARITY)
        self.assertEqual(rep["status"], de.NOT_APPLICABLE)
        self.assertEqual(rep["aggregate"]["scenarios"], 0)

    def test_matching_corpus_passes(self):
        corpus = [{"scenario_id": "s1", "req_id": "REQ-1", "golden_output": {"AMT": "5.00"}}]
        rep = de.run_harness(corpus, {"s1": {"AMT": "5.004"}}, self.PARITY)
        self.assertEqual(rep["status"], de.PASS)
        self.assertEqual(rep["aggregate"]["pass"], 1)

    def test_diverging_corpus_fails(self):
        corpus = [{"scenario_id": "s1", "req_id": "REQ-1", "golden_output": {"AMT": "5.00"}}]
        rep = de.run_harness(corpus, {"s1": {"AMT": "5.99"}}, self.PARITY)
        self.assertEqual(rep["status"], de.FAIL)
        self.assertEqual(rep["aggregate"]["fail"], 1)
        self.assertGreaterEqual(rep["aggregate"]["violations"], 1)

    def test_missing_actual_output_fails(self):
        corpus = [{"scenario_id": "s1", "req_id": "REQ-1", "golden_output": {"AMT": "5.00"}}]
        rep = de.run_harness(corpus, {}, self.PARITY)  # no actual for s1
        self.assertEqual(rep["status"], de.FAIL)


class LoadParityTest(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="al-de-contracts-"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_loads_parity_rules_per_req_from_contracts(self):
        dom = os.path.join(self.d, "billing")
        os.makedirs(dom)
        with open(os.path.join(dom, "REQ-1.contract.json"), "w", encoding="utf-8") as f:
            json.dump({"req_id": "REQ-1",
                       "parity_rules": [{"field": "AMT", "precision": 2}]}, f)
        by_req = de.load_parity_by_req(self.d)
        self.assertIn("REQ-1", by_req)
        self.assertEqual(by_req["REQ-1"][0]["field"], "AMT")

    def test_absent_contracts_dir_is_empty(self):
        self.assertEqual(de.load_parity_by_req(os.path.join(self.d, "nope")), {})


class Gate3CDifferentialTest(unittest.TestCase):
    """The validator_discovery GATE_3C_DIFFERENTIAL branch: vacuous-safe, blocks only on FAIL."""

    def setUp(self):
        from antilegacy_core.validator_discovery import ValidatorRunner  # noqa: E402
        self.Runner = ValidatorRunner
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="al-gate3c-"))
        self.config = os.path.join(self.dir, "config.json")
        self.manifest = os.path.join(self.dir, "manifest.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"target_stack": "python"}, f)
        with open(self.manifest, "w", encoding="utf-8") as f:
            json.dump({"version": "1.0.0", "project": {"name": "t"}, "artifacts": {}}, f)
        self.ev = os.path.join(self.dir, ".anti-legacy", "evidence")
        os.makedirs(self.ev, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _report(self, payload):
        with open(os.path.join(self.ev, "differential-equivalence-report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(payload, f)

    def _gate(self):
        return self.Runner(self.dir, self.config, self.manifest).run_gate("GATE_3C_DIFFERENTIAL")

    def test_absent_report_is_vacuous_pass(self):
        self.assertTrue(self._gate())  # no report -> NOT EVALUATED, non-blocking

    def test_not_applicable_is_non_blocking(self):
        self._report({"status": "NOT_APPLICABLE", "aggregate": {}})
        self.assertTrue(self._gate())

    def test_pass_report_passes(self):
        self._report({"status": "PASS",
                      "aggregate": {"scenarios": 2, "pass": 2, "fail": 0, "violations": 0}})
        self.assertTrue(self._gate())

    def _fail_report(self, golden_confidence):
        self._report({"status": "FAIL", "golden_confidence": golden_confidence,
                      "warnings": ["w"],
                      "aggregate": {"scenarios": 1, "pass": 0, "fail": 1, "violations": 1},
                      "scenarios": [{"scenario_id": "s1", "req_id": "R1", "status": "FAIL",
                                     "fields": [{"field": "AMT", "parity": False,
                                                 "detail": "PARITY LOSS at 2 dp"}]}]})

    def test_fail_against_captured_legacy_blocks(self):
        self._fail_report("high")          # captured-legacy golden -> a real divergence
        self.assertFalse(self._gate())

    def test_fail_against_low_confidence_warns_not_blocks(self):
        self._fail_report("low")           # contract-expected/oracle golden -> WARN, not block
        self.assertTrue(self._gate())

    def test_fail_against_source_oracle_warns_not_blocks(self):
        self._fail_report("medium")        # source-oracle is medium, still not captured -> WARN
        self.assertTrue(self._gate())


class PostureConfidenceTest(unittest.TestCase):
    """run_harness now grades golden trustworthiness; gate_posture turns that into BLOCK/WARN/PASS."""

    def _corpus(self, provenance):
        return [{"scenario_id": "s1", "req_id": "R", "golden_output": {"AMT": "5.00"},
                 "provenance": provenance}]

    PARITY = {"R": [{"field": "AMT", "precision": 2}]}

    def test_confidence_is_weakest_provenance(self):
        corpus = [{"scenario_id": "s1", "req_id": "R", "golden_output": {"AMT": "5.00"},
                   "provenance": "captured-legacy"},
                  {"scenario_id": "s2", "req_id": "R", "golden_output": {"AMT": "6.00"},
                   "provenance": "contract-expected"}]
        rep = de.run_harness(corpus, {"s1": {"AMT": "5.00"}, "s2": {"AMT": "6.00"}}, self.PARITY)
        self.assertEqual(rep["golden_confidence"], "low")  # weakest of {high, low}

    def test_captured_fail_posture_is_block(self):
        rep = de.run_harness(self._corpus("captured-legacy"), {"s1": {"AMT": "5.99"}}, self.PARITY)
        self.assertEqual(rep["status"], de.FAIL)
        self.assertEqual(de.gate_posture(rep), de.BLOCK)

    def test_low_confidence_fail_posture_is_warn(self):
        rep = de.run_harness(self._corpus("contract-expected"), {"s1": {"AMT": "5.99"}}, self.PARITY)
        self.assertEqual(rep["status"], de.FAIL)
        self.assertEqual(de.gate_posture(rep), de.WARN)
        self.assertTrue(rep["warnings"])  # explains the data could be incorrect

    def test_pass_posture_is_pass_with_caveat_when_low(self):
        rep = de.run_harness(self._corpus("contract-expected"), {"s1": {"AMT": "5.00"}}, self.PARITY)
        self.assertEqual(de.gate_posture(rep), de.PASS)
        self.assertTrue(rep["warnings"])  # PASS-with-caveat: agrees with assumed, not captured

    def test_not_applicable_posture(self):
        rep = de.run_harness([], {}, self.PARITY)
        self.assertEqual(de.gate_posture(rep), de.NOT_APPLICABLE)
        self.assertEqual(rep["golden_confidence"], "none")


if __name__ == "__main__":
    unittest.main()
