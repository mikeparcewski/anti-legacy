#!/usr/bin/env python3
"""antilegacy_core.graph_validator — audits every `legacy` reference in requirements graph.

This script implements the four-pass verification for the requirements graph legacy fields:
  Pass 1: Legacy field existence and JCL step vs program type checks, duplicate detection.
  Pass 2: Content spot-checks comparing program comments/headers to requirement descriptions.
  Pass 3: Uncaptured program identification and heuristic utility/gap classification.
  Pass 4: Report generation (.md and .json) and manifest registration.
"""
import argparse
import glob as _g
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

# JCL step kinds — not valid as a legacy program reference
JCL_STEP_KINDS = {"step"}
# Behavior-bearing program kinds — valid targets
PROGRAM_KINDS = {
    "module", "function", "method", "class", "struct", "interface",
    "cics_program", "db2_table"
}

# Defaults for classify_candidate — COBOL/mainframe names.
# Override via config.json "coverage.utility_name_exact" / "coverage.utility_name_patterns".
_DEFAULT_UTILITY_EXACT = frozenset({
    "COBSWAIT", "MVSWAIT", "SLEEP", "DELAY", "WAIT",
})
_DEFAULT_UTILITY_PATTERNS = (
    r".*WAIT$", r".*SLEEP$", r"^MQ.*", r"^VSAM.*", r".*ADAPT$", r".*BRIDGE$",
    r".*GEN$", r".*UTIL$", r".*LOG$", r".*DUMP$", r"^SORT.*", r"^COPY.*",
)


def _graph_validator_settings(config, workspace="."):
    """Read utility-classification knobs. Priority chain:

      1. config.coverage.utility_name_patterns  (explicit operator override)
      2. stack-profile.json naming.utility_patterns  (discovered by stack_discovery)
      3. _DEFAULT_UTILITY_PATTERNS  (COBOL/mainframe hardcoded fallback)

    Returns a dict with compiled utility_name_exact (frozenset) and
    utility_name_patterns (list of compiled re.Pattern).
    """
    cov_cfg = config.get("coverage", {})
    exact = frozenset(s.upper() for s in cov_cfg.get("utility_name_exact", _DEFAULT_UTILITY_EXACT))

    if "utility_name_patterns" in cov_cfg:
        raw_patterns = cov_cfg["utility_name_patterns"]
    else:
        # Try the stack-profile produced by stack_discovery
        try:
            from antilegacy_core.stack_discovery import load_profile
            profile = load_profile(workspace)
            discovered = (profile or {}).get("naming", {}).get("utility_patterns", [])
        except Exception:
            discovered = []
        raw_patterns = discovered if discovered else list(_DEFAULT_UTILITY_PATTERNS)

    return {"utility_name_exact": exact, "utility_name_patterns": [re.compile(p) for p in raw_patterns]}


def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _bootstrap_libs(workspace):
    # Find antilegacy_core library dir and add to sys.path
    lib_paths = _g.glob(os.path.join(workspace, "**/antilegacy_core"), recursive=True)
    for p in lib_paths:
        parent = os.path.dirname(p)
        if os.path.isdir(p) and parent not in sys.path:
            sys.path.insert(0, parent)
            break


def _extract_pgm_from_jcl(file_path):
    if not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # Find all EXEC PGM=... matches
        matches = re.findall(r"EXEC\s+PGM=([A-Za-z0-9#@$]+)", content, re.IGNORECASE)
        if len(matches) == 1:
            return matches[0]
        unique_matches = list(set(matches))
        if len(unique_matches) == 1:
            return unique_matches[0]
    except Exception:
        pass
    return None


def check_content_mismatch(workspace, matches, req_desc):
    for m in matches:
        if m["kind"] in ("module", "function", "method", "class", "cics_program"):
            file_path = os.path.join(workspace, m.get("file", ""))
            if not os.path.isfile(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [f.readline() for _ in range(50)]
                content = "".join(lines).upper()
                req_desc_upper = req_desc.upper()
                
                # Check for "consolidat", "update", "write", "post", "apply" vs "print", "report"
                update_keywords = ["WRITE", "REWRITE", "UPDATE", "INSERT", "DELETE"]
                read_only_keywords = ["PRINT", "REPORT", "LIST", "DISPLAY", "SHOW", "WRITES A REPORT", "PRINTS A REPORT"]
                
                has_any_update = any(kw in content for kw in update_keywords)
                has_any_read_only = any(kw in content for kw in read_only_keywords)
                
                req_has_update = any(kw in req_desc_upper for kw in ["CONSOLIDAT", "UPDATE", "WRITE", "POST", "APPLY"])
                
                if req_has_update and has_any_read_only and not has_any_update:
                    return {
                        "type": "CONTENT_MISMATCH",
                        "program_says": "Looks like a read-only report/print utility (contains print/report keywords, lacks DB update keywords)",
                        "requirement_says": req_desc,
                        "msg": f"Requirement description implies an update/calculation ({req_desc}), but the program header/source implies it is a read-only report/print utility."
                    }
            except Exception:
                pass
    return None


def classify_candidate(node, workspace, settings=None):
    name = node["name"]
    kind = node["kind"]
    file_path = os.path.join(workspace, node.get("file", ""))

    if settings is None:
        settings = _graph_validator_settings({})

    exact = settings["utility_name_exact"]
    patterns = settings["utility_name_patterns"]

    # 1. Check wait/sleep utilities by exact name or configurable pattern
    if name.upper() in exact:
        return "UTILITY_OMIT", "Pure sleep/wait utility"

    if any(p.match(name.upper()) for p in patterns):
        return "UTILITY_OMIT", "Transport or adapter utility matching pattern"
    
    # 2. Check kind
    if kind == "cics_program":
        return "NEEDS_REQUIREMENT", "CICS online program (always behavior-bearing)"
    if kind == "db2_table":
        return "NEEDS_REQUIREMENT", "DB2 database table representing domain entity"
        
    # 3. Read source file to find sleep calls only
    if os.path.isfile(file_path):
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                source = f.read().upper()
            if "CALL 'MVSWAIT'" in source or "CALL 'SLEEP'" in source:
                if len(source) < 2000 and "MVSWAIT" in source:
                    return "UTILITY_OMIT", "Sleep/wait utility (CALL MVSWAIT)"
        except Exception:
            pass
            
    return "NEEDS_REQUIREMENT", "Behavior-bearing program node containing logic"


def load_existing_classifications(workspace):
    p = os.path.join(workspace, ".anti-legacy", "validation", "al_pass3.json")
    data = _read_json(p)
    if isinstance(data, dict) and "classifications" in data:
        return data["classifications"]
    if isinstance(data, list):
        return data
    return []


def run_validation(workspace, app_filter=None, auto_fix=False):
    # Verify prerequisites
    manifest_path = os.path.join(workspace, ".anti-legacy", "manifest.json")
    m = _read_json(manifest_path)
    if not m:
        return None, "manifest.json not found. Run setup and survey first."
        
    completed = m.get("phase", {}).get("completed", [])
    if "extraction" not in completed and "graph-translate" not in completed:
        return None, "extraction phase not complete. Run anti-legacy:extraction first."
        
    rg_path = os.path.join(workspace, ".anti-legacy", "requirements", "requirements_graph.json")
    rg = _read_json(rg_path)
    if not rg:
        return None, "requirements_graph.json not found."
        
    # Load config and coverage settings
    config_path = os.path.join(workspace, ".anti-legacy", "config.json")
    config = _read_json(config_path)
    if not config:
        return None, "config.json not found."
        
    _bootstrap_libs(workspace)
    from antilegacy_core import wicked_estate as we, coverage as cov
    
    settings = cov.coverage_settings(config)
    gv_settings = _graph_validator_settings(config, workspace)
    os.makedirs(os.path.join(workspace, ".anti-legacy", "validation"), exist_ok=True)
    
    # Step 2: Build the source program inventory
    inventory = {}
    apps_list = config.get("source_apps", [])
    if app_filter:
        apps_list = [a for a in apps_list if a["name"] == app_filter]
        
    for app in apps_list:
        app_name = app["name"]
        db_path = os.path.join(workspace, ".anti-legacy", "graphs", f"{app_name}.db")
        if not os.path.exists(db_path):
            print(f"WARNING: DB not found for {app_name}: {db_path}", file=sys.stderr)
            continue
            
        nodes = we.list_nodes(db_path)
        for node in nodes:
            key = node["name"].upper()
            entry = {
                "app": app_name,
                "name": node["name"],
                "kind": cov.normalize_kind(node.get("kind", "")),
                "file": node.get("file", ""),
                "symbol_id": node.get("symbol_id", ""),
                "is_behavior_bearing": cov.is_behavior_bearing(node, settings),
            }
            inventory.setdefault(key, []).append(entry)
            
    _write_json(os.path.join(workspace, ".anti-legacy", "validation", "al_inventory.json"), inventory)
    
    # Step 3: Pass 1 — Legacy field check
    errors = []
    warnings = []
    fixed_requirements = {}
    
    # Get all active requirements from the domains structure
    reqs_dict = {}
    for domain_name, domain in rg.get("domains", {}).items():
        for req_id, req in domain.get("requirements", {}).items():
            reqs_dict[req_id] = (domain_name, req)
            
    for req_id, (domain_name, req) in reqs_dict.items():
        if req.get("status", "active") == "inactive":
            continue
            
        legacy = req.get("legacy", "").strip()
        if not legacy:
            errors.append({
                "req_id": req_id,
                "type": "NO_LEGACY",
                "msg": "No legacy field — every active requirement must trace to a source program"
            })
            continue
            
        key = legacy.upper()
        matches = inventory.get(key, [])
        
        if not matches:
            warnings.append({
                "req_id": req_id,
                "type": "NOT_FOUND",
                "legacy": legacy,
                "msg": f"{legacy} is not in the wicked-estate graph"
            })
            continue
            
        program_nodes = [m for m in matches if (m["kind"] in PROGRAM_KINDS or m["is_behavior_bearing"]) and m["kind"] not in JCL_STEP_KINDS]
        jcl_nodes = [m for m in matches if m["kind"] in JCL_STEP_KINDS]
        
        if not program_nodes and jcl_nodes:
            # We have only JCL step nodes matching this legacy field
            jcl_node = jcl_nodes[0]
            jcl_file = os.path.join(workspace, jcl_node["file"])
            pgm_name = _extract_pgm_from_jcl(jcl_file)
            
            if auto_fix and pgm_name and pgm_name.upper() in inventory:
                # Apply auto-fix directly
                req["legacy"] = pgm_name
                fixed_requirements[req_id] = (legacy, pgm_name)
                print(f"Auto-fixed {req_id}: Remapped JCL step '{legacy}' -> COBOL program '{pgm_name}'")
            else:
                errors.append({
                    "req_id": req_id,
                    "type": "JCL_NOT_PROGRAM",
                    "legacy": legacy,
                    "jcl_files": [m["file"] for m in jcl_nodes],
                    "msg": f"{legacy} is a JCL step (kind=step), not a program. EXEC PGM target program name should be used instead."
                })
                
    # If auto-fix was applied and succeeded, rewrite requirements_graph.json
    if fixed_requirements:
        _write_json(rg_path, rg)
        
    # Duplicate legacy reference check (active requirements only)
    legacy_map = defaultdict(list)
    for req_id, (domain_name, req) in reqs_dict.items():
        if req.get("status", "active") == "inactive":
            continue
        leg = req.get("legacy", "").strip().upper()
        if leg:
            legacy_map[leg].append(req_id)
            
    for leg, reqs in legacy_map.items():
        if len(reqs) > 1:
            errors.append({
                "type": "DUPLICATE_LEGACY",
                "legacy": leg,
                "requirements": reqs,
                "msg": f"{leg} is referenced by multiple active requirements: {reqs}"
            })
            
    _write_json(os.path.join(workspace, ".anti-legacy", "validation", "al_pass1.json"), {
        "errors": errors,
        "warnings": warnings
    })
    
    # Step 4: Pass 2 — Program content spot check
    pass2_errors = []
    for req_id, (domain_name, req) in reqs_dict.items():
        if req.get("status", "active") == "inactive":
            continue
        legacy = req.get("legacy", "").strip()
        if not legacy:
            continue
        matches = inventory.get(legacy.upper(), [])
        mismatch = check_content_mismatch(workspace, matches, req.get("description", ""))
        if mismatch:
            mismatch["req_id"] = req_id
            mismatch["legacy"] = legacy
            pass2_errors.append(mismatch)
            
    _write_json(os.path.join(workspace, ".anti-legacy", "validation", "al_pass2.json"), {
        "pass1": {"errors": errors, "warnings": warnings},
        "pass2_errors": pass2_errors
    })
    
    # Step 5: Pass 3 — Uncaptured programs check
    covered = set()
    for domain_name, domain in rg.get("domains", {}).items():
        for req_id, req in domain.get("requirements", {}).items():
            if req.get("status", "active") != "inactive" and req.get("legacy"):
                covered.add(req["legacy"].strip().upper())
                
    # Load manual classifications
    existing_classifications = load_existing_classifications(workspace)
    existing_map = {c["name"].upper(): c for c in existing_classifications}
    
    candidates = []
    classifications = []
    
    for name_upper, entries in inventory.items():
        if name_upper in covered:
            continue
        behavior_entries = [e for e in entries if e["is_behavior_bearing"] and e["kind"] not in JCL_STEP_KINDS]
        if not behavior_entries:
            continue
            
        candidate_node = behavior_entries[0]
        candidates.append({
            "name": candidate_node["name"],
            "kind": candidate_node["kind"],
            "file": candidate_node["file"],
            "app": candidate_node["app"],
            "symbol_id": candidate_node["symbol_id"]
        })
        
        # Check if already classified in existing json
        if name_upper in existing_map:
            classifications.append(existing_map[name_upper])
        else:
            cls, rationale = classify_candidate(candidate_node, workspace, gv_settings)
            classifications.append({
                "name": candidate_node["name"],
                "kind": candidate_node["kind"],
                "file": candidate_node["file"],
                "classification": cls,
                "rationale": rationale,
                "suggested_req_id": None,
                "suggested_domain": None,
                "suggested_description": None
            })
            
    _write_json(os.path.join(workspace, ".anti-legacy", "validation", "al_pass3_candidates.json"), {
        "uncaptured": candidates
    })
    _write_json(os.path.join(workspace, ".anti-legacy", "validation", "al_pass3.json"), {
        "classifications": classifications
    })
    
    # Step 6: Write the validation report
    gaps = [c for c in classifications if c["classification"] == "NEEDS_REQUIREMENT"]
    omissions = [c for c in classifications if c["classification"] == "UTILITY_OMIT"]
    
    all_errors = errors + pass2_errors
    
    report_status = "CLEAN"
    if any(e["type"] in ("JCL_NOT_PROGRAM", "DUPLICATE_LEGACY", "CONTENT_MISMATCH", "NO_LEGACY") for e in all_errors):
        report_status = "BLOCKED"
    elif gaps:
        report_status = "GAPS"
        
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": report_status,
        "summary": {
            "errors": len(all_errors),
            "gaps": len(gaps),
            "omissions": len(omissions),
            "warnings": len(warnings)
        },
        "errors": all_errors,
        "gaps": gaps,
        "omissions": omissions,
        "warnings": warnings
    }
    
    report_json_path = os.path.join(workspace, ".anti-legacy", "requirements", "graph-validation-report.json")
    _write_json(report_json_path, report)
    
    # Write human-readable markdown report
    md_lines = [
        "# Requirements Graph Validation Report",
        "",
        f"**Status**: `{report_status}`",
        f"**Generated**: {report['generated_at']}",
        "",
        "## Summary",
        f"- Errors (blocking): {report['summary']['errors']}",
        f"- Gaps (uncaptured programs): {report['summary']['gaps']}",
        f"- Correctly Omitted Utility Programs: {report['summary']['omissions']}",
        f"- Warnings: {report['summary']['warnings']}",
        ""
    ]
    
    if all_errors:
        md_lines.append("## Errors")
        for e in all_errors:
            md_lines.append(f"- **{e.get('req_id', e.get('legacy', 'Duplicate check'))}** [{e.get('type')}]: {e.get('msg')}")
        md_lines.append("")
        
    if gaps:
        md_lines.append("## Gaps (Programs with no Requirement — human disposition required)")
        md_lines.append("| Program | Kind | File | Suggested Rationale |")
        md_lines.append("|---|---|---|---|")
        for g in gaps:
            md_lines.append(f"| {g['name']} | {g['kind']} | {g['file']} | {g['rationale']} |")
        md_lines.append("")
        
    if omissions:
        md_lines.append("## Correctly Omitted Programs")
        md_lines.append("| Program | Kind | File | Omission Rationale |")
        md_lines.append("|---|---|---|---|")
        for o in omissions:
            md_lines.append(f"| {o['name']} | {o['kind']} | {o['file']} | {o['rationale']} |")
        md_lines.append("")
        
    if warnings:
        md_lines.append("## Warnings")
        for w in warnings:
            md_lines.append(f"- **{w.get('req_id')}** [{w.get('type')}]: {w.get('msg')}")
        md_lines.append("")
        
    report_md_path = os.path.join(workspace, ".anti-legacy", "requirements", "graph-validation-report.md")
    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
        
    return report, None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="graph_validator",
        description="Graph Validator — audits every legacy reference in requirements graph"
    )
    ap.add_argument("--app", help="Restrict validation to a single source app name")
    ap.add_argument("--auto-fix", action="store_true", help="Apply non-ambiguous JCL remappings")
    ap.add_argument("--workspace", default=".", help="Workspace root (default: .)")
    
    args = ap.parse_args(argv)
    
    report, err = run_validation(args.workspace, args.app, args.auto_fix)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
        
    status = report["status"]
    summary = report["summary"]
    
    # Done-gate prints and exit codes
    if status == "BLOCKED":
        print(f"BLOCKED: {summary['errors']} error(s) in requirements graph. Fix all errors before blueprint.", file=sys.stderr)
        for e in report["errors"]:
            print(f"  [{e.get('type')}] {e.get('req_id', e.get('legacy', ''))}: {e.get('msg')}", file=sys.stderr)
        exit_code = 1
    elif status == "GAPS":
        print(f"WARNING: {summary['gaps']} uncaptured program(s) with business logic found. Review graph-validation-report.md.")
        exit_code = 0
    else:
        print(f"Graph validation: CLEAN — no errors, no gaps, {summary['omissions']} programs correctly omitted.")
        exit_code = 0
        
    # Step 8: Register artifacts
    try:
        reg_status = "final" if status == "CLEAN" else "draft"
        subprocess.run([
            sys.executable, ".anti-legacy/run.py", "manifest", "register", "graph-validation-report",
            "--path", "requirements/graph-validation-report.json",
            "--format", "json",
            "--produced-by", "anti-legacy:graph-validator",
            "--status", reg_status,
            "--depends-on", "requirements-graph"
        ], cwd=args.workspace, check=True)
        
        subprocess.run([
            sys.executable, ".anti-legacy/run.py", "manifest", "register", "graph-validation-md",
            "--path", "requirements/graph-validation-report.md",
            "--format", "markdown",
            "--produced-by", "anti-legacy:graph-validator",
            "--status", reg_status,
            "--depends-on", "requirements-graph"
        ], cwd=args.workspace, check=True)
        
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to run manifest registration or telemetry: {e}", file=sys.stderr)
        
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
