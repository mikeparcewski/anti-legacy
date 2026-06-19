#!/usr/bin/env python3
"""
decisions_log — render an ADR-style (Architecture Decision Record) decisions log
from the anti-legacy pipeline's structured data.

The decisions log is a LIVING deliverable (status=draft): it grows as gates are
signed off, as the blueprint settles the architecture, and as scope is cut. It is
rendered DETERMINISTICALLY from three decision sources — never coined by an LLM:

  1. Gate sign-offs   — audit.jsonl `gate-signed-off` events (each Accepted) plus
                        the manifest's current `gates` opinions.
  2. Architecture     — blueprint.json `style` + per-domain `package`, and the
                        config/requirements `migration_mode`.
  3. Scope cuts       — dropped requirements (disposition == "drop") with reason.

Each ADR cites its source. A summary index table sits at the top. The renderer
states which sources were available and surfaces when the log is partial (§6
voice — name the gap, do not soften it).

A deliverable REGISTERS its artifact (deliverable-decisions-log, markdown, status
draft); it NEVER advances the phase. Use --no-register for hermetic tests.

Pure standard library + antilegacy_core.deliverables. Cross-platform (every path
via os.path); workspace is os.getcwd() (the library owns anchoring), never
__file__.
"""
import argparse
import json
import os
import sys

from antilegacy_core import deliverables as D

ARTIFACT_ID = "deliverable-decisions-log"
PRODUCED_BY = "anti-legacy:decisions-log"
OUTPUT_REL = "decisions-log.md"

# Source labels used in the index table + the per-ADR "Source" line.
SRC_GATE = "gate sign-off"
SRC_ARCH = "architecture (blueprint/config)"
SRC_SCOPE = "scope (dropped requirement)"


# --------------------------------------------------------------------------- #
# Decision builders — each returns a list of ADR dicts. An ADR dict carries:
#   title, status, date, context, decision, consequences, source
# --------------------------------------------------------------------------- #
def _gate_decisions(audit, manifest):
    """Gate sign-offs: each `gate-signed-off` audit event is an Accepted decision.

    The manifest's current `gates` opinions are reflected too — a gate present in
    the manifest but with no audit row (e.g. waived directly) is still surfaced.
    """
    adrs = []
    seen = set()  # (gate_id, opinion, timestamp) de-dupe across audit+manifest

    for ev in D.audit_events(audit, "gate-signed-off"):
        det = ev.get("details") or {}
        gate_id = det.get("gate_id") or "UNKNOWN_GATE"
        opinion = (det.get("opinion") or "passed").lower()
        evaluator = det.get("evaluator") or "_unrecorded_"
        rationale = det.get("rationale") or "_no rationale recorded_"
        ts = ev.get("timestamp") or "_undated_"
        evidence = det.get("evidence") or []
        key = (gate_id, opinion, ts)
        if key in seen:
            continue
        seen.add(key)

        status = {"passed": "Accepted", "waived": "Accepted (waived)",
                  "failed": "Superseded"}.get(opinion, "Accepted")
        ev_str = ", ".join(str(e) for e in evidence) if isinstance(evidence, list) else str(evidence)
        consequences = ("Gate {0} recorded **{1}**; the pipeline may proceed past "
                        "this checkpoint.".format(gate_id, opinion))
        if opinion == "failed":
            consequences = ("Gate {0} recorded **failed** — the pipeline was kicked "
                            "back to that gate's producing phase.".format(gate_id))
        adrs.append({
            "title": "Gate {0}: {1}".format(gate_id, opinion),
            "status": status,
            "date": ts,
            "context": ("Transition gate {0} required a sign-off before the pipeline "
                        "could advance.".format(gate_id)),
            "decision": ("{0} signed off **{1}** — \"{2}\"".format(evaluator, opinion, rationale)),
            "consequences": consequences + (" Evidence: {0}.".format(ev_str) if ev_str else ""),
            "source": SRC_GATE,
        })

    # Reflect manifest.gates opinions that produced no matching audit row.
    # A gate is a DECISION only once it carries a RESOLVED opinion
    # (passed/waived/failed). The manifest seeds every gate `status: pending`
    # with no opinion — pending/undecided gates are NOT decisions and are
    # skipped (rendering them would invent decisions that were never made).
    _resolved = {"passed", "waived", "failed"}
    gates = (manifest or {}).get("gates") or {}
    for gate_id, g in sorted(gates.items()):
        if not isinstance(g, dict):
            continue
        opinion = (g.get("opinion") or g.get("status") or "").lower()
        if opinion not in _resolved:
            continue
        ts = g.get("decided_at") or g.get("timestamp") or "_undated_"
        # Skip if an equivalent audit-derived ADR already exists for this gate+opinion.
        if any(k[0] == gate_id and k[1] == opinion for k in seen):
            continue
        seen.add((gate_id, opinion, ts))
        status = {"passed": "Accepted", "waived": "Accepted (waived)",
                  "failed": "Superseded"}.get(opinion, "Proposed")
        adrs.append({
            "title": "Gate {0}: {1}".format(gate_id, opinion),
            "status": status,
            "date": ts,
            "context": "Transition gate {0} (current manifest state).".format(gate_id),
            "decision": "{0} recorded **{1}** — \"{2}\"".format(
                g.get("evaluator") or "_unrecorded_", opinion,
                g.get("rationale") or "_no rationale recorded_"),
            "consequences": "Reflected from manifest.gates (no separate audit row found).",
            "source": SRC_GATE,
        })
    return adrs


def _architecture_decisions(blueprint, config, requirements):
    """Architectural choices: blueprint `style`, per-domain `package`, migration_mode."""
    adrs = []

    style = blueprint.get("style") if isinstance(blueprint, dict) else None
    if style:
        target_stack = blueprint.get("target_stack") or config.get("target_stack") or "the target stack"
        adrs.append({
            "title": "Target architecture style: {0}".format(style),
            "status": "Accepted",
            "date": "_design-time_",
            "context": ("The target system needs an architecture style that organizes its "
                        "domains and components on {0}.".format(target_stack)),
            "decision": "Adopt the **{0}** architecture style.".format(style),
            "consequences": ("All target domains and components follow the {0} layout; "
                             "see blueprint.json and ARCHITECTURE.md.".format(style)),
            "source": SRC_ARCH,
        })

    # Per-domain package decisions (one ADR per domain that declares a package).
    domains = blueprint.get("domains") if isinstance(blueprint, dict) else None
    if isinstance(domains, dict):
        for dname in sorted(domains):
            d = domains[dname] or {}
            pkg = d.get("package")
            if not pkg:
                continue
            comps = d.get("components") or {}
            ccount = len(comps) if isinstance(comps, dict) else 0
            adrs.append({
                "title": "Domain `{0}` → package `{1}`".format(dname, pkg),
                "status": "Accepted",
                "date": "_design-time_",
                "context": ("Capability domain `{0}` must map to a concrete package in the "
                            "target codebase.".format(dname)),
                "decision": "Place domain `{0}` under package `{1}`.".format(dname, pkg),
                "consequences": ("{0} component(s) are generated under `{1}`.".format(ccount, pkg)),
                "source": SRC_ARCH,
            })

    # migration_mode — config wins, fall back to requirements metadata.
    mode = config.get("migration_mode") if isinstance(config, dict) else None
    if not mode:
        meta = requirements.get("metadata") if isinstance(requirements, dict) else None
        if isinstance(meta, dict):
            mode = meta.get("migration_mode")
    if mode:
        if mode == "functional":
            cons = ("Legacy modules are grouped into business capabilities; the requirements "
                    "graph is a capability plan, not a 1:1 code skeleton.")
        elif mode == "structural":
            cons = ("Produces 1:1 code-equivalent nodes for like-for-like rehost — not a "
                    "capability re-think.")
        else:
            cons = "Migration mode governs how the requirements graph is shaped."
        adrs.append({
            "title": "Migration mode: {0}".format(mode),
            "status": "Accepted",
            "date": "_design-time_",
            "context": ("The pipeline must choose whether to re-think the estate into "
                        "capabilities (functional) or rehost like-for-like (structural)."),
            "decision": "Run the pipeline in **{0}** migration mode.".format(mode),
            "consequences": cons,
            "source": SRC_ARCH,
        })
    return adrs


def _scope_decisions(requirements):
    """Scope cuts: every dropped requirement is an explicit, reasoned decision."""
    adrs = []
    for domain, req_id, node in D.dropped_requirements(requirements):
        reason = node.get("disposition_reason") or "_no reason recorded_"
        title = node.get("title") or req_id
        legacy = node.get("legacy_components") or []
        legacy_str = ", ".join(str(c) for c in legacy) if legacy else "_none recorded_"
        adrs.append({
            "title": "Drop requirement {0} ({1})".format(req_id, title),
            "status": "Accepted",
            "date": "_graph-translate_",
            "context": ("Requirement `{0}` in domain `{1}` was a candidate capability from the "
                        "legacy estate (legacy_components: {2}).".format(req_id, domain, legacy_str)),
            "decision": "**Drop** `{0}` — it will not be built in the target system.".format(req_id),
            "consequences": ("Reason: {0}. This is an explicit scope cut, not a silent "
                             "omission — the legacy behavior is intentionally not carried "
                             "forward.".format(reason)),
            "source": SRC_SCOPE,
        })
    return adrs


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def render(audit, manifest, blueprint, config, requirements):
    """Render the full ADR-style decisions log. Returns (markdown, adr_count)."""
    gate = _gate_decisions(audit, manifest)
    arch = _architecture_decisions(blueprint, config, requirements)
    scope = _scope_decisions(requirements)

    # Number ADRs in a stable, grouped order: gate, arch, scope.
    grouped = [
        (SRC_GATE, "Gate sign-offs", gate),
        (SRC_ARCH, "Architectural choices", arch),
        (SRC_SCOPE, "Scope decisions", scope),
    ]
    numbered = []
    n = 0
    for _src, _label, adrs in grouped:
        for adr in adrs:
            n += 1
            adr["id"] = "ADR-{0:03d}".format(n)
            numbered.append(adr)
    total = len(numbered)

    project = (config.get("project") or {}).get("name") if isinstance(config.get("project"), dict) else None
    project = project or config.get("project_name") or (blueprint or {}).get("project") or "the modernization"

    md = []
    md.append("# Decisions Log — {0}".format(project))
    md.append("")
    md.append("> ADR-style (Architecture Decision Record) log, rendered by "
              "`{0}` from the pipeline's structured decision sources. "
              "**Living document** (status: draft) — re-run as gates clear, the "
              "blueprint settles, scope is cut, and decisions are recorded. Edit "
              "the sources and re-render, not this file by hand.".format(PRODUCED_BY))
    md.append("")
    md.append("_Generated: {0}_".format(D.now_iso()))
    md.append("")

    # ---- Source availability (§6: state what was available; name the gaps). --
    md.append("## Sources")
    md.append("")
    avail = []
    avail.append("Gate sign-offs: **{0}**".format(
        "{0} recorded".format(len(gate)) if gate else "none recorded yet"))
    avail.append("Architecture (blueprint/config): **{0}**".format(
        "{0} decision(s)".format(len(arch)) if arch else "no blueprint style / migration mode found"))
    avail.append("Scope cuts (dropped requirements): **{0}**".format(
        "{0} dropped".format(len(scope)) if scope else "none dropped"))
    for line in avail:
        md.append("- {0}".format(line))
    md.append("")
    if total == 0:
        md.append("_No decisions found in any source yet. This log is empty — it will "
                  "populate as gates are signed off, the blueprint is produced, and scope "
                  "is cut._")
        md.append("")
        return "\n".join(md), 0

    missing = [name for name, lst in
               (("gate sign-offs", gate), ("architecture", arch),
                ("scope cuts", scope)) if not lst]
    if missing:
        md.append("_This log is **partial**: no {0} contributed. It reflects only the "
                  "sources present above._".format(", ".join(missing)))
        md.append("")

    # ---- Index table. -------------------------------------------------------
    md.append("## Index")
    md.append("")
    rows = [[a["id"], a["title"], a["status"], a["source"]] for a in numbered]
    md.append(D.md_table(["ADR", "Title", "Status", "Source"], rows))
    md.append("")

    # ---- Grouped detail. ----------------------------------------------------
    by_src = {}
    for a in numbered:
        by_src.setdefault(a["source"], []).append(a)
    for _src, label, adrs in grouped:
        group = by_src.get(_src) or []
        if not group:
            continue
        md.append("## {0}".format(label))
        md.append("")
        for a in group:
            md.append("### {0} — {1}".format(a["id"], a["title"]))
            md.append("")
            md.append("- **Status:** {0}".format(a["status"]))
            md.append("- **Date:** {0}".format(a["date"]))
            md.append("- **Source:** {0}".format(a["source"]))
            md.append("")
            md.append("**Context.** {0}".format(a["context"]))
            md.append("")
            md.append("**Decision.** {0}".format(a["decision"]))
            md.append("")
            md.append("**Consequences.** {0}".format(a["consequences"]))
            md.append("")
    return "\n".join(md), total


def main():
    parser = argparse.ArgumentParser(
        prog="decisions_log",
        description="Render an ADR-style decisions log from gate sign-offs, the "
                    "blueprint/config architecture, and dropped-requirement scope cuts. "
                    "Registers deliverable-decisions-log (status draft); never advances the phase.",
    )
    parser.add_argument("--requirements", default=None,
                        help="Path to requirements_graph.json (default: the standard location)")
    parser.add_argument("--blueprint", default=None,
                        help="Path to blueprint.json (default: the standard location)")
    parser.add_argument("--config", default=None,
                        help="Path to config.json (default: the standard location)")
    parser.add_argument("--audit", default=None,
                        help="Path to audit.jsonl (default: the standard location)")
    parser.add_argument("--manifest", default=None,
                        help="Path to manifest.json (default: the standard location)")
    parser.add_argument("--no-register", action="store_true",
                        help="Write the log but do not register it in the manifest")
    args = parser.parse_args()

    # Loaders own the default paths; only pass an override when given.
    requirements = D.load_requirements_graph(args.requirements) if args.requirements else D.load_requirements_graph()
    blueprint = D.load_blueprint(args.blueprint) if args.blueprint else D.load_blueprint()
    config = D.load_config(args.config) if args.config else D.load_config()
    audit = D.load_audit(args.audit) if args.audit else D.load_audit()
    manifest = D.load_manifest(args.manifest) if args.manifest else D.load_manifest()
    markdown, count = render(audit, manifest, blueprint, config, requirements)

    # Done-gate: the .md must be non-empty (it always is — header + sources at
    # minimum). Guard anyway so we never register an empty artifact.
    if not markdown.strip():
        print("Error: rendered decisions log is empty; refusing to write.", file=sys.stderr)
        sys.exit(1)

    abs_path = D.write_deliverable(OUTPUT_REL, markdown)
    print("Wrote {0}".format(abs_path))
    print("ADRs: {0}".format(count))

    if not args.no_register:
        stored = D.register_deliverable(
            ARTIFACT_ID, abs_path, PRODUCED_BY,
            fmt="markdown", status="draft", depends_on=["requirements-graph"],
        )
        if stored:
            print("Registered {0} -> {1} (status draft)".format(ARTIFACT_ID, stored))
        else:
            print("No manifest found; wrote the log but did not register it.")


if __name__ == "__main__":
    main()
