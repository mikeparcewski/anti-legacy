#!/usr/bin/env python3
"""
UAT Crew CLI — batch dispatch helper for anti-legacy:uat-crew.

The uat-crew SKILL describes dispatching parallel UAT reviewer subagents —
one per requirement. At scale (hundreds of requirements) that requires
structured pre/post bookkeeping that the skill prose cannot provide as a
script. This CLI handles that bookkeeping so operators do not need to write
custom automation.

Subcommands
-----------
assemble   Read all contracts/{domain}/*.contract.json, emit a dispatch
           manifest listing every requirement → (domain, contract_path,
           target_component). Operators iterate the manifest to dispatch
           uat-reviewer subagents one job at a time (or in parallel batches).

collect    Walk evidence/uat/ and aggregate all *-verdict.json files into
           a single uat-dispatch-report.json with coverage statistics and a
           top-level status (PASS when all verdicts are PASS; FAIL otherwise).

status     Print a human-readable progress table: total requirements,
           verdicts collected, outstanding, PASS/FAIL breakdown.

Usage (via run.py dispatcher)
------------------------------
  python3 .anti-legacy/run.py uat_crew assemble [--workspace .]
  python3 .anti-legacy/run.py uat_crew collect  [--workspace .]
  python3 .anti-legacy/run.py uat_crew status   [--workspace .]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone


def _load_requirements_graph(workspace):
    path = os.path.join(workspace, ".anti-legacy", "requirements", "requirements_graph.json")
    if not os.path.exists(path):
        return None, path
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def _contracts_dir(workspace):
    return os.path.join(workspace, ".anti-legacy", "contracts")


def _uat_evidence_dir(workspace):
    return os.path.join(workspace, ".anti-legacy", "evidence", "uat")


def cmd_assemble(args):
    """Build a flat dispatch manifest from contracts + requirements graph."""
    workspace = os.path.abspath(args.workspace)
    rg, rg_path = _load_requirements_graph(workspace)
    if rg is None:
        print(f"Error: requirements_graph.json not found at {rg_path}", file=sys.stderr)
        return 1

    contracts_root = _contracts_dir(workspace)
    jobs = []

    for domain, d_data in rg.get("domains", {}).items():
        reqs = d_data.get("requirements", {})
        for req_id, req in reqs.items():
            if req.get("status") == "unresolvable":
                continue
            contract_path = os.path.join(contracts_root, domain, f"{req_id}.contract.json")
            target_component = req.get("target_component") or req.get("component", "")
            jobs.append({
                "req_id": req_id,
                "domain": domain,
                "contract_path": contract_path,
                "contract_exists": os.path.exists(contract_path),
                "target_component": target_component,
                "verdict_path": os.path.join(
                    _uat_evidence_dir(workspace), f"{req_id}-verdict.json"
                ),
                "verdict_exists": os.path.exists(
                    os.path.join(_uat_evidence_dir(workspace), f"{req_id}-verdict.json")
                ),
            })

    missing_contracts = [j for j in jobs if not j["contract_exists"]]
    if missing_contracts and not args.allow_missing_contracts:
        print(
            f"Error: {len(missing_contracts)} requirement(s) have no contract file. "
            "Run anti-legacy:test-strategy first, or pass --allow-missing-contracts to "
            "include them anyway.",
            file=sys.stderr,
        )
        for j in missing_contracts[:10]:
            print(f"  missing: {j['contract_path']}", file=sys.stderr)
        if len(missing_contracts) > 10:
            print(f"  ...and {len(missing_contracts) - 10} more", file=sys.stderr)
        return 1

    out_path = os.path.join(workspace, ".anti-legacy", "evidence", "uat-dispatch-manifest.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": workspace,
        "total_jobs": len(jobs),
        "jobs": jobs,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"UAT dispatch manifest written: {out_path}")
    print(f"  total requirements : {len(jobs)}")
    print(f"  contracts present  : {sum(1 for j in jobs if j['contract_exists'])}")
    print(f"  verdicts present   : {sum(1 for j in jobs if j['verdict_exists'])}")
    print()
    print("Next: dispatch anti-legacy:uat-reviewer for each job in the manifest.")
    print("  Each reviewer writes evidence/uat/{req_id}-verdict.json.")
    print("  When all verdicts are collected, run: run.py uat_crew collect")
    return 0


def cmd_collect(args):
    """Aggregate all verdict files into uat-dispatch-report.json."""
    workspace = os.path.abspath(args.workspace)
    uat_dir = _uat_evidence_dir(workspace)

    if not os.path.exists(uat_dir):
        print(f"Error: UAT evidence directory not found: {uat_dir}", file=sys.stderr)
        return 1

    verdict_files = sorted(f for f in os.listdir(uat_dir) if f.endswith("-verdict.json"))
    if not verdict_files:
        print("Error: No *-verdict.json files found in evidence/uat/", file=sys.stderr)
        return 1

    verdicts = []
    errors = []
    pass_count = 0
    fail_count = 0

    for vf in verdict_files:
        vpath = os.path.join(uat_dir, vf)
        try:
            with open(vpath, encoding="utf-8") as f:
                ev = json.load(f)
        except Exception as e:
            errors.append(f"{vf}: parse error — {e}")
            continue
        v = (ev.get("verdict") or ev.get("status") or "").upper()
        verdicts.append({
            "file": vf,
            "req_id": ev.get("req_id", vf.replace("-verdict.json", "")),
            "domain": ev.get("domain", ""),
            "verdict": v,
            "findings_count": len(ev.get("findings", []) or []),
            "has_rationale": bool(ev.get("overall_rationale")),
        })
        if v == "PASS":
            pass_count += 1
        else:
            fail_count += 1

    status = "PASS" if fail_count == 0 and not errors else "FAIL"
    report = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(verdicts),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "parse_errors": errors,
        "verdicts": verdicts,
    }

    out_path = os.path.join(workspace, ".anti-legacy", "evidence", "uat-dispatch-report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"UAT dispatch report: {out_path}")
    print(f"  status  : {status}")
    print(f"  total   : {len(verdicts)}")
    print(f"  PASS    : {pass_count}")
    print(f"  FAIL    : {fail_count}")
    if errors:
        print(f"  errors  : {len(errors)}")
        for e in errors[:5]:
            print(f"    {e}")

    if fail_count:
        print("\nFailing verdicts:")
        for v in verdicts:
            if v["verdict"] != "PASS":
                print(f"  [{v['verdict']}] {v['req_id']} ({v['domain']})")

    return 0 if status == "PASS" else 1


def cmd_status(args):
    """Print a progress table: collected vs outstanding verdicts."""
    workspace = os.path.abspath(args.workspace)

    manifest_path = os.path.join(workspace, ".anti-legacy", "evidence", "uat-dispatch-manifest.json")
    if not os.path.exists(manifest_path):
        print("No dispatch manifest found. Run: run.py uat_crew assemble")
        return 1

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    uat_dir = _uat_evidence_dir(workspace)
    jobs = manifest.get("jobs", [])

    collected = 0
    outstanding = []
    pass_count = 0
    fail_count = 0

    for job in jobs:
        vpath = os.path.join(uat_dir, f"{job['req_id']}-verdict.json")
        if os.path.exists(vpath):
            collected += 1
            try:
                with open(vpath, encoding="utf-8") as f:
                    ev = json.load(f)
                v = (ev.get("verdict") or ev.get("status") or "").upper()
                if v == "PASS":
                    pass_count += 1
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1
        else:
            outstanding.append(job["req_id"])

    total = len(jobs)
    pct = (collected / total * 100) if total else 0
    print(f"UAT Progress: {collected}/{total} verdicts collected ({pct:.1f}%)")
    print(f"  PASS    : {pass_count}")
    print(f"  FAIL    : {fail_count}")
    print(f"  pending : {len(outstanding)}")

    if outstanding and args.show_pending:
        print("\nOutstanding (no verdict yet):")
        for req_id in outstanding[:50]:
            print(f"  {req_id}")
        if len(outstanding) > 50:
            print(f"  ...and {len(outstanding) - 50} more")

    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="UAT crew dispatch helper — batch bookkeeping for anti-legacy:uat-crew."
    )
    subs = parser.add_subparsers(dest="cmd")

    asm = subs.add_parser("assemble", help="Build a dispatch manifest from contracts + requirements graph")
    asm.add_argument("--workspace", default=".", help="Pipeline workspace root (default: .)")
    asm.add_argument(
        "--allow-missing-contracts", action="store_true",
        help="Include requirements even when their contract file is absent",
    )

    col = subs.add_parser("collect", help="Aggregate verdict files into uat-dispatch-report.json")
    col.add_argument("--workspace", default=".", help="Pipeline workspace root (default: .)")

    sta = subs.add_parser("status", help="Print UAT progress table")
    sta.add_argument("--workspace", default=".", help="Pipeline workspace root (default: .)")
    sta.add_argument("--show-pending", action="store_true", help="List req_ids with no verdict yet")

    args = parser.parse_args(argv)

    if args.cmd == "assemble":
        return cmd_assemble(args)
    elif args.cmd == "collect":
        return cmd_collect(args)
    elif args.cmd == "status":
        return cmd_status(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
