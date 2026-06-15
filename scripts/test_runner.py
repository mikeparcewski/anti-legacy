#!/usr/bin/env python3
"""
Executable functional test runner for the anti-legacy modernization pipeline.
Reads declarative test contracts from `.anti-legacy/contracts/{domain}/*.contract.json`
and executes them against the target codebase, verifying inputs, outputs, and parity.
"""
import os
import sys
import json
import importlib.util
import argparse
import traceback

class TestRunner:
    def __init__(self, workspace_path, target_stack, contracts_dir=".anti-legacy/contracts"):
        self.workspace_path = os.path.abspath(workspace_path)
        self.target_stack = target_stack.lower()
        self.contracts_dir = os.path.abspath(contracts_dir)
        self.results = []

    def discover_contracts(self):
        """Find all contract.json files in the contracts directory."""
        contracts = []
        if not os.path.exists(self.contracts_dir):
            return contracts
        
        for root, _, files in os.walk(self.contracts_dir):
            for file in files:
                if file.endswith(".contract.json"):
                    contracts.append(os.path.join(root, file))
        return sorted(contracts)

    def run_tests(self):
        """Discover and execute all contracts."""
        contracts = self.discover_contracts()
        print(f"Discovered {len(contracts)} test contracts in {self.contracts_dir}")
        
        if self.target_stack == "python":
            # Add workspace path to sys.path so we can import target modules
            if self.workspace_path not in sys.path:
                sys.path.insert(0, self.workspace_path)

        for contract_path in contracts:
            self._run_contract(contract_path)

        return self.results

    def _run_contract(self, contract_path):
        """Run a single contract file."""
        try:
            with open(contract_path, "r") as f:
                contract = json.load(f)
        except Exception as e:
            self.results.append({
                "contract": contract_path,
                "status": "FAIL",
                "error": f"Failed to load contract JSON: {e}"
            })
            return

        req_id = contract.get("req_id", "UNKNOWN")
        target_component = contract.get("target_component", "")
        scenarios = contract.get("test_scenarios", contract.get("scenarios", []))
        
        print(f"\nRunning Contract [{req_id}] for {target_component} ({len(scenarios)} scenarios)")
        
        contract_results = []
        
        for tc in scenarios:
            tc_id = tc.get("id", "TC")
            tc_name = tc.get("name", tc.get("description", ""))
            inputs = tc.get("inputs", {})
            expected_output = tc.get("expected_output", {})
            expected_err = tc.get("expected_error", tc.get("expected_err", None))
            
            print(f"  - Scenario {tc_id}: {tc_name}")
            
            tc_res = {
                "id": tc_id,
                "name": tc_name,
                "status": "PENDING"
            }

            if self.target_stack == "python":
                self._execute_python_tc(target_component, tc, tc_res)
            else:
                # No real per-stack execution runner is wired for non-Python stacks.
                # Emit an explicit non-PASS verdict so the contract cannot roll up to
                # PASS — a silent skip-PASS would be a false green on java/go/c#/ts.
                # Mirrors the validator_discovery B3 contract: unsupported stacks FAIL,
                # never silently pass.
                tc_res["status"] = "ERROR"
                tc_res["error"] = f"contract execution not supported for stack '{self.target_stack}'"
            
            print(f"    Verdict: {tc_res['status']}" + (f" (Error: {tc_res.get('error')})" if tc_res['status'] == 'FAIL' else ""))
            contract_results.append(tc_res)

        # Overall contract status
        contract_status = "PASS" if all(r["status"] == "PASS" for r in contract_results) else "FAIL"
        self.results.append({
            "req_id": req_id,
            "contract": os.path.basename(contract_path),
            "status": contract_status,
            "scenarios": contract_results
        })

    def _execute_python_tc(self, target_component, tc, tc_res):
        """Dynamically load and run Python target classes."""
        inputs = tc.get("inputs", {})
        expected_output = tc.get("expected_output", {})
        expected_err = tc.get("expected_error", tc.get("expected_err", None))
        method_name = tc.get("method", None)

        try:
            parts = target_component.split(".")
            if len(parts) >= 3:
                module_name = ".".join(parts[:-2])
                class_name = parts[-2]
                method_name = parts[-1]
            elif len(parts) == 2:
                module_name = parts[0]
                class_name = parts[1]
            else:
                tc_res["status"] = "FAIL"
                tc_res["error"] = f"Invalid target_component format '{target_component}'. Expected module_name.ClassName or module_name.ClassName.method_name"
                return

            # Import module
            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                # Try loading by direct file path check
                module_file = os.path.join(self.workspace_path, module_name.replace(".", "/") + ".py")
                if os.path.exists(module_file):
                    spec = importlib.util.spec_from_file_location(module_name, module_file)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                else:
                    raise e

            # Instantiate class
            klass = getattr(module, class_name)
            instance = klass()

            # Find matching callable method on the class
            method_to_call = None
            if method_name:
                if not hasattr(instance, method_name):
                    tc_res["status"] = "FAIL"
                    tc_res["error"] = f"Method '{method_name}' not found on target class '{class_name}'"
                    return
                method_to_call = getattr(instance, method_name)
            else:
                # Heuristic: Find first method that doesn't start with underscore
                methods = [m for m in dir(instance) if callable(getattr(instance, m)) and not m.startswith("_")]
                if not methods:
                    tc_res["status"] = "FAIL"
                    tc_res["error"] = f"No public methods found on target class '{class_name}'"
                    return
                # Select first method
                method_to_call = getattr(instance, methods[0])
            
            # Call method
            try:
                # Try calling with kwargs
                actual_output = method_to_call(**inputs)
            except TypeError:
                try:
                    # Try calling with positional dict
                    actual_output = method_to_call(inputs)
                except Exception as e:
                    # Re-raise
                    raise e
            
            # Verify Output
            if expected_err:
                tc_res["status"] = "FAIL"
                tc_res["error"] = f"Expected error '{expected_err}' but execution succeeded."
                return

            # If expected_output is a dictionary, we assert subset match or exact match
            if isinstance(expected_output, dict):
                if not isinstance(actual_output, dict):
                    # Check if actual output has attributes matching keys
                    for k, expected_val in expected_output.items():
                        if not hasattr(actual_output, k) and not hasattr(actual_output, k.lower()):
                            tc_res["status"] = "FAIL"
                            tc_res["error"] = f"Expected output dictionary/object key '{k}' missing. Got: {actual_output}"
                            return
                        actual_val = getattr(actual_output, k, getattr(actual_output, k.lower(), None))
                        if not self._compare_values(actual_val, expected_val):
                            tc_res["status"] = "FAIL"
                            tc_res["error"] = f"Value mismatch for key '{k}'. Expected {expected_val}, got {actual_val}"
                            return
                else:
                    for k, expected_val in expected_output.items():
                        if k not in actual_output:
                            tc_res["status"] = "FAIL"
                            tc_res["error"] = f"Expected key '{k}' missing from output. Got: {actual_output}"
                            return
                        actual_val = actual_output[k]
                        if not self._compare_values(actual_val, expected_val):
                            tc_res["status"] = "FAIL"
                            tc_res["error"] = f"Value mismatch for key '{k}'. Expected {expected_val}, got {actual_val}"
                            return
            else:
                if not self._compare_values(actual_output, expected_output):
                    tc_res["status"] = "FAIL"
                    tc_res["error"] = f"Output mismatch. Expected {expected_output}, got {actual_output}"
                    return

            tc_res["status"] = "PASS"

        except Exception as e:
            if expected_err:
                # Check if the error string matches
                err_str = str(e)
                if expected_err.lower() in err_str.lower():
                    tc_res["status"] = "PASS"
                else:
                    tc_res["status"] = "FAIL"
                    tc_res["error"] = f"Error mismatch. Expected message containing '{expected_err}', got '{err_str}'"
            else:
                tc_res["status"] = "FAIL"
                tc_res["error"] = f"Execution threw unexpected exception: {e}\n{traceback.format_exc()}"

    def _compare_values(self, actual, expected):
        """Compare values, accommodating rounding/precision tolerance."""
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            # Float tolerance for money/COMP-3 checks
            return abs(actual - expected) < 1e-5
        return str(actual) == str(expected)

    def write_report(self, output_path):
        """Save results as build-evidence JSON."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        status = "PASS" if all(r["status"] == "PASS" for r in self.results) else "FAIL"
        
        report = {
            "scope": "functional-testing",
            "phase": "pre-build",
            "claim": "functional-parity-passes",
            "status": status,
            "contracts_evaluated": len(self.results),
            "results": self.results
        }
        
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)
            
        print(f"\nFunctional test report saved to {output_path} (Overall: {status})")
        return status == "PASS"

def main():
    parser = argparse.ArgumentParser(description="Runs functional parity test contracts against target code.")
    parser.add_argument("--workspace", required=True, help="Path to target codebase")
    parser.add_argument("--stack", required=True, help="Target state stack (e.g. python, java, go)")
    parser.add_argument("--contracts", default=".anti-legacy/contracts", help="Path to test contracts folder")
    parser.add_argument("--report", required=True, help="Path to write functional test report JSON")
    
    args = parser.parse_args()
    
    runner = TestRunner(args.workspace, args.stack, args.contracts)
    runner.run_tests()
    success = runner.write_report(args.report)
    
    if not success:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
