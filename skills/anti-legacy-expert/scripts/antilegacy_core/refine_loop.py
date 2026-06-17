#!/usr/bin/env python3
"""antilegacy_core.refine_loop — the bounded make -> review -> refine loop primitive (ISS-8).

`anti-legacy:adversarial-review` is the critic; this module is the LOOP that distrusts a
producer's output until it converges or is deliberately accepted. It does the two
DETERMINISTIC halves so the agent can do the two non-deterministic ones:

  1. DESCRIPTOR  — given ANY registered artifact id, resolve the single-artifact review
     descriptor (rendered file + the source data the critic must cross-check it against +
     the producing skill to re-run). This is the GENERIC analog of the deliverables-only
     `deliverable_review_worklist` (ISS-12): it works for requirements_graph.json,
     blueprint.json, task.md, a generated build skill — any artifact, not just the suite.
     Source data is the artifact's manifest `depends_on` edges (resolved id->path) plus the
     requirements graph (the §2 traceability spine).

  2. DECISION    — given a critic verdict + the attempt number, decide make/review/refine's
     next move: PASS -> stop (converged); REVISE/BLOCK & attempt < cap -> refine (re-run the
     producer at its source, re-review); REVISE/BLOCK & attempt >= cap -> STOP and recommend
     a read-only recon (AGENTS.md §7: "three failures, then recon" — the repeated failure is
     evidence the model of the problem is wrong, not the fix). `--forced` stops loudly past a
     non-PASS (mirrors `precheck --force`: an override is a stated decision, never silent).

The agent owns the non-deterministic steps: `make` = re-dispatch the producing skill (half
the producers are instruction-only — no render function — so this MUST be a skill dispatch,
not a Python call), and `review` = dispatch the read-only critic subagent. This module only
computes the descriptor + the decision + the §6 status report.

ADVISORY / READ-ONLY: never writes audit.jsonl, never advances a phase, never clears a gate,
never registers or edits an artifact. It only reports — exactly like the critic it drives.

CLI:   python3 .anti-legacy/run.py refine_loop descriptor --artifact <id> [--json]
       python3 .anti-legacy/run.py refine_loop decide --verdict <PASS|REVISE|BLOCK> \
                                   --attempt <n> [--cap 3] [--forced] [--json]
         descriptor exit: 0 = artifact present to review · 1 = registered-but-missing/absent
                          · 2 = no manifest.
         decide exit:     0 = stop (converged | forced) · 3 = refine (loop again)
                          · 4 = stop at cap (recon recommended). Distinct codes let
                          orchestrate/CI branch on the loop's verdict.

Pure standard library + antilegacy_core.deliverables (manifest/path helpers). Cross-platform.
"""
import argparse
import json
import os
import sys

from antilegacy_core import deliverables as D

# AGENTS.md §7: "After 3 failed attempts at the same problem: stop. Send a read-only recon
# agent before attempt 4." So 3 review attempts may refine; the 3rd still-failing review
# stops and recommends recon rather than a blind 4th try.
DEFAULT_CAP = 3
VERDICTS = ("PASS", "REVISE", "BLOCK")

# Exit codes for `decide` (the loop's branchable signal).
EXIT_STOP = 0     # converged (PASS) or deliberate --forced stop
EXIT_REFINE = 3   # non-PASS, attempts remain -> re-run producer + re-review
EXIT_CAP = 4      # non-PASS at the §7 cap -> stop, recommend recon


# --------------------------------------------------------------------------------------
# 1. DESCRIPTOR — generic single-artifact review target (ISS-12)
# --------------------------------------------------------------------------------------

def _abs_ws(rel):
    """Absolute path for a workspace-relative path (or an already-absolute one)."""
    return rel if os.path.isabs(rel) else os.path.join(D.workspace_root(), rel)


def _artifact_rendered_path(artifact):
    """A registered artifact's `path` is relative to .anti-legacy/ -> (ws-rel, present)."""
    path = artifact.get("path")
    if not path:
        return None, False
    abs_path = path if os.path.isabs(path) else os.path.join(D.workspace_root(), D.WS, path)
    rel = os.path.relpath(abs_path, D.workspace_root()).replace(os.sep, "/")
    return rel, os.path.isfile(abs_path)


def _resolve_source(dep, arts):
    """Resolve one `depends_on` entry to a workspace-relative path that EXISTS, else None.
    A dep may be another artifact id (resolve via its registered path) or a bare path."""
    a = arts.get(dep)
    if a and a.get("path"):
        rel, present = _artifact_rendered_path(a)
        return rel if present else None
    # Not a known artifact id — treat as a path (ws-relative or .anti-legacy/-relative).
    for cand in (dep, os.path.join(D.WS, dep)):
        if os.path.exists(_abs_ws(cand)):
            return cand.replace(os.sep, "/")
    return None


def build_descriptor(manifest, artifact_id):
    """The generic single-artifact critic descriptor for ANY registered artifact.

    source_data = the requirements graph (§2 spine, if present) + the artifact's
    `depends_on` edges resolved to existing files. The critic reads the rendered file and
    every source path and reports where the artifact says more/less/other than its sources.
    """
    arts = D.manifest_artifacts(manifest)
    a = arts.get(artifact_id)
    rendered, present = (_artifact_rendered_path(a) if a else (None, False))

    sources, seen = [], set()
    # The requirements graph is the traceability spine for every behavior-bearing artifact.
    spine = D.P_REQUIREMENTS.replace(os.sep, "/")
    if os.path.exists(_abs_ws(D.P_REQUIREMENTS)):
        sources.append(spine)
        seen.add(spine)
    for dep in (a.get("depends_on") or []) if a else []:
        rel = _resolve_source(dep, arts)
        if rel and rel not in seen:
            sources.append(rel)
            seen.add(rel)

    return {
        "artifact_id": artifact_id,
        "registered": bool(a),
        "producing_skill": (a or {}).get("produced_by"),
        "rendered_path": rendered,
        "present": present,
        "source_data": sources,
    }


# --------------------------------------------------------------------------------------
# 2. DECISION — bounded make -> review -> refine (ISS-8)
# --------------------------------------------------------------------------------------

def loop_decision(verdict, attempt, cap=DEFAULT_CAP, forced=False):
    """Decide the loop's next move from a critic verdict + the just-completed attempt number.

    Returns a dict: {action, terminal, recommend_recon, exit_code, reason}.
      action     ∈ {"stop", "refine"}
      terminal   ∈ {"converged", "forced", "cap-reached", None}   (None only when refining)
    """
    v = (verdict or "").strip().upper()
    if v not in VERDICTS:
        raise ValueError("verdict must be one of %s, got %r" % (", ".join(VERDICTS), verdict))
    if attempt < 1:
        raise ValueError("attempt must be >= 1, got %r" % (attempt,))

    if v == "PASS":
        return {"action": "stop", "terminal": "converged", "recommend_recon": False,
                "exit_code": EXIT_STOP, "verdict": v, "attempt": attempt, "cap": cap,
                "reason": "critic returned PASS — output converged with its source data."}
    if forced:
        return {"action": "stop", "terminal": "forced", "recommend_recon": False,
                "exit_code": EXIT_STOP, "verdict": v, "attempt": attempt, "cap": cap,
                "reason": "deliberate --forced stop past a %s verdict; you MUST state this "
                          "override loudly in your report (never a silent skip)." % v}
    if attempt >= cap:
        return {"action": "stop", "terminal": "cap-reached", "recommend_recon": True,
                "exit_code": EXIT_CAP, "verdict": v, "attempt": attempt, "cap": cap,
                "reason": "hit the §7 cap of %d attempts still at %s — STOP. Send a read-only "
                          "recon agent before a %dth attempt: the repeated failure is evidence "
                          "the model of the problem is wrong, not the fix." % (cap, v, cap + 1)}
    return {"action": "refine", "terminal": None, "recommend_recon": False,
            "exit_code": EXIT_REFINE, "verdict": v, "attempt": attempt, "cap": cap,
            "reason": "%s on attempt %d/%d — re-run the producing skill to fix the output at "
                      "its source, then re-review (make -> review -> refine)." % (v, attempt, cap)}


def status_report(artifact_id, decision, history=None):
    """A §6 status line set: what is TRUE, what is NOT yet true, what is NEXT."""
    hist = " -> ".join(history) if history else decision["verdict"]
    nxt = ("re-run the producing skill, then re-review" if decision["action"] == "refine"
           else "send a read-only recon agent, then reconsider the approach"
           if decision["recommend_recon"]
           else "proceed — output accepted" if decision["terminal"] == "converged"
           else "proceed under a STATED override (forced)")
    return (
        "adversarial-review loop — %s\n"
        "  verdicts: %s (attempt %d/%d)\n"
        "  TRUE:     the critic rendered a %s verdict on the latest output.\n"
        "  NOT YET:  %s\n"
        "  NEXT:     %s." % (
            artifact_id, hist, decision["attempt"], decision["cap"], decision["verdict"],
            ("output is converged with its sources" if decision["terminal"] == "converged"
             else "open findings remain (%s)" % decision["verdict"]),
            nxt))


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _cmd_descriptor(args):
    if not os.path.isfile(os.path.join(D.workspace_root(), D.P_MANIFEST)):
        sys.stderr.write("refine_loop: no .anti-legacy/manifest.json — run anti-legacy:setup first.\n")
        sys.exit(2)
    desc = build_descriptor(D.load_manifest(), args.artifact)
    if args.json:
        json.dump(desc, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        if not desc["registered"]:
            print("refine_loop: artifact %r is not registered in the manifest." % args.artifact)
        elif not desc["present"]:
            print("refine_loop: %s registered but file MISSING (%s) — re-run %s." % (
                args.artifact, desc["rendered_path"], desc["producing_skill"] or "its producer"))
        else:
            print("review target: %s" % args.artifact)
            print("  rendered:     %s" % desc["rendered_path"])
            print("  producing:    %s" % (desc["producing_skill"] or "(unknown)"))
            print("  critic reads: %s" % (", ".join(desc["source_data"]) or "(no source data found)"))
    sys.exit(0 if desc["present"] else 1)


def _cmd_decide(args):
    try:
        decision = loop_decision(args.verdict, args.attempt, cap=args.cap, forced=args.forced)
    except ValueError as e:
        sys.stderr.write("refine_loop: %s\n" % e)
        sys.exit(2)
    if args.json:
        json.dump(decision, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(status_report(args.artifact or "(artifact)", decision))
        print("  decision: %s — %s" % (decision["action"].upper(), decision["reason"]))
    sys.exit(decision["exit_code"])


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="refine_loop",
        description="The bounded make -> review -> refine loop primitive (advisory, read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("descriptor", help="resolve the single-artifact critic descriptor")
    d.add_argument("--artifact", required=True, help="registered artifact id (e.g. requirements-graph)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_descriptor)

    c = sub.add_parser("decide", help="decide the loop's next move from a verdict + attempt")
    c.add_argument("--verdict", required=True, choices=VERDICTS)
    c.add_argument("--attempt", required=True, type=int, help="the attempt number just reviewed (>=1)")
    c.add_argument("--cap", type=int, default=DEFAULT_CAP, help="max attempts before recon (§7, default 3)")
    c.add_argument("--forced", action="store_true", help="deliberate loud stop past a non-PASS verdict")
    c.add_argument("--artifact", default=None, help="artifact id (for the status report header)")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_decide)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
