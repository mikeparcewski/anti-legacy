#!/usr/bin/env python3
"""anti-legacy:deliverables — compile + register the deliverables index (README).

The umbrella `anti-legacy:deliverables` skill runs the individual deliverable renders
(prd, diagrams, test_plan, test_scripts, migration_plan, risk_log, decisions_log,
evidence_log), then runs THIS script to compile one index of the deliverables package:
what was produced, where it lives, its status, and a present/absent receipt — plus the
canonical expected set, so a missing deliverable is NAMED, never silently absent (§6).

Stem `deliverables_index` (deliberately NOT `deliverables`: that stem collides with the
antilegacy_core.deliverables library module under run.py's `-m antilegacy_core.<stem>`
probe).

Reads `.anti-legacy/manifest.json`; writes `.anti-legacy/deliverables/README.md`;
registers `deliverables-index`. Register-only; NEVER advances the phase.

Pure standard library + antilegacy_core.deliverables. Cross-platform (os.path).
"""
import argparse
import os
import sys

from antilegacy_core import deliverables as D

# The canonical deliverables suite: (artifact_id, human label, producing skill).
SUITE = [
    ("deliverable-prd",                "Product Requirements (PRD)",      "anti-legacy:prd"),
    ("deliverable-diagrams",           "Architecture Diagrams (Mermaid)", "anti-legacy:diagrams"),
    ("deliverable-test-strategy",      "Test Strategy",                   "anti-legacy:test-plan"),
    ("deliverable-test-scripts",       "Functional Test Scripts",         "anti-legacy:test-scripts"),
    ("deliverable-migration-plan",     "Migration Plan",                  "anti-legacy:migration-plan"),
    ("deliverable-migration-plan-csv", "Migration Plan — Jira CSV",  "anti-legacy:migration-plan"),
    ("deliverable-risk-log",           "Risk Log",                        "anti-legacy:risk-log"),
    ("deliverable-decisions-log",      "Decisions Log (ADRs)",            "anti-legacy:decisions-log"),
    ("deliverable-evidence-log",       "Evidence Log (with receipts)",    "anti-legacy:evidence-log"),
]

# Living deliverables: re-render at each gate so they reflect current state.
LIVING = {"deliverable-risk-log", "deliverable-decisions-log", "deliverable-evidence-log"}


def _receipt(artifact):
    """A present/absent receipt for a registered artifact's file (path is rel to .anti-legacy/)."""
    path = artifact.get("path")
    if not path:
        return "✗ no path"
    abs_path = path if os.path.isabs(path) else os.path.join(D.workspace_root(), D.WS, path)
    return "✓ present" if os.path.isfile(abs_path) else "✗ MISSING FILE"


def render(manifest):
    arts = D.manifest_artifacts(manifest)
    produced, missing, rows = [], [], []
    for art_id, label, skill in SUITE:
        a = arts.get(art_id)
        living = " (living)" if art_id in LIVING else ""
        if a:
            produced.append(art_id)
            rows.append([label + living, "`%s`" % a.get("path", "?"),
                         a.get("status", "?"), a.get("produced_at", "?"), _receipt(a)])
        else:
            missing.append((label, skill))
            rows.append([label + living, "_not yet produced_", "—", "—",
                         "run `%s`" % skill])

    md = [
        "# Deliverables", "",
        "> Generated %s. Stakeholder deliverables rendered from the requirements graph "
        "and downstream artifacts. Each is registered in the manifest; this is the "
        "package's table of contents." % D.now_iso(), "",
        "**Produced: %d / %d**" % (len(produced), len(SUITE)), "",
        D.md_table(["Deliverable", "Path", "Status", "Produced", "Receipt"], rows), "",
    ]
    if missing:
        md += ["## Not yet produced", ""]
        md += ["- **%s** — run `%s`" % (label, skill) for label, skill in missing]
        md += [""]
    md += [
        "## Living deliverables", "",
        "`risk-log`, `decisions-log`, and `evidence-log` are *living*: re-run them at each "
        "gate so they reflect current state (new RISK rows, gate sign-offs, fresh receipts).",
        "",
    ]
    return "\n".join(md), produced, missing


def main():
    ap = argparse.ArgumentParser(description="Compile + register the deliverables index (README).")
    ap.add_argument("--no-register", action="store_true",
                    help="write the index but do not register it in the manifest")
    args = ap.parse_args()

    manifest = D.load_manifest()
    if not manifest:
        sys.stderr.write("deliverables_index: no manifest at .anti-legacy/manifest.json — "
                         "run anti-legacy:setup first.\n")
        sys.exit(1)

    content, produced, missing = render(manifest)
    path = D.write_deliverable("README.md", content)
    if not os.path.getsize(path):
        sys.stderr.write("deliverables_index: wrote an empty index — aborting.\n")
        sys.exit(1)

    if not args.no_register:
        D.register_deliverable("deliverables-index", path, "anti-legacy:deliverables",
                               depends_on=[i for i, _, _ in SUITE if i in produced])
    print("deliverables index: %s (%d/%d produced)" %
          (os.path.relpath(path, D.workspace_root()), len(produced), len(SUITE)))
    if missing:
        print("  not yet produced: " + ", ".join(skill for _, skill in missing))


if __name__ == "__main__":
    main()
