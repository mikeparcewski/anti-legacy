#!/usr/bin/env python3
"""antilegacy_core.precheck — execution-time readiness gate.

`GATING_REVIEW.md` ROOT A: a producer must consult a gate that can REFUSE — not merely
check that it wrote a non-empty file. ROOT B: detect when manifest/derived state has
desynced from disk reality (a derived `requirements_graph.json` that outlived its
gitignored `legacy-graph` evidence). This module answers **"is phase X ready to run?"**
by probing four categories:

  gate         — the required upstream gate(s) are `passed`/`waived`
  artifact     — the required input artifact(s) are registered, present, checksum-verified
  completeness — per-phase machine-checkable C1/C2 predicates (e.g. every active rule
                 carries a numeric confidence; resolved-or-flagged coverage == 1.0)
  reconcile    — a required artifact is not orphaned/stale vs its declared `depends_on`
                 sources (the ROOT B probe — derived artifact outliving its source)

READ-ONLY: never writes audit.jsonl, never advances a phase, never clears a gate.

CLI:   python3 .anti-legacy/run.py precheck <phase> [--advisory] [--json] [--strict]
         exit 0 = ready · 1 = NOT ready (blocking) · 2 = bad arg.
         --advisory : always exit 0 and just report (the engine-scan "exit-code flips by
                      caller" pattern — reporting vs gating).
         --json     : machine-readable {phase, ready, probes:[...]}.
         --strict   : ISS-22 — treat an UNLISTED phase (one with no PHASE_READINESS
                      profile) as a hard BLOCK instead of a warn-pass, so a new
                      producer cannot silently skip readiness gating. Default is
                      lenient (backward-compatible); also enabled by the
                      PRECHECK_STRICT env var (--strict wins when both are present).

In-process:
  check(phase, strict=None) -> (ready: bool, probes: list[dict])
  require_ready(phase, force=False, strict=None) -> None  (prints blockers + sys.exit(1) unless force)
    strict=None consults the PRECHECK_STRICT env var; pass strict=True/False to force the mode.

Pure standard library + antilegacy_core.manifest helpers. Cross-platform (os.path).
"""
import argparse
import json
import os
import sys

from antilegacy_core import manifest as mf

WS = ".anti-legacy"
RESOLVE_THRESHOLD_DEFAULT = 0.75

# ISS-22: strict mode turns the unlisted-phase catch-all from a warn-pass into a
# hard BLOCK, so a new producer with no PHASE_READINESS profile cannot silently
# skip readiness gating. Opt-in (default lenient/backward-compatible): set the
# env var PRECHECK_STRICT to a truthy value, pass strict=True in-process, or use
# the --strict CLI flag.
_STRICT_ENV = "PRECHECK_STRICT"


def _strict_from_env():
    """True iff PRECHECK_STRICT is set to a truthy value (1/true/yes/on, any case)."""
    return os.environ.get(_STRICT_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# cwd-anchored loaders (the workspace is the CWD — never __file__).
# --------------------------------------------------------------------------- #
def _ws(*parts):
    return os.path.join(os.getcwd(), WS, *parts)


def _load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _probe(pid, category, ok, severity, detail, fix=""):
    return {"id": pid, "category": category, "ok": bool(ok),
            "severity": severity, "detail": detail, "fix": fix}


def _iter_active_requirements(graph):
    """Yield (domain, req_id, node) for active (not dropped / not unresolvable) requirements."""
    for domain, ddata in ((graph or {}).get("domains") or {}).items():
        for req_id, node in (ddata.get("requirements") or {}).items():
            if node.get("disposition") == "drop":
                continue
            if node.get("status") == "unresolvable":
                continue
            yield domain, req_id, node


def _resolve_threshold(config):
    cov = (config or {}).get("coverage") or {}
    t = cov.get("resolve_threshold")
    return float(t) if isinstance(t, (int, float)) else RESOLVE_THRESHOLD_DEFAULT


# --------------------------------------------------------------------------- #
# Shared probe helpers (reuse manifest's own integrity predicate).
# --------------------------------------------------------------------------- #
def _gate_probe(manifest, gate_id):
    status = ((manifest.get("gates") or {}).get(gate_id) or {}).get("status")
    ok = status in mf._SATISFIED_GATE_STATUSES
    return _probe("gate:%s" % gate_id, "gate", ok, "block",
                  "%s is '%s' (need passed/waived)." % (gate_id, status or "pending"),
                  "Clear it: run.py manifest gate %s --opinion passed --evaluator <name> --evidence <ids>" % gate_id)


def _artifact_verify(manifest, art_id):
    """Return (ok, detail) — registered AND file present AND checksum matches (if recorded)."""
    art = (manifest.get("artifacts") or {}).get(art_id)
    if not art:
        return False, "not registered in the manifest"
    full = mf._artifact_full_path(art)
    if not os.path.exists(full) or os.path.isdir(full):
        return False, "registered but file missing on disk (%s)" % art.get("path")
    if "checksum" in art:
        if mf.file_checksum(full) != art["checksum"]:
            return False, "checksum drift — file changed since registration (%s)" % art.get("path")
    return True, "present + verified"


def _artifact_probe(manifest, art_id):
    ok, detail = _artifact_verify(manifest, art_id)
    return _probe("artifact:%s" % art_id, "artifact", ok, "block",
                  "%s: %s" % (art_id, detail),
                  "Produce/re-register %s (run its producing phase)." % art_id)


def _reconcile_probe(manifest, art_id):
    """ROOT B: a present artifact whose declared depends_on source is missing/stale is ORPHANED."""
    art = (manifest.get("artifacts") or {}).get(art_id)
    if not art:
        return None  # absence is caught by the artifact probe, not here
    bad = []
    for dep in (art.get("depends_on") or []):
        ok, detail = _artifact_verify(manifest, dep)
        if not ok:
            bad.append("%s (%s)" % (dep, detail))
    ok = not bad
    return _probe("reconcile:%s" % art_id, "reconcile", ok, "block",
                  "%s is orphaned/stale — its source(s) %s" % (art_id, "; ".join(bad)) if bad
                  else "%s sources present + verified" % art_id,
                  "Re-run the phase that produces the missing/changed source, then rebuild %s." % art_id)


def _confidence_probe(graph, config):
    """C2: every active requirement's business_rules must carry a numeric confidence."""
    threshold = _resolve_threshold(config)
    missing, no_rules = [], []
    for _domain, req_id, node in _iter_active_requirements(graph):
        rules = node.get("business_rules") or []
        if not rules:
            no_rules.append(req_id)
            continue
        for rule in rules:
            if not isinstance(rule.get("confidence"), (int, float)):
                missing.append("%s/%s" % (req_id, rule.get("id", "RULE-?")))
    probes = []
    probes.append(_probe(
        "completeness:rule-confidence", "completeness", not missing, "block",
        ("%d rule(s) lack a numeric confidence and would silently escape coverage/risk "
         "scoring: %s" % (len(missing), ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "")))
        if missing else "every active rule carries a numeric confidence (threshold %.2f)" % threshold,
        "Re-run extraction on those nodes to record a confidence, or RISK-flag them."))
    probes.append(_probe(
        "completeness:rules-present", "completeness", not no_rules, "block",
        ("%d active requirement(s) have NO business_rules: %s"
         % (len(no_rules), ", ".join(no_rules[:8]))) if no_rules
        else "every active requirement carries ≥1 business rule",
        "Extract the rule(s) or mark the requirement unresolvable with a reason."))
    return probes


def _coverage_probe():
    """If a coverage-report exists, resolved-or-flagged coverage must be complete."""
    cov = _load_json(_ws("coverage-report.json"))
    if cov is None:
        return _probe("completeness:coverage", "completeness", True, "warn",
                      "coverage-report.json absent — resolved-or-flagged coverage not verified.",
                      "Run extraction → run.py coverage to compute the terminal.")
    val = cov.get("coverage")
    ok = isinstance(val, (int, float)) and float(val) >= 1.0
    return _probe("completeness:coverage", "completeness", ok, "block",
                  "resolved-or-flagged coverage = %s (need 1.0); %s node(s) unaccounted."
                  % (val, cov.get("unaccounted", "?")),
                  "Resolve or RISK-flag the unaccounted nodes (run.py coverage lists them).")


def _shallow_extraction_probe(graph):
    """Advisory (warn): a behavior requirement with zero validations AND zero error_paths."""
    thin = [req_id for _d, req_id, node in _iter_active_requirements(graph)
            if not (node.get("validations") or []) and not (node.get("error_paths") or [])]
    return _probe("completeness:extraction-depth", "completeness", not thin, "warn",
                  ("%d requirement(s) have no validations AND no error_paths — extraction may be "
                   "shallow (ring[0]): %s" % (len(thin), ", ".join(thin[:8]))) if thin
                  else "requirements carry validation/error-path behavior",
                  "Crawl deeper (ring≥1) to capture exception/validation behavior, if the source has it.")


# --------------------------------------------------------------------------- #
# Per-phase readiness registry. Phases not listed get the generic fallback:
# a warn-pass by default (lenient), or a hard BLOCK under strict mode (ISS-22 —
# `--strict` / PRECHECK_STRICT / check(strict=True)), so a new producer with no
# profile cannot silently skip readiness gating.
# --------------------------------------------------------------------------- #
def _graph_completeness_probes(manifest):
    graph = _load_json(_ws("requirements", "requirements_graph.json"))
    config = _load_json(_ws("config.json"))
    if graph is None:
        return [_probe("completeness:graph", "completeness", False, "block",
                       "requirements_graph.json is absent or unreadable.",
                       "Run anti-legacy:graph-translator to produce the requirements graph.")]
    active = list(_iter_active_requirements(graph))
    probes = [_probe("completeness:graph", "completeness", bool(active), "block",
                     "requirements graph has %d active requirement(s)" % len(active),
                     "Produce a non-empty requirements graph first.")]
    probes.extend(_confidence_probe(graph, config))
    probes.append(_coverage_probe())
    probes.append(_shallow_extraction_probe(graph))
    return probes


# registry: phase -> {gates: [...], artifacts: [...], completeness: callable(manifest)->[probes]}
PHASE_READINESS = {
    "deliverables": {
        "gates": [],
        "artifacts": ["requirements-graph"],
        "completeness": _graph_completeness_probes,
    },
    "blueprint": {
        "gates": [],
        "artifacts": ["requirements-graph"],
        "completeness": _graph_completeness_probes,
    },
    "graph-translate": {
        "gates": [],
        "artifacts": ["legacy-graph"],
        "completeness": lambda m: [_coverage_probe()],
    },
    "document": {
        "gates": ["GATE_4_UAT"],
        "artifacts": ["requirements-graph", "blueprint-json"],
        "completeness": lambda m: [],
    },
}


def check(phase, strict=None):
    """Return (ready, probes). `ready` is False iff any block-severity probe failed.

    strict (ISS-22): when an unlisted phase (no PHASE_READINESS profile) is checked,
    the catch-all is a warn-pass by default (lenient, backward-compatible). In strict
    mode it becomes a hard BLOCK so a new producer cannot silently skip readiness
    gating. `strict=None` (default) falls back to the PRECHECK_STRICT env var; pass
    strict=True/False to force the mode in-process.
    """
    if strict is None:
        strict = _strict_from_env()
    if not os.path.isfile(_ws("manifest.json")):
        return False, [_probe("manifest", "artifact", False, "block",
                              "no .anti-legacy/manifest.json — workspace not initialized.",
                              "Run anti-legacy:setup first.")]
    manifest = mf.load_manifest()
    spec = PHASE_READINESS.get(phase)
    probes = []
    if spec is None:
        # Unlisted phase. Lenient (default): warn-pass — only basic checks apply.
        # Strict (ISS-22): BLOCK — refuse to run a producer with no readiness profile.
        if strict:
            probes.append(_probe("phase:%s" % phase, "artifact", False, "block",
                                 "STRICT: phase '%s' has no PHASE_READINESS profile — "
                                 "refusing to run an ungated producer." % phase,
                                 "Add a PHASE_READINESS entry for '%s' in precheck.py "
                                 "(or drop --strict / unset PRECHECK_STRICT to allow it)." % phase))
        else:
            probes.append(_probe("phase:%s" % phase, "artifact", True, "warn",
                                 "no readiness profile for phase '%s' — only basic checks apply." % phase,
                                 "Add a PHASE_READINESS entry for '%s' in precheck.py." % phase))
        spec = {"gates": [], "artifacts": [], "completeness": lambda m: []}
    for gid in spec.get("gates", []):
        probes.append(_gate_probe(manifest, gid))
    for aid in spec.get("artifacts", []):
        probes.append(_artifact_probe(manifest, aid))
        rec = _reconcile_probe(manifest, aid)
        if rec is not None:
            probes.append(rec)
    probes.extend(spec.get("completeness", lambda m: [])(manifest))
    ready = not any((not p["ok"]) and p["severity"] == "block" for p in probes)
    return ready, probes


def require_ready(phase, force=False, strict=None):
    """Producer-side gate: refuse to proceed unless `phase` is ready (ROOT A). Lazy-import safe.

    strict (ISS-22): forwarded to check() — when True, an unlisted phase (no
    PHASE_READINESS profile) BLOCKS instead of warn-passing. `strict=None` (default)
    consults the PRECHECK_STRICT env var, so a producer stays backward-compatible
    unless strictness is explicitly opted in.
    """
    ready, probes = check(phase, strict=strict)
    for w in [p for p in probes if not p["ok"] and p["severity"] == "warn"]:
        sys.stderr.write("precheck WARN [%s]: %s\n" % (w["id"], w["detail"]))
    blockers = [p for p in probes if not p["ok"] and p["severity"] == "block"]
    if blockers and not force:
        sys.stderr.write("precheck BLOCKED phase '%s' (%d blocker(s)):\n" % (phase, len(blockers)))
        for b in blockers:
            sys.stderr.write("  - [%s] %s\n    fix: %s\n" % (b["id"], b["detail"], b["fix"]))
        sys.stderr.write("Resolve the above, or pass --force to override (NOT recommended).\n")
        sys.exit(1)
    if blockers and force:
        sys.stderr.write("precheck: %d blocker(s) OVERRIDDEN by --force for phase '%s'.\n"
                         % (len(blockers), phase))


def _print_report(phase, ready, probes):
    blockers = [p for p in probes if not p["ok"] and p["severity"] == "block"]
    warns = [p for p in probes if not p["ok"] and p["severity"] == "warn"]
    for p in probes:
        mark = "ok " if p["ok"] else ("!! " if p["severity"] == "block" else "~  ")
        print("  [%s] %s — %s" % (mark, p["id"], p["detail"]))
    for b in blockers:
        print("    fix: %s" % b["fix"], file=sys.stderr)
    if ready:
        print("READY: phase '%s' (%d checks, %d advisory)" % (phase, len(probes), len(warns)))
    else:
        print("NOT READY: phase '%s' — %d blocker(s), %d advisory" % (phase, len(blockers), len(warns)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="precheck",
                                 description="Execution-time readiness gate for a pipeline phase.")
    ap.add_argument("phase", help="the phase/skill to check (e.g. deliverables, document, blueprint)")
    ap.add_argument("--advisory", action="store_true",
                    help="always exit 0 (report only) instead of gating")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON report")
    ap.add_argument("--strict", action="store_true",
                    help="ISS-22: treat an unlisted phase (no PHASE_READINESS profile) as a "
                         "hard BLOCK instead of a warn-pass — refuse to run an ungated producer "
                         "(also enabled by the PRECHECK_STRICT env var). Default: lenient.")
    args = ap.parse_args(argv)

    # --strict OR the env var enables strict mode (CLI flag wins when set).
    strict = True if args.strict else _strict_from_env()
    ready, probes = check(args.phase, strict=strict)
    if args.json:
        json.dump({"phase": args.phase, "ready": ready, "probes": probes}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_report(args.phase, ready, probes)
    sys.exit(0 if (ready or args.advisory) else 1)


if __name__ == "__main__":
    main()
