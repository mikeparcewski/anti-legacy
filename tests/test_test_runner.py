#!/usr/bin/env python3
import unittest
import tempfile
import shutil
import json
import os
import sys

# Add scripts directory to sys.path so we can import test_runner
# legacy scripts/ insert removed — leaf modules resolved via tests/conftest.py
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

    def test_unsupported_stack_must_not_silent_pass(self):
        # B3-style contract: a stack with NO real per-stack runner wired (e.g. go,
        # csharp) must NEVER silently roll up to PASS. The old `else` branch set
        # status='PASS' with a "Skipped execution check" note — a false green.
        # Uses a generic calc-style synthetic contract; no CardDemo data.
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

        contract_file = os.path.join(self.contracts_dir, "tax_unsup.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        for stack in ("go", "csharp"):
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

    def test_java_no_pom_is_error_not_pass(self):
        # Java IS a supported execution stack now, but executing a Java contract
        # requires a Maven project (pom.xml). With no pom.xml present the runner
        # must ERROR (cannot prove anything), never silently PASS.
        contract_data = {
            "req_id": "RULE-017",
            "target_component": "TransactionPoster.updateAccount",
            "scenarios": [
                {
                    "id": "TC-001",
                    "type": "happy_path",
                    "inputs": {"amount": 100.0},
                    "expected_output": {"rewritten": True}
                }
            ]
        }
        contract_file = os.path.join(self.contracts_dir, "rule017.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "java", self.contracts_dir)
        results = runner.run_tests()

        self.assertEqual(len(results), 1)
        self.assertNotEqual(results[0]["status"], "PASS")
        scenario = results[0]["scenarios"][0]
        self.assertEqual(scenario["status"], "ERROR")
        self.assertIn("pom.xml", scenario.get("error", ""))

    def test_java_passes_when_maven_layer_reports_pass(self):
        # Hermetic Java execution: stub the mvn/exec seam (_run_maven) so no real
        # JDK/Maven is needed. A stubbed Surefire-PASS rolls up to a passing
        # contract AND a passing report.
        contract_data = {
            "req_id": "RULE-017",
            "target_component": "TransactionPoster.updateAccount",
            "scenarios": [
                {
                    "id": "TC-001",
                    "type": "happy_path",
                    "inputs": {"amount": 100.0},
                    "expected_output": {"rewritten": True}
                }
            ]
        }
        contract_file = os.path.join(self.contracts_dir, "rule017_pass.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "java", self.contracts_dir)
        captured = {}

        def fake_run_maven(test_class, test_source, target_component, tc):
            captured["test_class"] = test_class
            captured["test_source"] = test_source
            return {"status": "PASS"}

        runner._run_maven = fake_run_maven
        results = runner.run_tests()

        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(results[0]["scenarios"][0]["status"], "PASS")
        # The generated JUnit source must do REAL behavioral testing — instantiate
        # the target, invoke the method with the scenario inputs, and assert the
        # expected output — NOT merely check the class/method exist (a broken
        # target must be catchable by the generated test, not phantom-pass).
        src = captured["test_source"]
        self.assertIn("TransactionPoster", src)
        self.assertIn("org.junit.jupiter.api.Test", src)
        self.assertIn("newInstance(target)", src)              # instantiates
        self.assertIn("method.invoke(instance, args)", src)    # invokes with inputs
        self.assertIn("looseEq(readField(result", src)         # asserts expected_output
        self.assertIn("Double.valueOf(100.0)", src)            # the scenario input is emitted

        report_path = os.path.join(self.test_dir, "java_report.json")
        passed = runner.write_report(report_path)
        self.assertTrue(passed)
        with open(report_path) as f:
            report = json.load(f)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["stack"], "java")
        self.assertEqual(report["pass_count"], 1)

    def test_java_fails_when_surefire_reports_failure(self):
        # A real Surefire FAIL (stubbed) must roll up to a FAILing contract and a
        # non-PASS report — no false green when the target is wrong.
        contract_data = {
            "req_id": "RULE-099",
            "target_component": "TransactionPoster.updateAccount",
            "scenarios": [
                {
                    "id": "TC-001",
                    "type": "happy_path",
                    "inputs": {"amount": 100.0},
                    "expected_output": {"rewritten": True}
                }
            ]
        }
        contract_file = os.path.join(self.contracts_dir, "rule099_fail.contract.json")
        with open(contract_file, "w") as f:
            json.dump(contract_data, f)

        runner = TestRunner(self.workspace_dir, "java", self.contracts_dir)
        runner._run_maven = lambda tc_class, src, comp, tc: {
            "status": "FAIL", "error": "1 failure(s), 0 error(s)", "detail": "assertNotNull failed"
        }
        results = runner.run_tests()

        self.assertEqual(results[0]["status"], "FAIL")
        self.assertEqual(results[0]["scenarios"][0]["status"], "FAIL")

        report_path = os.path.join(self.test_dir, "java_fail_report.json")
        passed = runner.write_report(report_path)
        self.assertFalse(passed)
        with open(report_path) as f:
            report = json.load(f)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["fail_count"], 1)

    def test_render_java_is_behavioral_not_a_presence_check(self):
        # Regression guard for the adversarial finding: the generated Java test
        # must INVOKE the method with inputs and ASSERT the output — not just
        # reflect getMethods() for the name. A wrong-balance target must be
        # catchable by the generated test.
        runner = TestRunner(self.workspace_dir, "java", self.contracts_dir)
        tc = {"id": "TC9", "inputs": {"amount": 100, "acctId": "A1"},
              "expected_output": {"balance": 250.75}}
        src = runner._render_java_test_source(
            "posting.TransactionPoster.updateAccount", tc, "T9", "scenario_TC9")
        self.assertIn("newInstance(target)", src)
        self.assertIn("method.invoke(instance, args)", src)
        self.assertIn('looseEq(readField(result, "balance")', src)
        self.assertIn("Long.valueOf(100L)", src)        # int input emitted + coerced
        self.assertIn("Double.valueOf(250.75)", src)    # expected output emitted
        # It must NOT be a presence-only check (the old faked form looped
        # getMethods() and asserted only hasMethod, never invoking).
        self.assertNotIn("boolean hasMethod = false;", src)

    def test_render_java_expected_error_asserts_a_throw(self):
        runner = TestRunner(self.workspace_dir, "java", self.contracts_dir)
        tc = {"id": "TCE", "inputs": {"amount": -1},
              "expected_error": "InsufficientFundsException"}
        src = runner._render_java_test_source(
            "posting.AccountGate.debit", tc, "TE", "scenario_TCE")
        # The error path must actually call the method and require it to throw.
        self.assertIn("method.invoke(instance, args)", src)
        self.assertIn("InvocationTargetException expected", src)
        self.assertIn("expected error", src)            # fail() if no throw

    def test_parse_surefire_reads_real_xml(self):
        # Exercise the real Surefire XML parser against a synthetic report — this
        # is the parsing path used after a real `mvn test`, kept hermetic.
        runner = TestRunner(self.workspace_dir, "java", self.contracts_dir)
        report_dir = os.path.join(self.workspace_dir, "target", "surefire-reports")
        os.makedirs(report_dir, exist_ok=True)

        # PASS report
        with open(os.path.join(report_dir, "TEST-AntiLegacy_Foo_TC_001_AcceptanceTest.xml"), "w") as f:
            f.write(
                '<?xml version="1.0"?>'
                '<testsuite name="AntiLegacy_Foo_TC_001_AcceptanceTest" '
                'tests="1" failures="0" errors="0">'
                '<testcase name="scenario_TC_001"/></testsuite>'
            )
        verdict = runner._parse_surefire("AntiLegacy_Foo_TC_001_AcceptanceTest")
        self.assertEqual(verdict["status"], "PASS")

        # FAIL report
        with open(os.path.join(report_dir, "TEST-AntiLegacy_Bar_TC_002_AcceptanceTest.xml"), "w") as f:
            f.write(
                '<?xml version="1.0"?>'
                '<testsuite name="AntiLegacy_Bar_TC_002_AcceptanceTest" '
                'tests="1" failures="1" errors="0">'
                '<testcase name="scenario_TC_002">'
                '<failure message="boom">stack</failure></testcase></testsuite>'
            )
        verdict = runner._parse_surefire("AntiLegacy_Bar_TC_002_AcceptanceTest")
        self.assertEqual(verdict["status"], "FAIL")
        self.assertIn("boom", verdict.get("detail", ""))

        # Missing report -> None (so the runner can fall back to exit code).
        self.assertIsNone(runner._parse_surefire("AntiLegacy_Nope_AcceptanceTest"))

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
