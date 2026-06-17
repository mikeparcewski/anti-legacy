#!/usr/bin/env python3
"""anti-legacy:deliverable-review — assemble the per-deliverable critic worklist.

`anti-legacy:deliverable-review` dispatches one READ-ONLY critic subagent per rendered
deliverable. A critic cannot adversarially challenge a deliverable without two things:
the RENDERED file (what the stakeholder will read) and the SOURCE DATA the deliverable was
rendered FROM (the ground truth it must NOT diverge from). Picking those two — per
deliverable — is the deterministic part; this leaf script does exactly that and nothing
else. The judgment (findings + PASS/REVISE/BLOCK) is the subagent's, not this script's.

It reads `.anti-legacy/manifest.json` (the registered `deliverable-*` artifacts) + the
canonical suite, resolves each rendered file to disk, attaches the source-data pointers
the critic must cross-check against, and emits a worklist. A deliverable that the manifest
says is registered but whose file is missing on disk is reported (present=False) so the
skill surfaces it rather than dispatching a critic against nothing.

READ-ONLY: never writes the manifest, never advances a phase, never clears a gate, never
edits any deliverable. It only reports.

CLI:   python3 .anti-legacy/run.py deliverable_review_worklist [--json] [--deliverable ID]
         exit 0 = >=1 deliverable present to review · 1 = nothing to review · 2 = no manifest.
         --json        : machine-readable {workspace, count, items:[...]}.
         --deliverable : restrict to a single artifact id (e.g. deliverable-prd).

In-process:
  build_worklist(manifest) -> list[dict]   (one entry per CANONICAL deliverable)

Pure standard library + antilegacy_core.deliverables. Cross-platform (os.path).
"""
import argparse
import json
import os
import sys

from antilegacy_core import deliverables as D

# The canonical deliverables suite, mirrored from skills/deliverables/scripts/
# deliverables_index.py SUITE (kept deliberately in sync — one row per registered
# deliverable artifact id). label + producing skill let the critic know what it is
# reviewing and which skill to re-run on REVISE/BLOCK.
SUITE = [
    ("deliverable-prd",                "Product Requirements (PRD)",      "anti-legacy:prd"),
    ("deliverable-diagrams",           "Architecture Diagrams (Mermaid)", "anti-legacy:diagrams"),
    ("deliverable-test-strategy",      "Test Strategy",                   "anti-legacy:test-plan"),
    ("deliverable-test-scripts",       "Functional Test Scripts",         "anti-legacy:test-scripts"),
    ("deliverable-migration-plan",     "Migration Plan",                  "anti-legacy:migration-plan"),
    ("deliverable-migration-plan-csv", "Migration Plan — Jira CSV",       "anti-legacy:migration-plan"),
    ("deliverable-risk-log",           "Risk Log",                        "anti-legacy:risk-log"),
    ("deliverable-decisions-log",      "Decisions Log (ADRs)",            "anti-legacy:decisions-log"),
    ("deliverable-evidence-log",       "Evidence Log (with receipts)",    "anti-legacy:evidence-log"),
]

# Per-deliverable source-data pointers: the workspace-relative files the critic must read
# and adversarially cross-check the rendered deliverable against. Every deliverable is
# checked against the requirements graph (the §2 traceability spine); the rest are the
# enriching inputs each renderer consumes (see DELIVERABLES_CONTRACT.md §3/§7). A pointer
# is reported only when the file actually exists, so the critic is never sent a dead path.
_GRAPH = D.P_REQUIREMENTS
_SOURCES = {
    "deliverable-prd":                [_GRAPH, D.P_COVERAGE, D.P_ANNOTATIONS],
    "deliverable-diagrams":           [_GRAPH, D.P_BLUEPRINT, D.P_CONFIG],
    "deliverable-test-strategy":      [_GRAPH, D.P_CONTRACTS, D.P_BLUEPRINT],
    "deliverable-test-scripts":       [_GRAPH, D.P_CONTRACTS],
    "deliverable-migration-plan":     [_GRAPH, D.P_BLUEPRINT, D.P_CONFIG],
    "deliverable-migration-plan-csv": [_GRAPH, D.P_BLUEPRINT],
    "deliverable-risk-log":           [_GRAPH, D.P_COVERAGE, D.P_ANNOTATIONS],
    "deliverable-decisions-log":      [_GRAPH, D.P_MANIFEST, D.P_AUDIT, D.P_BLUEPRINT],
    "deliverable-evidence-log":       [D.P_MANIFEST, D.P_AUDIT],
}

# Living deliverables reflect current state (re-rendered at each gate); the critic judges
# them against the present moment, not as a frozen artifact.
LIVING = {"deliverable-risk-log", "deliverable-decisions-log", "deliverable-evidence-log"}


def _present_sources(art_id):
    """Workspace-relative source paths for `art_id` that actually exist on disk."""
    out = []
    for rel in _SOURCES.get(art_id, [_GRAPH]):
        ap = rel if os.path.isabs(rel) else os.path.join(D.workspace_root(), rel)
        if os.path.exists(ap):
            out.append(rel.replace(os.sep, "/"))
    return out


def _rendered_path(artifact):
    """Resolve a registered deliverable's file (path is rel to .anti-legacy/) -> (rel, present)."""
    path = artifact.get("path")
    if not path:
        return None, False
    abs_path = path if os.path.isabs(path) else os.path.join(D.workspace_root(), D.WS, path)
    rel = os.path.relpath(abs_path, D.workspace_root()).replace(os.sep, "/")
    return rel, os.path.isfile(abs_path)


def build_worklist(manifest, only=None):
    """One entry per CANONICAL deliverable. `registered`/`present` flag the gaps so the
    skill can surface a missing render instead of silently dispatching against nothing."""
    arts = D.manifest_artifacts(manifest)
    items = []
    for art_id, label, skill in SUITE:
        if only and art_id != only:
            continue
        a = arts.get(art_id)
        rendered, present = (_rendered_path(a) if a else (None, False))
        items.append({
            "artifact_id": art_id,
            "label": label,
            "producing_skill": skill,
            "living": art_id in LIVING,
            "registered": bool(a),
            "rendered_path": rendered,
            "present": present,
            "source_data": _present_sources(art_id),
        })
    return items


def _print_report(items):
    reviewable = [it for it in items if it["present"]]
    print("Deliverable critic worklist — %d reviewable / %d canonical" % (len(reviewable), len(items)))
    for it in items:
        if it["present"]:
            mark, where = "ok ", it["rendered_path"]
        elif it["registered"]:
            mark, where = "!! ", "registered but MISSING FILE"
        else:
            mark, where = "-  ", "not produced (run %s)" % it["producing_skill"]
        print("  [%s] %s%s — %s" % (mark, it["artifact_id"], " (living)" if it["living"] else "", where))
        if it["present"]:
            print("        critic reads: %s" % ", ".join(it["source_data"]))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="deliverable_review_worklist",
        description="Assemble the per-deliverable adversarial-critic worklist (read-only).")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON worklist")
    ap.add_argument("--deliverable", default=None,
                    help="restrict to a single artifact id (e.g. deliverable-prd)")
    args = ap.parse_args(argv)

    if not os.path.isfile(os.path.join(D.workspace_root(), D.P_MANIFEST)):
        sys.stderr.write("deliverable_review_worklist: no .anti-legacy/manifest.json — "
                         "run anti-legacy:setup, then render the deliverables first.\n")
        sys.exit(2)

    manifest = D.load_manifest()
    items = build_worklist(manifest, only=args.deliverable)
    reviewable = [it for it in items if it["present"]]

    if args.json:
        json.dump({"workspace": D.workspace_root(), "count": len(reviewable), "items": items},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_report(items)

    if not reviewable:
        sys.stderr.write("deliverable_review_worklist: nothing to review — no rendered deliverable "
                         "present on disk. Run anti-legacy:deliverables first.\n")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
