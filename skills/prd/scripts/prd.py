#!/usr/bin/env python3
"""prd — render the stakeholder-facing Product Requirements Document.

The PRD is a DELIVERABLE (anti-legacy:prd). It renders a detailed, human-facing
Product Requirements Document deterministically FROM the pipeline's structured
data — the requirements graph (the spine) plus the coverage report (the
resolved-or-flagged evidence). It does NOT coin prose with an LLM and it does
NOT re-extract anything: it reads what graph-translator already produced and
presents it for product / business stakeholders.

Inputs (all loaded via antilegacy_core.deliverables, which owns the default
.anti-legacy/... paths and degrades gracefully on a partial workspace):

  - requirements_graph.json  — REQUIRED. domains -> requirements / entities,
                               each requirement carrying its legacy_components
                               (traceability), business_rules, validations,
                               error_paths, data_access, dependencies,
                               disposition + status.
  - coverage-report.json     — OPTIONAL. resolved-or-flagged coverage %,
                               mean_confidence, resolve_threshold. When absent,
                               the executive summary says "coverage not yet
                               computed" rather than fabricating a number.
  - config.json              — OPTIONAL. project name, migration_mode,
                               coverage.resolve_threshold (the low-confidence
                               flag line).

Output: .anti-legacy/deliverables/product-requirements.md
Artifact id: deliverable-prd (registered, fmt markdown, depends_on
requirements-graph). NEVER advances the phase (a deliverable registers; phase
advancement is owned by the phase skills).

Traceability is mandatory (AGENTS.md §2): every active requirement cites its
legacy_components and its RULE-* ids, and a closing appendix tabulates
req_id -> legacy_components -> rule ids so the thread never breaks.

Voice (AGENTS.md §6 / §Voice): factual; surface gaps, do not soften them. The
"Out of scope (dropped)" section names every dropped requirement WITH its
reason; the "Coverage & gaps" section names requirements with no business
rules, review/unresolvable requirements, and every low-confidence rule.

Pure standard library + antilegacy_core.deliverables. Cross-platform
(macOS / Linux / WSL / Windows): every path is built with os.path; no
shell-isms.
"""
import argparse
import os
import sys

from antilegacy_core import deliverables as D

ARTIFACT_ID = "deliverable-prd"
PRODUCED_BY = "anti-legacy:prd"
OUTPUT_RELNAME = "product-requirements.md"
DEFAULT_RESOLVE_THRESHOLD = 0.75


# --------------------------------------------------------------------------- #
# Small helpers (resolution + formatting). All pure / deterministic.
# --------------------------------------------------------------------------- #
def _project_name(config, graph):
    """Resolve the project's display name across known config shapes.

    The setup skill writes a FLAT config.json (`project_name`); some recon docs
    describe a nested `project.name`. Accept both, then fall back to the graph
    metadata, then a stable default — never crash on a thin/absent config.
    """
    name = config.get("project_name")
    if not name:
        proj = config.get("project")
        if isinstance(proj, dict):
            name = proj.get("name")
        elif isinstance(proj, str):
            name = proj
    if not name:
        meta = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
        name = meta.get("project") or meta.get("name")
    return name or "modernized-application"


def _migration_mode(config, graph):
    """migration_mode: config is the source of truth; graph metadata is fallback."""
    mode = config.get("migration_mode")
    if not mode:
        proj = config.get("project")
        if isinstance(proj, dict):
            mode = proj.get("migration_mode")
    if not mode:
        meta = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
        mode = meta.get("migration_mode")
    return mode or "(not set)"


def _resolve_threshold(config, coverage):
    """The low-confidence flag threshold.

    Precedence: config.coverage.resolve_threshold, then the coverage report's
    own resolve_threshold, then the documented default (0.75).
    """
    cov_cfg = config.get("coverage")
    if isinstance(cov_cfg, dict):
        t = cov_cfg.get("resolve_threshold")
        if isinstance(t, (int, float)):
            return float(t)
    t = coverage.get("resolve_threshold") if isinstance(coverage, dict) else None
    if isinstance(t, (int, float)):
        return float(t)
    return DEFAULT_RESOLVE_THRESHOLD


def _fmt_conf(value, threshold):
    """Render a confidence as 'NN%' with a ⚠ flag when below threshold.

    Missing confidence renders as '—' (and counts as a gap upstream).
    """
    if not isinstance(value, (int, float)):
        return "—"
    pct = "{0:.0f}%".format(float(value) * 100)
    return ("⚠ " + pct) if float(value) < threshold else pct


def _fmt_pct(value):
    if not isinstance(value, (int, float)):
        return "—"
    return "{0:.1f}%".format(float(value) * 100)


def _join_code(items):
    """Render a list as comma-joined `code` spans, or an em-dash when empty."""
    vals = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not vals:
        return "—"
    return ", ".join("`{0}`".format(v) for v in vals)


# --------------------------------------------------------------------------- #
# Section renderers — each returns a list[str] of Markdown lines.
# --------------------------------------------------------------------------- #
def _render_header(project, mode, graph_src):
    return [
        "# Product Requirements Document — {0}".format(project),
        "",
        "> Generated by the anti-legacy `{0}` deliverable from `{1}` (and the "
        "coverage report when present). This document is DERIVED from the "
        "requirements graph, not hand-written — edit the source artifacts and "
        "re-render, not this file.".format(PRODUCED_BY, graph_src),
        "",
        "- **Project:** {0}".format(project),
        "- **Migration mode:** {0}".format(mode),
        "- **Generated from:** `{0}`".format(graph_src),
        "- **Generated at:** {0}".format(D.now_iso()),
        "",
    ]


def _render_executive_summary(graph, coverage, counts):
    md = ["## Executive summary", ""]
    domains = graph.get("domains") if isinstance(graph.get("domains"), dict) else {}
    md.append("This system comprises **{0} business domain{1}** and **{2} "
              "requirement{3}** ({4} active, {5} dropped, {6} unresolvable).".format(
                  len(domains), "" if len(domains) == 1 else "s",
                  counts["total"], "" if counts["total"] == 1 else "s",
                  counts["active"], counts["dropped"], counts["unresolvable"]))
    md.append("")
    if coverage:
        cov_line = "Resolved-or-flagged coverage: **{0}**".format(
            _fmt_pct(coverage.get("coverage")))
        mc = coverage.get("mean_confidence")
        if isinstance(mc, (int, float)):
            cov_line += " · mean rule confidence: **{0}**".format(_fmt_pct(mc))
        rf = coverage.get("risk_flagged")
        if isinstance(rf, int):
            cov_line += " · {0} node(s) RISK-flagged for human research".format(rf)
        md.append(cov_line + ".")
    else:
        md.append("Coverage not yet computed (no `coverage-report.json` present "
                  "— run extraction to produce it).")
    md.append("")
    return md


def _render_business_rules_table(node, threshold):
    rules = node.get("business_rules") or []
    if not rules:
        return ["_No business rules captured for this requirement._", ""]
    rows = []
    for r in rules:
        rows.append([
            r.get("id", "—"),
            r.get("statement", ""),
            _fmt_conf(r.get("confidence"), threshold),
        ])
    return ["**Business rules**", "",
            D.md_table(["RULE", "Statement", "Confidence"], rows), ""]


def _render_validations_table(node):
    vals = node.get("validations") or []
    if not vals:
        return []
    rows = []
    for v in vals:
        rows.append([
            v.get("id", "—"),
            v.get("statement", ""),
            v.get("field", "—"),
            v.get("error_ref", "—"),
        ])
    return ["**Validations**", "",
            D.md_table(["VAL", "Statement", "Field", "Error ref"], rows), ""]


def _render_error_paths_table(node):
    errs = node.get("error_paths") or []
    if not errs:
        return []
    rows = []
    for e in errs:
        rows.append([
            e.get("id", "—"),
            e.get("statement", ""),
            e.get("code", "—"),
        ])
    return ["**Error paths**", "",
            D.md_table(["ERR", "Statement", "Code"], rows), ""]


def _render_requirement(domain, req_id, node, threshold):
    md = []
    title = node.get("title") or req_id
    md.append("#### {0} — {1}".format(req_id, title))
    md.append("")
    desc = (node.get("description") or "").strip()
    md.append(desc if desc else "_No description provided._")
    md.append("")

    # Traceability (mandatory): legacy_components ground the requirement.
    md.append("- **Legacy components:** {0}".format(_join_code(node.get("legacy_components"))))
    if node.get("data_access"):
        md.append("- **Data access:** {0}".format(_join_code(node.get("data_access"))))
    if node.get("dependencies"):
        md.append("- **Depends on:** {0}".format(_join_code(node.get("dependencies"))))
    disp = node.get("disposition")
    if disp:
        md.append("- **Disposition:** `{0}`".format(disp))
    md.append("")

    md.extend(_render_business_rules_table(node, threshold))
    md.extend(_render_validations_table(node))
    md.extend(_render_error_paths_table(node))
    return md


def _render_entities(graph, domain):
    """Entities table for one domain (entity | field | type | description)."""
    ddata = (graph.get("domains") or {}).get(domain) or {}
    entities = ddata.get("entities") if isinstance(ddata.get("entities"), dict) else {}
    if not entities:
        return []
    rows = []
    for ename in sorted(entities):
        ent = entities[ename] or {}
        fields = ent.get("fields") or []
        if not fields:
            rows.append([ename, "—", "—", (ent.get("description") or "")])
            continue
        for f in fields:
            rows.append([
                ename,
                f.get("name", "—"),
                f.get("type", "—"),
                f.get("description", ""),
            ])
    return ["### Entities — {0}".format(domain), "",
            D.md_table(["Entity", "Field", "Type", "Description"], rows), ""]


def _render_domains(graph, active_by_domain, threshold):
    md = ["## Requirements by domain", ""]
    domains = graph.get("domains") if isinstance(graph.get("domains"), dict) else {}
    if not domains:
        md.append("_No domains found in the requirements graph._")
        md.append("")
        return md
    for domain in sorted(domains):
        md.append("### Domain: {0}".format(domain))
        md.append("")
        active = active_by_domain.get(domain, [])
        if active:
            for req_id, node in active:
                md.extend(_render_requirement(domain, req_id, node, threshold))
        else:
            md.append("_No active requirements in this domain "
                      "(all dropped or unresolvable — see the sections below)._")
            md.append("")
        md.extend(_render_entities(graph, domain))
    return md


def _render_dropped(dropped):
    """Explicit scope cuts: every dropped requirement WITH its reason."""
    md = ["## Out of scope (dropped)", ""]
    if not dropped:
        md.append("_No requirements were dropped — nothing was cut from scope._")
        md.append("")
        return md
    md.append("These legacy behaviors are explicitly **out of scope** for the "
              "target system. Each is a deliberate scope cut with a stated "
              "reason — never a silent omission:")
    md.append("")
    rows = []
    for domain, req_id, node in dropped:
        rows.append([
            domain,
            req_id,
            node.get("title") or req_id,
            node.get("disposition_reason") or "_(no reason recorded)_",
        ])
    md.append(D.md_table(["Domain", "Requirement", "Title", "Reason dropped"], rows))
    md.append("")
    return md


def _render_coverage_gaps(graph, active, threshold):
    """Surface gaps factually: no-rules, review/unresolvable, low-confidence rules."""
    md = ["## Coverage & gaps", ""]

    no_rules = []          # active reqs with zero business rules
    needs_review = []      # status review / unresolvable
    low_conf = []          # (domain, req_id, rule) with confidence < threshold

    active_keys = {(d, r) for d, r, _ in active}
    for domain, req_id, node in D.iter_requirements(graph):
        status = node.get("status")
        if status in ("review", "unresolvable"):
            needs_review.append((domain, req_id, node, status))
        if (domain, req_id) in active_keys:
            if not (node.get("business_rules") or []):
                no_rules.append((domain, req_id, node))
            for r in (node.get("business_rules") or []):
                c = r.get("confidence")
                if isinstance(c, (int, float)) and float(c) < threshold:
                    low_conf.append((domain, req_id, r))

    if not (no_rules or needs_review or low_conf):
        md.append("No open gaps detected: every active requirement carries "
                  "business rules, none are flagged for review or marked "
                  "unresolvable, and all rule confidences are at or above the "
                  "resolve threshold ({0:.0%}).".format(threshold))
        md.append("")
        return md

    md.append("The following items are **not yet fully resolved** and must be "
              "closed (or consciously accepted) before this PRD is treated as "
              "complete. Threshold for a low-confidence rule: **{0:.0%}**."
              .format(threshold))
    md.append("")

    md.append("### Active requirements with NO business rules")
    md.append("")
    if no_rules:
        rows = [[d, r, (n.get("title") or r), _join_code(n.get("legacy_components"))]
                for d, r, n in no_rules]
        md.append(D.md_table(["Domain", "Requirement", "Title", "Legacy components"], rows))
    else:
        md.append("_None — every active requirement has at least one business rule._")
    md.append("")

    md.append("### Requirements flagged for review / unresolvable")
    md.append("")
    if needs_review:
        rows = [[d, r, status, (n.get("title") or r)]
                for d, r, n, status in needs_review]
        md.append(D.md_table(["Domain", "Requirement", "Status", "Title"], rows))
    else:
        md.append("_None — no requirement is in `review` or `unresolvable` status._")
    md.append("")

    md.append("### Low-confidence business rules (below threshold)")
    md.append("")
    if low_conf:
        rows = [[d, r, rule.get("id", "—"), _fmt_pct(rule.get("confidence")),
                 rule.get("statement", "")]
                for d, r, rule in low_conf]
        md.append(D.md_table(["Domain", "Requirement", "RULE", "Confidence", "Statement"], rows))
    else:
        md.append("_None — all captured rule confidences are at or above the threshold._")
    md.append("")
    return md


def _render_traceability_appendix(active):
    """req_id -> legacy_components -> rule ids. The thread, in one table."""
    md = ["## Appendix: traceability", "",
          "Every active requirement traces back to the legacy components it "
          "replaces and the business rules extracted from them.", ""]
    if not active:
        md.append("_No active requirements to trace._")
        md.append("")
        return md
    rows = []
    for domain, req_id, node in active:
        rule_ids = [r.get("id") for r in (node.get("business_rules") or []) if r.get("id")]
        rows.append([
            req_id,
            domain,
            _join_code(node.get("legacy_components")),
            _join_code(rule_ids),
        ])
    md.append(D.md_table(["Requirement", "Domain", "Legacy components", "Rule ids"], rows))
    md.append("")
    return md


# --------------------------------------------------------------------------- #
# Document assembly
# --------------------------------------------------------------------------- #
def render_prd(graph, coverage, config, graph_src):
    project = _project_name(config, graph)
    mode = _migration_mode(config, graph)
    threshold = _resolve_threshold(config, coverage)

    active = D.active_requirements(graph)
    dropped = D.dropped_requirements(graph)
    unresolvable = [(d, r, n) for d, r, n in D.iter_requirements(graph)
                    if n.get("status") == "unresolvable"]
    total = sum(1 for _ in D.iter_requirements(graph))
    counts = {
        "total": total,
        "active": len(active),
        "dropped": len(dropped),
        "unresolvable": len(unresolvable),
    }

    active_by_domain = {}
    for domain, req_id, node in active:
        active_by_domain.setdefault(domain, []).append((req_id, node))
    for domain in active_by_domain:
        active_by_domain[domain].sort(key=lambda pair: pair[0])

    md = []
    md += _render_header(project, mode, graph_src)
    md += _render_executive_summary(graph, coverage, counts)
    md += _render_domains(graph, active_by_domain, threshold)
    md += _render_dropped(dropped)
    md += _render_coverage_gaps(graph, active, threshold)
    md += _render_traceability_appendix(active)
    return "\n".join(md)


def main():
    parser = argparse.ArgumentParser(
        prog="prd",
        description="Render the stakeholder-facing Product Requirements Document "
                    "(product-requirements.md) from the requirements graph + "
                    "coverage report, and register it as a manifest artifact.",
    )
    parser.add_argument("--requirements", default=D.P_REQUIREMENTS,
                        help="Path to requirements_graph.json "
                             "(default: .anti-legacy/requirements/requirements_graph.json)")
    parser.add_argument("--coverage", default=D.P_COVERAGE,
                        help="Path to coverage-report.json "
                             "(default: .anti-legacy/coverage-report.json)")
    parser.add_argument("--config", default=D.P_CONFIG,
                        help="Path to config.json (default: .anti-legacy/config.json)")
    parser.add_argument("--no-register", action="store_true",
                        help="Write the PRD but do not register it in the manifest")
    parser.add_argument("--force", action="store_true",
                        help="override a precheck BLOCK and render anyway (loud warning)")
    args = parser.parse_args()
    D.require_ready("deliverables", force=args.force)

    graph = D.load_requirements_graph(args.requirements)
    coverage = D.load_coverage(args.coverage)
    config = D.load_config(args.config)

    # Done-gate: a real requirements graph with >=1 requirement is mandatory.
    # Without it there is nothing to render — fail loudly rather than write a
    # hollow PRD (AGENTS.md: don't advance with broken assertions).
    if not isinstance(graph, dict) or not graph.get("domains"):
        print("Error: no requirements graph at '{0}' (or it has no domains). "
              "Run anti-legacy:graph-translator first — refusing to write a "
              "hollow PRD.".format(args.requirements), file=sys.stderr)
        sys.exit(1)
    req_count = sum(1 for _ in D.iter_requirements(graph))
    if req_count < 1:
        print("Error: requirements graph at '{0}' has 0 requirements. "
              "Nothing to render — refusing to write a hollow PRD.".format(
                  args.requirements), file=sys.stderr)
        sys.exit(1)

    content = render_prd(graph, coverage, config, args.requirements)

    # The .md must be non-empty before we register it.
    if not content.strip():
        print("Error: rendered PRD is empty — not writing or registering.",
              file=sys.stderr)
        sys.exit(1)

    out_path = D.write_deliverable(OUTPUT_RELNAME, content)
    if not (os.path.exists(out_path) and os.path.getsize(out_path) > 0):
        print("Error: PRD was not written to '{0}' (empty file).".format(out_path),
              file=sys.stderr)
        sys.exit(1)

    print("PRD written to: {0}".format(out_path))

    if not args.no_register:
        stored = D.register_deliverable(
            ARTIFACT_ID, out_path, PRODUCED_BY,
            fmt="markdown", status="final", depends_on=["requirements-graph"],
        )
        if stored:
            print("Registered artifact '{0}' -> {1}".format(ARTIFACT_ID, stored))
        else:
            print("Note: manifest absent — PRD written but not registered "
                  "(use a workspace with .anti-legacy/manifest.json to register).")


if __name__ == "__main__":
    main()
