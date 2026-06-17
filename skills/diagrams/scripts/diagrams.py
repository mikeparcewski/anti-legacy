#!/usr/bin/env python3
"""diagrams — render a Mermaid architecture diagram set from pipeline data.

A deliverable renderer (anti-legacy:diagrams). Reads the committed pipeline
artifacts — requirements graph (the structural spine), blueprint (target
components + schema), and config (source apps + deployment target) — and emits a
set of Mermaid (.mmd) diagrams plus an index README under
.anti-legacy/deliverables/diagrams/:

  context.mmd          C4 L1 system context (target system + external actors).
  containers.mmd       C4 L2 containers (domains -> components, blueprint-driven).
  domain-deps.mmd      Requirement-dependency flowchart, grouped by domain.
  erd.mmd              Entity-relationship diagram (blueprint or graph entities).
  sequence-<domain>.mmd  Per-domain interaction (top 3 domains by req count).
  deployment.mmd       Deployment topology (client -> service[stack] -> store).
  README.md            Index: links + embedded ```mermaid``` blocks (the artifact).

Mermaid is the ONLY diagram syntax (a baked-in user decision — no PlantUML).
Every node id is sanitized via D.mermaid_id() because raw legacy names (dots,
hyphens, spaces) break the Mermaid parser.

The renderer DEGRADES GRACEFULLY: the requirements graph is the one hard
requirement; the blueprint, entities, and config are optional. When the
blueprint is absent the container/sequence diagrams fall back to the
requirement level and the README flags the degradation. It REGISTERS its index
artifact but NEVER advances the phase (phase advancement is owned by the phase
skills).

Pure standard library + antilegacy_core.deliverables. Cross-platform
(macOS / Linux / WSL / Windows) — every path via os.path; workspace is
os.getcwd() (the library owns anchoring); no shell-isms.
"""
import argparse
import os
import sys

from antilegacy_core import deliverables as D

ARTIFACT_ID = "deliverable-diagrams"
PRODUCED_BY = "anti-legacy:diagrams"
DEPENDS_ON = ["requirements-graph", "blueprint-json"]

# How many domains get their own sequence diagram (top-N by requirement count).
TOP_SEQUENCE_DOMAINS = 3

# Canonical layered call chain for a sequence diagram. component_type -> rank.
_COMPONENT_RANK = {
    "controller": 0,
    "service": 1,
    "repository": 2,
    "model": 3,
    "batch": 1,  # a batch job sits at the service tier
}


# --------------------------------------------------------------------------- #
# Small shared accessors over the loaded data.
# --------------------------------------------------------------------------- #
def _component_type(comp):
    """Blueprint components carry the type as 'component_type' or 'type'."""
    return comp.get("component_type") or comp.get("type") or "component"


def _domains_with_req_counts(graph):
    """[(domain, req_count)] over the requirements graph, count-desc then name."""
    out = []
    for domain, ddata in (graph.get("domains") or {}).items():
        reqs = (ddata or {}).get("requirements") or {}
        out.append((domain, len(reqs) if isinstance(reqs, dict) else 0))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


def _blueprint_domains(blueprint):
    doms = blueprint.get("domains")
    return doms if isinstance(doms, dict) else {}


# --------------------------------------------------------------------------- #
# Diagram renderers — each returns a complete, syntactically valid Mermaid doc.
# --------------------------------------------------------------------------- #
def render_context(graph, config):
    """C4 L1 context: target system as one node; externals = source apps +
    deployment target. Honest when no externals are known (system + source apps).
    """
    project = (config.get("project_name")
               or graph.get("metadata", {}).get("project")
               or "Target System")
    stack = config.get("target_stack") or "target stack"
    sys_id = D.mermaid_id("sys_" + str(project))

    lines = ["flowchart TB",
             "  %% C4 L1 — System Context (target system + external actors/systems)",
             '  {0}["{1}<br/>(modernized · {2})"]'.format(
                 sys_id, D.md_escape(project), D.md_escape(stack))]

    source_apps = config.get("source_apps") or []
    edges = []
    if isinstance(source_apps, list) and source_apps:
        for sa in source_apps:
            if not isinstance(sa, dict):
                continue
            name = sa.get("name") or "?"
            lang = sa.get("language") or "?"
            aid = D.mermaid_id("src_" + str(name))
            lines.append('  {0}(["Legacy source: {1}<br/>({2})"])'.format(
                aid, D.md_escape(name), D.md_escape(lang)))
            edges.append("  {0} -. modernized into .-> {1}".format(aid, sys_id))
    else:
        # Honest fallback: no source apps configured.
        none_id = D.mermaid_id("src_none")
        lines.append('  {0}(["Legacy estate<br/>(source apps not configured)"])'.format(none_id))
        edges.append("  {0} -. modernized into .-> {1}".format(none_id, sys_id))

    deploy = config.get("deployment_target")
    if deploy:
        did = D.mermaid_id("deploy_" + str(deploy))
        lines.append('  {0}[("Deployment platform:<br/>{1}")]'.format(did, D.md_escape(deploy)))
        edges.append("  {0} -- deployed to --> {1}".format(sys_id, did))

    lines.extend(edges)
    return "\n".join(lines)


def render_containers(graph, blueprint):
    """C4 L2: domains as subgraphs; components from blueprint
    domains[d].components (class_name + component_type); edges from
    component.dependencies. Falls back to domains->requirements from the graph
    when no blueprint is present (caller notes the degradation).

    Returns (mermaid_text, degraded_bool).
    """
    bdoms = _blueprint_domains(blueprint)
    lines = ["flowchart TB",
             "  %% C4 L2 — Containers (domains -> components)"]

    if bdoms:
        # Blueprint-driven: real components with class names + types.
        node_ids = {}  # req_id -> mermaid node id (for dependency edges)
        for domain in sorted(bdoms):
            d = bdoms[domain] or {}
            comps = d.get("components") or {}
            sub_id = D.mermaid_id("dom_" + str(domain))
            lines.append('  subgraph {0}["Domain: {1}"]'.format(sub_id, D.md_escape(domain)))
            if isinstance(comps, dict) and comps:
                for req_id in sorted(comps):
                    c = comps[req_id] or {}
                    cls = c.get("class_name") or req_id
                    ctype = _component_type(c)
                    cid = D.mermaid_id("c_{0}_{1}".format(domain, req_id))
                    node_ids[req_id] = cid
                    lines.append('    {0}["{1}<br/><i>{2}</i><br/>({3})"]'.format(
                        cid, D.md_escape(cls), D.md_escape(ctype), D.md_escape(req_id)))
            else:
                empty = D.mermaid_id("dom_{0}_empty".format(domain))
                lines.append('    {0}["(no components)"]'.format(empty))
            lines.append("  end")
        # Dependency edges between components (by req_id).
        for domain in sorted(bdoms):
            comps = (bdoms[domain] or {}).get("components") or {}
            if not isinstance(comps, dict):
                continue
            for req_id in sorted(comps):
                src = node_ids.get(req_id)
                for dep in (comps[req_id] or {}).get("dependencies") or []:
                    dst = node_ids.get(str(dep))
                    if src and dst:
                        lines.append("  {0} --> {1}".format(src, dst))
        return "\n".join(lines), False

    # Fallback: requirement-level containers from the graph.
    lines.append("  %% NOTE: blueprint absent — showing requirement-level containers")
    node_ids = {}
    domains = graph.get("domains") or {}
    for domain in sorted(domains):
        reqs = (domains[domain] or {}).get("requirements") or {}
        sub_id = D.mermaid_id("dom_" + str(domain))
        lines.append('  subgraph {0}["Domain: {1}"]'.format(sub_id, D.md_escape(domain)))
        if isinstance(reqs, dict) and reqs:
            for req_id in sorted(reqs):
                node = reqs[req_id] or {}
                title = node.get("title") or req_id
                rid = D.mermaid_id("r_{0}_{1}".format(domain, req_id))
                node_ids[req_id] = rid
                lines.append('    {0}["{1}<br/>({2})"]'.format(
                    rid, D.md_escape(title), D.md_escape(req_id)))
        else:
            empty = D.mermaid_id("dom_{0}_empty".format(domain))
            lines.append('    {0}["(no requirements)"]'.format(empty))
        lines.append("  end")
    for domain in sorted(domains):
        reqs = (domains[domain] or {}).get("requirements") or {}
        if not isinstance(reqs, dict):
            continue
        for req_id in sorted(reqs):
            src = node_ids.get(req_id)
            for dep in (reqs[req_id] or {}).get("dependencies") or []:
                dst = node_ids.get(str(dep))
                if src and dst:
                    lines.append("  {0} --> {1}".format(src, dst))
    return "\n".join(lines), True


def render_domain_deps(graph):
    """Flowchart of requirement dependencies (node.dependencies, req->req),
    grouped by domain subgraph. The traceability backbone in visual form.
    """
    lines = ["flowchart LR",
             "  %% Requirement dependency graph (req_id -> req_id), grouped by domain"]
    node_ids = {}
    domains = graph.get("domains") or {}
    for domain in sorted(domains):
        reqs = (domains[domain] or {}).get("requirements") or {}
        sub_id = D.mermaid_id("dom_" + str(domain))
        lines.append('  subgraph {0}["{1}"]'.format(sub_id, D.md_escape(domain)))
        if isinstance(reqs, dict) and reqs:
            for req_id in sorted(reqs):
                node = reqs[req_id] or {}
                title = node.get("title") or req_id
                rid = D.mermaid_id("r_" + str(req_id))
                node_ids[req_id] = rid
                lines.append('    {0}["{1}<br/>{2}"]'.format(
                    rid, D.md_escape(req_id), D.md_escape(title)))
        else:
            empty = D.mermaid_id("dom_{0}_empty".format(domain))
            lines.append('    {0}["(no requirements)"]'.format(empty))
        lines.append("  end")

    edge_count = 0
    for _domain, req_id, node in D.iter_requirements(graph):
        src = node_ids.get(req_id)
        if not src:
            continue
        for dep in node.get("dependencies") or []:
            dst = node_ids.get(str(dep))
            if dst:
                lines.append("  {0} --> {1}".format(dst, src))
                edge_count += 1
    if edge_count == 0:
        lines.append("  %% (no inter-requirement dependencies declared)")
    return "\n".join(lines)


def render_erd(graph, blueprint):
    """Mermaid erDiagram. Prefer blueprint entities (columns name/type/pk),
    else graph entities (fields name/type). Best-effort relationships from
    data_access (a requirement's entity -> the stores it accesses).

    Returns (mermaid_text, source_label).
    """
    lines = ["erDiagram"]
    bdoms = _blueprint_domains(blueprint)
    rendered = {}  # raw entity name -> mermaid entity id
    source_label = "blueprint.json"

    def emit_entity(ent_id, attrs):
        # attrs: list of (type, name, key) — Mermaid: TYPE name KEY
        lines.append("  {0} {{".format(ent_id))
        if attrs:
            for typ, name, key in attrs:
                typ_s = D.mermaid_id(typ or "string")
                name_s = D.mermaid_id(name or "field")
                suffix = " {0}".format(key) if key else ""
                lines.append("    {0} {1}{2}".format(typ_s, name_s, suffix))
        else:
            lines.append("    string id")
        lines.append("  }")

    if bdoms:
        for domain in sorted(bdoms):
            entities = (bdoms[domain] or {}).get("entities") or {}
            if not isinstance(entities, dict):
                continue
            for ename in sorted(entities):
                ent = entities[ename] or {}
                eid = D.mermaid_id(ename)
                rendered[ename] = eid
                attrs = []
                for col in ent.get("columns") or []:
                    if not isinstance(col, dict):
                        continue
                    key = "PK" if col.get("pk") else ""
                    attrs.append((col.get("type"), col.get("name"), key))
                emit_entity(eid, attrs)
    else:
        source_label = "requirements_graph.json"
        for domain, ename, ent in D.iter_entities(graph):
            eid = D.mermaid_id(ename)
            if ename in rendered:
                continue
            rendered[ename] = eid
            attrs = []
            for fld in (ent or {}).get("fields") or []:
                if not isinstance(fld, dict):
                    continue
                attrs.append((fld.get("type"), fld.get("name"), ""))
            emit_entity(eid, attrs)

    if not rendered:
        # erDiagram with no entities is still valid but empty; add a placeholder.
        lines.append("  NO_ENTITIES {")
        lines.append("    string note")
        lines.append("  }")
        return "\n".join(lines), source_label

    # Best-effort relationships: a requirement co-located in a domain that owns
    # entity E and accesses store S implies E ||--o{ S. Only emit when BOTH the
    # owning entity and the accessed store are rendered entities.
    rel_seen = set()
    for domain, _req_id, node in D.iter_requirements(graph):
        ddata = (graph.get("domains") or {}).get(domain) or {}
        own_entities = list((ddata.get("entities") or {}).keys())
        for store in node.get("data_access") or []:
            store = str(store)
            if store not in rendered:
                continue
            for owner in own_entities:
                if owner == store or owner not in rendered:
                    continue
                # Dedupe on the UNORDERED pair so A<->B is drawn once, not twice.
                pair = tuple(sorted((owner, store)))
                if pair in rel_seen:
                    continue
                rel_seen.add(pair)
                lines.append('  {0} ||--o{{ {1} : accesses'.format(
                    rendered[owner], rendered[store]))
    return "\n".join(lines), source_label


def render_sequence(domain, graph, blueprint):
    """One sequenceDiagram for a domain: actor -> controller -> service ->
    repository -> entity, derived from the blueprint component_type chain.
    Falls back to req -> legacy_components when there is no blueprint.

    Returns (mermaid_text, degraded_bool).
    """
    lines = ["sequenceDiagram",
             "  %% Interaction within domain: {0}".format(domain),
             "  actor User"]
    bdoms = _blueprint_domains(blueprint)
    bd = bdoms.get(domain) if isinstance(bdoms, dict) else None

    if bd and isinstance(bd.get("components"), dict) and bd["components"]:
        comps = bd["components"]
        # Order components along the call chain by component_type rank.
        ordered = sorted(
            comps.items(),
            key=lambda kv: (_COMPONENT_RANK.get(_component_type(kv[1]), 9), kv[0]),
        )
        participants = []
        for req_id, c in ordered:
            cls = (c or {}).get("class_name") or req_id
            pid = D.mermaid_id("p_" + str(cls))
            participants.append((pid, cls, _component_type(c or {})))
        # Declare participants up front (stable, valid Mermaid).
        for pid, cls, _t in participants:
            lines.append("  participant {0} as {1}".format(pid, D.md_escape(cls)))
        # Chain the calls down the tiers, recording each callee's actual caller
        # so the activation unwind returns to the right participant.
        callers = []  # caller for participants[i]
        prev = "User"
        for pid, _cls, ctype in participants:
            lines.append("  {0}->>+{1}: {2}()".format(prev, pid, D.md_escape(ctype)))
            callers.append(prev)
            prev = pid
        # Unwind responses in reverse, each back to its recorded caller.
        for i in range(len(participants) - 1, -1, -1):
            pid = participants[i][0]
            lines.append("  {0}-->>-{1}: result".format(pid, callers[i]))
        return "\n".join(lines), False

    # Fallback: req -> its legacy_components (no blueprint component chain).
    lines.append("  %% NOTE: blueprint absent — sequence derived from requirement -> legacy_components")
    reqs = ((graph.get("domains") or {}).get(domain) or {}).get("requirements") or {}
    if isinstance(reqs, dict) and reqs:
        # Use the first requirement as the representative interaction.
        for req_id in sorted(reqs):
            node = reqs[req_id] or {}
            rpid = D.mermaid_id("p_" + str(req_id))
            lines.append("  participant {0} as {1}".format(rpid, D.md_escape(req_id)))
            lines.append("  User->>+{0}: invoke".format(rpid))
            for lc in node.get("legacy_components") or []:
                lpid = D.mermaid_id("p_" + str(lc))
                lines.append("  participant {0} as {1}".format(lpid, D.md_escape(lc)))
                lines.append("  {0}->>{1}: delegate".format(rpid, lpid))
            lines.append("  {0}-->>-User: result".format(rpid))
            break
    else:
        lines.append("  User->>+System: invoke")
        lines.append("  System-->>-User: result")
    return "\n".join(lines), True


def render_deployment(config):
    """Deployment topology from config.deployment_target + target_stack:
    client -> service[target_stack] -> datastore, annotating the platform.
    """
    stack = config.get("target_stack") or "service"
    deploy = config.get("deployment_target") or "unspecified platform"
    project = config.get("project_name") or "service"

    client = D.mermaid_id("client")
    svc = D.mermaid_id("svc_" + str(project))
    store = D.mermaid_id("datastore")
    plat = D.mermaid_id("platform")

    lines = ["flowchart LR",
             "  %% Deployment topology (client -> service -> datastore)",
             '  {0}(["Client"])'.format(client),
             '  subgraph {0}["Deployment platform: {1}"]'.format(plat, D.md_escape(deploy)),
             '    {0}["{1} service<br/>({2})"]'.format(svc, D.md_escape(project), D.md_escape(stack)),
             '    {0}[("Datastore")]'.format(store),
             "  end",
             "  {0} -- HTTPS --> {1}".format(client, svc),
             "  {0} -- query --> {1}".format(svc, store)]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_all(graph, blueprint, config):
    """Render every diagram. Returns (files, notes):

      files: ordered list of (relname, caption, mermaid_text)
      notes: list of degradation strings to surface in the README + stdout.
    """
    files = []
    notes = []

    files.append(("diagrams/context.mmd",
                  "C4 L1 — system context: the target system and its external "
                  "actors/systems (legacy source apps + deployment platform).",
                  render_context(graph, config)))

    containers, c_degraded = render_containers(graph, blueprint)
    files.append(("diagrams/containers.mmd",
                  "C4 L2 — containers: each domain as a subgraph with its "
                  "components (class + type) and their dependencies.",
                  containers))
    if c_degraded:
        notes.append("blueprint not yet produced — `containers.mmd` is rendered at "
                     "the requirement level (one node per requirement), not the "
                     "component level.")

    files.append(("diagrams/domain-deps.mmd",
                  "Requirement dependency graph (req_id -> req_id), grouped by "
                  "domain.",
                  render_domain_deps(graph)))

    erd, erd_source = render_erd(graph, blueprint)
    files.append(("diagrams/erd.mmd",
                  "Entity-relationship diagram derived from {0}.".format(erd_source),
                  erd))
    if erd_source == "requirements_graph.json" and _blueprint_domains(blueprint):
        pass  # blueprint present but no entities — unlikely; no note needed.

    # Per-domain sequence diagrams for the top N domains by requirement count.
    seq_degraded_any = False
    for domain, count in _domains_with_req_counts(graph)[:TOP_SEQUENCE_DOMAINS]:
        if count == 0:
            continue
        seq, s_degraded = render_sequence(domain, graph, blueprint)
        seq_degraded_any = seq_degraded_any or s_degraded
        files.append((
            "diagrams/sequence-{0}.mmd".format(D.mermaid_id(domain)),
            "Interaction sequence for the **{0}** domain "
            "(actor -> controller -> service -> repository).".format(domain),
            seq,
        ))
    if seq_degraded_any:
        notes.append("blueprint not yet produced — sequence diagrams are derived "
                     "from each requirement's `legacy_components`, not the "
                     "controller/service/repository component chain.")

    files.append(("diagrams/deployment.mmd",
                  "Deployment topology: client -> service (target stack) -> "
                  "datastore, on the configured deployment platform.",
                  render_deployment(config)))

    return files, notes


def render_readme(files, notes, graph, blueprint, config):
    """The index: links each .mmd with a one-line caption AND embeds it as a
    fenced ```mermaid``` block so it renders in any Markdown viewer.
    """
    project = config.get("project_name") or "the target system"
    n_domains = len(graph.get("domains") or {})
    has_bp = bool(_blueprint_domains(blueprint))

    md = []
    md.append("# Architecture diagrams — {0}".format(D.md_escape(project)))
    md.append("")
    md.append("> Mermaid diagram set rendered by the anti-legacy `diagrams` "
              "deliverable from the requirements graph"
              + (" + blueprint" if has_bp else "")
              + ". Re-render with `python3 .anti-legacy/run.py diagrams` after the "
                "source artifacts change — do not hand-edit these files.")
    md.append("")
    md.append("Source: {0} domain(s); blueprint {1}.".format(
        n_domains, "present" if has_bp else "**not yet produced**"))
    md.append("")

    if notes:
        md.append("## Degraded diagrams")
        md.append("")
        for note in notes:
            md.append("- {0}".format(note))
        md.append("")

    md.append("## Index")
    md.append("")
    for relname, caption, _text in files:
        base = os.path.basename(relname)
        md.append("- [`{0}`]({0}) — {1}".format(base, caption))
    md.append("")

    # Embedded blocks (so the index renders standalone in any Markdown viewer).
    for relname, caption, text in files:
        base = os.path.basename(relname)
        md.append("## {0}".format(base))
        md.append("")
        md.append(caption)
        md.append("")
        md.append("```mermaid")
        md.append(text)
        md.append("```")
        md.append("")

    return "\n".join(md)


def generate(register=True):
    """Load sources, render all diagrams + the index, optionally register.

    Returns (written_paths, index_path, notes). Raises ValueError when the
    requirements graph is absent or carries no domains (nothing to draw).
    """
    graph = D.load_requirements_graph()
    if not graph or not (graph.get("domains") or {}):
        raise ValueError(
            "No requirements graph with domains found at "
            "{0}. Run the pipeline through graph-translator first (the diagram set "
            "is rendered once the graph is ready).".format(D.P_REQUIREMENTS))

    blueprint = D.load_blueprint()
    config = D.load_config()

    files, notes = build_all(graph, blueprint, config)

    written = []
    for relname, _caption, text in files:
        written.append(D.write_deliverable(relname, text))

    index_md = render_readme(files, notes, graph, blueprint, config)
    index_path = D.write_deliverable("diagrams/README.md", index_md)

    # Done-gate: graph exists + >=1 domain (checked above); index non-empty.
    if not index_md.strip():
        raise ValueError("Diagram index rendered empty — aborting before register.")

    if register:
        D.register_deliverable(
            ARTIFACT_ID, index_path, PRODUCED_BY,
            fmt="markdown", status="final", depends_on=DEPENDS_ON,
        )

    return written, index_path, notes


def main():
    parser = argparse.ArgumentParser(
        prog="diagrams",
        description="Render a Mermaid architecture diagram set (context, "
                    "containers, domain-deps, ERD, per-domain sequence, "
                    "deployment) + an index README from the requirements graph "
                    "and blueprint. Registers the index as a manifest artifact.",
    )
    parser.add_argument("--no-register", action="store_true",
                        help="Write the diagrams but do not register them in the manifest.")
    parser.add_argument("--force", action="store_true",
                        help="override a precheck BLOCK and render anyway (loud warning)")
    args = parser.parse_args()
    D.require_ready("deliverables", force=args.force)

    try:
        written, index_path, notes = generate(register=not args.no_register)
    except ValueError as e:
        print("Error: {0}".format(e), file=sys.stderr)
        sys.exit(1)

    print("Mermaid diagram set written under {0}:".format(
        os.path.join(D.deliverables_dir(create=False), "diagrams")))
    for path in written:
        print("  {0}".format(path))
    print("  {0}  (index, registered as '{1}')".format(
        index_path, ARTIFACT_ID if not args.no_register else ARTIFACT_ID + " [not registered]"))
    for note in notes:
        print("  NOTE: {0}".format(note))


if __name__ == "__main__":
    main()
