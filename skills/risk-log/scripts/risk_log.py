#!/usr/bin/env python3
"""
anti-legacy:risk-log — render a living migration RISK LOG / register, mined
deterministically from the committed pipeline data.

This is a DELIVERABLE renderer (see DELIVERABLES_CONTRACT.md): it reads the
pipeline's structured artifacts and emits a human-facing Markdown risk register
under `.anti-legacy/deliverables/risk-log.md`. It REGISTERS its artifact in the
manifest (`deliverable-risk-log`, status=draft — the log is living and gets
re-rendered as the pipeline progresses) and NEVER advances the phase.

The log is NOT coined prose. Every row is mined from a source and traces back to
its origin (a req_id, a graph node SymbolId, or a file) so a reviewer can follow
the thread to evidence (§5 of AGENTS.md — the traceability thread never breaks).

Risk SOURCES mined (each row carries its source ref):
  1. RISK-flagged graph nodes  — annotations.jsonl rows with status=="risk".
  2. Low-confidence rules       — business_rules with confidence < resolve_threshold.
  3. Dropped requirements       — disposition=="drop" (intentional scope cut).
  4. Parity / COMP-3 precision  — contract parity_rules + numeric (DECIMAL/COMP-3)
                                  entity fields from the blueprint / graph.
  5. Coverage holes             — coverage-report.json unaccounted_nodes.
  6. Unresolvable + rule-less   — status=="unresolvable" or NO business_rules.
  7. Cross-language / cross-repo seams — >1 source language, cross-app deps.

The log is HONEST about its own coverage: a "Sources assessed" section states
which inputs were present vs absent (e.g. "coverage-report.json absent — coverage
holes (source 5) NOT assessed"). A clean-looking log that hides a missing input is
wrong (§6 of AGENTS.md — surface gaps, do not soften them).

Pure standard library + antilegacy_core.deliverables. Cross-platform (macOS /
Linux / WSL / Windows): every path via os.path; no shell-isms. Anchored on the
workspace (os.getcwd()) by the shared library, never on __file__.
"""
import argparse
import os
import sys

from antilegacy_core import deliverables as D

ARTIFACT_ID = "deliverable-risk-log"
PRODUCED_BY = "anti-legacy:risk-log"
OUT_RELNAME = "risk-log.md"

DEFAULT_RESOLVE_THRESHOLD = 0.75

# Numeric / packed-decimal markers in an entity field type. A COMP-3 / DECIMAL
# source loses precision silently when re-typed in the target — High impact.
# Mirrors the spirit of antilegacy_core.domain_graph's parity heuristic, applied
# here to the field TYPE string (blueprint column source_type / graph field type).
_NUMERIC_TYPE_MARKERS = (
    "comp-3", "comp3", "comp_3", "packed", "packed-decimal", "packed decimal",
    "decimal", "numeric", "number", "bigdecimal", "money", "currency",
    "pic 9", "pic9", "s9", "v9", "comp", "float", "double",
)


# --------------------------------------------------------------------------- #
# Likelihood / impact heuristics (documented IN the rendered doc too).
#   parity / COMP-3        -> Impact High      (silent, catastrophic precision loss)
#   RISK-flagged node      -> Likelihood High  (extraction could NOT resolve it)
#   dropped requirement    -> Impact Medium    (intentional scope cut — verify intent)
#   low-confidence rule    -> Likelihood Medium (mis-translation risk)
#   coverage hole          -> Likelihood Medium (unknown behavior, no rule)
#   unresolvable/rule-less  -> Likelihood High   (no rule to build against)
#   cross-language seam    -> Likelihood Medium (cross-tech edge mis-wiring)
# Severity = max(L, I) biased up when both are High.
# --------------------------------------------------------------------------- #
_RANK = {"L": 1, "M": 2, "H": 3}


def _severity(likelihood, impact):
    """Severity label from a simple L/M/H matrix (deterministic)."""
    l, i = _RANK.get(likelihood, 2), _RANK.get(impact, 2)
    if l == 3 and i == 3:
        return "Critical"
    score = max(l, i)
    return {1: "Low", 2: "Medium", 3: "High"}[score]


def _sev_order(sev):
    return {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(sev, 4)


class Risk:
    """One mined risk row. `source_ref` is the traceability anchor (never blank)."""

    __slots__ = ("category", "description", "source_ref", "likelihood",
                 "impact", "mitigation")

    def __init__(self, category, description, source_ref, likelihood, impact,
                 mitigation):
        self.category = category
        self.description = description
        self.source_ref = source_ref
        self.likelihood = likelihood
        self.impact = impact
        self.mitigation = mitigation

    @property
    def severity(self):
        return _severity(self.likelihood, self.impact)


# --------------------------------------------------------------------------- #
# Source miners. Each returns a list[Risk]; each Risk carries a source_ref that
# traces to a req_id, a node SymbolId/db_id, or a file path.
# --------------------------------------------------------------------------- #
def mine_risk_flagged(annotations):
    """Source 1 — annotations with status=='risk' (HITL research queue)."""
    out = []
    for row in annotations:
        if (row.get("status") or "").lower() != "risk":
            continue
        db_id = row.get("db_id")
        sym = row.get("symbol_id")
        name = row.get("name") or row.get("requirement") or "?"
        reason = (row.get("risk_reason") or row.get("statement")
                  or row.get("description") or "flagged for human research")
        ref = "node {0}/{1} ({2})".format(db_id, sym, name)
        out.append(Risk(
            category="Unresolved rule (RISK-flagged)",
            description="Extraction could not resolve this node's business rule: {0}".format(reason),
            source_ref=ref,
            likelihood="H",          # extraction already failed to resolve it
            impact="M",
            mitigation="Human research the legacy source slice for this node; resolve the "
                       "rule or confirm it carries no behavior before build.",
        ))
    return out


def mine_low_confidence(graph, threshold):
    """Source 2 — business_rules with confidence < resolve_threshold."""
    out = []
    for domain, req_id, node in D.iter_requirements(graph):
        for rule in (node.get("business_rules") or []):
            c = rule.get("confidence")
            rid = rule.get("id", "RULE-?")
            stmt = (rule.get("statement") or "").strip()
            legacy = ", ".join(str(x) for x in (node.get("legacy_components") or [])) or "?"
            ref = "{0} / {1} (legacy: {2})".format(req_id, rid, legacy)
            if not isinstance(c, (int, float)):
                # C2: a rule with no numeric confidence silently escapes coverage/risk
                # scoring — SURFACE it as a risk rather than skipping it.
                out.append(Risk(
                    category="Rule missing confidence (un-scoreable)",
                    description="Rule has no numeric confidence; it silently escapes "
                                "coverage/risk scoring: {0}".format(stmt[:160] or "(no statement)"),
                    source_ref=ref,
                    likelihood="M",
                    impact="M",
                    mitigation="Re-run extraction to record a confidence, or RISK-flag the node — "
                               "do not ship an un-scoreable rule.",
                ))
                continue
            if float(c) >= threshold:
                continue
            out.append(Risk(
                category="Low-confidence rule (mis-translation)",
                description="Rule stated at confidence {0:.2f} (< {1:.2f}) — risk of "
                            "mis-translation: {2}".format(float(c), threshold,
                                                          stmt[:160] or "(no statement)"),
                source_ref=ref,
                likelihood="M",
                impact="M",
                mitigation="Re-read the legacy source slice and raise confidence, or write a "
                           "parity/characterization test pinning the behavior before build.",
            ))
    return out


def mine_dropped(graph):
    """Source 3 — dropped requirements (intentional scope cut)."""
    out = []
    for domain, req_id, node in D.dropped_requirements(graph):
        reason = (node.get("disposition_reason") or "no reason recorded").strip()
        title = (node.get("title") or "").strip()
        legacy = ", ".join(str(x) for x in (node.get("legacy_components") or [])) or "?"
        ref = "{0} (legacy: {1})".format(req_id, legacy)
        desc = "Functionality intentionally removed"
        if title:
            desc += " — '{0}'".format(title)
        desc += ". Disposition reason: {0}".format(reason)
        out.append(Risk(
            category="Scope cut (dropped requirement)",
            description=desc,
            source_ref=ref,
            likelihood="M",
            impact="M",          # scope removal — confirm it is genuinely unwanted
            mitigation="Confirm with the business owner the dropped capability is genuinely "
                       "out of scope; record the sign-off so the cut is not a silent omission.",
        ))
    return out


def _is_numeric_type(type_str):
    s = (type_str or "").lower()
    return any(m in s for m in _NUMERIC_TYPE_MARKERS)


def mine_parity(graph, blueprint, contracts):
    """Source 4 — parity / COMP-3 precision-loss risks.

    From two places: (a) contract parity_rules (explicit), and (b) numeric
    (DECIMAL / COMP-3) entity fields from the blueprint columns (source_type) and
    the requirements-graph entity fields — a numeric source that loses precision
    silently. Deduped on (owner, field).
    """
    out = []
    seen = set()

    # (a) Contract parity_rules — the explicit, already-identified parity surface.
    for (domain, req_id), contract in (contracts or {}).items():
        for pr in (contract.get("parity_rules") or []):
            field = pr.get("field", "?")
            prec = pr.get("precision", "?")
            stype = pr.get("source_type", "?")
            key = (req_id, str(field))
            if key in seen:
                continue
            seen.add(key)
            ref = "{0} / contract field '{1}' (source_type {2})".format(req_id, field, stype)
            out.append(Risk(
                category="Parity / precision (COMP-3)",
                description="Numeric output '{0}' requires precision '{1}' (source {2}); "
                            "silent precision loss if mis-typed in the target.".format(
                                field, prec, stype),
                source_ref=ref,
                likelihood="M",
                impact="H",          # COMP-3 precision loss is silent + catastrophic
                mitigation="Type as exact decimal (e.g. BigDecimal / NUMERIC) and add a "
                           "data-parity test asserting the contract precision rule.",
            ))

    # (b) Numeric entity fields from the blueprint (columns -> source_type/type).
    for ename, ent in (blueprint.get("entities") or {}).items():
        for col in (ent.get("columns") or []):
            cname = col.get("name", "?")
            stype = col.get("source_type") or col.get("type") or ""
            if not _is_numeric_type(stype):
                continue
            key = ("blueprint:" + str(ename), str(cname))
            if key in seen:
                continue
            seen.add(key)
            ref = "blueprint entity '{0}' field '{1}' (source_type {2})".format(
                ename, cname, stype or "?")
            out.append(Risk(
                category="Parity / precision (COMP-3)",
                description="Numeric field '{0}.{1}' typed from source '{2}' — silent "
                            "precision loss if re-typed without exact decimal.".format(
                                ename, cname, stype or "?"),
                source_ref=ref,
                likelihood="M",
                impact="H",
                mitigation="Confirm the target column is an exact decimal type; add a "
                           "data-parity test for this field.",
            ))

    # (c) Numeric entity fields from the requirements-graph entities (fields -> type).
    for domain, ename, ent in D.iter_entities(graph):
        for fld in (ent.get("fields") or []):
            fname = fld.get("name", "?")
            ftype = fld.get("type") or ""
            if not _is_numeric_type(ftype):
                continue
            key = ("graph:" + str(ename), str(fname))
            if key in seen:
                continue
            seen.add(key)
            ref = "graph entity '{0}' field '{1}' (type {2})".format(ename, fname, ftype or "?")
            out.append(Risk(
                category="Parity / precision (COMP-3)",
                description="Numeric field '{0}.{1}' (type '{2}') — verify exact-decimal "
                            "handling to avoid silent precision loss.".format(
                                ename, fname, ftype or "?"),
                source_ref=ref,
                likelihood="M",
                impact="H",
                mitigation="Ensure a parity_rule exists in the field's test contract and the "
                           "target type is exact decimal.",
            ))
    return out


def mine_coverage_holes(coverage):
    """Source 5 — coverage-report.json unaccounted_nodes (behavior, no rule)."""
    out = []
    for node in (coverage.get("unaccounted_nodes") or []):
        sym = node.get("symbol_id", "?")
        name = node.get("name", "?")
        kind = node.get("kind", "?")
        f = node.get("file", "?")
        app = node.get("app")
        ref = "{0} ({1}) — {2}".format(sym, kind, f)
        if app:
            ref += " [{0}]".format(app)
        out.append(Risk(
            category="Coverage hole (unknown behavior)",
            description="Behavior-bearing node '{0}' ({1}) has NO resolved rule and is not "
                        "RISK-flagged — its behavior is unknown to the build.".format(name, kind),
            source_ref=ref,
            likelihood="M",
            impact="M",
            mitigation="Re-run extraction over this node (annotate to RESOLVED or RISK) so "
                       "coverage reaches 1.0 before graph-translate.",
        ))
    return out


def mine_unresolvable_and_ruleless(graph):
    """Source 6 — status=='unresolvable' OR no business_rules (a placeholder)."""
    out = []
    for domain, req_id, node in D.iter_requirements(graph):
        if node.get("disposition") == "drop":
            continue  # dropped reqs are source 3, not a build gap
        legacy = ", ".join(str(x) for x in (node.get("legacy_components") or [])) or "?"
        ref = "{0} (legacy: {1})".format(req_id, legacy)
        status = (node.get("status") or "").lower()
        rules = node.get("business_rules") or []
        if status == "unresolvable":
            out.append(Risk(
                category="Unresolvable requirement",
                description="Requirement marked unresolvable: {0}".format(
                    (node.get("disposition_reason") or node.get("description")
                     or "no reason recorded").strip()[:160]),
                source_ref=ref,
                likelihood="H",
                impact="M",
                mitigation="Locate the missing source (called program with no source in the "
                           "tree?) or formally accept the gap with sign-off before build.",
            ))
        elif not rules:
            out.append(Risk(
                category="Requirement with no business rules",
                description="Active requirement carries NO business rules — it is a "
                            "placeholder, nothing to build against.",
                source_ref=ref,
                likelihood="H",
                impact="M",
                mitigation="Re-read the legacy source for this requirement and extract its "
                           "rules, or mark it unresolvable with a reason.",
            ))
    return out


def mine_cross_language(config, graph):
    """Source 7 — cross-language source estate + cross-app capability seams."""
    out = []
    apps = config.get("source_apps") or []
    langs = {}
    for sa in apps:
        if isinstance(sa, dict):
            lang = (sa.get("language") or "?").strip() or "?"
            langs.setdefault(lang, []).append(sa.get("name", "?"))
    if len(langs) > 1:
        ref = "config.source_apps: " + "; ".join(
            "{0}=[{1}]".format(lang, ", ".join(names)) for lang, names in sorted(langs.items()))
        out.append(Risk(
            category="Cross-language / cross-repo seam",
            description="Source estate spans {0} languages ({1}) merged into one target — "
                        "cross-tech edges (calls / interfaces) are a mis-wiring risk.".format(
                            len(langs), ", ".join(sorted(langs))),
            source_ref=ref,
            likelihood="M",
            impact="M",
            mitigation="Validate every cross-language seam in the requirements graph "
                       "dependencies; add API/contract tests at each boundary.",
        ))
    # Cross-app dependency: a requirement merging programs from >1 source app.
    app_names = {sa.get("name") for sa in apps if isinstance(sa, dict) and sa.get("name")}
    if len(app_names) > 1:
        for domain, req_id, node in D.iter_requirements(graph):
            seen_apps = set()
            for mp in (node.get("merged_programs") or []):
                # merged_programs may be strings or {program, source_app}
                if isinstance(mp, dict):
                    a = mp.get("source_app")
                    if a:
                        seen_apps.add(a)
            for rule in (node.get("business_rules") or []):
                prov = rule.get("provenance")
                if isinstance(prov, dict) and prov.get("source_app"):
                    seen_apps.add(prov["source_app"])
            if len(seen_apps) > 1:
                ref = "{0} (apps: {1})".format(req_id, ", ".join(sorted(seen_apps)))
                out.append(Risk(
                    category="Cross-language / cross-repo seam",
                    description="Requirement merges programs from {0} source apps ({1}) — "
                                "an inter-repo contract that must be reconciled.".format(
                                    len(seen_apps), ", ".join(sorted(seen_apps))),
                    source_ref=ref,
                    likelihood="M",
                    impact="M",
                    mitigation="Confirm the merged behavior is consistent across apps; add a "
                               "contract test spanning the joined repositories.",
                ))
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _present(label, ok, note=""):
    mark = "present" if ok else "ABSENT"
    line = "- **{0}** — {1}".format(label, mark)
    if note:
        line += " — {0}".format(note)
    return line


def render(graph, annotations, coverage, blueprint, contracts, config, sources_state):
    """Render the full risk-log Markdown. Always renders methodology + sources even
    when there are zero risks (an honest empty log, not a blank file)."""
    project = (config.get("project", {}) or {}).get("name") or config.get("project_name") or "the target system"
    owner = (config.get("roles", {}) or {}).get("architect") or "Lead Architect (config.roles.architect unset)"
    threshold = sources_state["threshold"]

    md = []
    md.append("# Migration Risk Log — {0}".format(project))
    md.append("")
    md.append("> Living risk register, mined deterministically from the committed pipeline "
              "data (requirements graph, annotations, coverage report, blueprint, contracts, "
              "config). Re-rendered by `anti-legacy:risk-log` as the pipeline progresses — "
              "status **draft**. Every row traces to its source (a req_id, a graph node, or a "
              "file); do not hand-edit — fix the source artifact and re-render.")
    md.append("")
    md.append("Generated: {0}".format(D.now_iso()))
    md.append("")

    # -- Methodology (heuristics documented in-doc, per the spec) ----------- #
    md.append("## Methodology")
    md.append("")
    md.append("Seven sources are mined; each risk row carries a **Source** ref so the thread "
              "back to evidence never breaks. Likelihood / Impact use a fixed L/M/H heuristic:")
    md.append("")
    md.append(D.md_table(
        ["Source", "Risk category", "Likelihood", "Impact", "Why"],
        [
            ["1. RISK-flagged nodes", "Unresolved rule", "H", "M",
             "extraction could not resolve the rule"],
            ["2. Low-confidence rules (conf < {0:.2f})".format(threshold),
             "Mis-translation", "M", "M", "rule stated below the resolve threshold"],
            ["3. Dropped requirements", "Scope cut", "M", "M",
             "functionality intentionally removed — confirm intent"],
            ["4. Parity / COMP-3 numeric", "Precision loss", "M", "H",
             "COMP-3 / decimal precision loss is silent and catastrophic"],
            ["5. Coverage holes", "Unknown behavior", "M", "M",
             "behavior-bearing node with no rule"],
            ["6. Unresolvable / rule-less", "Build gap", "H", "M",
             "no rule to build against"],
            ["7. Cross-language / cross-repo", "Seam mis-wiring", "M", "M",
             "cross-tech / inter-repo edges"],
        ]))
    md.append("")
    md.append("**Severity** = the higher of Likelihood/Impact; **Critical** when both are High. "
              "All rows start **Open**. Default **Owner** is `{0}`.".format(owner))
    md.append("")

    # -- Sources assessed (honesty about coverage — §6) --------------------- #
    md.append("## Sources assessed")
    md.append("")
    md.append("Which inputs were present when this log was rendered. An ABSENT input means "
              "the corresponding risk source was **not assessed** — not that it is risk-free.")
    md.append("")
    md.append(_present("requirements_graph.json (sources 2,3,4c,6,7)",
                       sources_state["graph"],
                       "" if sources_state["graph"] else "no graph — risk log cannot be mined"))
    md.append(_present("annotations.jsonl (source 1 — RISK-flagged nodes)",
                       sources_state["annotations"],
                       "" if sources_state["annotations"]
                       else "RISK-flagged-node risks NOT assessed"))
    md.append(_present("coverage-report.json (source 5 — coverage holes)",
                       sources_state["coverage"],
                       "" if sources_state["coverage"]
                       else "coverage-hole risks NOT assessed"))
    md.append(_present("blueprint.json (source 4b — numeric columns)",
                       sources_state["blueprint"],
                       "" if sources_state["blueprint"]
                       else "blueprint numeric-field parity risks NOT assessed"))
    md.append(_present("contracts/*.contract.json (source 4a — parity_rules)",
                       sources_state["contracts"],
                       "" if sources_state["contracts"]
                       else "explicit contract parity_rules NOT assessed"))
    md.append(_present("config.json (source 7 — cross-language estate)",
                       sources_state["config"],
                       "" if sources_state["config"]
                       else "cross-language seam risks NOT assessed; owner defaulted"))
    md.append("")

    # -- Assemble + number the risks ---------------------------------------- #
    risks = sources_state["risks"]
    # Stable order: severity (worst first), then category, then source_ref.
    risks_sorted = sorted(
        risks, key=lambda r: (_sev_order(r.severity), r.category, r.source_ref))
    numbered = []
    for i, r in enumerate(risks_sorted, 1):
        numbered.append(("RISK-LOG-{0:03d}".format(i), r))

    # -- Summary count-by-category ------------------------------------------ #
    md.append("## Summary")
    md.append("")
    if not numbered:
        md.append("**0 risks identified** from the inputs that were present. This is not a "
                  "guarantee of zero risk — see *Sources assessed* above for inputs that were "
                  "ABSENT and therefore not mined.")
        md.append("")
    else:
        md.append("**{0} risk{1} identified.** Count by category:".format(
            len(numbered), "" if len(numbered) == 1 else "s"))
        md.append("")
        by_cat = {}
        for _id, r in numbered:
            by_cat[r.category] = by_cat.get(r.category, 0) + 1
        md.append(D.md_table(
            ["Category", "Count"],
            [[cat, by_cat[cat]] for cat in sorted(by_cat)]))
        md.append("")
        by_sev = {}
        for _id, r in numbered:
            by_sev[r.severity] = by_sev.get(r.severity, 0) + 1
        md.append("By severity: " + ", ".join(
            "{0} {1}".format(by_sev[s], s) for s in ("Critical", "High", "Medium", "Low")
            if s in by_sev) + ".")
        md.append("")

    # -- Top risks callout (highest severity first) ------------------------- #
    md.append("## Top risks")
    md.append("")
    if not numbered:
        md.append("_None — no risks mined from the present inputs._")
        md.append("")
    else:
        top = numbered[:5]
        for rid, r in top:
            md.append("- **{0}** [{1}] _{2}_ — {3} (source: {4})".format(
                rid, r.severity, r.category,
                D.md_escape(r.description)[:140], D.md_escape(r.source_ref)))
        md.append("")

    # -- Full register table ------------------------------------------------ #
    md.append("## Risk register")
    md.append("")
    if not numbered:
        md.append("_No risk rows. Methodology and sources are recorded above so this log is "
                  "auditable even when empty._")
        md.append("")
    else:
        rows = []
        for rid, r in numbered:
            rows.append([
                rid, r.category, r.description, r.source_ref,
                r.likelihood, r.impact, r.severity, r.mitigation, "Open", owner,
            ])
        md.append(D.md_table(
            ["Risk ID", "Category", "Description", "Source (traceability)",
             "Likelihood", "Impact", "Severity", "Mitigation", "Status", "Owner"],
            rows))
        md.append("")

    md.append("---")
    md.append("")
    md.append("_Risk log is a living artifact (status: draft). Re-run "
              "`python3 .anti-legacy/run.py risk_log` after extraction / graph-translate / "
              "blueprint / test-strategy to refresh it._")
    md.append("")
    return "\n".join(md), len(numbered)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build(config, graph, annotations, coverage, blueprint, contracts):
    """Mine every source and return (sources_state dict, risks list)."""
    cov_cfg = config.get("coverage", {}) or {}
    try:
        threshold = float(cov_cfg.get("resolve_threshold", DEFAULT_RESOLVE_THRESHOLD))
    except (TypeError, ValueError):
        threshold = DEFAULT_RESOLVE_THRESHOLD

    risks = []
    risks += mine_risk_flagged(annotations)
    risks += mine_low_confidence(graph, threshold)
    risks += mine_dropped(graph)
    risks += mine_parity(graph, blueprint, contracts)
    risks += mine_coverage_holes(coverage)
    risks += mine_unresolvable_and_ruleless(graph)
    risks += mine_cross_language(config, graph)

    sources_state = {
        "threshold": threshold,
        "graph": bool(graph),
        "annotations": bool(annotations),
        "coverage": bool(coverage),
        "blueprint": bool(blueprint),
        "contracts": bool(contracts),
        "config": bool(config),
        "risks": risks,
    }
    return sources_state


def main():
    parser = argparse.ArgumentParser(
        prog="risk_log",
        description="Render a living migration risk log / register mined from the "
                    "graph + coverage + annotations + dropped requirements, and "
                    "register it as a manifest deliverable (status=draft).",
    )
    parser.add_argument("--no-register", action="store_true",
                        help="Write the risk log but do not register it in the manifest "
                             "(hermetic / dry run).")
    args = parser.parse_args()

    # Load every source via the shared library (each degrades gracefully).
    config = D.load_config()
    graph = D.load_requirements_graph()
    annotations = D.load_annotations()
    coverage = D.load_coverage()
    blueprint = D.load_blueprint()
    contracts = D.load_contracts()

    # Done-gate precondition: the risk log is a "graph is ready" deliverable. With
    # NO requirements graph at all there is nothing to mine — fail loudly (the
    # other sources only enrich; the graph is the spine).
    if not graph or not (graph.get("domains")):
        sys.stderr.write(
            "risk-log: no requirements graph found at "
            ".anti-legacy/requirements/requirements_graph.json (or it has no domains). "
            "Run the pipeline through graph-translate first.\n")
        sys.exit(1)

    sources_state = build(config, graph, annotations, coverage, blueprint, contracts)
    content, count = render(graph, annotations, coverage, blueprint, contracts,
                            config, sources_state)

    # Done-gate: the rendered content must be non-empty before we write/register.
    if not content.strip():
        sys.stderr.write("risk-log: rendered content was empty — refusing to write.\n")
        sys.exit(1)

    abs_path = D.write_deliverable(OUT_RELNAME, content)

    # Post-write done-gate: file exists and is non-empty.
    if not (os.path.isfile(abs_path) and os.path.getsize(abs_path) > 0):
        sys.stderr.write("risk-log: written file is missing or empty: {0}\n".format(abs_path))
        sys.exit(1)

    stored = None
    if not args.no_register:
        stored = D.register_deliverable(
            ARTIFACT_ID, abs_path, PRODUCED_BY,
            fmt="markdown", status="draft", depends_on=["requirements-graph"])

    print("risk-log written: {0}".format(abs_path))
    print("risks identified: {0}".format(count))
    if args.no_register:
        print("(not registered — --no-register)")
    elif stored:
        print("registered artifact '{0}' (status=draft) at manifest path: {1}".format(
            ARTIFACT_ID, stored))
    else:
        print("(manifest absent — artifact not registered)")


if __name__ == "__main__":
    main()
