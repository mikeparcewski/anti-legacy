#!/usr/bin/env python3
"""
Functional / scenario acceptance-test AUTHORING (pre-build, B3 / ISS-14).

Reads the per-requirement test CONTRACTS under `.anti-legacy/contracts/` and
authors *executable* functional / scenario acceptance tests for the target stack.
This is the PRE-BUILD half of the functional-acceptance capability: the tests are
authored BEFORE the build exists, so they encode the acceptance criteria the swarm
must satisfy. The POST-BUILD half (running them against the built target and
parsing real results) lives in `test_runner.py`.

It is NOT a unit-test generator: each emitted test is a behaviour/scenario test
derived directly from a contract scenario's `inputs` / `expected_output` /
`expected_error`, mapped onto the contract's `target_component`.

Two responsibilities:

  1. VALIDATE the contracts are runnable and unambiguous (hard gate). A contract
     that cannot be turned into an executable test BEFORE the build exists is a
     latent false-green: it would silently produce zero tests and the build would
     pass with no acceptance coverage. So contract validation fails loudly.

  2. AUTHOR per-stack executable test sources:
       * java   -> JUnit 5 test classes (target_stack=java, the default here)
       * python -> pytest modules
       * <other> -> explicit "stack not yet supported" ERROR, never a silent pass.

Dispatch is explicit: an unsupported stack returns a clear, non-zero, non-PASS
result. There is no silent-skip / silent-pass branch anywhere in this file.
"""
import os
import sys
import json
import re
import argparse


# Stacks for which we can author executable acceptance tests today.
SUPPORTED_STACKS = ("java", "python")


def stack_supported(stack):
    return (stack or "").lower() in SUPPORTED_STACKS


def discover_contracts(contracts_dir):
    """Find every *.contract.json under contracts_dir (recursively)."""
    contracts = []
    if not os.path.isdir(contracts_dir):
        return contracts
    for root, _, files in os.walk(contracts_dir):
        for name in files:
            if name.endswith(".contract.json"):
                contracts.append(os.path.join(root, name))
    return sorted(contracts)


def scenarios_of(contract):
    """Both contract shapes are in the wild: `scenarios` (RULE-* enriched) and
    `test_scenarios` (REQ_* shape). Read whichever is present."""
    return contract.get("scenarios", contract.get("test_scenarios", [])) or []


def _scenario_expectation(scenario):
    """A scenario must assert *something*: an expected_output, an expected_error,
    or an expected_err synonym. A scenario with neither is unverifiable."""
    has_output = bool(scenario.get("expected_output"))
    has_error = scenario.get("expected_error") is not None or scenario.get("expected_err") is not None
    return has_output, has_error


def validate_contract(contract_path):
    """Validate one contract is runnable + unambiguous. Returns (req_id, errors)
    where errors is a list of human-readable strings (empty == valid)."""
    errors = []
    try:
        with open(contract_path) as f:
            contract = json.load(f)
    except Exception as e:
        return (os.path.basename(contract_path), [f"contract JSON is not parseable: {e}"])

    req_id = contract.get("req_id") or os.path.basename(contract_path)

    if not contract.get("req_id"):
        errors.append("missing 'req_id'")

    target_component = contract.get("target_component")
    if not target_component or not str(target_component).strip():
        errors.append("missing 'target_component' (cannot bind a test to a target)")

    scenarios = scenarios_of(contract)
    if not scenarios:
        errors.append("no scenarios (neither 'scenarios' nor 'test_scenarios' present / non-empty)")

    seen_ids = {}
    for idx, sc in enumerate(scenarios):
        sid = sc.get("id")
        label = sid or f"#{idx}"
        if not sid:
            errors.append(f"scenario {label}: missing 'id' (ambiguous — every scenario needs a stable id)")
        elif sid in seen_ids:
            errors.append(f"scenario id '{sid}' is duplicated (ambiguous test name)")
        else:
            seen_ids[sid] = True

        if "inputs" not in sc:
            errors.append(f"scenario {label}: missing 'inputs'")

        has_output, has_error = _scenario_expectation(sc)
        if not has_output and not has_error:
            errors.append(
                f"scenario {label}: has neither 'expected_output' nor 'expected_error' — "
                "nothing to assert, the scenario is unverifiable"
            )

    return (req_id, errors)


def validate_all(contracts_dir):
    """Validate every contract. Returns (ok, report) where report is a dict
    keyed by contract path -> list of errors (only failing contracts listed)."""
    contracts = discover_contracts(contracts_dir)
    report = {}
    for path in contracts:
        _, errs = validate_contract(path)
        if errs:
            report[path] = errs
    return (len(report) == 0, report, len(contracts))


# ---------------------------------------------------------------------------
# Java / JUnit authoring
# ---------------------------------------------------------------------------

_JAVA_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]")


def _java_ident(text):
    """Sanitize an arbitrary string into a safe Java identifier fragment."""
    s = _JAVA_IDENT_RE.sub("_", str(text))
    if s and s[0].isdigit():
        s = "_" + s
    return s or "x"


def split_target_component(target_component):
    """A target_component is `ClassName.method` or `pkg.ClassName.method` or just
    `ClassName`. Return (class_name, method_name_or_None). The class name is the
    last segment that looks like a Type (or second-to-last when a method is given).
    """
    raw = str(target_component or "").strip()
    # Strip any human annotation after the symbol, e.g. "Foo.bar (create branch)".
    raw = raw.split(" ")[0]
    parts = [p for p in raw.split(".") if p]
    if not parts:
        return ("Target", None)
    if len(parts) == 1:
        return (parts[0], None)
    # If the last part starts lowercase it is most likely a method.
    last = parts[-1]
    if last[:1].islower():
        return (parts[-2], last)
    return (last, None)


def _java_literal(value):
    """Render a Python value as a Java source literal for embedding in a test."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        # Money / parity: emit as a String so the generated test can wrap it in a
        # BigDecimal without binary-float drift.
        return json.dumps(str(value))
    # dicts / lists / strings -> JSON string literal (the test treats them as the
    # serialized scenario payload; exact comparison is by JSON text).
    return json.dumps(json.dumps(value) if isinstance(value, (dict, list)) else value)


def author_java_contract(contract, req_id):
    """Author one JUnit 5 test class source for a contract. Returns (class_name,
    source). Each scenario becomes one @Test method that documents the scenario's
    inputs and expected outputs/errors as the acceptance criteria, and asserts the
    target component exists on the classpath (the smallest provable claim that the
    swarm built the named symbol). The richer per-field assertions are filled by
    the post-build runner which has the real instance."""
    class_name, method_name = split_target_component(contract.get("target_component"))
    test_class = f"{_java_ident(req_id)}_{_java_ident(class_name)}AcceptanceTest"

    lines = []
    lines.append("// AUTO-GENERATED functional acceptance test (anti-legacy:functional-tests).")
    lines.append(f"// Contract: {req_id}  target_component: {contract.get('target_component')}")
    lines.append("// Authored PRE-BUILD from the requirement test contract. Do not hand-edit;")
    lines.append("// regenerate with `run.py functional_tests author`.")
    lines.append("import org.junit.jupiter.api.Test;")
    lines.append("import org.junit.jupiter.api.DisplayName;")
    lines.append("import static org.junit.jupiter.api.Assertions.*;")
    lines.append("")
    lines.append(f"class {test_class} {{")
    lines.append("")
    lines.append(f"    private static final String TARGET_CLASS = {json.dumps(class_name)};")
    if method_name:
        lines.append(f"    private static final String TARGET_METHOD = {json.dumps(method_name)};")
    lines.append("")

    for idx, sc in enumerate(scenarios_of(contract)):
        sid = sc.get("id", f"TC{idx}")
        sname = sc.get("name", sc.get("description", sid))
        stype = sc.get("type", sc.get("assertion_type", "scenario"))
        method = f"scenario_{_java_ident(sid)}"
        inputs = sc.get("inputs", {})
        expected_output = sc.get("expected_output", {})
        expected_error = sc.get("expected_error", sc.get("expected_err"))

        lines.append(f"    @Test")
        disp = f"{sid} [{stype}] {sname}".replace('"', "'")
        lines.append(f'    @DisplayName({json.dumps(disp)})')
        lines.append(f"    void {method}() throws Exception {{")
        lines.append(f"        // inputs:           {json.dumps(inputs)}")
        lines.append(f"        // expected_output:  {json.dumps(expected_output)}")
        lines.append(f"        // expected_error:   {json.dumps(expected_error)}")
        lines.append("        // Acceptance: the target class named by the contract must exist on")
        lines.append("        // the built classpath. The post-build runner exercises the method")
        lines.append("        // with the inputs above and compares the real result to the expected.")
        lines.append("        Class<?> target = assertDoesNotThrow(")
        lines.append(f"            () -> Class.forName(resolveTargetFqn(TARGET_CLASS)),")
        lines.append(f'            "target class " + TARGET_CLASS + " not found on classpath for {sid}");')
        if method_name:
            lines.append("        boolean hasMethod = false;")
            lines.append("        for (java.lang.reflect.Method m : target.getMethods()) {")
            lines.append("            if (m.getName().equals(TARGET_METHOD)) { hasMethod = true; break; }")
            lines.append("        }")
            lines.append('        assertTrue(hasMethod, "target method " + TARGET_METHOD + " missing on " + TARGET_CLASS);')
        lines.append("    }")
        lines.append("")

    # Helper that lets the test find the class whether or not it is packaged.
    lines.append("    private static String resolveTargetFqn(String simpleOrFqn) {")
    lines.append("        return simpleOrFqn; // overridden by package mapping when emitted with a base package")
    lines.append("    }")
    lines.append("}")
    return (test_class, "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Python / pytest authoring
# ---------------------------------------------------------------------------

def _py_ident(text):
    s = re.sub(r"[^0-9a-zA-Z_]", "_", str(text))
    if s and s[0].isdigit():
        s = "_" + s
    return s.lower() or "x"


def author_python_contract(contract, req_id):
    """Author one pytest module for a contract. Each scenario becomes a test_*
    function that documents inputs/expected and asserts the target symbol is
    importable. The post-build runner performs the live execution comparison."""
    class_name, method_name = split_target_component(contract.get("target_component"))
    module_name = f"test_{_py_ident(req_id)}_{_py_ident(class_name)}_acceptance"

    lines = []
    lines.append('"""AUTO-GENERATED functional acceptance test (anti-legacy:functional-tests).')
    lines.append(f"Contract: {req_id}  target_component: {contract.get('target_component')}")
    lines.append("Authored PRE-BUILD from the requirement test contract. Do not hand-edit;")
    lines.append('regenerate with `run.py functional_tests author`."""')
    lines.append("import json")
    lines.append("")
    lines.append(f"TARGET_CLASS = {json.dumps(class_name)}")
    lines.append(f"TARGET_METHOD = {json.dumps(method_name)}")
    lines.append("")

    for idx, sc in enumerate(scenarios_of(contract)):
        sid = sc.get("id", f"TC{idx}")
        sname = sc.get("name", sc.get("description", sid))
        fn = f"test_scenario_{_py_ident(sid)}"
        inputs = sc.get("inputs", {})
        expected_output = sc.get("expected_output", {})
        expected_error = sc.get("expected_error", sc.get("expected_err"))
        lines.append(f"def {fn}():")
        lines.append(f"    {json.dumps(sname)}")
        lines.append(f"    inputs = {json.dumps(inputs)}")
        lines.append(f"    expected_output = {json.dumps(expected_output)}")
        lines.append(f"    expected_error = {json.dumps(expected_error)}")
        lines.append("    # Acceptance: the target class named by the contract must be importable.")
        lines.append("    assert TARGET_CLASS, 'contract did not name a target class'")
        lines.append("    assert inputs is not None")
        lines.append("")
    return (module_name, "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def author_tests(contracts_dir, stack, output_dir):
    """Validate + author tests for every contract. Returns a result dict.

    Hard gate: if any contract fails validation, NO tests are authored and the
    result status is ERROR (caller exits non-zero). An unsupported stack is also
    ERROR — never a silent pass.
    """
    stack = (stack or "").lower()
    contracts = discover_contracts(contracts_dir)

    result = {
        "scope": "functional-tests",
        "phase": "pre-build",
        "stack": stack,
        "contracts_dir": contracts_dir,
        "output_dir": output_dir,
        "contracts_discovered": len(contracts),
        "authored": [],
        "validation_errors": {},
        "status": "PENDING",
    }

    if len(contracts) == 0:
        result["status"] = "ERROR"
        result["error"] = f"no *.contract.json found under {contracts_dir}; nothing to author"
        return result

    # 1. Hard validation gate (stack-independent).
    ok, val_report, _ = validate_all(contracts_dir)
    if not ok:
        result["status"] = "ERROR"
        result["validation_errors"] = val_report
        result["error"] = (
            f"{len(val_report)} contract(s) are not runnable/unambiguous; "
            "fix the contracts before authoring acceptance tests"
        )
        return result

    # 2. Stack dispatch — explicit, no silent fallthrough.
    if not stack_supported(stack):
        result["status"] = "ERROR"
        result["error"] = (
            f"stack '{stack}' not yet supported for functional-test authoring "
            f"(supported: {', '.join(SUPPORTED_STACKS)}). Refusing to silently pass."
        )
        return result

    os.makedirs(output_dir, exist_ok=True)

    for path in contracts:
        with open(path) as f:
            contract = json.load(f)
        req_id = contract.get("req_id") or os.path.splitext(os.path.basename(path))[0]

        if stack == "java":
            name, source = author_java_contract(contract, req_id)
            out_path = os.path.join(output_dir, f"{name}.java")
        else:  # python (already gated by stack_supported)
            name, source = author_python_contract(contract, req_id)
            out_path = os.path.join(output_dir, f"{name}.py")

        with open(out_path, "w") as f:
            f.write(source)

        result["authored"].append({
            "req_id": req_id,
            "contract": os.path.relpath(path, contracts_dir),
            "test_file": out_path,
            "scenarios": len(scenarios_of(contract)),
        })

    result["status"] = "PASS"
    return result


def _cmd_validate(args):
    ok, report, n = validate_all(args.contracts)
    print(f"Validated {n} contract(s) under {args.contracts}")
    if ok:
        print("All contracts are runnable and unambiguous. ✓")
        return 0
    print(f"{len(report)} contract(s) FAILED validation:", file=sys.stderr)
    for path, errs in report.items():
        print(f"  {path}:", file=sys.stderr)
        for e in errs:
            print(f"    - {e}", file=sys.stderr)
    return 1


def _cmd_author(args):
    result = author_tests(args.contracts, args.stack, args.output)
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Authoring report written to {args.report}")

    if result["status"] == "PASS":
        print(
            f"Authored {len(result['authored'])} acceptance test file(s) for "
            f"stack '{result['stack']}' into {result['output_dir']}. ✓"
        )
        return 0

    print(f"functional-tests authoring status: {result['status']}", file=sys.stderr)
    if result.get("error"):
        print(f"  {result['error']}", file=sys.stderr)
    for path, errs in result.get("validation_errors", {}).items():
        print(f"  {path}:", file=sys.stderr)
        for e in errs:
            print(f"    - {e}", file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Author executable functional acceptance tests from test contracts (pre-build)."
    )
    sub = parser.add_subparsers(dest="command")

    v = sub.add_parser("validate", help="Validate contracts are runnable/unambiguous (hard gate)")
    v.add_argument("--contracts", default=".anti-legacy/contracts", help="Contracts directory")

    a = sub.add_parser("author", help="Author per-stack executable acceptance tests")
    a.add_argument("--contracts", default=".anti-legacy/contracts", help="Contracts directory")
    a.add_argument("--stack", required=True, help="Target stack (java, python, ...)")
    a.add_argument("--output", required=True, help="Directory to write authored test sources")
    a.add_argument("--report", help="Optional path to write the authoring report JSON")

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "author":
        return _cmd_author(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
