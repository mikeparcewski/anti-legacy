#!/usr/bin/env python3
"""antilegacy_core.capability_graph — treat an AGENTIC codebase's markdown as CODE.

For a normal codebase the behavior lives in source files wicked-estate indexes. For an *agentic*
codebase the behavior also lives in the **skill / agent / prompt markdown** — those files ARE the
program, and wicked-estate (content/tree-sitter driven) does not index markdown. This module is the
md-as-code seam: it classifies each `.md` (skill vs reference vs doc), extracts the agentic
capabilities, and joins them with the pipeline's phase/gate model into a **capability graph** — the
domain graph of an agentic codebase.

This is a JSON/CLI artifact only — there is no static HTML page. (A gh-pages feature page once
rendered this graph; it used absolute hrefs that broke under a project Pages subpath and was
removed in PR #14. The CLI/JSON artifact is revived here on its own; the page is not — see #23.)

Classification (from the agentic-codebase discriminators):
  * skill/agent  : `skills/<name>/SKILL.md` with frontmatter `name:` + `description:` → BEHAVIOR.
  * reference    : `skills/<name>/reference[s]/*.md` (no `name:` frontmatter) → supporting doc.
  * doc          : top-level `*.md` (README, AGENTS.md, HOW_*.md) → governance/onboarding.

CLI:  python3 .anti-legacy/run.py capability_graph [--root <dir>] [--json]
        prints the capability-graph JSON (the default and only output — no page).

Pure standard library (no PyYAML — a minimal frontmatter parser). Cross-platform os.path.
"""
import argparse
import glob
import json
import os
import re
import sys

# Pipeline phase/gate truth lives in the manifest module.
try:
    from antilegacy_core import manifest as _mf
except Exception:  # pragma: no cover - manifest is a sibling; importable under run.py
    _mf = None

_TRIGGER_RE = re.compile(r'"([^"]+)"')


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def parse_frontmatter(text):
    """Minimal YAML-frontmatter parse — top-level scalar keys only (name, description)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm, key, val = {}, None, []
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):(.*)$", line)
        if m:
            if key is not None:
                fm[key] = " ".join(v for v in val if v).strip().strip('"')
            key, val = m.group(1), [m.group(2).strip().lstrip(">|").strip()]
        elif key is not None and (line.startswith(" ") or line.startswith("\t")):
            val.append(line.strip())
    if key is not None:
        fm[key] = " ".join(v for v in val if v).strip().strip('"')
    return fm


def _triggers(description):
    """The quoted 'Use when:' phrases in a skill description."""
    tail = description.split("Use when", 1)[-1] if "Use when" in description else ""
    return _TRIGGER_RE.findall(tail)


def scan_capabilities(root):
    """Return capability nodes from skills/<name>/SKILL.md (the agentic behavior units)."""
    caps = []
    for skill_md in sorted(glob.glob(os.path.join(root, "skills", "*", "SKILL.md"))):
        fm = parse_frontmatter(_read(skill_md))
        name = (fm.get("name") or "").strip()
        if not name:
            continue  # not a skill (no agentic invocation id)
        desc = (fm.get("description") or "").strip()
        # the human summary is the description up to "Use when"
        summary = desc.split("Use when", 1)[0].strip().rstrip(".").strip()
        caps.append({
            "name": name,
            "dir": os.path.basename(os.path.dirname(skill_md)),
            "summary": summary,
            "triggers": _triggers(desc),
            "kind": "skill",
            "path": os.path.relpath(skill_md, root),
        })
    return caps


def classify_markdown(root):
    """Count the .md population by role (behavior vs reference vs doc) — the md-as-code census."""
    skills = len(glob.glob(os.path.join(root, "skills", "*", "SKILL.md")))
    refs = len(glob.glob(os.path.join(root, "skills", "*", "reference*", "*.md"))) + \
        len(glob.glob(os.path.join(root, "skills", "*", "references", "*.md")))
    docs = len([p for p in glob.glob(os.path.join(root, "*.md"))])
    return {"skill_agents": skills, "reference_docs": refs, "project_docs": docs}


def pipeline_model():
    """Phase sequence + gates from the manifest (the workflow spine)."""
    if _mf is None:
        return {"phases": [], "gates": [], "gate_producing": {}, "advance_preconditions": {}}
    phases = list(getattr(_mf, "PHASE_SEQUENCE", None) or getattr(_mf, "PHASE_ENUM", []) or [])
    gate_producing = {g: list(v) if isinstance(v, (list, tuple)) else v
                      for g, v in dict(getattr(_mf, "GATE_PRODUCING_PHASE", {})).items()}
    preconds = {p: list(v) for p, v in dict(getattr(_mf, "GATE_PHASE_PRECONDITIONS", {})).items()}
    gates = sorted(set(list(gate_producing.keys()) +
                       [g for v in preconds.values() for g in v]))
    return {"phases": phases, "gates": gates,
            "gate_producing": gate_producing, "advance_preconditions": preconds}


def build_graph(root):
    return {
        "project": "anti-legacy",
        "root": os.path.abspath(root),
        "markdown_census": classify_markdown(root),
        "capabilities": scan_capabilities(root),
        "pipeline": pipeline_model(),
    }


def main():
    ap = argparse.ArgumentParser(description="Capability graph of an agentic codebase (md-as-code).")
    ap.add_argument("--root", default=os.getcwd(), help="repo root to introspect (default: cwd)")
    ap.add_argument("--json", action="store_true",
                    help="print the capability graph as JSON (default and only output)")
    args = ap.parse_args()

    graph = build_graph(args.root)
    if not graph["capabilities"]:
        sys.stderr.write("capability_graph: no skills/<name>/SKILL.md found under %s\n" % args.root)
        sys.exit(1)
    json.dump(graph, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
