#!/usr/bin/env python3
"""
Executable functional test runner for the anti-legacy modernization pipeline.
Reads declarative test contracts from `.anti-legacy/contracts/{domain}/*.contract.json`
and executes them against the target codebase, verifying inputs, outputs, and parity.

This is the POST-BUILD half of the functional-acceptance capability: it runs the
authored scenarios against the BUILT target and records a real pass/fail per
scenario. Python contracts execute in-process; Java contracts are executed by
generating a JUnit test from the contract, running Maven against the built
target, and parsing the Surefire results. Any stack with no real per-stack
runner returns ERROR (never a silent PASS) so the gate cannot phantom-pass.
"""
import os
import sys
import json
import shutil
import subprocess
import importlib.util
import argparse
import traceback
import xml.etree.ElementTree as ET

class TestRunner:
    def __init__(self, workspace_path, target_stack, contracts_dir=".anti-legacy/contracts"):
        self.workspace_path = os.path.abspath(workspace_path)
        self.target_stack = target_stack.lower()
        self.contracts_dir = os.path.abspath(contracts_dir)
        self.results = []
        # Subprocess timeout for the Java/Maven build+test cycle (seconds).
        self.java_timeout = 600

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
            elif self.target_stack in ("java", "maven"):
                self._execute_java_tc(target_component, tc, tc_res)
            else:
                # No real per-stack execution runner is wired for this stack.
                # Emit an explicit non-PASS verdict so the contract cannot roll up to
                # PASS — a silent skip-PASS would be a false green on go/c#/ts.
                # Mirrors the validator_discovery B3 contract: unsupported stacks
                # ERROR, never silently pass.
                tc_res["status"] = "ERROR"
                tc_res["error"] = f"contract execution not supported for stack '{self.target_stack}'"
            
            print(f"    Verdict: {tc_res['status']}" + (f" (Error: {tc_res.get('error')})" if tc_res['status'] in ('FAIL', 'ERROR') else ""))
            contract_results.append(tc_res)

        # Overall contract status. PASS only when every scenario passed. We keep
        # ERROR distinct from FAIL: ERROR means the runner could not execute (no
        # toolchain / unsupported stack / missing target) — neither rolls up to
        # PASS, preserving the no-false-positive property, but the distinction
        # tells the operator whether to fix the build or fix the environment.
        statuses = [r["status"] for r in contract_results]
        if statuses and all(s == "PASS" for s in statuses):
            contract_status = "PASS"
        elif any(s == "FAIL" for s in statuses):
            contract_status = "FAIL"
        else:
            contract_status = "ERROR"
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

    # ------------------------------------------------------------------
    # Java / Maven execution
    # ------------------------------------------------------------------

    def _split_java_target(self, target_component):
        """Parse `[pkg.]ClassName[.method]` from a contract target_component.
        Returns (class_simple_name, method_name_or_None). Strips any trailing
        human annotation after a space (e.g. 'Foo.bar (create branch)')."""
        raw = str(target_component or "").strip().split(" ")[0]
        parts = [p for p in raw.split(".") if p]
        if not parts:
            return ("Target", None)
        if len(parts) == 1:
            return (parts[0], None)
        if parts[-1][:1].islower():
            return (parts[-2], parts[-1])
        return (parts[-1], None)

    def _java_test_identifier(self, target_component, tc):
        """Build the JUnit test class + method names for a scenario."""
        class_simple, _ = self._split_java_target(target_component)
        sid = tc.get("id", "TC")
        safe_cls = "".join(c if c.isalnum() or c == "_" else "_" for c in class_simple)
        safe_sid = "".join(c if c.isalnum() or c == "_" else "_" for c in str(sid))
        if safe_sid and safe_sid[0].isdigit():
            safe_sid = "_" + safe_sid
        test_class = f"AntiLegacy_{safe_cls}_{safe_sid}_AcceptanceTest"
        return test_class, f"scenario_{safe_sid}"

    @staticmethod
    def _java_literal(value):
        """Emit a Java expression (a boxed Object) for a JSON-scalar contract
        value, known at generation time. Numbers stay boxed (Long/Double) and are
        coerced to the method's declared parameter type at run time; dict/list
        values are emitted as their JSON string (best-effort)."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "Boolean.valueOf(%s)" % ("true" if value else "false")
        if isinstance(value, int):
            return "Long.valueOf(%dL)" % value
        if isinstance(value, float):
            return "Double.valueOf(%s)" % repr(value)
        if isinstance(value, str):
            return json.dumps(value)            # a valid Java String literal
        return json.dumps(json.dumps(value))    # dict/list -> JSON string literal

    def _render_java_test_source(self, target_component, tc, test_class, test_method):
        """Render a single-scenario JUnit 5 test that REALLY exercises the built
        target: it resolves the class, instantiates it (no-arg ctor), invokes the
        named method with the scenario's inputs (coerced to the declared parameter
        types via reflection), and asserts the result against expected_output —
        or asserts an exception is thrown for expected_error. A behaviorally-broken
        target (wrong return value) therefore FAILS; a missing class/method/ctor
        ERRORs honestly. This mirrors the dynamic Python path (_execute_python_tc).
        The contract's input ORDER is taken as the method's positional arg order."""
        class_simple, method_name = self._split_java_target(target_component)
        inputs = tc.get("inputs", {}) or {}
        expected_output = tc.get("expected_output", {})
        expected_error = tc.get("expected_error", tc.get("expected_err"))
        sid = tc.get("id", "TC")
        args_java = ", ".join(self._java_literal(v) for v in inputs.values())

        L = []
        L.append("import org.junit.jupiter.api.Test;")
        L.append("import static org.junit.jupiter.api.Assertions.*;")
        L.append("import java.lang.reflect.*;")
        L.append("import java.math.BigDecimal;")
        L.append("")
        L.append("public class %s {" % test_class)
        L.append("    @Test")
        L.append("    public void %s() throws Exception {" % test_method)
        L.append("        // scenario %s inputs=%s expected_output=%s expected_error=%s"
                 % (sid, json.dumps(inputs), json.dumps(expected_output), json.dumps(expected_error)))
        L.append("        Class<?> target = Class.forName(%s);" % json.dumps(class_simple))
        L.append("        Object instance = newInstance(target);")
        L.append("        Object[] rawArgs = new Object[]{ %s };" % args_java)
        if method_name:
            L.append("        Method method = findMethod(target, %s, rawArgs.length);" % json.dumps(method_name))
            L.append('        assertNotNull(method, "no method %s with " + rawArgs.length + " params on %s");'
                     % (method_name, class_simple))
        else:
            L.append("        Method method = findFirstPublicMethod(target, rawArgs.length);")
            L.append('        assertNotNull(method, "no public method with " + rawArgs.length + " params on %s");'
                     % class_simple)
        L.append("        method.setAccessible(true);")
        L.append("        Object[] args = coerce(method.getParameterTypes(), rawArgs);")
        if expected_error:
            L.append("        try {")
            L.append("            method.invoke(instance, args);")
            L.append('            fail("expected error %s but the call succeeded");'
                     % json.dumps(str(expected_error))[1:-1])
            L.append("        } catch (InvocationTargetException expected) {")
            L.append("            // contract requires the target to throw — satisfied")
            L.append("        }")
        else:
            L.append("        Object result = method.invoke(instance, args);")
            if isinstance(expected_output, dict) and expected_output:
                for k, ev in expected_output.items():
                    L.append("        assertTrue(looseEq(readField(result, %s), %s), %s);"
                             % (json.dumps(k), self._java_literal(ev),
                                json.dumps("field %s mismatch (got=" % k) + ' + String.valueOf(readField(result, %s)))' % json.dumps(k)))
            elif expected_output not in (None, {}, ""):
                L.append("        assertTrue(looseEq(result, %s), %s);"
                         % (self._java_literal(expected_output),
                            json.dumps("return value mismatch (got=") + ' + String.valueOf(result))'))
        L.append("    }")
        L.extend(self._JAVA_REFLECTION_HELPERS)
        L.append("}")
        return "\n".join(L) + "\n"

    # Inlined reflection helpers the generated test relies on (instantiate,
    # locate the method, coerce JSON-scalar args to declared param types, read a
    # result field via getter/field, compare with numeric tolerance).
    _JAVA_REFLECTION_HELPERS = [
        "",
        "    static Object newInstance(Class<?> t) throws Exception {",
        "        try { Constructor<?> c = t.getDeclaredConstructor(); c.setAccessible(true); return c.newInstance(); }",
        "        catch (NoSuchMethodException e) { fail(\"target \" + t.getName() + \" has no no-arg constructor\"); return null; }",
        "    }",
        "    static Method findMethod(Class<?> t, String name, int n) {",
        "        Method any = null;",
        "        for (Method m : t.getMethods()) { if (m.getName().equals(name) && m.getParameterCount()==n) return m; if (m.getName().equals(name)) any = m; }",
        "        return any;",
        "    }",
        "    static Method findFirstPublicMethod(Class<?> t, int n) {",
        "        for (Method m : t.getDeclaredMethods()) if (Modifier.isPublic(m.getModifiers()) && m.getParameterCount()==n && !m.getName().equals(\"equals\")) return m;",
        "        return null;",
        "    }",
        "    static Object[] coerce(Class<?>[] types, Object[] args) {",
        "        Object[] out = new Object[args.length];",
        "        for (int i=0;i<args.length;i++) out[i] = coerceOne(i<types.length?types[i]:Object.class, args[i]);",
        "        return out;",
        "    }",
        "    static Object coerceOne(Class<?> t, Object v) {",
        "        if (v == null) return null;",
        "        if (t.isInstance(v)) return v;",
        "        if ((t==int.class||t==Integer.class) && v instanceof Number) return ((Number)v).intValue();",
        "        if ((t==long.class||t==Long.class) && v instanceof Number) return ((Number)v).longValue();",
        "        if ((t==double.class||t==Double.class) && v instanceof Number) return ((Number)v).doubleValue();",
        "        if ((t==float.class||t==Float.class) && v instanceof Number) return ((Number)v).floatValue();",
        "        if ((t==short.class||t==Short.class) && v instanceof Number) return ((Number)v).shortValue();",
        "        if (t==boolean.class||t==Boolean.class) return Boolean.valueOf(String.valueOf(v));",
        "        if (t==BigDecimal.class) return new BigDecimal(v.toString());",
        "        if (t==String.class) return String.valueOf(v);",
        "        try { return t.getConstructor(String.class).newInstance(v.toString()); } catch (Exception e) { return v; }",
        "    }",
        "    static Object readField(Object o, String key) {",
        "        if (o == null) return null;",
        "        String cap = key.isEmpty()?key:Character.toUpperCase(key.charAt(0))+key.substring(1);",
        "        for (String mn : new String[]{\"get\"+cap, \"is\"+cap, key}) {",
        "            try { Method m = o.getClass().getMethod(mn); m.setAccessible(true); return m.invoke(o); } catch (Exception ignored) {}",
        "        }",
        "        try { Field f = o.getClass().getField(key); return f.get(o); } catch (Exception ignored) {}",
        "        return o;",
        "    }",
        "    static boolean looseEq(Object a, Object b) {",
        "        if (a == null || b == null) return a == b;",
        "        if (a instanceof Number && b instanceof Number) return Math.abs(((Number)a).doubleValue()-((Number)b).doubleValue()) < 1e-5;",
        "        try { return Math.abs(Double.parseDouble(a.toString())-Double.parseDouble(b.toString())) < 1e-5; } catch (Exception ignored) {}",
        "        return String.valueOf(a).equals(String.valueOf(b));",
        "    }",
    ]

    def _run_maven(self, test_class, test_source, target_component, tc):
        """Execute a single JUnit scenario via Maven against the built target.

        This is the ONLY place real `mvn` / filesystem side effects happen, and
        it is the seam tests stub out (so the suite is hermetic — no JDK/Maven
        required in CI). Returns a dict:
            {"status": "PASS"|"FAIL"|"ERROR", "error": <str?>, "detail": <str?>}

        Real behaviour:
          * Requires a pom.xml in the workspace and `mvn` on PATH; if either is
            missing -> ERROR (never PASS).
          * Writes the generated test under src/test/java, runs
            `mvn -q -Dtest=<class> test`, then parses the Surefire XML report.
        """
        # Maven toolchain must be present; absence is ERROR, not PASS.
        pom = os.path.join(self.workspace_path, "pom.xml")
        if not os.path.exists(pom):
            return {
                "status": "ERROR",
                "error": f"no pom.xml under {self.workspace_path}; cannot run Maven for Java contract",
            }
        if shutil.which("mvn") is None:
            return {
                "status": "ERROR",
                "error": "Maven ('mvn') is not installed on PATH; cannot execute Java functional test",
            }

        test_dir = os.path.join(self.workspace_path, "src", "test", "java")
        os.makedirs(test_dir, exist_ok=True)
        test_path = os.path.join(test_dir, f"{test_class}.java")
        with open(test_path, "w") as f:
            f.write(test_source)

        try:
            proc = subprocess.run(
                ["mvn", "-q", f"-Dtest={test_class}", "test"],
                cwd=self.workspace_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.java_timeout,
            )
        except subprocess.TimeoutExpired as e:
            return {"status": "ERROR", "error": f"mvn test timed out after {e.timeout}s"}
        except Exception as e:
            return {"status": "ERROR", "error": f"failed to invoke mvn: {e}"}

        parsed = self._parse_surefire(test_class)
        if parsed is not None:
            return parsed

        # No Surefire report produced — fall back to the process exit code, but a
        # zero exit with no report is still ERROR (we cannot confirm a real PASS).
        if proc.returncode != 0:
            return {
                "status": "FAIL",
                "error": "mvn test exited non-zero and produced no Surefire report",
                "detail": (proc.stderr or proc.stdout or "")[-1000:],
            }
        return {
            "status": "ERROR",
            "error": "mvn test exited 0 but produced no Surefire report; cannot confirm a real PASS",
        }

    def _surefire_dir(self):
        return os.path.join(self.workspace_path, "target", "surefire-reports")

    def _parse_surefire(self, test_class):
        """Parse the Surefire XML report for `test_class`. Returns a verdict dict
        or None when no report is found. A report with failures/errors -> FAIL,
        otherwise PASS."""
        report_dir = self._surefire_dir()
        candidates = [
            os.path.join(report_dir, f"TEST-{test_class}.xml"),
        ]
        # Surefire prefixes with the (possibly empty) package; also scan the dir.
        if os.path.isdir(report_dir):
            for name in os.listdir(report_dir):
                if name.endswith(".xml") and test_class in name:
                    full = os.path.join(report_dir, name)
                    if full not in candidates:
                        candidates.append(full)

        report_path = next((c for c in candidates if os.path.exists(c)), None)
        if not report_path:
            return None

        try:
            tree = ET.parse(report_path)
            root = tree.getroot()
        except Exception as e:
            return {"status": "ERROR", "error": f"could not parse Surefire report {report_path}: {e}"}

        failures = int(root.get("failures", "0") or "0")
        errors = int(root.get("errors", "0") or "0")
        tests = int(root.get("tests", "0") or "0")

        if tests == 0:
            return {"status": "ERROR", "error": f"Surefire report {report_path} ran 0 tests"}
        if failures > 0 or errors > 0:
            # Collect the first failure/error message for diagnostics.
            detail = ""
            for tc_el in root.iter("testcase"):
                fail_el = tc_el.find("failure")
                err_el = tc_el.find("error")
                node = fail_el if fail_el is not None else err_el
                if node is not None:
                    detail = node.get("message") or (node.text or "")
                    break
            return {"status": "FAIL", "error": f"{failures} failure(s), {errors} error(s)", "detail": detail[:1000]}
        return {"status": "PASS"}

    def _execute_java_tc(self, target_component, tc, tc_res):
        """Execute a single Java scenario against the built target via Maven.

        Generates a JUnit test for the scenario, runs it, and records the real
        verdict. No mock PASS: a missing toolchain, a missing target class, or a
        Surefire failure all surface as ERROR/FAIL — never a false green."""
        if not target_component or not str(target_component).strip():
            tc_res["status"] = "ERROR"
            tc_res["error"] = "contract has no target_component; cannot bind a Java test"
            return

        test_class, test_method = self._java_test_identifier(target_component, tc)
        test_source = self._render_java_test_source(target_component, tc, test_class, test_method)

        outcome = self._run_maven(test_class, test_source, target_component, tc)
        status = outcome.get("status", "ERROR")
        tc_res["status"] = status
        tc_res["test_class"] = test_class
        if status != "PASS":
            err = outcome.get("error", "Java functional test did not pass")
            if outcome.get("detail"):
                err = f"{err}: {outcome['detail']}"
            tc_res["error"] = err

    def _compare_values(self, actual, expected):
        """Compare values, accommodating rounding/precision tolerance."""
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            # Float tolerance for money/COMP-3 checks
            return abs(actual - expected) < 1e-5
        return str(actual) == str(expected)

    def write_report(self, output_path):
        """Save results as build-evidence JSON.

        Overall status is PASS only when every contract passed; ERROR when no
        contract FAILed but at least one could not execute (unsupported stack /
        missing toolchain); FAIL when any contract had a real assertion failure.
        Neither ERROR nor FAIL is a passing report — the gate treats both as a
        block, preserving the no-false-positive property."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        statuses = [r["status"] for r in self.results]
        pass_count = sum(1 for s in statuses if s == "PASS")
        fail_count = sum(1 for s in statuses if s == "FAIL")
        error_count = sum(1 for s in statuses if s == "ERROR")

        if statuses and fail_count == 0 and error_count == 0:
            status = "PASS"
        elif fail_count > 0:
            status = "FAIL"
        elif error_count > 0:
            status = "ERROR"
        else:
            # No contracts evaluated at all -> not a pass (nothing was proven).
            status = "ERROR"

        report = {
            "scope": "functional-testing",
            "phase": "post-build",
            "claim": "functional-acceptance-passes",
            "stack": self.target_stack,
            "status": status,
            "contracts_evaluated": len(self.results),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "error_count": error_count,
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
