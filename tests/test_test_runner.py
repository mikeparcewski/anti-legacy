#!/usr/bin/env python3
import unittest
import tempfile
import shutil
import json
import os
import sys

# Add scripts directory to sys.path so we can import test_runner
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
from test_runner import TestRunner

class TestTestRunner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.contracts_dir = os.path.join(self.test_dir, "contracts")
        self.workspace_dir = os.path.join(self.test_dir, "target")
        os.makedirs(self.contracts_dir, exist_ok=True)
        os.makedirs(self.workspace_dir, exist_ok=True)

        # Create mock target Python file
        self.module_file = os.path.join(self.workspace_dir, "calc.py")
        with open(self.module_file, "w") as f:
            f.write("class Calculator:\n")
            f.write("    def add_tax(self, amount, rate):\n")
            f.write("        return {\"gross\": round(amount * (1 + rate), 2), \"tax\": round(amount * rate, 2)}\n")
            f.write("    def throw_err(self, val):\n")
            f.write("        if val < 0:\n")
            f.write("            raise ValueError(\"Negative value not allowed\")\n")
            f.write("        return val\n")

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        if self.workspace_dir in sys.path:
            sys.path.remove(self.workspace_dir)

    def test_run_successful_contract(self):
        # Create mock contract
        contract_data = {
            "req_id": "REQ_TAX_ADD",
            "target_component": "calc.Calculator.add_tax",
            "test_scenarios": [
                {
                    "id": "TC-001",
                    "name": "Add tax to positive amount",
                    "inputs": {"amount": 100.0, "rate": 0.15},
                    "expected_output": {"gross": 115.0, "tax": 15.0}
                }
            ]
        }
        
        contract_file = os.path.join(self.contracts_dir, "tax.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "python", self.contracts_dir)
        results = runner.run_tests()
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(results[0]["scenarios"][0]["status"], "PASS")

    def test_run_contract_with_precision_tolerance(self):
        # Create mock contract expecting float output
        contract_data = {
            "req_id": "REQ_TAX_ADD_PRECISION",
            "target_component": "calc.Calculator.add_tax",
            "test_scenarios": [
                {
                    "id": "TC-002",
                    "name": "Check precision tolerance",
                    "inputs": {"amount": 100.000001, "rate": 0.15},
                    # 115.00000115 -> rounded to 115.00 in mock, we check float tolerance
                    "expected_output": {"gross": 115.0, "tax": 15.0}
                }
            ]
        }
        
        contract_file = os.path.join(self.contracts_dir, "tax_precision.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "python", self.contracts_dir)
        results = runner.run_tests()
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "PASS")

    def test_run_contract_with_expected_error(self):
        contract_data = {
            "req_id": "REQ_THROW_ERR",
            "target_component": "calc.Calculator.throw_err",
            "test_scenarios": [
                {
                    "id": "TC-003",
                    "name": "Throw exception for negative input",
                    "inputs": {"val": -5},
                    "expected_output": {},
                    "expected_error": "Negative value not allowed"
                }
            ]
        }
        
        contract_file = os.path.join(self.contracts_dir, "error.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "python", self.contracts_dir)
        results = runner.run_tests()
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(results[0]["scenarios"][0]["status"], "PASS")

    def test_run_contract_mismatch_fails(self):
        # Output mismatch scenario
        contract_data = {
            "req_id": "REQ_TAX_ADD_BAD",
            "target_component": "calc.Calculator.add_tax",
            "test_scenarios": [
                {
                    "id": "TC-004",
                    "name": "Wrong output expectations",
                    "inputs": {"amount": 100.0, "rate": 0.15},
                    "expected_output": {"gross": 999.0, "tax": 15.0}
                }
            ]
        }
        
        contract_file = os.path.join(self.contracts_dir, "tax_bad.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "python", self.contracts_dir)
        results = runner.run_tests()
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["scenarios"][0]["status"], "FAIL")
        self.assertIn("Value mismatch for key 'gross'", results[0]["scenarios"][0]["error"])

    def test_non_python_stack_must_not_silent_pass(self):
        # B3-style contract: an unsupported target_stack (no real per-stack runner
        # wired) must NEVER silently roll up to PASS. The old `else` branch set
        # status='PASS' with a "Skipped execution check" note — a false green on
        # java/go/c#/ts. Uses the same generic calc-style synthetic contract; no
        # test-repo (CardDemo) data.
        contract_data = {
            "req_id": "REQ_TAX_ADD",
            "target_component": "calc.Calculator.add_tax",
            "test_scenarios": [
                {
                    "id": "TC-005",
                    "name": "Add tax to positive amount",
                    "inputs": {"amount": 100.0, "rate": 0.15},
                    "expected_output": {"gross": 115.0, "tax": 15.0}
                }
            ]
        }

        contract_file = os.path.join(self.contracts_dir, "tax_java.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        for stack in ("java", "go"):
            with self.subTest(stack=stack):
                runner = TestRunner(self.workspace_dir, stack, self.contracts_dir)
                results = runner.run_tests()

                self.assertEqual(len(results), 1)
                # Contract must NOT be PASS.
                self.assertNotEqual(results[0]["status"], "PASS")
                scenario = results[0]["scenarios"][0]
                self.assertNotEqual(scenario["status"], "PASS")
                self.assertIn(
                    f"contract execution not supported for stack '{stack}'",
                    scenario.get("error", "")
                )

    def test_non_python_stack_report_is_non_pass(self):
        # The overall written report (and exit code via write_report) must be
        # non-PASS for an unsupported stack — locking the no-silent-pass contract
        # end to end. Generic synthetic contract only.
        contract_data = {
            "req_id": "REQ_TAX_ADD",
            "target_component": "calc.Calculator.add_tax",
            "test_scenarios": [
                {
                    "id": "TC-006",
                    "name": "Add tax to positive amount",
                    "inputs": {"amount": 100.0, "rate": 0.15},
                    "expected_output": {"gross": 115.0, "tax": 15.0}
                }
            ]
        }

        contract_file = os.path.join(self.contracts_dir, "tax_go.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "go", self.contracts_dir)
        runner.run_tests()

        report_path = os.path.join(self.test_dir, "report.json")
        passed = runner.write_report(report_path)

        self.assertFalse(passed, "Unsupported stack must not produce a passing report")
        with open(report_path) as f:
            report = json.load(f)
        self.assertNotEqual(report["status"], "PASS")

if __name__ == "__main__":
    unittest.main()
