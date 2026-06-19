#!/usr/bin/env python3
"""antilegacy_core.antagonist — context assembler for the pre-build threat modeler (PEP step 3).

The antagonist skill (anti-legacy:antagonist) is a read-only critic that attacks a phase
plan BEFORE the producer runs. The non-deterministic threat generation is the critic's work
(agent/LLM); this module does the DETERMINISTIC half: assembling the threat-surface context
from on-disk pipeline state so the critic has the right inputs.

Phase Execution Protocol (PEP, AGENTS.md §10):
  plan → review → antagonist → resolve → test → validate

This module implements the `antagonist context` CLI subcommand.  It reads:
  - manifest status (current phase, gates)
  - coverage-report.json  (for extraction / graph-translator)
  - requirements_graph.json summary (for blueprint / planner / swarm)
  - functional_comparison_report.json (for semantic-validation)
  - audit.jsonl (for uat-crew reviewer-conflict check)
  - config.json roles (architect, for UAT reviewer-conflict)

and emits a structured context block that the critic reads as its input alongside
the phase plan text.

CLI:
  python3 .anti-legacy/run.py antagonist context --phase <phase> [--workspace .] [--json]
    exit 0 = context assembled · 1 = manifest missing (pipeline not initialized)
    --json   emit machine-readable JSON (default: human-readable text block)

Pure standard library. Cross-platform.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Phase tier registry — which PEP steps apply to each phase.
# full      : all 6 steps including antagonist
# lite      : review + validate only (mechanical renders)
# minus-ant : plan + review + resolve + test + validate (no antagonist)
# ---------------------------------------------------------------------------
PHASE_TIERS = {
    # Full PEP — semantic/judgment-heavy phases
    "extraction":          "full",
    "graph-translate":     "full",
    "blueprint":           "full",
    "test-strategy":       "full",
    "plan":                "full",
    "build":               "full",
    "semantic-validation": "full",
    "functional-tests":    "full",
    # Minus-antagonist — deterministic renders where threats are low-value
    "analyze":             "minus-ant",
    "review-packet":       "minus-ant",
    "document":            "minus-ant",
    "final-review":        "minus-ant",
    # Lite — purely structural / mechanical phases
    "setup":               "lite",
    "survey":              "lite",
    "deploy":              "lite",
    # UAT — validation phase (full PEP, reviewer-conflict category applies)
    "uat":                 "full",
}

# ---------------------------------------------------------------------------
# Per-phase threat category relevance map.
# Maps phase-tier → relevant threat categories for the critic to check.
# ---------------------------------------------------------------------------
_DESIGN_CATS = [
    "confidence-laundering", "coverage-phantom", "micro-domain-fragmentation",
    "silent-drop", "traceability-break", "precision-blindspot",
    "ring-depth-insufficient",
]
_BUILD_CATS = [
    "annotation-stacking", "reflection-test", "weak-evidence",
    "scope-creep", "dependency-inversion",
]
_VALIDATION_CATS = [
    "reviewer-conflict", "vacuous-pass", "missing-contract",
    "semantic-gap-suppression",
]
_UNIVERSAL_CATS = ["gate-bypass", "precheck-skip", "forced-override-abuse"]

PHASE_CATEGORIES = {
    "extraction":          _DESIGN_CATS + _UNIVERSAL_CATS,
    "graph-translate":     _DESIGN_CATS + _UNIVERSAL_CATS,
    "blueprint":           _DESIGN_CATS + _UNIVERSAL_CATS,
    "test-strategy":       _DESIGN_CATS + _UNIVERSAL_CATS,
    "plan":                _BUILD_CATS + _UNIVERSAL_CATS,
    "build":               _BUILD_CATS + _UNIVERSAL_CATS,
    "functional-tests":    _BUILD_CATS + _UNIVERSAL_CATS,
    "semantic-validation": _VALIDATION_CATS + _UNIVERSAL_CATS,
    "uat":                 _VALIDATION_CATS + _UNIVERSAL_CATS,
    "analyze":             _UNIVERSAL_CATS,
    "review-packet":       _UNIVERSAL_CATS,
    "document":            _UNIVERSAL_CATS,
    "final-review":        _UNIVERSAL_CATS,
    "setup":               [],
    "survey":              [],
    "deploy":              [],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_jsonl_last(path, n=20):
    """Read last n lines of a .jsonl file as parsed records."""
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return records[-n:]


def _manifest_path(workspace):
    return os.path.join(workspace, ".anti-legacy", "manifest.json")


def _coverage_summary(workspace):
    """Return a compact summary of coverage-report.json or None."""
    p = os.path.join(workspace, ".anti-legacy", "coverage-report.json")
    data = _read_json(p)
    if not data:
        return None
    return {
        "coverage": data.get("coverage"),
        "resolved": data.get("resolved"),
        "risk_flagged": data.get("risk_flagged"),
        "unaccounted": data.get("unaccounted"),
        "total_behavior_bearing": data.get("total_behavior_bearing"),
    }


def _graph_summary(workspace):
    """Return a compact summary of requirements_graph.json or None."""
    p = os.path.join(workspace, ".anti-legacy", "requirements", "requirements_graph.json")
    data = _read_json(p)
    if not data:
        return None
    domains = data.get("domains", {})
    total_reqs = sum(len(d.get("requirements", {})) for d in domains.values())
    n_domains = len(domains)
    # Low-confidence rule count (H1 signal)
    low_conf = 0
    placeholder = 0
    _ph = re.compile(r"REVIEW REQUIRED|TBD|\btodo\b|\bfixme\b|\bplaceholder\b", re.I)
    for d_data in domains.values():
        for req in d_data.get("requirements", {}).values():
            if req.get("status") == "unresolvable":
                continue
            for br in req.get("business_rules") or []:
                if not isinstance(br, dict):
                    continue
                try:
                    if float(br.get("confidence") or 1.0) < 0.75:
                        low_conf += 1
                except (TypeError, ValueError):
                    pass
                if _ph.search(br.get("statement", "") or ""):
                    placeholder += 1
    return {
        "n_domains": n_domains,
        "total_requirements": total_reqs,
        "avg_req_per_domain": round(total_reqs / n_domains, 2) if n_domains else 0,
        "low_confidence_rules": low_conf,
        "placeholder_rules": placeholder,
    }


def _functional_comparison_summary(workspace):
    """Return compact summary of functional_comparison_report.json or None."""
    p = os.path.join(workspace, ".anti-legacy", "evidence",
                     "functional_comparison_report.json")
    data = _read_json(p)
    if not data:
        return None
    strength = data.get("evidence_strength_per_rule") or {}
    total = len(strength)
    weak = sum(1 for v in strength.values() if str(v).lower() == "weak")
    fail_reqs = [r for r in (data.get("requirements") or [])
                 if r.get("status") == "FAIL"]
    return {
        "rule_coverage": data.get("rule_coverage"),
        "fail_count": len(fail_reqs),
        "total_rules": total,
        "weak_evidence_count": weak,
        "weak_evidence_fraction": round(weak / total, 3) if total else 0,
    }


def _uat_reserved_identities(workspace):
    """Return list of reserved identities for UAT reviewer-conflict check."""
    cfg = _read_json(os.path.join(workspace, ".anti-legacy", "config.json")) or {}
    architect = (cfg.get("roles") or {}).get("architect", "")

    manifest = _read_json(_manifest_path(workspace)) or {}
    gate1_evaluator = (manifest.get("gates", {}).get("GATE_1_DESIGN") or {}).get("evaluator", "")

    audit_signers = []
    audit_p = os.path.join(workspace, ".anti-legacy", "audit.jsonl")
    for rec in _read_jsonl_last(audit_p, 200):
        if rec.get("event") != "anti-legacy:gate-signed-off":
            continue
        d = rec.get("details") or {}
        if d.get("gate_id") == "GATE_1_DESIGN":
            ev = (d.get("evaluator") or "").strip()
            if ev and ev not in audit_signers:
                audit_signers.append(ev)

    return {
        "architect": architect,
        "gate1_evaluator": gate1_evaluator,
        "gate1_audit_signers": audit_signers,
    }


# ---------------------------------------------------------------------------
# Main context assembler
# ---------------------------------------------------------------------------

def assemble_context(phase, workspace="."):
    """Assemble the threat-surface context for a given phase.

    Returns a dict with:
      - phase, tier, applicable_categories
      - manifest_status (current_phase, gates summary)
      - phase_specific_signals (coverage, graph, functional-comparison, uat)
      - assembled_at (ISO-8601)
    """
    workspace = os.path.abspath(workspace)
    tier = PHASE_TIERS.get(phase, "full")
    categories = PHASE_CATEGORIES.get(phase, _UNIVERSAL_CATS)

    # Manifest status
    manifest = _read_json(_manifest_path(workspace))
    if manifest is None:
        return None, "manifest not found — run anti-legacy:setup first"

    manifest_status = {
        "current_phase": (manifest.get("phase") or {}).get("current"),
        "gates": {
            gid: gdata.get("status")
            for gid, gdata in (manifest.get("gates") or {}).items()
        },
    }

    # Phase-specific signals
    signals = {}
    if phase in ("extraction", "graph-translate"):
        cov = _coverage_summary(workspace)
        if cov:
            signals["coverage_report"] = cov
    if phase in ("blueprint", "plan", "build", "functional-tests",
                 "semantic-validation", "uat"):
        gs = _graph_summary(workspace)
        if gs:
            signals["requirements_graph"] = gs
    if phase == "semantic-validation":
        fc = _functional_comparison_summary(workspace)
        if fc:
            signals["functional_comparison"] = fc
    if phase == "uat":
        reserved = _uat_reserved_identities(workspace)
        signals["uat_reserved_identities"] = reserved

    return {
        "phase": phase,
        "tier": tier,
        "applicable_categories": categories,
        "manifest_status": manifest_status,
        "phase_specific_signals": signals,
        "assembled_at": datetime.now(timezone.utc).isoformat(),
    }, None


def _format_text(ctx):
    """Human-readable text block for the critic."""
    lines = [
        f"=== Antagonist Context: phase '{ctx['phase']}' (PEP tier: {ctx['tier']}) ===",
        f"Assembled: {ctx['assembled_at']}",
        "",
        "--- Pipeline state ---",
        f"  current_phase : {ctx['manifest_status']['current_phase']}",
    ]
    for gid, status in ctx["manifest_status"]["gates"].items():
        lines.append(f"  gate {gid:<28}: {status}")
    lines.append("")
    lines.append("--- Applicable threat categories ---")
    cats = ctx["applicable_categories"]
    if cats:
        for c in cats:
            lines.append(f"  {c}")
    else:
        lines.append("  (none — lite phase, only precheck / manifest checks apply)")
    lines.append("")

    signals = ctx.get("phase_specific_signals") or {}
    if signals:
        lines.append("--- Phase-specific signals ---")
        for key, val in signals.items():
            lines.append(f"  [{key}]")
            if isinstance(val, dict):
                for k, v in val.items():
                    lines.append(f"    {k}: {v}")
            else:
                lines.append(f"    {val}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="antagonist",
        description="Antagonist — pre-build threat context assembler (PEP step 3)",
    )
    sub = ap.add_subparsers(dest="cmd")

    ctx_p = sub.add_parser("context", help="Assemble threat-surface context for a phase")
    ctx_p.add_argument("--phase", required=True,
                       help="Pipeline phase to threat-model (e.g. extraction, blueprint)")
    ctx_p.add_argument("--workspace", default=".",
                       help="Workspace root (default: .)")
    ctx_p.add_argument("--json", dest="as_json", action="store_true",
                       help="Emit machine-readable JSON (default: human-readable text)")

    tier_p = sub.add_parser("tier", help="Print the PEP tier for a phase")
    tier_p.add_argument("--phase", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "tier":
        tier = PHASE_TIERS.get(args.phase, "full")
        print(f"{args.phase}: {tier}")
        return 0

    if args.cmd == "context":
        ctx, err = assemble_context(args.phase, args.workspace)
        if ctx is None:
            print(f"Error: {err}", file=sys.stderr)
            return 1
        if args.as_json:
            print(json.dumps(ctx, indent=2))
        else:
            print(_format_text(ctx))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
