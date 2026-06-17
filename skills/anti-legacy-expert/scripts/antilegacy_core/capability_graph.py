#!/usr/bin/env python3
"""antilegacy_core.capability_graph — treat an AGENTIC codebase's markdown as CODE.

For a normal codebase the behavior lives in source files wicked-estate indexes. For an *agentic*
codebase the behavior also lives in the **skill / agent / prompt markdown** — those files ARE the
program, and wicked-estate (content/tree-sitter driven) does not index markdown. This module is the
md-as-code seam: it classifies each `.md` (skill vs reference vs doc), extracts the agentic
capabilities, and joins them with the pipeline's phase/gate model + deliverables into a
**capability graph** — the domain graph of an agentic codebase. It then renders that graph as a
static feature page for the gh site.

Classification (from the agentic-codebase discriminators):
  * skill/agent  : `skills/<name>/SKILL.md` with frontmatter `name:` + `description:` → BEHAVIOR.
  * reference    : `skills/<name>/reference[s]/*.md` (no `name:` frontmatter) → supporting doc.
  * doc          : top-level `*.md` (README, AGENTS.md, HOW_*.md) → governance/onboarding.

CLI:  python3 .anti-legacy/run.py capability_graph [--root <dir>] [--json | --site <out.html>]
        default: print the capability-graph JSON. --site: render a static feature page.

Pure standard library (no PyYAML — a minimal frontmatter parser). Cross-platform os.path.
"""
import argparse
import glob
import html
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


# --------------------------------------------------------------------------- #
# Static site rendering (reuses site/assets/style.css classes).
# --------------------------------------------------------------------------- #
_HEADER = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="dark">
  <title>Features &amp; capabilities — anti-legacy</title>
  <meta name="description" content="The anti-legacy plugin's own capabilities, pipeline phases, gates, and deliverables — generated from its capability graph (skills are code).">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,400;1,9..144,300&family=Inter+Tight:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header">
  <div class="container site-header__inner">
    <a class="brand" href="/" aria-label="anti-legacy — home">
      <svg class="brand__mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="2.5" y="6" width="8" height="6" rx="1.6" fill="none" stroke="var(--amber-core)" stroke-width="1.4"/>
        <rect x="13.5" y="12" width="8" height="6" rx="1.6" fill="none" stroke="var(--cyan-core)" stroke-width="1.4"/>
        <path d="M10.5 9.5C13 9.5 11.5 15 13.5 15" fill="none" stroke="var(--fog-500)" stroke-width="1.1" stroke-dasharray="1.6 2.4"/>
        <circle cx="6.5" cy="9" r="1.1" fill="var(--amber-bright)"/>
        <circle cx="17.5" cy="15" r="1.1" fill="var(--cyan-bright)"/>
      </svg>
      <span>anti-legacy</span>
    </a>
    <nav class="nav" aria-label="Primary">
      <a href="/" class="nav-link-extra">Home</a>
      <a href="/#how-it-works" class="nav-link-extra">How it works</a>
      <a href="/#gates" class="nav-link-extra">Gates</a>
      <a href="https://github.com/mikeparcewski/anti-legacy" class="is-cta" aria-label="GitHub">GitHub</a>
    </nav>
  </div>
</header>
<main id="main">
"""

_FOOTER = """</main>
</body>
</html>
"""


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def render_site_html(graph):
    c = graph["markdown_census"]
    caps = graph["capabilities"]
    pipe = graph["pipeline"]
    out = [_HEADER]
    # 1 — what it does
    out.append('<section class="section section--panel"><div class="container">')
    out.append('<p class="eyebrow">anti-legacy · the domain graph of this codebase</p>')
    out.append('<h1 class="display">What it does, how to run it, how agents read the graph</h1>')
    out.append('<p class="lead">anti-legacy indexes one or more legacy codebases into a '
               '<strong>wicked-estate</strong> code graph, annotates every behavior-bearing node with '
               'its business rule (resolved or risk-flagged, to a provable coverage terminal), runs gated '
               'human review, then builds the target system against those requirements — not against the '
               'legacy code. Gates need human sign-off; everything between runs autonomously.</p>')
    out.append('</div></section>')

    # 2 — how to execute it
    out.append('<section class="section"><div class="container">')
    out.append('<h2 class="h2">How to execute it</h2>')
    out.append('<p>Point the orchestrator at a source + target — it sequences every phase and enforces '
               'the gates:</p>')
    out.append('<pre class="mono"><code>"Run the anti-legacy pipeline on ./legacy/cobol targeting java"</code></pre>')
    if pipe.get("phases"):
        out.append('<p class="mono-micro">%d phases: %s</p>'
                   % (len(pipe["phases"]), " &rarr; ".join(_esc(p) for p in pipe["phases"])))
    if pipe.get("gates"):
        out.append('<p class="mono-micro">%d gates: %s</p>'
                   % (len(pipe["gates"]), ", ".join(_esc(g) for g in pipe["gates"])))
    out.append('<p>Or run a single phase as a skill (<code>anti-legacy:survey</code>), or a script via '
               'the dispatcher <code>python3 .anti-legacy/run.py &lt;stem&gt;</code>.</p>')
    out.append('</div></section>')

    # 3 — interactive filter / refine over the domain graph (how agents use it)
    out.append('<section class="section section--ink"><div class="container">')
    out.append('<h2 class="h2">Explore the domain graph</h2>')
    out.append('<p class="lead">These %d capabilities are the domain graph of anti-legacy\'s OWN code: '
               'in an agentic codebase the skills <em>are</em> the program (the %d skill-agents below; the '
               '%d reference + %d project docs are not behavior). Filter and refine the way an agent does '
               'when it queries the graph for the capability that matches an intent — type an intent, a '
               'name, or a trigger:</p>'
               % (len(caps), c["skill_agents"], c["reference_docs"], c["project_docs"]))
    out.append('<input id="capq" type="search" autocomplete="off" aria-label="Filter capabilities" '
               'placeholder="e.g. extract rules · gate · deliverable · build the target" '
               'style="width:100%;max-width:620px;padding:.7rem 1rem;margin:.5rem 0;font:inherit;'
               'color:var(--paper-50,#eee);background:var(--ink-800,#181818);'
               'border:1px solid var(--fog-500,#666);border-radius:10px">')
    out.append('<p class="mono-micro"><span id="capcount">%d</span> / %d capabilities</p>'
               % (len(caps), len(caps)))
    out.append('<div id="caplist" class="grid">')
    for cap in caps:
        hay = " ".join([cap["name"], cap["dir"], cap["summary"]] + list(cap["triggers"])).lower()
        trig = "".join('<span class="pill">%s</span> ' % _esc(t) for t in cap["triggers"][:4])
        out.append('<div class="col-6 cap-item" data-hay="%s"><div class="card">'
                   '<p class="mono-micro">%s</p><h3 class="h3">%s</h3><p>%s</p><p>%s</p></div></div>'
                   % (_esc(hay), _esc(cap["name"]), _esc(cap["dir"]), _esc(cap["summary"]), trig))
    out.append('</div>')
    out.append('<p id="capnone" class="lead" style="display:none">No capability matches — refine the query.</p>')
    out.append('</div></section>')
    out.append(r'''<script>
(function(){
  var q=document.getElementById('capq'),
      items=[].slice.call(document.querySelectorAll('.cap-item')),
      count=document.getElementById('capcount'),
      none=document.getElementById('capnone');
  if(!q)return;
  function refine(){
    var terms=q.value.toLowerCase().split(/\s+/).filter(Boolean), shown=0;
    items.forEach(function(el){
      var hay=el.getAttribute('data-hay')||'';
      var ok=terms.every(function(t){return hay.indexOf(t)>=0;});
      el.style.display=ok?'':'none'; if(ok)shown++;
    });
    count.textContent=shown; if(none)none.style.display=shown?'none':'';
  }
  q.addEventListener('input',refine);
})();
</script>''')
    out.append(_FOOTER)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Capability graph of an agentic codebase (md-as-code).")
    ap.add_argument("--root", default=os.getcwd(), help="repo root to introspect (default: cwd)")
    ap.add_argument("--site", default=None, help="render a static feature page to this HTML path")
    ap.add_argument("--json", action="store_true", help="print the capability graph as JSON (default)")
    args = ap.parse_args()

    graph = build_graph(args.root)
    if not graph["capabilities"]:
        sys.stderr.write("capability_graph: no skills/<name>/SKILL.md found under %s\n" % args.root)
        sys.exit(1)
    if args.site:
        parent = os.path.dirname(os.path.abspath(args.site))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.site, "w", encoding="utf-8") as f:
            f.write(render_site_html(graph))
        print("capability_graph: wrote %s (%d capabilities)" % (args.site, len(graph["capabilities"])))
    else:
        json.dump(graph, sys.stdout, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
