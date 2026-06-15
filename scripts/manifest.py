#!/usr/bin/env python3
"""
Manifest manager for anti-legacy pipeline.

The manifest (.anti-legacy/manifest.json) is the single source of truth for:
  - What phase the pipeline is in
  - What artifacts exist and where they are
  - What gates have been passed
  - What learnings have been indexed

Every skill reads the manifest first. Every skill updates it after producing output.
This script provides deterministic CLI operations so skills don't hand-roll JSON mutations.
"""
import json
import os
import sys
import hashlib
import argparse
from datetime import datetime, timezone


MANIFEST_PATH = ".anti-legacy/manifest.json"

# Gate-precondition map (B2). Maps each gate-* PHASE to the gate id(s) that must be
# status in {passed, waived} BEFORE the pipeline may LEAVE that phase. The check is
# applied in cmd_advance against the CURRENT phase (i.e. on EXIT of a gate phase), so
# advancing INTO a gate phase (e.g. review-packet -> gate-design-review) is always
# allowed — the human can then sign/waive the gate while parked in it, and only then
# advance out. This mirrors AGENTS.md 'Gate Approval Cycle'.
#
#   'gate-design-review'    -> ['GATE_1_DESIGN']
#   'gate-plan-review'      -> ['GATE_2_PLAN']
#   'gate-build-integrity'  -> ['GATE_3_BUILD', 'GATE_3B_SEMANTIC']
#   'gate-uat-signoff'      -> ['GATE_4_UAT']
#
# GATE_1B_SEMANTIC_JOIN and GATE_0_DISCOVERY are intentionally NOT in this map: neither
# has a dedicated gate-* phase enum value (semantic-join is an optional pre-survey side
# phase; discovery is a survey concern), so there is no gate phase to gate on exit.
#
# GATE_5_COMPLETENESS sits on the final-review phase: it auto-clears on a passing
# completeness-report and kicks back (resets phase) on a FAIL, exactly like the build
# gate, so it is precondition-bound to its own gate-* phase here.
GATE_PHASE_PRECONDITIONS = {
    "gate-design-review": ["GATE_1_DESIGN"],
    "gate-plan-review": ["GATE_2_PLAN"],
    "gate-build-integrity": ["GATE_3_BUILD", "GATE_3B_SEMANTIC"],
    "gate-uat-signoff": ["GATE_4_UAT"],
    "final-review": ["GATE_5_COMPLETENESS"],
}

# Generalized gate kick-back map (B1a) — the INVERSE of GATE_PHASE_PRECONDITIONS, keyed
# by gate id rather than by gate-* phase. Maps EVERY gate id (mainline, side, and
# automated) to the (producing_phase, producing_skill) pair the pipeline must rewind to
# when that gate is recorded `failed`. On a failed gate, cmd_gate performs a GUIDED reset:
# it sets phase.current back to the producing phase and prints the skill to re-run — it
# does NOT auto-dispatch the skill (the human/orchestrator decides when to re-run). This
# generalizes the old GATE_1-only targeted re-run to ALL gates. passed/waived never reset.
#
# The producing phase is the phase whose work feeds the gate — i.e. where the reviewed
# artifact is (re)produced — NOT the gate-* parking phase. Resetting to the gate-* phase
# would be a no-op loop; resetting to the producing phase rewinds the actual work.
GATE_PRODUCING_PHASE = {
    "GATE_0_DISCOVERY": ("survey", "anti-legacy:survey"),
    "GATE_1_DESIGN": ("graph-translate", "anti-legacy:graph-translator"),
    "GATE_1B_SEMANTIC_JOIN": ("semantic-join", "anti-legacy:semantic-join"),
    "GATE_2_PLAN": ("planning", "anti-legacy:planner"),
    "GATE_3_BUILD": ("build", "anti-legacy:swarm"),
    "GATE_3B_SEMANTIC": ("semantic-validation", "anti-legacy:semantic-validation"),
    "GATE_4_UAT": ("uat", "anti-legacy:uat-crew"),
    "GATE_5_COMPLETENESS": ("document", "anti-legacy:document"),
}

# Gate statuses that satisfy a precondition (signed off, or explicit human waiver).
_SATISFIED_GATE_STATUSES = {"passed", "waived"}

# Legal phase enum values (must match schemas/manifest.schema.json phase.current enum).
# Three phases added by B1a wiring:
#   - functional-tests: blocking pre-build validation, AFTER gate-plan-review, BEFORE build
#   - document:         documentation pass, AFTER gate-uat-signoff, BEFORE final-review
#   - final-review:     automated completeness gate (GATE_5_COMPLETENESS), AFTER document,
#                       BEFORE complete — auto-clears on a passing completeness-report and
#                       kicks back on FAIL
PHASE_ENUM = (
    "uninitialized",
    "survey",
    "semantic-join",
    "analyze",
    "graph-translate",
    "blueprint",
    "test-strategy",
    "review-packet",
    "gate-design-review",
    "planning",
    "gate-plan-review",
    "functional-tests",
    "build",
    "target-review",
    "semantic-validation",
    "gate-build-integrity",
    "uat",
    "gate-uat-signoff",
    "document",
    "final-review",
    "complete",
)


def load_manifest(manifest_path=MANIFEST_PATH):
    """Load and return the manifest. Exits with error if missing."""
    if not os.path.exists(manifest_path):
        print(f"Error: Manifest not found at {manifest_path}. Run 'init' first.", file=sys.stderr)
        sys.exit(1)
    with open(manifest_path, 'r') as f:
        return json.load(f)


def save_manifest(manifest, manifest_path=MANIFEST_PATH):
    """Write manifest back to disk."""
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)


def file_checksum(file_path):
    """SHA-256 checksum of a file."""
    if not os.path.exists(file_path):
        return None
    if os.path.isdir(file_path):
        return None
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _artifact_full_path(art):
    """Resolve an artifact's on-disk path, anchoring relative paths under .anti-legacy/.

    Shared by cmd_check and _verify_evidence so the existence/checksum predicate is
    identical for integrity checks and gate evidence verification.
    """
    path = art["path"]
    return path if path.startswith(".anti-legacy") else os.path.join(".anti-legacy", path)


def _verify_evidence(m, evidence_ids):
    """Content-verify cited evidence ids (B1). Returns a dict of problem buckets.

    For each cited id this checks, using the SAME predicate as cmd_check:
      - unregistered: id is not a key in m['artifacts']
      - bad_status:   artifact status is in {failed, pending} (as "<id> (status: <s>)")
      - missing:      resolved file does not exist or is a directory
      - drifted:      artifact has a recorded checksum that no longer matches the file

    An empty value in every bucket means every cited evidence id is registered, has an
    acceptable status, points at a present file, and (if checksummed) is undrifted.
    """
    artifacts = m.get("artifacts", {})
    problems = {"unregistered": [], "bad_status": [], "missing": [], "drifted": []}
    for e in evidence_ids:
        if e not in artifacts:
            problems["unregistered"].append(e)
            continue
        art = artifacts[e]
        status = art.get("status")
        if status in {"failed", "pending"}:
            problems["bad_status"].append(f"{e} (status: {status})")
        full_path = _artifact_full_path(art)
        if not os.path.exists(full_path) or os.path.isdir(full_path):
            problems["missing"].append(e)
            continue
        if "checksum" in art:
            if file_checksum(full_path) != art["checksum"]:
                problems["drifted"].append(e)
    return problems


def cmd_init(args):
    """Initialize .anti-legacy/ workspace and manifest from template."""
    template_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_path = os.path.join(template_dir, "templates", "manifest.json")

    # Create directory structure
    dirs = [
        ".anti-legacy",
        ".anti-legacy/evidence",
        ".anti-legacy/contracts",
        ".anti-legacy/requirements",
        ".anti-legacy/patterns/learnings",
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)

    # Copy template
    if os.path.exists(MANIFEST_PATH) and not args.force:
        print(f"Manifest already exists at {MANIFEST_PATH}. Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    with open(template_path, 'r') as f:
        manifest = json.load(f)

    # Fill in project fields if provided
    if args.name:
        manifest["project"]["name"] = args.name
    if args.target_stack:
        manifest["project"]["target_stack"] = args.target_stack
    if args.target_path:
        manifest["project"]["target_path"] = args.target_path

    save_manifest(manifest)

    # Create empty audit trail
    audit_path = ".anti-legacy/audit.jsonl"
    if not os.path.exists(audit_path):
        open(audit_path, 'a').close()

    print(f"Initialized .anti-legacy/ workspace with manifest at {MANIFEST_PATH}")


def cmd_status(args):
    """Print current pipeline status."""
    m = load_manifest()
    print(f"Project:       {m['project']['name'] or '(unnamed)'}")
    print(f"Target Stack:  {m['project']['target_stack'] or '(not set)'}")
    print(f"Current Phase: {m['phase']['current']}")
    print(f"Completed:     {' → '.join(m['phase']['completed']) or '(none)'}")
    print()

    print("Gates:")
    for gate_id, gate in m.get("gates", {}).items():
        status = gate["status"].upper()
        evaluator = gate.get("evaluator", "")
        print(f"  {gate_id}: {status}" + (f" (by {evaluator})" if evaluator else ""))
    print()

    print(f"Artifacts: {len(m.get('artifacts', {}))}")
    for art_id, art in m.get("artifacts", {}).items():
        print(f"  [{art['status']:>8}] {art_id} → {art['path']} ({art['format']})")
    print()

    print(f"Learnings: {len(m.get('learnings', []))}")


def cmd_advance(args):
    """Advance pipeline to next phase."""
    if args.phase not in PHASE_ENUM:
        print(
            f"Error: illegal phase '{args.phase}'. Legal phases: {', '.join(PHASE_ENUM)}",
            file=sys.stderr,
        )
        sys.exit(2)

    m = load_manifest()
    current = m["phase"]["current"]

    # B2 gate precondition: if we are LEAVING a gate-* phase, every gate bound to that
    # phase must be passed/waived first. Checked against the CURRENT phase so advancing
    # INTO a gate phase stays free. Do NOT mutate or save the manifest when blocking.
    if current in GATE_PHASE_PRECONDITIONS:
        gates = m.get("gates", {})
        unmet = []
        for gid in GATE_PHASE_PRECONDITIONS[current]:
            status = gates.get(gid, {}).get("status")
            if status not in _SATISFIED_GATE_STATUSES:
                unmet.append(f"{gid} (status: {status or 'absent'})")
        if unmet:
            print(
                f"Error: cannot advance out of gate phase {current} to {args.phase}: "
                f"gate(s) {', '.join(unmet)} not passed/waived. "
                f"Sign or waive the gate first.",
                file=sys.stderr,
            )
            sys.exit(2)

    # Record current as completed
    if current != "uninitialized":
        if current not in m["phase"]["completed"]:
            m["phase"]["completed"].append(current)

    m["phase"]["current"] = args.phase

    save_manifest(m)
    print(f"Pipeline advanced: {current} → {args.phase}")

    # Append audit event
    _append_audit({
        "event": f"anti-legacy:phase-advanced",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {"from": current, "to": args.phase}
    })


def cmd_register(args):
    """Register an artifact in the manifest."""
    m = load_manifest()

    # Compute checksum if file exists
    full_path = os.path.join(".anti-legacy", args.path) if not args.path.startswith(".anti-legacy") else args.path
    cs = file_checksum(full_path)

    artifact = {
        "path": args.path,
        "format": args.format,
        "produced_by": args.produced_by,
        "status": args.status or "draft",
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "depends_on": args.depends_on.split(",") if args.depends_on else [],
    }
    if args.schema:
        artifact["schema"] = args.schema
    if cs:
        artifact["checksum"] = cs

    m["artifacts"][args.artifact_id] = artifact
    save_manifest(m)
    print(f"Registered artifact: {args.artifact_id} → {args.path}")

    _append_audit({
        "event": "anti-legacy:artifact-registered",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {"artifact_id": args.artifact_id, "path": args.path, "status": artifact["status"]}
    })


def cmd_gate(args):
    """Record a gate decision."""
    m = load_manifest()

    # A gate id is valid if it is already tracked in this manifest OR it is one of the
    # canonical gate ids manifest.py knows (GATE_PRODUCING_PHASE). The second clause lets
    # newly-wired gates (e.g. GATE_5_COMPLETENESS) be recorded even on a manifest minted
    # from a template that predates them — the gate row is materialized below. Genuinely
    # unknown ids (e.g. GATE_99_FAKE) are still rejected.
    valid_gates = set(m.get("gates", {})) | set(GATE_PRODUCING_PHASE)
    if args.gate_id not in valid_gates:
        print(f"Error: Unknown gate '{args.gate_id}'. Valid gates: {sorted(valid_gates)}", file=sys.stderr)
        sys.exit(1)

    evidence_ids = [e.strip() for e in args.evidence.split(",") if e.strip()] if args.evidence else []

    # Deterministic gate guard (B1): a gate may only be recorded PASSED if every cited
    # evidence id is a registered artifact AND content-verifies — the file exists, its
    # recorded checksum still matches, and its status is not failed/pending. This stops
    # phantom (membership-only) passes at the CLI itself, not just via skill prose.
    # FAILED needs no evidence; WAIVED is an explicit, audited human override that
    # bypasses this check entirely.
    if args.opinion.lower() == "passed":
        if not evidence_ids:
            print(f"Error: cannot record gate '{args.gate_id}' as PASSED with no --evidence. "
                  f"Cite the registered artifact(s) backing this decision, or use --opinion waived "
                  f"for an explicit human override.", file=sys.stderr)
            sys.exit(1)

        problems = _verify_evidence(m, evidence_ids)
        if any(problems.values()):
            parts = []
            if problems["unregistered"]:
                parts.append(f"not registered: {problems['unregistered']}")
            if problems["bad_status"]:
                parts.append(f"unacceptable status: {problems['bad_status']}")
            if problems["missing"]:
                parts.append(f"file missing: {problems['missing']}")
            if problems["drifted"]:
                parts.append(f"checksum drifted: {problems['drifted']}")
            print(f"Error: cannot record gate '{args.gate_id}' as PASSED — evidence failed "
                  f"content verification ({'; '.join(parts)}). Register/repair the artifact(s) "
                  f"and re-checksum, or use --opinion waived to override.", file=sys.stderr)
            sys.exit(1)

    opinion = args.opinion.lower()
    m["gates"][args.gate_id] = {
        "status": opinion,
        "evaluator": args.evaluator,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "rationale": args.rationale or "",
        "evidence_artifacts": evidence_ids
    }

    # Generalized gate kick-back (B1a): recording a gate `failed` rewinds the pipeline to
    # the phase that PRODUCES that gate's reviewed artifact, so the failing work is redone
    # — not a full restart. This is a GUIDED reset: we set phase.current back and name the
    # skill to re-run; we do NOT auto-dispatch it. Applies to EVERY gate, not just GATE_1.
    # passed/waived leave the phase untouched (behavior unchanged). The reset is recorded
    # in phase.completed-aware fashion: the producing phase is removed from `completed` so
    # the pipeline genuinely re-enters it rather than treating it as already done.
    kicked_back = None
    if opinion == "failed" and args.gate_id in GATE_PRODUCING_PHASE:
        producing_phase, producing_skill = GATE_PRODUCING_PHASE[args.gate_id]
        prior_phase = m["phase"]["current"]
        m["phase"]["current"] = producing_phase
        if producing_phase in m["phase"].get("completed", []):
            m["phase"]["completed"] = [p for p in m["phase"]["completed"] if p != producing_phase]
        m["phase"]["blocked_reason"] = (
            f"{args.gate_id} failed — pipeline reset to '{producing_phase}'. "
            f"Re-run {producing_skill}, regenerate evidence, and re-present the gate."
        )
        kicked_back = (prior_phase, producing_phase, producing_skill)

    save_manifest(m)
    print(f"Gate {args.gate_id}: {args.opinion.upper()} (by {args.evaluator})")

    _append_audit({
        "event": "anti-legacy:gate-signed-off",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {
            "gate_id": args.gate_id,
            "opinion": args.opinion.upper(),
            "evaluator": args.evaluator,
            "rationale": args.rationale or ""
        }
    })

    if kicked_back is not None:
        prior_phase, producing_phase, producing_skill = kicked_back
        _append_audit({
            "event": "anti-legacy:gate-kicked-back",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "gate_id": args.gate_id,
                "from_phase": prior_phase,
                "reset_to_phase": producing_phase,
                "re_run_skill": producing_skill,
            }
        })
        print(
            f"KICK-BACK: {args.gate_id} FAILED — pipeline phase reset {prior_phase} -> "
            f"{producing_phase}.",
            file=sys.stderr,
        )
        print(
            f"  Re-run {producing_skill} to address the failure, regenerate its evidence, "
            f"then re-present {args.gate_id} for sign-off.",
            file=sys.stderr,
        )
        if args.rationale:
            print(f"  Rationale on record: {args.rationale}", file=sys.stderr)
        # Exit non-zero so callers (orchestrate, CI) can branch on a failed-gate record.
        sys.exit(3)


def cmd_learn(args):
    """Index a learning note in the manifest."""
    m = load_manifest()

    learning = {
        "id": args.learning_id,
        "path": args.path,
        "tags": args.tags.split(",") if args.tags else [],
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    m.setdefault("learnings", []).append(learning)
    save_manifest(m)
    print(f"Indexed learning: {args.learning_id} ({', '.join(learning['tags'])})")


def cmd_check(args):
    """Verify artifact checksums haven't drifted from manifest."""
    m = load_manifest()
    artifacts = m.get("artifacts", {})

    target_id = getattr(args, "artifact_id", None)
    if target_id is not None:
        if target_id not in artifacts:
            print(f"Error: Unknown artifact '{target_id}'. Not registered in manifest.", file=sys.stderr)
            sys.exit(1)
        items = [(target_id, artifacts[target_id])]
    else:
        items = list(artifacts.items())

    issues = []
    for art_id, art in items:
        full_path = _artifact_full_path(art)
        if not os.path.exists(full_path) or os.path.isdir(full_path):
            issues.append(f"  MISSING: {art_id} → {art['path']}")
            continue
        if "checksum" in art:
            current_cs = file_checksum(full_path)
            if current_cs != art["checksum"]:
                issues.append(f"  DRIFTED: {art_id} → {art['path']} (manifest: {art['checksum'][:12]}... actual: {current_cs[:12]}...)")

    if issues:
        print("Integrity issues found:")
        for i in issues:
            print(i)
        sys.exit(1)
    else:
        print(f"All {len(items)} artifacts verified. No drift detected.")


def cmd_audit_report(args):
    """Compile audit.jsonl into a formatted markdown audit report."""
    m = load_manifest()
    project_name = m["project"]["name"] or "unnamed-project"
    
    audit_path = ".anti-legacy/audit.jsonl"
    report_path = ".anti-legacy/audit_report.md"
    config_path = ".anti-legacy/config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path) as cf:
                cfg = json.load(cf)
            if "paths" in cfg and "audit_report" in cfg["paths"]:
                report_path = cfg["paths"]["audit_report"]
        except Exception:
            pass
            
    if not os.path.exists(audit_path):
        print(f"Error: Audit log not found at {audit_path}", file=sys.stderr)
        sys.exit(1)
        
    md = []
    md.append(f"# Compliance Audit Report — {project_name}")
    md.append(f"\n*Generated at: {datetime.now(timezone.utc).isoformat()}*")
    md.append("\nThis document contains the complete, chronological history of all phase transitions, gate approvals, and artifact registrations recorded in the project manifest.\n")
    md.append("## Chronological Audit Trail\n")
    md.append("| Timestamp (UTC) | Event Type | Details / Rationale | Evaluator / Source |")
    md.append("|---|---|---|---|")
    
    try:
        with open(audit_path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    ts = event.get("timestamp", "")
                    evt_type = event.get("event", "").replace("anti-legacy:", "")
                    details_obj = event.get("details", {})
                    
                    details_str = ""
                    evaluator = "system"
                    
                    if evt_type == "phase-advanced":
                        details_str = f"Phase advanced from `{details_obj.get('from')}` to `{details_obj.get('to')}`"
                    elif evt_type == "artifact-registered":
                        details_str = f"Artifact `{details_obj.get('artifact_id')}` registered at path `{details_obj.get('path')}` (Status: `{details_obj.get('status')}`)"
                    elif evt_type == "gate-signed-off":
                        gate_id = details_obj.get("gate_id")
                        opinion = details_obj.get("opinion")
                        evaluator = details_obj.get("evaluator", "unknown")
                        rationale = details_obj.get("rationale", "")
                        details_str = f"Gate **{gate_id}** signed off as **{opinion}**. Rationale: *{rationale}*"
                    else:
                        details_str = json.dumps(details_obj)
                        
                    md.append(f"| {ts} | {evt_type} | {details_str} | {evaluator} |")
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Error parsing audit log: {e}", file=sys.stderr)
        sys.exit(1)
        
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(md))
        
    print(f"Compliance audit report written to {report_path}")


def _append_audit(event):
    """Append an audit event to audit.jsonl."""
    audit_path = ".anti-legacy/audit.jsonl"
    with open(audit_path, 'a') as f:
        f.write(json.dumps(event) + "\n")


def main():
    parser = argparse.ArgumentParser(
        prog="manifest",
        description="Deterministic manifest manager for the anti-legacy pipeline."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_p = subparsers.add_parser("init", help="Initialize .anti-legacy/ workspace and manifest")
    init_p.add_argument("--name", help="Project name")
    init_p.add_argument("--target-stack", help="Target technology stack")
    init_p.add_argument("--target-path", help="Target code output path")
    init_p.add_argument("--force", action="store_true", help="Overwrite existing manifest")
    init_p.set_defaults(func=cmd_init)

    # status
    status_p = subparsers.add_parser("status", help="Print current pipeline status")
    status_p.set_defaults(func=cmd_status)

    # advance
    advance_p = subparsers.add_parser("advance", help="Advance pipeline to next phase")
    advance_p.add_argument("phase", help="Phase to advance to")
    advance_p.set_defaults(func=cmd_advance)

    # register
    reg_p = subparsers.add_parser("register", help="Register an artifact in the manifest")
    reg_p.add_argument("artifact_id", help="Unique artifact ID (e.g. 'legacy-graph', 'requirements-graph')")
    reg_p.add_argument("--path", required=True, help="Relative path from .anti-legacy/")
    reg_p.add_argument("--format", required=True, choices=["json", "jsonl", "markdown", "text"])
    reg_p.add_argument("--produced-by", required=True, help="Skill or script name")
    reg_p.add_argument("--status", default="draft", choices=["pending", "draft", "final", "approved", "failed"])
    reg_p.add_argument("--schema", help="Schema file reference")
    reg_p.add_argument("--depends-on", help="Comma-separated artifact IDs this depends on")
    reg_p.set_defaults(func=cmd_register)

    # gate
    gate_p = subparsers.add_parser("gate", help="Record a gate decision")
    gate_p.add_argument("gate_id", help="Gate ID (e.g. GATE_1_DESIGN)")
    gate_p.add_argument("--opinion", required=True, choices=["passed", "failed", "waived"])
    gate_p.add_argument("--evaluator", required=True, help="Who signed off")
    gate_p.add_argument("--rationale", help="Why")
    gate_p.add_argument("--evidence", help="Comma-separated artifact IDs backing this decision")
    gate_p.set_defaults(func=cmd_gate)

    # learn
    learn_p = subparsers.add_parser("learn", help="Index a learning note")
    learn_p.add_argument("learning_id", help="Unique learning ID")
    learn_p.add_argument("--path", required=True, help="Path to the learning note markdown")
    learn_p.add_argument("--tags", help="Comma-separated searchable tags")
    learn_p.set_defaults(func=cmd_learn)

    # check
    check_p = subparsers.add_parser("check", help="Verify artifact integrity (checksums)")
    check_p.add_argument("artifact_id", nargs="?", default=None, help="Optional single artifact id to verify; omit to verify all")
    check_p.set_defaults(func=cmd_check)

    # audit-report
    audit_report_p = subparsers.add_parser("audit-report", help="Compile audit log into a markdown report")
    audit_report_p.set_defaults(func=cmd_audit_report)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == '__main__':
    main()
