#!/usr/bin/env python3
import unittest
import tempfile
import shutil
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import functional_tests as ft


class TestFunctionalTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.contracts_dir = os.path.join(self.test_dir, "contracts")
        self.out_dir = os.path.join(self.test_dir, "authored")
        os.makedirs(os.path.join(self.contracts_dir, "TxnDomain"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _write_contract(self, name, data):
        path = os.path.join(self.contracts_dir, "TxnDomain", name)
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def _valid_contract(self, req_id="RULE-017"):
        return {
            "req_id": req_id,
            "domain": "TxnDomain",
            "target_component": "TransactionPoster.updateAccount",
            "scenarios": [
                {
                    "id": "TC-001",
                    "type": "happy_path",
                    "description": "credit routing",
                    "inputs": {"amount": 100.0, "acctFoundOnRewrite": True},
                    "expected_output": {"rewritten": True, "curr_bal": 1100.0},
                    "expected_error": None,
                },
                {
                    "id": "TC-002",
                    "type": "error",
                    "description": "reject 109",
                    "inputs": {"amount": 100.0, "acctFoundOnRewrite": False},
                    "expected_output": {},
                    "expected_error": "REJECT_109_ACCOUNT_NOT_FOUND",
                },
            ],
        }

    # ---------------- validation ----------------

    def test_valid_contract_passes_validation(self):
        self._write_contract("rule017.contract.json", self._valid_contract())
        ok, report, n = ft.validate_all(self.contracts_dir)
        self.assertTrue(ok, report)
        self.assertEqual(n, 1)
        self.assertEqual(report, {})

    def test_missing_target_component_fails_validation(self):
        c = self._valid_contract()
        del c["target_component"]
        self._write_contract("bad.contract.json", c)
        ok, report, _ = ft.validate_all(self.contracts_dir)
        self.assertFalse(ok)
        errs = next(iter(report.values()))
        self.assertTrue(any("target_component" in e for e in errs))

    def test_scenario_without_expectation_fails_validation(self):
        c = self._valid_contract()
        c["scenarios"] = [
            {"id": "TC-001", "inputs": {"x": 1}}  # no expected_output / expected_error
        ]
        self._write_contract("noexp.contract.json", c)
        ok, report, _ = ft.validate_all(self.contracts_dir)
        self.assertFalse(ok)
        errs = next(iter(report.values()))
        self.assertTrue(any("unverifiable" in e for e in errs))

    def test_duplicate_scenario_ids_fail_validation(self):
        c = self._valid_contract()
        c["scenarios"][1]["id"] = "TC-001"  # collide with the first
        self._write_contract("dupe.contract.json", c)
        ok, report, _ = ft.validate_all(self.contracts_dir)
        self.assertFalse(ok)
        errs = next(iter(report.values()))
        self.assertTrue(any("duplicated" in e for e in errs))

    def test_empty_scenarios_fail_validation(self):
        c = self._valid_contract()
        c["scenarios"] = []
        self._write_contract("empty.contract.json", c)
        ok, report, _ = ft.validate_all(self.contracts_dir)
        self.assertFalse(ok)

    # ---------------- target_component parsing ----------------

    def test_split_target_component_class_and_method(self):
        self.assertEqual(ft.split_target_component("TransactionPoster.updateAccount"),
                         ("TransactionPoster", "updateAccount"))
        self.assertEqual(ft.split_target_component("com.acme.TransactionPoster.post"),
                         ("TransactionPoster", "post"))
        self.assertEqual(ft.split_target_component("Cbtrn01cService"),
                         ("Cbtrn01cService", None))
        # trailing human annotation is stripped
        self.assertEqual(ft.split_target_component("TransactionPoster.updateCategoryBalance (create branch)"),
                         ("TransactionPoster", "updateCategoryBalance"))

    # ---------------- java authoring ----------------

    def test_author_java_emits_junit_per_scenario(self):
        self._write_contract("rule017.contract.json", self._valid_contract())
        result = ft.author_tests(self.contracts_dir, "java", self.out_dir)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(len(result["authored"]), 1)

        files = [f for f in os.listdir(self.out_dir) if f.endswith(".java")]
        self.assertEqual(len(files), 1)
        src = open(os.path.join(self.out_dir, files[0])).read()
        # One @Test per scenario, JUnit imports, and the target symbol referenced.
        self.assertEqual(src.count("@Test"), 2)
        self.assertIn("org.junit.jupiter.api.Test", src)
        self.assertIn("TransactionPoster", src)
        self.assertIn("updateAccount", src)
        # Scenario ids are encoded into method names.
        self.assertIn("scenario_TC_001", src)
        self.assertIn("scenario_TC_002", src)

    def test_author_python_emits_pytest_module(self):
        self._write_contract("rule017.contract.json", self._valid_contract())
        result = ft.author_tests(self.contracts_dir, "python", self.out_dir)
        self.assertEqual(result["status"], "PASS", result)
        files = [f for f in os.listdir(self.out_dir) if f.endswith(".py")]
        self.assertEqual(len(files), 1)
        src = open(os.path.join(self.out_dir, files[0])).read()
        self.assertIn("def test_scenario_tc_001", src)
        self.assertIn("def test_scenario_tc_002", src)
        self.assertIn("TransactionPoster", src)

    def test_unsupported_stack_errors_not_silent_pass(self):
        self._write_contract("rule017.contract.json", self._valid_contract())
        result = ft.author_tests(self.contracts_dir, "go", self.out_dir)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("not yet supported", result["error"])
        # Nothing authored for an unsupported stack.
        self.assertFalse(os.path.isdir(self.out_dir) and os.listdir(self.out_dir))

    def test_invalid_contract_blocks_authoring(self):
        # A valid + an invalid contract: the hard gate must block ALL authoring.
        self._write_contract("good.contract.json", self._valid_contract("RULE-001"))
        bad = self._valid_contract("RULE-002")
        del bad["target_component"]
        self._write_contract("bad.contract.json", bad)

        result = ft.author_tests(self.contracts_dir, "java", self.out_dir)
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["validation_errors"])
        # Hard gate: not even the valid contract was authored.
        self.assertFalse(os.path.isdir(self.out_dir) and os.listdir(self.out_dir))

    def test_no_contracts_errors(self):
        result = ft.author_tests(self.contracts_dir, "java", self.out_dir)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("no", result["error"].lower())

    def test_test_scenarios_shape_is_supported(self):
        # The REQ_* contract shape uses `test_scenarios` not `scenarios`.
        c = {
            "req_id": "REQ_CBTRN01C",
            "target_component": "Cbtrn01cService.run",
            "test_scenarios": [
                {
                    "id": "TC-001",
                    "name": "happy path",
                    "inputs": {"id": "12345"},
                    "expected_output": {"statusCode": 200},
                    "assertion_type": "field_exact",
                }
            ],
        }
        self._write_contract("req.contract.json", c)
        result = ft.author_tests(self.contracts_dir, "java", self.out_dir)
        self.assertEqual(result["status"], "PASS", result)
        files = [f for f in os.listdir(self.out_dir) if f.endswith(".java")]
        self.assertEqual(len(files), 1)
        src = open(os.path.join(self.out_dir, files[0])).read()
        self.assertIn("Cbtrn01cService", src)
        self.assertEqual(src.count("@Test"), 1)

    # ---------------- CLI ----------------

    def test_cli_validate_returns_nonzero_on_bad_contract(self):
        bad = self._valid_contract()
        del bad["target_component"]
        self._write_contract("bad.contract.json", bad)
        rc = ft.main(["validate", "--contracts", self.contracts_dir])
        self.assertEqual(rc, 1)

    def test_cli_author_writes_report(self):
        self._write_contract("rule017.contract.json", self._valid_contract())
        report_path = os.path.join(self.test_dir, "authoring-report.json")
        rc = ft.main([
            "author",
            "--contracts", self.contracts_dir,
            "--stack", "java",
            "--output", self.out_dir,
            "--report", report_path,
        ])
        self.assertEqual(rc, 0)
        with open(report_path) as f:
            report = json.load(f)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["stack"], "java")


class TestFunctionalAcceptanceGate(unittest.TestCase):
    """ISS-14: GATE_3_BUILD must require functional-test-report PASS when present,
    and must NOT phantom-pass on an ERROR (unsupported-stack) report. Exercises
    ValidatorRunner._check_functional_acceptance directly (hermetic — no tools)."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ev_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence")
        os.makedirs(self.ev_dir, exist_ok=True)
        from validator_discovery import ValidatorRunner
        self.runner = ValidatorRunner(self.test_dir,
                                      os.path.join(self.test_dir, "config.json"),
                                      os.path.join(self.test_dir, "manifest.json"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_report(self, status, fail_count=0, error_count=0):
        path = os.path.join(self.ev_dir, "functional-test-report.json")
        with open(path, "w") as f:
            json.dump({"status": status, "fail_count": fail_count,
                       "error_count": error_count}, f)
        return path

    def test_absent_report_is_vacuous_pass(self):
        # Presence is enforced by target-review's done-gate, not this layer.
        self.assertTrue(self.runner._check_functional_acceptance())

    def test_pass_report_clears(self):
        self._write_report("PASS")
        self.assertTrue(self.runner._check_functional_acceptance())

    def test_fail_report_blocks(self):
        self._write_report("FAIL", fail_count=2)
        self.assertFalse(self.runner._check_functional_acceptance())

    def test_error_report_blocks_no_phantom_pass(self):
        # An ERROR report (unsupported stack / missing toolchain) must NOT pass.
        self._write_report("ERROR", error_count=1)
        self.assertFalse(self.runner._check_functional_acceptance())

    def test_unparseable_report_blocks(self):
        path = os.path.join(self.ev_dir, "functional-test-report.json")
        with open(path, "w") as f:
            f.write("{not json")
        self.assertFalse(self.runner._check_functional_acceptance())


if __name__ == "__main__":
    unittest.main()
