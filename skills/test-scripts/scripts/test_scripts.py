#!/usr/bin/env python3
"""
test_scripts — scaffold the FOUR functional test-script families (deliverable).

A deliverable renderer (DELIVERABLES_CONTRACT.md). It SCAFFOLDS, deterministically,
a stakeholder-facing functional test tree under `.anti-legacy/deliverables/tests/`
across the four user-decided functional test types:

  data-parity/ — legacy-expected vs modern-actual within a declared precision, per
                 requirement carrying parity_rules (or numeric outputs). Functional
                 equivalence, NOT a unit test.
  uat/         — one <domain>.feature (Gherkin Given/When/Then), one Scenario per
                 requirement / contract scenario. Business-readable, stack-agnostic.
  e2e/         — one journey per domain stitching its requirements in dependency
                 order (a multi-step business journey).
  api/         — for requirements whose blueprint component exposes api{method,path},
                 a status + response-shape contract test.

It is the EARLY, broader sibling of `anti-legacy:functional-tests` (which authors the
gated, build-binding JUnit/pytest class-existence tests bound to a contract's
target_component). This deliverable complements it — it does not replace it.

Target-stack mapping (config.target_stack):
  java   -> JUnit 5 (parity/e2e/api; api also uses REST-assured)
  python -> pytest  (parity/e2e/api)
  other  -> Gherkin .feature for uat AND e2e; parity/api emit an explicit
            "stack <x>: ... not yet supported" note (mirrors functional-tests'
            no-silent-skip behaviour — never silently emit nothing).
uat is ALWAYS Gherkin (.feature) regardless of stack — it is business-facing.

Every generated test file opens with a traceability header citing req_id +
legacy_components + the rule / scenario ids it covers (§2 / §5 of AGENTS.md).

Degrades: no requirements graph -> exit non-zero (nothing to scaffold from).
Contracts empty -> scenario-less SKELETONS from requirements/rules, and the gap is
named in tests/README.md. Pure standard library; cross-platform os.path.
"""
import argparse
import os
import re
import sys

from antilegacy_core import deliverables as D

PRODUCED_BY = "anti-legacy:test-scripts"
ARTIFACT_ID = "deliverable-test-scripts"
TESTS_SUBDIR = "tests"
TYPE_DIRS = ("data-parity", "uat", "e2e", "api")

# Stacks for which we can emit executable parity/api/e2e harness code today. Any
# other stack falls back to Gherkin for uat/e2e and an explicit unsupported note
# for parity/api — never a silent empty emit (mirrors functional_tests.py).
CODE_STACKS = ("java", "python")


# --------------------------------------------------------------------------- #
# Identifier / literal helpers (Java + Python safe).
# --------------------------------------------------------------------------- #
_BAD = re.compile(r"[^0-9A-Za-z_]")


def _ident(text, lower=False):
    s = _BAD.sub("_", str(text or "").strip())
    if s and s[0].isdigit():
        s = "_" + s
    if not s:
        s = "x"
    return s.lower() if lower else s


def _safe_filename(text):
    """A filesystem-safe stem for a per-domain/per-req file."""
    s = _BAD.sub("-", str(text or "").strip()).strip("-")
    return s or "unnamed"


def _java_lit(value):
    """Render a Python value as a Java source literal (floats -> String for BigDecimal)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _q(str(value))
    if isinstance(value, (dict, list)):
        import json as _json
        return _q(_json.dumps(value))
    return _q(str(value))


def _q(s):
    """A double-quoted Java/JSON string literal."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _py_repr(value):
    return repr(value)


# --------------------------------------------------------------------------- #
# Graph helpers.
# --------------------------------------------------------------------------- #
def _legacy_components(node):
    lc = node.get("legacy_components") or []
    return [str(x) for x in lc if x]


def _rule_ids(node):
    """All rule/validation/error ids on a requirement node (for traceability)."""
    ids = []
    for key in ("business_rules", "validations", "error_paths"):
        for item in (node.get(key) or []):
            rid = item.get("id")
            if rid:
                ids.append(str(rid))
    return ids


def _component_for(blueprint, domain, req_id):
    """The blueprint component dict for (domain, req_id), or {}."""
    dom = (blueprint.get("domains") or {}).get(domain) or {}
    return (dom.get("components") or {}).get(req_id) or {}


def _api_of(component):
    """Return {method, path} if the component exposes an API, else None."""
    api = component.get("api")
    if isinstance(api, dict) and (api.get("method") or api.get("path")):
        return {"method": str(api.get("method") or "GET").upper(),
                "path": str(api.get("path") or "/")}
    return None


def _dependency_order(reqs_by_id, domain_req_ids):
    """Stable topological order of a domain's req_ids by intra-domain `dependencies`.

    Falls back to insertion order; cycles are broken deterministically (a req whose
    deps cannot all be satisfied is appended once its remaining deps are already
    placed or it is the lexicographically-smallest of the remaining frontier).
    """
    in_domain = list(domain_req_ids)
    placed = []
    placed_set = set()
    remaining = list(in_domain)
    # Guard against runaway loops.
    while remaining:
        progressed = False
        for rid in list(remaining):
            node = reqs_by_id.get(rid, {})
            deps = [d for d in (node.get("dependencies") or []) if d in in_domain]
            if all(d in placed_set for d in deps):
                placed.append(rid)
                placed_set.add(rid)
                remaining.remove(rid)
                progressed = True
        if not progressed:
            # Cycle / external dep: place the lexicographically smallest remaining.
            rid = sorted(remaining)[0]
            placed.append(rid)
            placed_set.add(rid)
            remaining.remove(rid)
    return placed


# --------------------------------------------------------------------------- #
# Traceability header (every generated file opens with this).
# --------------------------------------------------------------------------- #
def _header_lines(comment, *, req_id, domain, legacy_components, covers, test_type):
    """Return header lines using the given line-comment token ('//', '#')."""
    lc = ", ".join(legacy_components) if legacy_components else "(none — flagged below)"
    cov = ", ".join(covers) if covers else "(no contract scenarios — SKELETON)"
    return [
        f"{comment} AUTO-SCAFFOLDED functional test ({PRODUCED_BY}) — type: {test_type}",
        f"{comment} Traceability: req_id={req_id}  domain={domain}",
        f"{comment} legacy_components: {lc}",
        f"{comment} covers: {cov}",
        f"{comment} ENRICH ME: replace the scaffolded asserts with concrete checks from each",
        f"{comment} scenario's inputs/expected_output (see SKILL.md). Do not hand-wire fixtures",
        f"{comment} the contract already specifies.",
    ]


def _gherkin_header(*, req_id_or_domain, legacy_components, covers, test_type):
    lc = ", ".join(legacy_components) if legacy_components else "(none — flagged below)"
    cov = ", ".join(covers) if covers else "(no contract scenarios — SKELETON)"
    return [
        f"# AUTO-SCAFFOLDED functional test ({PRODUCED_BY}) — type: {test_type}",
        f"# Traceability: {req_id_or_domain}",
        f"# legacy_components: {lc}",
        f"# covers: {cov}",
        "# ENRICH ME: turn each step into a concrete Given/When/Then from the contract.",
    ]


# --------------------------------------------------------------------------- #
# data-parity emitters
# --------------------------------------------------------------------------- #
def _parity_scenarios(contract):
    """Scenarios suitable for parity assertions: parity-typed first, else all."""
    scs = contract.get("scenarios") or []
    parity = [s for s in scs if (s.get("type") == "parity")]
    return parity or scs


def emit_parity_java(domain, req_id, node, contract, parity_rules):
    cls = f"{_ident(req_id)}_ParityTest"
    covers = [s.get("id") for s in _parity_scenarios(contract) if s.get("id")]
    covers += [pr.get("field") for pr in parity_rules if pr.get("field")]
    L = []
    L += _header_lines("//", req_id=req_id, domain=domain,
                       legacy_components=_legacy_components(node), covers=covers,
                       test_type="data-parity")
    L += [
        "import org.junit.jupiter.api.DisplayName;",
        "import org.junit.jupiter.params.ParameterizedTest;",
        "import org.junit.jupiter.params.provider.CsvSource;",
        "import java.math.BigDecimal;",
        "import static org.junit.jupiter.api.Assertions.*;",
        "",
        f"class {cls} {{",
        "",
    ]
    cases = _parity_scenarios(contract)
    if not cases:
        L += [
            "    // SKELETON — no contract scenarios. Add one row per legacy-captured case:",
            "    //   legacyExpected, modernActual, precision",
            "    @ParameterizedTest",
            '    @DisplayName("parity SKELETON — supply legacy-captured rows")',
            '    @CsvSource({ "0.00, 0.00, 2" })',
            "    void parity(String legacyExpected, String modernActual, int precision) {",
            "        // ENRICH: invoke the modern component, capture modernActual, then assert:",
            "        assertEquals(0,",
            "            new BigDecimal(legacyExpected).setScale(precision).compareTo(",
            "                new BigDecimal(modernActual).setScale(precision)),",
            '            "legacy vs modern differ beyond precision");',
            "    }",
        ]
    else:
        # One @CsvSource row per (parity field x scenario) with expected legacy value.
        rows = []
        for pr in (parity_rules or [{"field": "result", "precision": 2}]):
            field = str(pr.get("field") or "result")
            prec = pr.get("precision")
            prec_i = prec if isinstance(prec, int) else 2  # 'exact' -> compare at 0 scale below
            for sc in cases:
                exp = (sc.get("expected_output") or {}).get(field)
                if exp is None:
                    continue
                rows.append(f'        "{field}, {exp}, {prec_i}, {sc.get("id","TC")}"')
        if not rows:
            rows = ['        "result, 0.00, 2, TC-SKEL"']
        L += [
            "    // Each row: field, legacyExpected, precision, scenarioId. The modern value",
            "    // is produced by the built component (wire it in the marked spot below).",
            "    @ParameterizedTest(name = \"{3}: {0}\")",
            f'    @DisplayName("data parity — {req_id}")',
            "    @CsvSource({",
            ",\n".join(rows),
            "    })",
            "    void parity(String field, String legacyExpected, int precision, String scenarioId) {",
            "        // ENRICH: BigDecimal modernActual = modernComponent.invoke(scenarioInputs).get(field);",
            "        BigDecimal modernActual = new BigDecimal(legacyExpected); // placeholder == legacy",
            "        assertEquals(0,",
            "            new BigDecimal(legacyExpected).setScale(precision).compareTo(",
            "                modernActual.setScale(precision)),",
            '            field + " parity drift for " + scenarioId);',
            "    }",
        ]
    L += ["}", ""]
    return cls + ".java", "\n".join(L)


def emit_parity_python(domain, req_id, node, contract, parity_rules):
    mod = f"test_{_ident(req_id, lower=True)}_parity"
    covers = [s.get("id") for s in _parity_scenarios(contract) if s.get("id")]
    covers += [pr.get("field") for pr in parity_rules if pr.get("field")]
    L = []
    L += _header_lines("#", req_id=req_id, domain=domain,
                       legacy_components=_legacy_components(node), covers=covers,
                       test_type="data-parity")
    L += [
        "import pytest",
        "from decimal import Decimal",
        "",
    ]
    cases = _parity_scenarios(contract)
    rows = []
    for pr in (parity_rules or [{"field": "result", "precision": 2}]):
        field = str(pr.get("field") or "result")
        prec = pr.get("precision")
        prec_i = prec if isinstance(prec, int) else 2
        for sc in cases:
            exp = (sc.get("expected_output") or {}).get(field)
            if exp is None:
                continue
            rows.append((sc.get("id", "TC"), field, str(exp), prec_i, dict(sc.get("inputs") or {})))
    if rows:
        L.append("PARITY_CASES = [")
        for sid, field, exp, prec_i, inputs in rows:
            L.append(f"    ({_py_repr(sid)}, {_py_repr(field)}, {_py_repr(exp)}, {prec_i}, {_py_repr(inputs)}),")
        L += [
            "]",
            "",
            '@pytest.mark.parametrize("scenario_id,field,legacy_expected,precision,inputs", PARITY_CASES)',
            "def test_parity(scenario_id, field, legacy_expected, precision, inputs):",
            "    # ENRICH: modern_actual = modern_component(**inputs)[field]",
            "    modern_actual = legacy_expected  # placeholder == legacy",
            "    q = Decimal(10) ** -precision",
            "    assert Decimal(str(legacy_expected)).quantize(q) == Decimal(str(modern_actual)).quantize(q), \\",
            "        f\"{field} parity drift for {scenario_id}\"",
        ]
    else:
        L += [
            "# SKELETON — no contract scenarios. Add (scenario_id, field, legacy_expected, precision, inputs).",
            "def test_parity_skeleton():",
            "    # ENRICH: capture a legacy-expected value and the modern actual, compare at precision.",
            "    legacy_expected, modern_actual, precision = Decimal('0.00'), Decimal('0.00'), 2",
            "    q = Decimal(10) ** -precision",
            "    assert legacy_expected.quantize(q) == modern_actual.quantize(q)",
        ]
    return mod + ".py", "\n".join(L)


# --------------------------------------------------------------------------- #
# uat emitter (Gherkin, always)
# --------------------------------------------------------------------------- #
def emit_uat_feature(domain, reqs, contracts):
    """One .feature per domain; one Scenario per requirement (or per contract scenario)."""
    L = []
    all_lc, all_cov = [], []
    for _, _, node in reqs:
        all_lc += _legacy_components(node)
    for (req_id, node) in [(r, n) for _, r, n in reqs]:
        all_cov += _rule_ids(node)
    L += _gherkin_header(req_id_or_domain=f"domain={domain}; reqs={', '.join(r for _, r, _ in reqs)}",
                         legacy_components=sorted(set(all_lc)), covers=sorted(set(all_cov)),
                         test_type="uat")
    L.append("")
    L.append(f"Feature: {domain} — business acceptance (UAT)")
    L.append(f"  Acceptance scenarios for the {domain} capability, one per requirement.")
    L.append("")
    for _, req_id, node in reqs:
        title = (node.get("title") or req_id).strip()
        contract = D.contract_for(contracts, req_id, domain)
        scs = (contract.get("scenarios") if contract else None) or []
        rules = node.get("business_rules") or []
        # Tag every scenario with its req for traceability.
        if scs:
            for sc in scs:
                desc = (sc.get("description") or title).strip()
                L.append(f"  @{_ident(req_id)} @{sc.get('id','TC')}")
                L.append(f"  Scenario: {sc.get('id','TC')} — {desc}")
                inputs = sc.get("inputs") or {}
                given = ", ".join(f"{k}={v}" for k, v in inputs.items()) or "the documented preconditions"
                L.append(f"    Given {given}")
                L.append(f"    When the {domain} capability processes the request")
                exp = sc.get("expected_output") or {}
                err = sc.get("expected_error")
                if err:
                    L.append(f"    Then it is rejected with {err}")
                elif exp:
                    then = "; ".join(f"{k} = {v}" for k, v in exp.items())
                    L.append(f"    Then {then}")
                else:
                    L.append("    Then the documented outcome holds  # ENRICH from contract")
                L.append("")
        else:
            # SKELETON from the rule statements.
            L.append(f"  @{_ident(req_id)}")
            L.append(f"  Scenario: {req_id} — {title}")
            if rules:
                first = rules[0]
                L.append(f"    Given the preconditions for {first.get('id','RULE')}")
                L.append(f"    When the {domain} capability runs")
                L.append(f"    Then {(first.get('statement') or 'the rule holds').strip()}  # {first.get('id','RULE')}")
            else:
                L.append("    Given the documented preconditions  # SKELETON: no business_rules")
                L.append(f"    When the {domain} capability runs")
                L.append("    Then the documented outcome holds  # ENRICH: requirement has no rules yet")
            L.append("")
    return f"{_safe_filename(domain)}.feature", "\n".join(L)


# --------------------------------------------------------------------------- #
# e2e emitter (journey per domain, dependency order)
# --------------------------------------------------------------------------- #
def emit_e2e_java(domain, ordered):
    cls = f"{_ident(domain)}_JourneyTest"
    covers = [r for r, _ in ordered]
    all_lc = []
    for _, node in ordered:
        all_lc += _legacy_components(node)
    L = []
    L += _header_lines("//", req_id=", ".join(covers), domain=domain,
                       legacy_components=sorted(set(all_lc)), covers=covers,
                       test_type="e2e")
    L += [
        "import org.junit.jupiter.api.Test;",
        "import org.junit.jupiter.api.DisplayName;",
        "import org.junit.jupiter.api.MethodOrderer;",
        "import org.junit.jupiter.api.Order;",
        "import org.junit.jupiter.api.TestMethodOrder;",
        "import static org.junit.jupiter.api.Assertions.*;",
        "",
        "@TestMethodOrder(MethodOrderer.OrderAnnotation.class)",
        f"class {cls} {{",
        "",
        f'    // Business journey across {domain} requirements, in dependency order.',
        "    // Each step depends on the state established by the previous one.",
    ]
    for i, (req_id, node) in enumerate(ordered, start=1):
        title = (node.get("title") or req_id).strip().replace('"', "'")
        L += [
            "",
            f"    @Test @Order({i})",
            f'    @DisplayName("step {i}: {req_id} — {title}")',
            f"    void step{i}_{_ident(req_id)}() {{",
            f"        // ENRICH: drive {req_id} using state from prior steps; assert the journey outcome.",
            '        assertTrue(true, "scaffold — replace with the journey step assertion");',
            "    }",
        ]
    L += ["}", ""]
    return cls + ".java", "\n".join(L)


def emit_e2e_python(domain, ordered):
    mod = f"test_{_ident(domain, lower=True)}_journey"
    covers = [r for r, _ in ordered]
    all_lc = []
    for _, node in ordered:
        all_lc += _legacy_components(node)
    L = []
    L += _header_lines("#", req_id=", ".join(covers), domain=domain,
                       legacy_components=sorted(set(all_lc)), covers=covers,
                       test_type="e2e")
    L += [
        "import pytest",
        "",
        f"# Business journey across {domain} requirements, in dependency order.",
        "",
        "@pytest.fixture(scope='module')",
        "def journey_state():",
        "    return {}",
        "",
    ]
    for i, (req_id, node) in enumerate(ordered, start=1):
        title = (node.get("title") or req_id).strip()
        L += [
            f"def test_step_{i:02d}_{_ident(req_id, lower=True)}(journey_state):",
            f"    {_py_repr('step %d: %s — %s' % (i, req_id, title))}",
            f"    # ENRICH: drive {req_id} using journey_state from prior steps; record outputs.",
            "    assert True  # scaffold — replace with the journey step assertion",
            "",
        ]
    return mod + ".py", "\n".join(L)


def emit_e2e_gherkin(domain, ordered):
    covers = [r for r, _ in ordered]
    all_lc = []
    for _, node in ordered:
        all_lc += _legacy_components(node)
    L = []
    L += _gherkin_header(req_id_or_domain=f"domain={domain}; journey={' -> '.join(covers)}",
                         legacy_components=sorted(set(all_lc)), covers=covers, test_type="e2e")
    L += [
        "",
        f"Feature: {domain} — end-to-end business journey",
        f"  A multi-step journey across the {domain} requirements in dependency order.",
        "",
        f"  Scenario: {domain} happy-path journey",
    ]
    for i, (req_id, node) in enumerate(ordered, start=1):
        title = (node.get("title") or req_id).strip()
        kw = "Given" if i == 1 else "When"
        L.append(f"    {kw} step {i} — {req_id} ({title})")
    L.append("    Then the journey completes with the documented end state  # ENRICH")
    return f"{_safe_filename(domain)}.feature", "\n".join(L)


# --------------------------------------------------------------------------- #
# api emitter
# --------------------------------------------------------------------------- #
def emit_api_java(domain, req_id, node, contract, api, component):
    cls = f"{_ident(req_id)}_ApiContractTest"
    scs = (contract.get("scenarios") if contract else None) or []
    covers = [s.get("id") for s in scs if s.get("id")] or _rule_ids(node)
    L = []
    L += _header_lines("//", req_id=req_id, domain=domain,
                       legacy_components=_legacy_components(node), covers=covers,
                       test_type="api")
    expect_ok = 200
    # error scenarios present -> also exercise a non-2xx path
    has_err = any(s.get("expected_error") for s in scs)
    L += [
        f"// API: {api['method']} {api['path']}  (component {component.get('class_name') or req_id})",
        "import io.restassured.RestAssured;",
        "import io.restassured.http.ContentType;",
        "import org.junit.jupiter.api.Test;",
        "import org.junit.jupiter.api.DisplayName;",
        "import static io.restassured.RestAssured.given;",
        "import static org.hamcrest.Matchers.*;",
        "",
        f"class {cls} {{",
        "",
        f'    private static final String PATH = {_q(api["path"])};',
        "",
        "    @Test",
        f'    @DisplayName("{api["method"]} {api["path"]} — returns {expect_ok} and the documented shape")',
        "    void happyPath() {",
        "        // ENRICH: set RestAssured.baseURI + a real request body from a happy_path scenario.",
        "        given().contentType(ContentType.JSON)",
        f"            .when().{api['method'].lower()}(PATH)",
        f"            .then().statusCode({expect_ok});",
        "        // assert response shape, e.g.: .body(\"id\", notNullValue())",
        "    }",
    ]
    if has_err:
        L += [
            "",
            "    @Test",
            f'    @DisplayName("{api["method"]} {api["path"]} — rejects invalid input")',
            "    void errorPath() {",
            "        // ENRICH: send an invalid body from an error scenario; assert 4xx + error code.",
            "        given().contentType(ContentType.JSON).body(\"{}\")",
            f"            .when().{api['method'].lower()}(PATH)",
            "            .then().statusCode(greaterThanOrEqualTo(400));",
            "    }",
        ]
    L += ["}", ""]
    return cls + ".java", "\n".join(L)


def emit_api_python(domain, req_id, node, contract, api, component):
    mod = f"test_{_ident(req_id, lower=True)}_api"
    scs = (contract.get("scenarios") if contract else None) or []
    covers = [s.get("id") for s in scs if s.get("id")] or _rule_ids(node)
    has_err = any(s.get("expected_error") for s in scs)
    L = []
    L += _header_lines("#", req_id=req_id, domain=domain,
                       legacy_components=_legacy_components(node), covers=covers,
                       test_type="api")
    L += [
        f"# API: {api['method']} {api['path']}  (component {component.get('class_name') or req_id})",
        "import os",
        "import requests",
        "",
        'BASE_URL = os.environ.get("TARGET_BASE_URL", "http://localhost:8080")',
        f"PATH = {_py_repr(api['path'])}",
        "",
        "def test_happy_path():",
        f"    # ENRICH: send a real body from a happy_path scenario for {req_id}.",
        f"    resp = requests.{api['method'].lower()}(BASE_URL + PATH, json={{}})",
        "    assert resp.status_code == 200, resp.text",
        "    # assert response shape, e.g.: assert 'id' in resp.json()",
    ]
    if has_err:
        L += [
            "",
            "def test_error_path():",
            f"    # ENRICH: send an invalid body from an error scenario for {req_id}.",
            f"    resp = requests.{api['method'].lower()}(BASE_URL + PATH, json={{}})",
            "    assert resp.status_code >= 400",
        ]
    return mod + ".py", "\n".join(L)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _resolve_stack():
    """Target stack from config.target_stack, falling back to the manifest's
    project.target_stack (the manifest is what `manifest init` seeds; config.json
    may not exist yet in an early/hermetic workspace)."""
    cfg = D.load_config()
    stack = cfg.get("target_stack")
    if stack:
        return str(stack)
    manifest = D.load_manifest()
    proj = (manifest.get("project") or {}) if isinstance(manifest, dict) else {}
    return str(proj.get("target_stack") or "")


def scaffold(stack):
    """Build the full tests/ tree. Returns (written_paths, counts, gaps, notes)."""
    graph = D.load_requirements_graph()
    if not graph or not (graph.get("domains")):
        return None  # caller surfaces the missing-graph error

    blueprint = D.load_blueprint()
    contracts = D.load_contracts()
    stack_l = (stack or "").lower()
    code_stack = stack_l if stack_l in CODE_STACKS else None

    written = []
    counts = {t: 0 for t in TYPE_DIRS}
    gaps = {"no_contract": [], "no_rules": [], "no_parity_target": [], "no_api": 0}
    notes = []

    active = D.active_requirements(graph)
    reqs_by_id = {req_id: node for _, req_id, node in active}

    # Group active reqs by domain (preserve graph order).
    by_domain = {}
    for domain, req_id, node in active:
        by_domain.setdefault(domain, []).append((domain, req_id, node))

    def _w(reltype, name, content):
        path = D.write_deliverable(os.path.join(TESTS_SUBDIR, reltype, name), content)
        written.append(path)
        counts[reltype] += 1
        return path

    # ---- data-parity (per requirement with parity_rules OR numeric outputs) ----
    for domain, req_id, node in active:
        contract = D.contract_for(contracts, req_id, domain)
        parity_rules = (contract.get("parity_rules") or [])
        # numeric outputs from scenarios when no explicit parity_rules
        numeric_fields = set()
        for sc in (contract.get("scenarios") or []):
            for k, v in (sc.get("expected_output") or {}).items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric_fields.add(k)
        if not parity_rules and not numeric_fields:
            gaps["no_parity_target"].append(f"{domain}/{req_id}")
            continue
        if not parity_rules:
            parity_rules = [{"field": f, "precision": 2} for f in sorted(numeric_fields)]
        if code_stack == "java":
            name, content = emit_parity_java(domain, req_id, node, contract, parity_rules)
        elif code_stack == "python":
            name, content = emit_parity_python(domain, req_id, node, contract, parity_rules)
        else:
            continue  # unsupported stack: note emitted once below
        _w("data-parity", os.path.join(_safe_filename(domain), name), content)

    if not code_stack:
        notes.append(
            f"stack '{stack}': data-parity script generation not yet supported "
            f"(supported: {', '.join(CODE_STACKS)}). No parity scripts emitted — "
            "supply parity assertions manually or target java/python."
        )

    # ---- uat (Gherkin per domain — always) ----
    for domain, reqs in by_domain.items():
        name, content = emit_uat_feature(domain, reqs, contracts)
        _w("uat", name, content)

    # ---- e2e (journey per domain in dependency order) ----
    for domain, reqs in by_domain.items():
        domain_req_ids = [r for _, r, _ in reqs]
        ordered_ids = _dependency_order(reqs_by_id, domain_req_ids)
        ordered = [(rid, reqs_by_id[rid]) for rid in ordered_ids]
        if code_stack == "java":
            name, content = emit_e2e_java(domain, ordered)
        elif code_stack == "python":
            name, content = emit_e2e_python(domain, ordered)
        else:
            name, content = emit_e2e_gherkin(domain, ordered)
        _w("e2e", name, content)

    # ---- api (per requirement whose blueprint component has api{method,path}) ----
    any_api = False
    for domain, req_id, node in active:
        component = _component_for(blueprint, domain, req_id)
        api = _api_of(component)
        if not api:
            continue
        any_api = True
        contract = D.contract_for(contracts, req_id, domain)
        if code_stack == "java":
            name, content = emit_api_java(domain, req_id, node, contract, api, component)
        elif code_stack == "python":
            name, content = emit_api_python(domain, req_id, node, contract, api, component)
        else:
            continue
        _w("api", os.path.join(_safe_filename(domain), name), content)
    if not any_api:
        gaps["no_api"] = "no blueprint component exposes api{method,path}"
        if (blueprint.get("domains")):
            notes.append("api/: no requirement's blueprint component declares api{method,path} — no API tests scaffolded.")
        else:
            notes.append("api/: blueprint.json absent — cannot determine API surface; no API tests scaffolded.")
    elif not code_stack:
        notes.append(
            f"stack '{stack}': api script generation not yet supported "
            f"(supported: {', '.join(CODE_STACKS)}). API surface exists but no scripts emitted."
        )

    # ---- contract gaps (active reqs with no contract at all) ----
    for domain, req_id, node in active:
        if not D.contract_for(contracts, req_id, domain):
            gaps["no_contract"].append(f"{domain}/{req_id}")
        if not (node.get("business_rules")):
            gaps["no_rules"].append(f"{domain}/{req_id}")

    if not contracts:
        notes.insert(0, "contracts/ is EMPTY — every test below is a scenario-less SKELETON "
                        "derived from requirements/rules. Run anti-legacy:test-strategy to enrich.")

    return written, counts, gaps, notes, len(active), stack_l, bool(code_stack)


def render_readme(counts, gaps, notes, n_active, stack, code_stack, written):
    cfg = D.load_config()
    proj_cfg = cfg.get("project")
    if isinstance(proj_cfg, dict):
        project = proj_cfg.get("name") or "(unknown)"
    elif proj_cfg:
        project = proj_cfg
    else:
        mproj = (D.load_manifest().get("project") or {})
        project = mproj.get("name") or "(unknown)"
    lines = []
    lines.append(f"# Functional Test Scripts — {D.md_escape(project)}")
    lines.append("")
    lines.append(f"_Scaffolded by `{PRODUCED_BY}` at {D.now_iso()} for target stack `{stack}`._")
    lines.append("")
    lines.append("These are EARLY, stakeholder-facing functional test scripts across four types. "
                 "They are scaffolds: enrich each with concrete assertions from the test contracts "
                 "(see the skill). They complement — do NOT replace — the gated build-binding tests "
                 "authored by `anti-legacy:functional-tests`.")
    lines.append("")
    lines.append("## What each directory holds")
    lines.append("")
    rows = [
        ["data-parity/", "legacy-expected vs modern-actual within precision (per req w/ parity_rules or numeric outputs)",
         "JUnit5 parameterized (java) / pytest (python)", counts["data-parity"]],
        ["uat/", "Given/When/Then acceptance, one Scenario per requirement / contract scenario",
         "Gherkin .feature (always, business-facing)", counts["uat"]],
        ["e2e/", "multi-step business journey per domain, requirements in dependency order",
         "JUnit5 / pytest / Gherkin", counts["e2e"]],
        ["api/", "status + response-shape contract test per API-exposing requirement",
         "REST-assured+JUnit5 (java) / requests+pytest (python)", counts["api"]],
    ]
    lines.append(D.md_table(["Directory", "Holds", "Form", "Files"], rows))
    lines.append("")
    total = sum(counts.values())
    lines.append(f"**Total scaffolded test files: {total}** across {n_active} active requirement(s).")
    lines.append("")

    # Requirements covered (by type).
    lines.append("## Requirements covered")
    lines.append("")
    lines.append(f"- Active requirements scaffolded: **{n_active}**")
    lines.append(f"- data-parity scripts: **{counts['data-parity']}**")
    lines.append(f"- uat feature files (one per domain): **{counts['uat']}**")
    lines.append(f"- e2e journeys (one per domain): **{counts['e2e']}**")
    lines.append(f"- api contract tests: **{counts['api']}**")
    lines.append("")

    # GAPS — never hidden.
    lines.append("## Gaps (surfaced, not hidden)")
    lines.append("")
    nc = gaps.get("no_contract") or []
    nr = gaps.get("no_rules") or []
    npt = gaps.get("no_parity_target") or []
    if nc:
        lines.append(f"- **{len(nc)} requirement(s) have NO contract** — UAT/journey steps are SKELETONS "
                     f"(derived from rules only): {', '.join(sorted(nc))}")
    else:
        lines.append("- Every active requirement has a contract. ✓")
    if npt:
        lines.append(f"- **{len(npt)} requirement(s) have no parity target** (no parity_rules and no numeric "
                     f"output) — no data-parity script: {', '.join(sorted(npt))}")
    if nr:
        lines.append(f"- **{len(nr)} requirement(s) carry NO business_rules** — their scenarios are placeholders: "
                     f"{', '.join(sorted(nr))}")
    if gaps.get("no_api"):
        lines.append(f"- **API:** {gaps['no_api']}.")
    lines.append("")

    if notes:
        lines.append("## Stack / coverage notes")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("## Index")
    lines.append("")
    base = D.deliverables_dir()
    for p in written:
        rel = os.path.relpath(p, base)
        lines.append(f"- `{rel.replace(os.sep, '/')}`")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Scaffold the four functional test-script families (deliverable).")
    ap.add_argument("--stack", help="Target stack override (default: config.target_stack).")
    ap.add_argument("--no-register", action="store_true",
                    help="Write files but do not touch the manifest (hermetic).")
    ap.add_argument("--force", action="store_true",
                    help="override a precheck BLOCK and render anyway (loud warning)")
    args = ap.parse_args(argv)
    D.require_ready("deliverables", force=args.force)

    stack = args.stack or _resolve_stack()

    result = scaffold(stack)
    if result is None:
        sys.stderr.write(
            "test-scripts: no requirements graph at "
            f"{D.P_REQUIREMENTS} — nothing to scaffold. Run the pipeline up to "
            "graph-translator first.\n")
        return 2

    written, counts, gaps, notes, n_active, stack_l, code_stack = result

    readme = render_readme(counts, gaps, notes, n_active, stack_l, code_stack, written)
    readme_path = D.write_deliverable(os.path.join(TESTS_SUBDIR, "README.md"), readme)

    # Done-gate: graph exists (checked above) AND index non-empty.
    if not os.path.exists(readme_path) or os.path.getsize(readme_path) == 0:
        sys.stderr.write("test-scripts: index README is empty — refusing to register.\n")
        return 1

    print(f"Scaffolded {sum(counts.values())} functional test file(s) for stack '{stack_l}':")
    for t in TYPE_DIRS:
        print(f"  {t:11s}: {counts[t]}")
    print(f"Index: {readme_path}")
    for p in written:
        print(f"  + {p}")

    if not args.no_register:
        stored = D.register_deliverable(
            ARTIFACT_ID, readme_path, PRODUCED_BY,
            fmt="markdown", status="final",
            depends_on=["requirements-graph", "test-strategy"])
        if stored:
            print(f"Registered artifact '{ARTIFACT_ID}' -> {stored}")
        else:
            print("Manifest absent — wrote files but did not register (no-op).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
