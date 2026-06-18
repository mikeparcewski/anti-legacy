#!/usr/bin/env python3
"""anti-legacy:skill-forge — generate target-state-specific BUILD skills.

A meta-skill (a skill that writes skills). Once the blueprint exists, this renders one
`build-<domain>` SKILL.md per target domain into `.anti-legacy/generated-skills/`, baking the
blueprint component specs + each requirement's business rules / validations / error paths +
entity parity into reusable, target-tailored build instructions. A CLI/IDE agent then FOLLOWS a
generated skill to build that domain natively + consistently — instead of re-assembling a
micro-context from scratch each time.

The generated artifacts are SKILL.md (instructions the agent reads), NOT scripts: building the
target is an agent task, so the forge's product is agent-followable skills. They are regenerated
(idempotent) whenever the blueprint changes, and they cite git-brain patterns so they improve as
the brain learns.

Deterministic: pure projection of blueprint.json + requirements_graph.json + config.json (no LLM).
Reuses antilegacy_core.deliverables loaders (cwd-anchored). Cross-platform stdlib + os.path.
"""
import argparse
import os
import sys

from antilegacy_core import deliverables as D

GENERATED_DIRNAME = os.path.join(".anti-legacy", "generated-skills")


def _ws(*parts):
    return os.path.join(os.getcwd(), *parts)


def _slug(text):
    out = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(text or "").strip().lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "domain"


def _requirements_by_id(graph):
    """{req_id: (domain, node)} across the whole requirements graph."""
    idx = {}
    for domain, req_id, node in D.iter_requirements(graph):
        idx[req_id] = (domain, node)
    return idx


def _ordered_component_ids(components, blueprint):
    """req_ids of `components`, sorted into the blueprint's declared build order.

    The blueprint's authoritative ordering signal is its **top-level** `build_order`
    list of req_ids (see `anti-legacy:blueprint` SKILL.md and `document.py`, which read
    it the same way). A component is keyed by its index in that list; ties / req_ids
    absent from `build_order` fall back to a per-component integer `build_order` field
    if present, else preserve insertion order. The sort is stable, so components that
    share an ordering key keep their declaration sequence.

    This is what makes the emitted build steps dependency-sorted rather than dict-insertion
    order — the dependency-of comes before the depends-on, because `build_order` lists it first.
    """
    global_order = (blueprint or {}).get("build_order")
    pos = {}
    if isinstance(global_order, list):
        for i, rid in enumerate(global_order):
            pos.setdefault(rid, i)
    sentinel = len(pos)  # any req_id not named in build_order sorts after every named one

    def key(item):
        i, rid = item
        comp = components.get(rid) or {}
        local = comp.get("build_order")
        local = local if isinstance(local, int) else None
        # 1) position in the blueprint's global build_order (authoritative);
        # 2) per-component build_order int as a fallback; a component WITHOUT a local
        #    order must sort AFTER any component that HAS one, regardless of magnitude —
        #    so use float('inf'), not `sentinel` (a local order >= sentinel, or sentinel==0
        #    when there is no global build_order, otherwise sorts incorrectly);
        # 3) insertion index, to keep the sort stable for unordered components.
        return (pos.get(rid, sentinel), local if local is not None else float("inf"), i)

    return [rid for _, rid in sorted(enumerate(components.keys()), key=key)]


def _md_list(items):
    return "\n".join("- %s" % i for i in items) if items else "- (none)"


def _component_section(req_id, comp, node):
    """Render one component's build spec from the blueprint component + the graph requirement."""
    L = []
    cls = comp.get("class_name") or req_id
    ctype = comp.get("component_type") or comp.get("type") or "component"
    L.append("### `%s` — %s  (req `%s`)" % (cls, ctype, req_id))
    if comp.get("target_file"):
        L.append("- **Target file:** `%s`" % comp["target_file"])
    api = comp.get("api") or {}
    if api.get("method") or api.get("path"):
        L.append("- **API:** `%s %s`" % (api.get("method", "?"), api.get("path", "?")))
    methods = comp.get("methods") or []
    if methods:
        L.append("- **Methods:** " + ", ".join(
            "`%s`" % (m.get("signature") or m.get("name") or "?") for m in methods))
    deps = comp.get("dependencies") or []
    if deps:
        L.append("- **Depends on:** " + ", ".join("`%s`" % d for d in deps))
    legacy = (node or {}).get("legacy_components") or []
    if legacy:
        L.append("- **Legacy provenance (§2 trace):** " + ", ".join("`%s`" % x for x in legacy))
    L.append("")
    rules = (node or {}).get("business_rules") or []
    L.append("**Business rules** — annotate each in code with `@ImplementsRule(\"<id>\")`:")
    L.append(_md_list("`%s` — %s" % (r.get("id", "RULE-?"), (r.get("statement") or "").strip())
                      for r in rules) if rules
             else "- ⚠ no business rules on this requirement — do NOT invent behavior; flag for review.")
    vals = (node or {}).get("validations") or []
    if vals:
        L.append("")
        L.append("**Validations** (reject-on-violation):")
        L.append(_md_list("`%s` — %s%s" % (
            v.get("id", "VAL-?"), (v.get("statement") or "").strip(),
            (" → field `%s`" % v["field"]) if v.get("field") else "") for v in vals))
    errs = (node or {}).get("error_paths") or []
    if errs:
        L.append("")
        L.append("**Error paths** (must be handled, not swallowed):")
        L.append(_md_list("`%s` — %s%s" % (
            e.get("id", "ERR-?"), (e.get("statement") or "").strip(),
            (" (code `%s`)" % e["code"]) if e.get("code") else "") for e in errs))
    return "\n".join(L)


def _entities_section(domain_bp):
    ents = (domain_bp or {}).get("entities") or {}
    if not ents:
        return ""
    L = ["## Data model (preserve precision — COMP-3/DECIMAL parity is silent & catastrophic)"]
    for name, ent in ents.items():
        L.append("### `%s`" % (ent.get("table_name") or name))
        for col in (ent.get("columns") or []):
            src = (" ← legacy `%s`" % col["source_type"]) if col.get("source_type") else ""
            pk = " **(PK)**" if col.get("pk") else ""
            L.append("- `%s` : `%s`%s%s" % (col.get("name", "?"), col.get("type", "?"), src, pk))
    return "\n".join(L)


def _render_build_skill(project, stack, style, domain, domain_bp, reqs_by_id, blueprint=None):
    components = (domain_bp or {}).get("components") or {}
    order = _ordered_component_ids(components, blueprint)
    pkg = (domain_bp or {}).get("package") or ""
    name = "anti-legacy:build-%s" % _slug(domain)
    head = [
        "---",
        'name: "%s"' % name,
        "description: >",
        "  Build the %s capability of %s in %s (generated by anti-legacy:skill-forge from the"
        " blueprint + requirements graph). Use when: \"build %s\", \"implement the %s domain\"."
        % (domain, project, stack, domain, domain),
        "---",
        "",
        "# Build: %s  ·  %s  (generated)" % (domain, stack),
        "",
        "> Generated by `anti-legacy:skill-forge` from `blueprint.json` + `requirements_graph.json`."
        " Regenerate after any blueprint change — do not hand-edit. This skill is the target-tailored"
        " build contract for the **%s** capability." % domain,
        "",
        "## Target",
        "- **Stack:** %s%s" % (stack, ("  ·  **style:** " + style) if style else ""),
        "- **Package/module:** `%s`" % (pkg or "(see blueprint)"),
        "",
        "## Conventions (read before writing code)",
        "1. Query the brain for this stack's patterns first:"
        " `python3 .anti-legacy/run.py git_brain search --query \"%s %s patterns\" --category patterns`"
        % (stack, style or ""),
        "2. Idiomatic %s — NO legacy constructs carried over." % stack,
        "3. Annotate every business rule / validation / error path with `@ImplementsRule(\"<id>\")`"
        " (or your stack's equivalent) — round-trip rule coverage must reach 1.0.",
        "4. No stubs / single-return placeholders; real logic only.",
        "5. Preserve numeric precision on money/rate/percent/count (see the data model).",
        "",
        "## Build order (dependency-sorted)",
    ]
    # `order` is the blueprint build_order projection (see _ordered_component_ids),
    # not dict-insertion order — the dependency comes before its dependent.
    rows = []
    for i, req_id in enumerate(order, 1):
        comp = components[req_id]
        rows.append("%d. `%s` (%s) → `%s`" % (
            i, comp.get("class_name") or req_id,
            comp.get("component_type") or comp.get("type") or "component",
            comp.get("target_file") or "?"))
    head.append("\n".join(rows) if rows else "- (no components in blueprint for this domain)")
    head.append("")
    head.append("## Components")
    out = ["\n".join(head)]
    for req_id in order:  # same build-order projection as the build-order list above
        comp = components[req_id]
        _dom, node = reqs_by_id.get(req_id, (None, {}))
        out.append(_component_section(req_id, comp, node))
        out.append("")
    ents = _entities_section(domain_bp)
    if ents:
        out.append(ents)
        out.append("")
    out.append("## Done")
    out.append("- every business rule/validation/error path annotated + implemented;"
               " rule coverage 1.0; numeric parity preserved; no stubs.")
    out.append("- write tests for each rule/validation/error path; run the target build.")
    return name, "\n".join(out)


def generate():
    blueprint = D.load_blueprint()
    graph = D.load_requirements_graph()
    config = D.load_config()
    if not blueprint or not (blueprint.get("domains")):
        sys.stderr.write("skill-forge: no blueprint.json with domains — run anti-legacy:blueprint "
                         "first (the forge generates build skills FROM the target architecture).\n")
        sys.exit(1)
    # Resolve project_name: config.project_name → config.project.name → blueprint.project → fallback.
    # (The old nested ternary repeated config.get("project_name") in its else branch — a dead
    #  duplicate of the first operand. The isinstance guard stays: it keeps a non-dict `project`
    #  value, e.g. a bare string, from blowing up on `.get("name")`.)
    proj = config.get("project")
    nested_name = proj.get("name") if isinstance(proj, dict) else None
    project = (config.get("project_name") or nested_name
               or blueprint.get("project") or "the target system")
    stack = blueprint.get("target_stack") or config.get("target_stack") or "the target stack"
    style = blueprint.get("style") or ""
    reqs_by_id = _requirements_by_id(graph)

    base = _ws(GENERATED_DIRNAME)
    os.makedirs(base, exist_ok=True)
    written = []
    for domain, domain_bp in blueprint["domains"].items():
        name, content = _render_build_skill(project, stack, style, domain, domain_bp, reqs_by_id,
                                             blueprint=blueprint)
        d = os.path.join(base, "build-%s" % _slug(domain))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "SKILL.md")
        if not content.endswith("\n"):
            content += "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append((domain, name, path))

    # index
    idx = ["# Generated build skills (anti-legacy:skill-forge)", "",
           "Target-tailored build skills for %s (%s). A CLI/IDE agent FOLLOWS the relevant"
           " `build-<domain>` skill to build that capability. Regenerate after a blueprint change."
           % (project, stack), "",
           D.md_table(["Domain", "Skill", "Path"],
                      [[dom, "`%s`" % nm, "`%s`" % os.path.relpath(p, os.getcwd())]
                       for dom, nm, p in written])]
    index_path = os.path.join(base, "README.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(idx) + "\n")
    return written, index_path


def main():
    argparse.ArgumentParser(description="Generate target-state build skills from the blueprint.").parse_args()
    written, index_path = generate()
    print("skill-forge: generated %d build skill(s) → %s" % (len(written),
          os.path.relpath(os.path.dirname(index_path), os.getcwd())))
    for dom, name, path in written:
        print("  %-20s %s" % (dom, os.path.relpath(path, os.getcwd())))


if __name__ == "__main__":
    main()
