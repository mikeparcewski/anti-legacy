#!/usr/bin/env python3
"""test_plan — render the stakeholder-facing functional Test Strategy deliverable.

This is the DELIVERABLE renderer (anti-legacy:test-plan). It produces a detailed,
human-readable functional test strategy document at
`.anti-legacy/deliverables/test-strategy.md`, DISTINCT from the pipeline-internal
`.anti-legacy/contracts/test-strategy.md` written by anti-legacy:test-strategy
(which is the master summary alongside the per-requirement .contract.json files).

The plan covers the FOUR user-chosen FUNCTIONAL test types (NOT unit tests):

  1. Data-parity / equivalence  — golden legacy output vs. modern output.
  2. UAT acceptance             — Given / When / Then (Gherkin) business sign-off.
  3. End-to-end business journeys — a flow that crosses domains in dependency order.
  4. API / contract             — request/response shape + status-code contracts.

It RENDERS from the committed pipeline data (it does not coin prose with an LLM):
  - requirements_graph.json  (the requirement set, rules, dependencies, dispositions)
  - contracts/*.contract.json (per-requirement scenarios + parity_rules, if present)
  - blueprint.json           (target stack, API surface, entity column source_types)
  - config.json              (project name, target_stack, deployment_target)

It DEGRADES GRACEFULLY: contracts/ may be empty (the test-strategy skill has not
run yet). When so, the plan is still rendered from the graph + parity-relevant
entity fields, and the document clearly states that per-requirement contracts are
not yet generated. Gaps (requirements with no contract, parity outputs with no
parity rule yet) are surfaced, never hidden (DELIVERABLES_CONTRACT §6).

Registers exactly one artifact (`deliverable-test-strategy`) and NEVER advances
the phase. Pure standard library; cross-platform (os.path, no shell-isms).
"""
import argparse
import os
import sys

from antilegacy_core import deliverables as D


# The four functional test types, in the canonical order they appear everywhere.
TEST_TYPES = ("data-parity", "uat", "e2e", "api")

TEST_TYPE_LABEL = {
    "data-parity": "Data-parity / equivalence",
    "uat": "UAT acceptance (Given/When/Then)",
    "e2e": "End-to-end business journeys",
    "api": "API / contract",
}

# A contract scenario `type` maps to which functional test type(s) it covers.
SCENARIO_TYPE_TO_TEST_TYPES = {
    "parity": ("data-parity",),
    "happy_path": ("uat", "e2e", "api"),
    "boundary": ("uat", "api"),
    "error": ("uat", "api"),
}

# Numeric / precision-sensitive legacy source-type fingerprints. A column or
# field whose declared source_type matches any of these is parity-sensitive
# (COMP-3 packed decimal precision loss is silent and catastrophic).
_NUMERIC_SOURCE_HINTS = (
    "comp-3", "comp3", "comp ", "packed", "decimal", "numeric",
    "pic 9", "pic9", "pic s9", "money", "rate", "percent", "amount",
)


# --------------------------------------------------------------------------- #
# Config helpers — the live config nests under `project.{...}`, while the older
# document.py shape uses flat top-level keys. Read BOTH so the plan works either
# way (DELIVERABLES_CONTRACT §7 lists the flat shape; the live workspace nests).
# --------------------------------------------------------------------------- #
def _cfg(config, *keys, default=None):
    """First present value across config[k] and config['project'][k] for each k."""
    proj = config.get("project") if isinstance(config.get("project"), dict) else {}
    for k in keys:
        if config.get(k):
            return config[k]
        if proj.get(k):
            return proj[k]
    return default


def _project_name(config, blueprint):
    return (_cfg(config, "project_name", "name")
            or blueprint.get("project")
            or "modernized-application")


def _target_stack(config, blueprint):
    return (_cfg(config, "target_stack")
            or blueprint.get("target_stack")
            or "the target stack")


def _deployment_target(config):
    return _cfg(config, "deployment_target")


def _migration_mode(graph, config):
    meta = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    return meta.get("migration_mode") or _cfg(config, "migration_mode")


# --------------------------------------------------------------------------- #
# Stack-keyed tooling notes per test type.
# --------------------------------------------------------------------------- #
def _stack_family(stack):
    s = (stack or "").lower()
    if "java" in s or "kotlin" in s:
        return "java"
    if "python" in s:
        return "python"
    if "dotnet" in s or "csharp" in s or "c#" in s:
        return "dotnet"
    if "go" in s:
        return "go"
    if "typescript" in s or "node" in s or "javascript" in s:
        return "node"
    return "other"


def _tooling_note(test_type, stack):
    """A tooling note keyed to config.target_stack for one test type."""
    fam = _stack_family(stack)
    base = {
        "java": "JUnit 5",
        "python": "pytest",
        "dotnet": "xUnit / NUnit",
        "go": "the `testing` package",
        "node": "Vitest / Jest",
        "other": "the target stack's standard test runner",
    }[fam]

    if test_type == "data-parity":
        return ("Golden-file comparison: capture legacy output for a fixed input "
                "set, run the modern path on the same inputs, assert field-by-field "
                "equality at the declared precision. Drive the comparison with {0} "
                "(parameterized over the golden corpus).".format(base))
    if test_type == "uat":
        gherkin_runner = {
            "java": "Cucumber-JVM (+ JUnit 5)",
            "python": "pytest-bdd or behave",
            "dotnet": "SpecFlow / Reqnroll",
            "go": "godog",
            "node": "CucumberJS",
            "other": "the stack's Gherkin runner",
        }[fam]
        return ("Given/When/Then scenarios in Gherkin, run with {0} — one feature "
                "per business capability, executed against the running service so a "
                "business reviewer can read the scenario and sign off.".format(
                    gherkin_runner))
    if test_type == "e2e":
        return ("Journey tests that drive several capabilities in dependency order "
                "against a deployed stack; assert the end state, not each internal "
                "step. Orchestrate with {0} plus the service's real API client.".format(base))
    if test_type == "api":
        api_extra = {
            "java": "REST-assured (+ JUnit 5)",
            "python": "pytest + httpx / requests",
            "dotnet": "xUnit + HttpClient",
            "go": "net/http/httptest",
            "node": "supertest",
            "other": "an HTTP client harness",
        }[fam]
        return ("Contract tests asserting request/response schema, status codes, and "
                "error bodies for every exposed endpoint, using {0}.".format(api_extra))
    return base


def _entry_exit(test_type):
    """(entry_criteria, exit_criteria) bullet lists for a test type."""
    if test_type == "data-parity":
        return (["Legacy golden outputs captured for the chosen input corpus.",
                 "Modern path build that produces the comparable output.",
                 "Parity rules (field / precision / source_type) enumerated below."],
                ["Every parity-sensitive output matches legacy at the declared "
                 "precision (exact for COMP-3 / packed-decimal money fields).",
                 "Any mismatch is triaged to a code defect or a documented, "
                 "approved intentional change — never left unexplained."])
    if test_type == "uat":
        return (["Per-requirement contracts (happy_path + error scenarios) exist.",
                 "A staging environment with representative data is available.",
                 "Business reviewer assigned (config role `uat_reviewer`)."],
                ["Every active requirement has at least one Given/When/Then scenario "
                 "passing in staging.",
                 "The independent UAT reviewer signs off (feeds GATE_4_UAT)."])
    if test_type == "e2e":
        return (["Inter-requirement `dependencies` resolved into journeys.",
                 "All capabilities on a journey are built and deployable together."],
                ["Each defined business journey completes end-to-end against the "
                 "deployed stack with the expected final state.",
                 "Cross-domain hand-offs (dependency edges) are exercised."])
    if test_type == "api":
        return (["API surface defined in the blueprint (method + path per endpoint).",
                 "Contract scenarios (happy_path / boundary / error) per endpoint."],
                ["Every exposed endpoint has request/response schema + status-code "
                 "assertions passing.",
                 "Error envelopes match the contract for each declared error path."])
    return ([], [])


# --------------------------------------------------------------------------- #
# Parity-sensitive output discovery (the migration safety net).
# --------------------------------------------------------------------------- #
def _is_numeric_source_type(source_type):
    s = (source_type or "").lower()
    return any(h in s for h in _NUMERIC_SOURCE_HINTS)


def _parity_rows_from_contracts(contracts):
    """Rows (source, output_field, precision, source_type, req_id) from contract parity_rules."""
    rows = []
    for (domain, req_id), c in sorted(contracts.items()):
        for pr in (c.get("parity_rules") or []):
            if not isinstance(pr, dict):
                continue
            rows.append(("contract", domain, req_id,
                         pr.get("field", "?"),
                         str(pr.get("precision", "?")),
                         pr.get("source_type", "?")))
    return rows


def _parity_rows_from_blueprint(blueprint):
    """Rows from blueprint entity columns whose source_type is numeric/packed."""
    rows = []
    domains = blueprint.get("domains") if isinstance(blueprint.get("domains"), dict) else {}
    seen_entities = set()
    for dname in sorted(domains):
        d = domains[dname] or {}
        ents = d.get("entities") if isinstance(d.get("entities"), dict) else {}
        for ename in sorted(ents):
            seen_entities.add((dname, ename))
            ent = ents[ename] or {}
            for col in (ent.get("columns") or []):
                if not isinstance(col, dict):
                    continue
                st = col.get("source_type")
                if _is_numeric_source_type(st) or _is_numeric_source_type(col.get("type")):
                    rows.append(("blueprint", dname, ename,
                                 col.get("name", "?"),
                                 "exact" if _is_numeric_source_type(st) else "review",
                                 st or col.get("type", "?")))
    # Top-level blueprint.entities (some blueprints place them flat).
    top_ents = blueprint.get("entities") if isinstance(blueprint.get("entities"), dict) else {}
    for ename in sorted(top_ents):
        if any(e == ename for _, e in seen_entities):
            continue
        ent = top_ents[ename] or {}
        for col in (ent.get("columns") or []):
            if not isinstance(col, dict):
                continue
            st = col.get("source_type")
            if _is_numeric_source_type(st) or _is_numeric_source_type(col.get("type")):
                rows.append(("blueprint", "-", ename,
                             col.get("name", "?"),
                             "exact" if _is_numeric_source_type(st) else "review",
                             st or col.get("type", "?")))
    return rows


def _parity_rows_from_graph(graph):
    """Rows from requirements-graph entity fields whose type/source_type is numeric."""
    rows = []
    for domain, ename, ent in D.iter_entities(graph):
        for fld in (ent.get("fields") or []):
            if not isinstance(fld, dict):
                continue
            st = fld.get("source_type") or fld.get("type")
            if _is_numeric_source_type(st):
                rows.append(("graph", domain, ename,
                             fld.get("name", "?"),
                             "exact",
                             st or "?"))
    return rows


# --------------------------------------------------------------------------- #
# Traceability — which test types cover each requirement, and the contract TCs.
# --------------------------------------------------------------------------- #
def _covering_test_types(contract):
    """Set of functional test types a contract's scenarios cover."""
    covered = set()
    for sc in (contract.get("scenarios") or []):
        for tt in SCENARIO_TYPE_TO_TEST_TYPES.get(sc.get("type"), ()):
            covered.add(tt)
    return covered


def _scenario_ids(contract):
    return [sc.get("id", "?") for sc in (contract.get("scenarios") or []) if isinstance(sc, dict)]


def _index_contracts(contracts):
    """Build robust lookups for joining contracts to requirement nodes.

    D.load_contracts() keys by (directory_basename, req_id) — but the directory
    name is a filesystem slug (e.g. 'billing') that does NOT necessarily equal the
    graph's capability-domain name (e.g. 'Billing'/'BillingCapability'). The stable
    join key in the traceability thread is the req_id (DELIVERABLES_CONTRACT §5),
    which is globally unique in the requirements graph. We therefore index:

      - by_req_id[req_id]            -> contract (primary lookup)
      - by_domain_req[(dom, rid)]    -> contract, where `dom` is BOTH the directory
                                        key AND the contract's own `domain` field,
                                        so a (graph_domain, req_id) probe also hits.

    Returns (by_req_id, by_domain_req).
    """
    by_req_id = {}
    by_domain_req = {}
    for (dir_domain, req_id), c in contracts.items():
        by_req_id.setdefault(req_id, c)
        by_domain_req[(dir_domain, req_id)] = c
        own_domain = c.get("domain")
        if own_domain:
            by_domain_req.setdefault((own_domain, req_id), c)
    return by_req_id, by_domain_req


def _resolve_contract(domain, req_id, by_req_id, by_domain_req):
    """Find the contract for a graph requirement, tolerating the domain-key skew.

    Prefer an exact (domain, req_id) match (directory key or contract's own domain
    field); fall back to req_id alone (unique in the graph).
    """
    if (domain, req_id) in by_domain_req:
        return by_domain_req[(domain, req_id)]
    return by_req_id.get(req_id)


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
def render(graph, contracts, blueprint, config):
    project = _project_name(config, blueprint)
    stack = _target_stack(config, blueprint)
    deploy = _deployment_target(config)
    mode = _migration_mode(graph, config)

    active = D.active_requirements(graph)
    dropped = D.dropped_requirements(graph)
    have_contracts = bool(contracts)
    by_req_id, by_domain_req = _index_contracts(contracts)

    md = []
    A = md.append

    # -- Header ------------------------------------------------------------- #
    A("# Functional Test Strategy — {0}".format(project))
    A("")
    A("> Stakeholder-facing functional test strategy, rendered by "
      "`anti-legacy:test-plan` from the committed pipeline data "
      "(`requirements_graph.json`, `contracts/`, `blueprint.json`, `config.json`). "
      "Generated {0}.".format(D.now_iso()))
    A("")
    A("> **This is the deliverable strategy document** at "
      "`.anti-legacy/deliverables/test-strategy.md`. It is DISTINCT from the "
      "pipeline-internal `.anti-legacy/contracts/test-strategy.md` (the master "
      "summary produced by `anti-legacy:test-strategy` alongside the per-requirement "
      "`*.contract.json` files). This document plans the *functional* test approach "
      "for stakeholders; that one is the engine-internal contract index.")
    A("")

    # -- Overview ----------------------------------------------------------- #
    A("## 1. Overview")
    A("")
    A("- **Project:** {0}".format(project))
    A("- **Target stack:** {0}".format(stack))
    if mode:
        A("- **Migration mode:** {0} — the requirements graph is a {1}.".format(
            mode,
            "capability plan grouping legacy modules into business capabilities"
            if mode == "functional" else "1:1 code-equivalent rehost map"))
    A("- **Active requirements in scope:** {0}".format(len(active)))
    if dropped:
        A("- **Dropped (out of scope):** {0} requirement(s) explicitly dropped — "
          "NOT tested (listed in §8).".format(len(dropped)))
    A("")
    A("This strategy covers **functional** testing — does the modernized system "
      "produce the right business outcomes — across four test types. It is "
      "explicitly **not** a unit-test plan: unit tests (per-class, isolated) are "
      "owned by the build and live with the code; this document is the "
      "behavior-level safety net that proves the modernization preserved the "
      "legacy system's business behavior.")
    A("")
    A("| Test type | What it covers |")
    A("|---|---|")
    A("| **Data-parity / equivalence** | Legacy output vs. modern output on identical "
      "inputs — the migration correctness net (COMP-3 / packed-decimal precision). |")
    A("| **UAT acceptance (Given/When/Then)** | Business-readable Gherkin scenarios a "
      "stakeholder signs off (feeds GATE_4_UAT). |")
    A("| **End-to-end business journeys** | A real flow crossing multiple capabilities "
      "in dependency order, asserting the end state. |")
    A("| **API / contract** | Request/response shape, status codes, and error envelopes "
      "for every exposed endpoint. |")
    A("")

    # -- One section per test type ------------------------------------------ #
    section_no = 2
    for tt in TEST_TYPES:
        A("## {0}. {1}".format(section_no, TEST_TYPE_LABEL[tt]))
        A("")
        A("**Approach.** {0}".format(_approach(tt)))
        A("")
        A("**In scope.** {0}".format(_in_scope(tt, len(active))))
        A("")
        entry, exit_ = _entry_exit(tt)
        A("**Entry criteria.**")
        A("")
        for e in entry:
            A("- {0}".format(e))
        A("")
        A("**Exit criteria.**")
        A("")
        for e in exit_:
            A("- {0}".format(e))
        A("")
        A("**Tooling ({0}).** {1}".format(stack, _tooling_note(tt, stack)))
        A("")
        section_no += 1

    # -- Data-parity detail: enumerate every parity-sensitive output -------- #
    A("## {0}. Data-parity safety net — parity-sensitive outputs".format(section_no))
    section_no += 1
    A("")
    A("> **Critical.** COMP-3 / packed-decimal precision loss is silent and "
      "catastrophic: a money, rate, percentage, or count field that is off by a "
      "fraction passes a smoke test but corrupts ledgers. Every numeric output below "
      "MUST be parity-checked field-by-field against the legacy golden output at the "
      "stated precision (`exact` for packed-decimal money).")
    A("")
    parity_rows = (_parity_rows_from_contracts(contracts)
                   + _parity_rows_from_blueprint(blueprint)
                   + _parity_rows_from_graph(graph))
    if parity_rows:
        A(D.md_table(
            ["Source", "Domain", "Owner (req/entity)", "Output field", "Precision", "Legacy source_type"],
            parity_rows))
        A("")
        n_contract = sum(1 for r in parity_rows if r[0] == "contract")
        A("{0} parity rule(s) total — {1} declared in contracts, {2} inferred from "
          "numeric entity fields (blueprint/graph) that still need an explicit parity "
          "rule in a contract.".format(
              len(parity_rows), n_contract, len(parity_rows) - n_contract))
        A("")
    else:
        A("_No parity-sensitive outputs found._ No contract declared `parity_rules`, "
          "and no entity field in the blueprint or requirements graph carries a "
          "numeric / packed-decimal `source_type` (DECIMAL, COMP-3, money, rate). "
          "If the legacy system computes money, rates, percentages, or counts, this "
          "is a GAP — the entity schema is missing precision metadata, or contracts "
          "have not yet been generated by `anti-legacy:test-strategy`.")
        A("")

    # -- Environments ladder ------------------------------------------------ #
    A("## {0}. Environments".format(section_no))
    section_no += 1
    A("")
    if deploy:
        A("Derived from `config.deployment_target` = **{0}**.".format(deploy))
    else:
        A("`config.deployment_target` is not set — the production target below is a "
          "placeholder. Set `deployment_target` in config to make this concrete.")
    A("")
    prod_target = deploy or "_deployment target not configured_"
    A(D.md_table(
        ["Environment", "Purpose", "Test types run here"],
        [["local", "Developer workstation; fast feedback.",
          "data-parity (golden corpus), API/contract"],
         ["staging", "Production-like; representative data.",
          "UAT acceptance, end-to-end journeys, full data-parity"],
         ["production ({0})".format(prod_target), "Live; promote only after GATE_4_UAT.",
          "Smoke + parity spot-check post-deploy"]]))
    A("")

    # -- Test data strategy (per domain) ------------------------------------ #
    A("## {0}. Test data strategy".format(section_no))
    section_no += 1
    A("")
    A("Per domain, the data each capability needs to be exercised. Parity tests "
      "additionally require the captured legacy golden output for the same inputs.")
    A("")
    data_rows = []
    for dname in sorted((graph.get("domains") or {}).keys()):
        ddata = graph["domains"][dname] or {}
        reqs = ddata.get("requirements") or {}
        ents = ddata.get("entities") or {}
        n_active = sum(1 for _r, n in reqs.items()
                       if n.get("disposition") != "drop" and n.get("status") != "unresolvable")
        data_access = set()
        for n in reqs.values():
            for a in (n.get("data_access") or []):
                if str(a).strip():
                    data_access.add(str(a))
        if ents:
            need = "Seed entities: " + ", ".join(sorted(ents.keys()))
        elif data_access:
            need = "Seed data stores: " + ", ".join(sorted(data_access))
        else:
            need = "Inputs per requirement (no entity schema declared in graph)."
        data_rows.append([dname, str(n_active), need])
    if data_rows:
        A(D.md_table(["Domain", "Active reqs", "Data needed"], data_rows))
        A("")
    else:
        A("_No domains in the requirements graph._")
        A("")

    # -- Traceability matrix ------------------------------------------------ #
    A("## {0}. Traceability matrix".format(section_no))
    section_no += 1
    A("")
    A("Every active requirement traces: **req_id → domain → legacy_components → "
      "covering test types → contract scenario ids**. A requirement with no contract "
      "yet is a **GAP** (no executable scenario covers it) and is flagged.")
    A("")
    if not have_contracts:
        A("> **Per-requirement contracts not yet generated by "
          "`anti-legacy:test-strategy`.** The `Covering test types` and `Contract "
          "scenarios` columns below are therefore empty for every requirement — the "
          "plan above is rendered from the requirements graph alone. Run "
          "`anti-legacy:test-strategy` to produce `contracts/{domain}/{req_id}."
          "contract.json`, then re-run this deliverable to populate coverage.")
        A("")

    trace_rows = []
    gaps = []
    for domain, req_id, node in sorted(active, key=lambda x: (x[0], x[1])):
        contract = _resolve_contract(domain, req_id, by_req_id, by_domain_req)
        legacy = ", ".join(node.get("legacy_components") or []) or "_none_"
        if contract:
            tts = _covering_test_types(contract)
            tts_str = ", ".join(TEST_TYPE_LABEL[t].split(" ")[0] for t in TEST_TYPES if t in tts) or "_none_"
            scens = ", ".join(_scenario_ids(contract)) or "_none_"
        else:
            tts_str = "— (no contract)"
            scens = "—"
            gaps.append((domain, req_id))
        trace_rows.append([req_id, domain, legacy, tts_str, scens])
    if trace_rows:
        A(D.md_table(
            ["req_id", "domain", "legacy_components", "covering test types", "contract scenarios"],
            trace_rows))
        A("")
    else:
        A("_No active requirements to trace._")
        A("")

    if gaps:
        A("### Coverage gaps — {0} requirement(s) with NO contract".format(len(gaps)))
        A("")
        A("These active requirements have no per-requirement contract, so no "
          "executable functional scenario covers them yet:")
        A("")
        for domain, req_id in gaps:
            A("- `{0}` (domain `{1}`)".format(req_id, domain))
        A("")

    # -- Coverage summary --------------------------------------------------- #
    A("## {0}. Coverage summary".format(section_no))
    A("")
    n_active = len(active)
    n_with_contract = sum(
        1 for d, r, _n in active
        if _resolve_contract(d, r, by_req_id, by_domain_req) is not None)
    n_parity = len(parity_rows)
    A(D.md_table(
        ["Metric", "Count"],
        [["Active requirements (in scope)", str(n_active)],
         ["Requirements with a contract", "{0} / {1}".format(n_with_contract, n_active)],
         ["Requirements WITHOUT a contract (gap)", str(n_active - n_with_contract)],
         ["Dropped requirements (out of scope, not tested)", str(len(dropped))],
         ["Parity rules / parity-sensitive outputs", str(n_parity)],
         ["Total contracts loaded", str(len(contracts))]]))
    A("")
    if dropped:
        A("### Dropped from scope (explicit, not tested)")
        A("")
        for domain, req_id, node in sorted(dropped, key=lambda x: (x[0], x[1])):
            reason = node.get("disposition_reason") or "no reason recorded"
            A("- `{0}` (domain `{1}`) — {2}".format(req_id, domain, reason))
        A("")

    A("---")
    A("")
    A("_Functional, not unit. Every covered requirement traces back to its "
      "`legacy_components` and the business rules extracted from them; every "
      "uncovered requirement is named above as a gap._")

    return "\n".join(md)


def _approach(tt):
    return {
        "data-parity": "Capture the legacy system's output for a fixed input corpus "
                       "(golden files), run the modern path on the same inputs, and "
                       "assert equality field-by-field at the declared precision. This "
                       "is the primary correctness mechanism for a modernization.",
        "uat": "Express each business capability as Given/When/Then scenarios a "
               "non-technical stakeholder can read and approve. Run them against the "
               "deployed service; the independent reviewer's sign-off feeds GATE_4_UAT.",
        "e2e": "Compose capabilities into business journeys that follow the "
               "requirement `dependencies` in order (e.g. open → transact → settle), "
               "exercising cross-domain hand-offs and asserting the final end state.",
        "api": "Pin every exposed endpoint's contract: request schema, response "
               "schema, status codes, and error envelopes. Guards against silent "
               "shape drift between the service and its consumers.",
    }[tt]


def _in_scope(tt, n_active):
    return {
        "data-parity": "All {0} active requirements that produce a comparable output, "
                       "with priority on any numeric / packed-decimal field "
                       "(see the parity table below).".format(n_active),
        "uat": "Every active requirement with a business-observable outcome "
               "({0} in scope).".format(n_active),
        "e2e": "Each multi-capability journey implied by the requirement dependency "
               "edges; standalone capabilities are covered by UAT instead.",
        "api": "Every requirement the blueprint exposes as an API endpoint "
               "(method + path).",
    }[tt]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        prog="test_plan",
        description="Render the stakeholder-facing functional Test Strategy "
                    "deliverable (.anti-legacy/deliverables/test-strategy.md) from "
                    "the requirements graph, contracts, blueprint, and config. "
                    "Registers `deliverable-test-strategy`; never advances the phase.",
    )
    parser.add_argument("--requirements", default=D.P_REQUIREMENTS,
                        help="Path to requirements_graph.json")
    parser.add_argument("--contracts", default=D.P_CONTRACTS,
                        help="Path to the contracts/ directory")
    parser.add_argument("--blueprint", default=D.P_BLUEPRINT,
                        help="Path to blueprint.json")
    parser.add_argument("--config", default=D.P_CONFIG,
                        help="Path to config.json")
    parser.add_argument("--no-register", action="store_true",
                        help="Write the document but do not register it in the manifest")
    parser.add_argument("--force", action="store_true",
                        help="override a precheck BLOCK and render anyway (loud warning)")
    args = parser.parse_args()
    D.require_ready("deliverables", force=args.force)

    graph = D.load_requirements_graph(args.requirements)
    # Done-gate precondition: the requirements graph must exist and have domains.
    if not graph or not graph.get("domains"):
        print("Error: requirements graph not found or has no domains at {0}. "
              "Run the pipeline through graph-translate (anti-legacy:graph-translator) "
              "first — there is no requirement set to plan tests against.".format(
                  args.requirements),
              file=sys.stderr)
        sys.exit(1)

    contracts = D.load_contracts(args.contracts)
    blueprint = D.load_blueprint(args.blueprint)
    config = D.load_config(args.config)

    content = render(graph, contracts, blueprint, config)

    # Done-gate: the rendered document must be non-empty before we write/register.
    if not content or not content.strip():
        print("Error: rendered test strategy is empty — refusing to write.",
              file=sys.stderr)
        sys.exit(1)

    out_path = D.write_deliverable("test-strategy.md", content)
    if os.path.getsize(out_path) == 0:
        print("Error: wrote an empty file at {0}.".format(out_path), file=sys.stderr)
        sys.exit(1)

    print("Functional test strategy written to: {0}".format(out_path))

    if not args.no_register:
        stored = D.register_deliverable(
            "deliverable-test-strategy", out_path,
            produced_by="anti-legacy:test-plan",
            fmt="markdown", status="final",
            depends_on=["requirements-graph", "test-strategy"],
        )
        if stored:
            print("Registered artifact `deliverable-test-strategy` -> {0}".format(stored))
        else:
            print("Manifest absent — wrote the document but did not register "
                  "(use a workspace with .anti-legacy/manifest.json to register).")


if __name__ == "__main__":
    main()
