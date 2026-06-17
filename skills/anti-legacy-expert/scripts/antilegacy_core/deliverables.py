#!/usr/bin/env python3
"""antilegacy_core.deliverables — shared loaders + registrar for the deliverable skills.

The deliverable skills (anti-legacy:prd, :diagrams, :test-plan, :test-scripts,
:migration-plan, :risk-log, :decisions-log, :evidence-log) all RENDER human-facing
artifacts FROM the pipeline's structured data. This module is the single place
that:

  * locates + loads each pipeline data source (requirements graph, blueprint,
    contracts, coverage, annotations, manifest, audit, config), degrading
    gracefully when a source is absent (a deliverable renders what EXISTS and
    names what is MISSING — it never crashes on a partial workspace);
  * owns the `.anti-legacy/deliverables/` output convention;
  * registers a produced deliverable as a manifest artifact, reusing
    antilegacy_core.manifest's own helpers so there is ONE definition of the
    artifact-row shape, checksum, and audit append.

Workspace state is anchored on the CURRENT WORKING DIRECTORY (the workspace),
never on this module's __file__ (which points at the install location). This
mirrors antilegacy_core.coverage / .domain_graph (ISS-23).

A deliverable renderer REGISTERS its artifact; it NEVER advances the phase —
phase advancement is owned by the phase skills (survey/graph-translate/...),
not by the deliverable renderers.

Pure standard library. Cross-platform (macOS / Linux / WSL / Windows): every
path is built with os.path; no shell-isms.
"""
import glob
import json
import os
import re
from datetime import datetime, timezone

DELIVERABLES_DIRNAME = "deliverables"

# Default workspace-relative locations of each pipeline data source. These match
# the paths the producing skills write to (see the recon contract).
WS = ".anti-legacy"
P_CONFIG        = os.path.join(WS, "config.json")
P_REQUIREMENTS  = os.path.join(WS, "requirements", "requirements_graph.json")
P_BLUEPRINT     = os.path.join(WS, "requirements", "blueprint.json")
P_NFRS          = os.path.join(WS, "requirements", "nfrs.md")
P_COVERAGE      = os.path.join(WS, "coverage-report.json")
P_MANIFEST      = os.path.join(WS, "manifest.json")
P_AUDIT         = os.path.join(WS, "audit.jsonl")
P_ANNOTATIONS   = os.path.join(WS, "annotations.jsonl")
P_CONTRACTS     = os.path.join(WS, "contracts")
P_TEST_STRATEGY = os.path.join(WS, "contracts", "test-strategy.md")
P_EVIDENCE      = os.path.join(WS, "evidence")


def workspace_root():
    """The workspace == the current working directory (run.py guarantees CWD)."""
    return os.getcwd()


def _abs(path):
    """Resolve a workspace-relative path against the CWD (the workspace)."""
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(workspace_root(), path)


# --------------------------------------------------------------------------- #
# Loaders — every loader degrades gracefully (absent/unreadable -> empty).
# --------------------------------------------------------------------------- #
def load_json(path, default=None):
    """Load a JSON file, returning `default` (or {}) when absent/unreadable."""
    fallback = {} if default is None else default
    p = _abs(path)
    if not p or not os.path.exists(p) or os.path.isdir(p):
        return fallback
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def load_jsonl(path):
    """Load a JSONL file into a list of dicts; tolerates blank / malformed lines."""
    p = _abs(path)
    rows = []
    if not p or not os.path.exists(p) or os.path.isdir(p):
        return rows
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return rows
    return rows


def load_config(path=P_CONFIG):                   return load_json(path)
def load_requirements_graph(path=P_REQUIREMENTS): return load_json(path)
def load_blueprint(path=P_BLUEPRINT):             return load_json(path)
def load_coverage(path=P_COVERAGE):               return load_json(path)
def load_manifest(path=P_MANIFEST):               return load_json(path)
def load_audit(path=P_AUDIT):                     return load_jsonl(path)
def load_annotations(path=P_ANNOTATIONS):         return load_jsonl(path)


def load_contracts(contracts_dir=P_CONTRACTS):
    """Return {(domain, req_id): contract_dict} for every *.contract.json found."""
    base = _abs(contracts_dir)
    out = {}
    if not base or not os.path.isdir(base):
        return out
    for hit in sorted(glob.glob(os.path.join(base, "*", "*.contract.json"))):
        domain = os.path.basename(os.path.dirname(hit))
        data = load_json(hit)
        if not isinstance(data, dict):
            continue
        req_id = data.get("req_id") or os.path.basename(hit).replace(".contract.json", "")
        out[(domain, req_id)] = data
    return out


def contract_for(contracts, req_id, domain=None):
    """Find a requirement's contract, joining on req_id — the globally-unique
    traceability key (§5) — NOT on (domain, req_id).

    load_contracts() keys by the contract *directory* basename, while the graph keys
    requirements by capability-domain name; the two can differ (case, sanitization,
    re-clustering), so a naive (domain, req_id) lookup silently misses contracts.
    `domain` (the graph domain) is only a tiebreaker for the rare cross-domain req_id
    collision. Returns {} when there is no match.
    """
    if not contracts:
        return {}
    if domain is not None and (domain, req_id) in contracts:
        return contracts[(domain, req_id)]
    matches = [(d, c) for (d, r), c in contracts.items() if r == req_id]
    if not matches:
        return {}
    if domain is not None:
        for d, c in matches:
            if d == domain or (isinstance(c, dict) and c.get("domain") == domain):
                return c
    return matches[0][1]


def evidence_files(evidence_dir=P_EVIDENCE):
    """Return abs paths of every file under .anti-legacy/evidence/ (recursive)."""
    base = _abs(evidence_dir)
    if not base or not os.path.isdir(base):
        return []
    out = []
    for root, _dirs, files in os.walk(base):
        for name in sorted(files):
            out.append(os.path.join(root, name))
    return sorted(out)


# --------------------------------------------------------------------------- #
# Iterators over the requirements graph (domains -> requirements / entities).
# --------------------------------------------------------------------------- #
def iter_requirements(graph):
    """Yield (domain, req_id, node) for every requirement node in the graph."""
    for domain, ddata in (graph.get("domains") or {}).items():
        for req_id, node in (ddata.get("requirements") or {}).items():
            yield domain, req_id, node


def iter_entities(graph):
    """Yield (domain, entity_name, entity) for every entity in the graph."""
    for domain, ddata in (graph.get("domains") or {}).items():
        for name, ent in (ddata.get("entities") or {}).items():
            yield domain, name, ent


def active_requirements(graph):
    """The build set: requirements that are not dropped and not unresolvable."""
    out = []
    for domain, req_id, node in iter_requirements(graph):
        if node.get("disposition") == "drop":
            continue
        if node.get("status") == "unresolvable":
            continue
        out.append((domain, req_id, node))
    return out


def dropped_requirements(graph):
    """Requirements explicitly dropped (disposition == 'drop') — the scope cuts."""
    return [(d, r, n) for d, r, n in iter_requirements(graph)
            if n.get("disposition") == "drop"]


def rule_confidences(node):
    """All business-rule confidences present on a requirement node (floats)."""
    out = []
    for rule in (node.get("business_rules") or []):
        c = rule.get("confidence")
        if isinstance(c, (int, float)):
            out.append(float(c))
    return out


# --------------------------------------------------------------------------- #
# Audit / manifest helpers (for the decisions-log and evidence-log renderers).
# --------------------------------------------------------------------------- #
def audit_events(audit, event):
    """Audit rows whose 'event' matches 'anti-legacy:<event>' (short or full id)."""
    full = event if event.startswith("anti-legacy:") else "anti-legacy:" + event
    return [e for e in (audit or []) if e.get("event") == full]


def manifest_artifacts(manifest):
    """The manifest's registered artifacts as {artifact_id: row}."""
    return (manifest or {}).get("artifacts") or {}


# --------------------------------------------------------------------------- #
# Output convention + in-process registration.
# --------------------------------------------------------------------------- #
def deliverables_dir(create=True):
    """Absolute path of .anti-legacy/deliverables/ (created when create=True)."""
    d = os.path.join(workspace_root(), WS, DELIVERABLES_DIRNAME)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def write_deliverable(relname, content):
    """Write `content` to .anti-legacy/deliverables/<relname>; return the abs path.

    `relname` may include subdirectories (e.g. 'diagrams/context.mmd',
    'tests/uat/billing.feature'). A trailing newline is ensured for text.
    """
    path = os.path.join(deliverables_dir(), relname)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if isinstance(content, str) and not content.endswith("\n"):
        content += "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def register_deliverable(artifact_id, abs_path, produced_by,
                         fmt="markdown", status="final", depends_on=None,
                         manifest_path=P_MANIFEST):
    """Register a produced deliverable as a manifest artifact (returns stored path).

    Reuses antilegacy_core.manifest's helpers (single source of truth for the
    artifact-row shape + checksum) and appends an 'anti-legacy:artifact-registered'
    audit event. The stored path is RELATIVE to the manifest's .anti-legacy/
    anchor (forward slashes, cross-platform stable) so `manifest check` resolves
    it even though deliverables live under .anti-legacy/deliverables/.

    No-ops (returns None) when the manifest is absent — a deliverable can be
    rendered before a full workspace exists (e.g. a dry run). NEVER advances the
    phase.
    """
    mpath = _abs(manifest_path)
    if not mpath or not os.path.exists(mpath):
        return None
    from antilegacy_core import manifest as mf  # lazy: avoid import cycle

    m = mf.load_manifest(mpath)
    anti_legacy_dir = os.path.dirname(os.path.abspath(mpath))
    rel = os.path.relpath(os.path.abspath(abs_path), anti_legacy_dir)
    stored_path = rel.replace(os.sep, "/")

    artifact = {
        "path": stored_path,
        "format": fmt,
        "produced_by": produced_by,
        "status": status,
        "produced_at": now_iso(),
        "depends_on": list(depends_on or []),
    }
    checksum = mf.file_checksum(abs_path)
    if checksum:
        artifact["checksum"] = checksum

    m.setdefault("artifacts", {})[artifact_id] = artifact
    mf.save_manifest(m, mpath)

    audit_path = os.path.join(anti_legacy_dir, "audit.jsonl")
    if os.path.isdir(anti_legacy_dir):
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "anti-legacy:artifact-registered",
                "timestamp": now_iso(),
                "details": {"artifact_id": artifact_id, "path": stored_path, "status": status},
            }) + "\n")
    return stored_path


# --------------------------------------------------------------------------- #
# Producer readiness gate (ROOT A): a producer refuses to render unless its phase
# passes precheck. Lazy-imports precheck to avoid an import cycle.
# --------------------------------------------------------------------------- #
def require_ready(phase, force=False):
    """Refuse to produce unless `phase` passes the precheck readiness gate (ROOT A).

    On block: prints the blockers to stderr and sys.exit(1); with force=True, downgrades
    to a loud warning and proceeds. The Tier-A "snapshot" deliverables (prd, diagrams,
    test-plan, test-scripts, migration-plan) call this; the living logs (risk-log,
    decisions-log, evidence-log) intentionally do NOT — they must run on an incomplete
    pipeline in order to SURFACE its gaps.
    """
    from antilegacy_core import precheck  # lazy: precheck imports nothing from this module
    precheck.require_ready(phase, force=force)


# --------------------------------------------------------------------------- #
# Small render utilities shared across the Markdown / Mermaid renderers.
# --------------------------------------------------------------------------- #
_MERMAID_BAD = re.compile(r"[^A-Za-z0-9_]")


def mermaid_id(text):
    """A safe Mermaid node id: alnum/underscore, never leading-digit, never empty."""
    s = _MERMAID_BAD.sub("_", str(text or "").strip())
    if not s:
        s = "n"
    if s[0].isdigit():
        s = "n_" + s
    return s


def md_escape(text):
    """Escape pipe + newline so a value is safe inside a Markdown table cell."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def md_table(headers, rows):
    """Render a GitHub-flavored Markdown table. `rows` is a list of cell-lists."""
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(md_escape(c) for c in row) + " |")
    return "\n".join(out)


def now_iso():
    """UTC ISO-8601 timestamp (matches manifest/audit timestamps)."""
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    # Guard: this is a LIBRARY, not a CLI. run.py's `-m antilegacy_core.<stem>` probe
    # would match the bare stem `deliverables` and run this module — fail loudly with
    # guidance instead of silently no-op'ing. The umbrella's leaf stem is
    # `deliverables_index`; the individual renders are `prd`, `diagrams`, etc.
    import sys as _sys
    _sys.stderr.write(
        "antilegacy_core.deliverables is a shared library, not a CLI.\n"
        "Run a deliverable stem instead: prd | diagrams | test_plan | test_scripts | "
        "migration_plan | risk_log | decisions_log | evidence_log | deliverables_index\n"
    )
    _sys.exit(2)
