#!/usr/bin/env python3
"""
Learning Coordinator — Automatically compiles metadata, logs, and metrics from
completed modernization phases, formats them into episodic memories, and persists
them in the git-brain.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

def run_git_brain_store(content, tags, category="learnings"):
    """Invoke scripts/git_brain.py store command to save a memory."""
    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "git_brain.py"),
        "store",
        "--content", content,
        "--tags", tags,
        "--category", category
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        print(f"Recorded learning in git-brain: {tags}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error calling git_brain.py store: {e.stderr}", file=sys.stderr)
        return False

def analyze_setup(manifest, workspace_dir="."):
    proj = manifest.get("project", {})
    summary = f"Setup Phase: Initialized project '{proj.get('name')}' targeting target stack '{proj.get('target_stack')}' at path '{proj.get('target_path')}'."
    details = f"- Project Name: {proj.get('name')}\n"
    details += f"- Target Stack: {proj.get('target_stack')}\n"
    details += f"- Target Path: {proj.get('target_path')}\n"
    details += f"- Deployment Target: {proj.get('deployment_target')}\n"
    return summary, details

def _parse_legacy_graph_digest(text):
    """Parse the deterministic wicked-estate stats digest produced by survey.

    The digest is a stable, checksummable text artifact (one stats_digest block
    per source app). Each app block is delineated by a `# app: <name>` header
    (single-app digests may omit the header). The canonical per-block stat line
    is `nodes=N edges=N files=N` (volatile lines like `repo:`, `STALENESS:`,
    and `db=` are stripped by the helper before the digest is written).

    Returns (apps: list[str], num_nodes: int).
    """
    apps = []
    num_nodes = 0
    saw_stat_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # App header: "# app: <name>" (case-insensitive on the "app" label).
        low = line.lower()
        if low.startswith("# app:") or low.startswith("#app:"):
            apps.append(line.split(":", 1)[1].strip())
            continue
        # Canonical stat line: tokens like "nodes=123 edges=45 files=7".
        if line.startswith("nodes="):
            saw_stat_block = True
            for tok in line.split():
                if tok.startswith("nodes="):
                    try:
                        num_nodes += int(tok.split("=", 1)[1])
                    except ValueError:
                        pass
    # Single-app digest with no header but a real stat block: count it as 1 app.
    if not apps and saw_stat_block:
        apps = ["(legacy-estate)"]
    return apps, num_nodes


def analyze_survey(manifest, workspace_dir="."):
    # Survey now indexes the source repo(s) with `wicked-estate index` and
    # registers the deterministic stats digest as the checksummed legacy-graph
    # evidence. The intermediate legacy_graph.json blob is gone; read the digest.
    digest_path = os.path.join(
        workspace_dir, ".anti-legacy", "legacy-graph.digest.txt"
    )
    if not os.path.exists(digest_path):
        return (
            "Survey Phase: Legacy graph digest not found.",
            "- No legacy-graph.digest.txt exists (run survey to index the source repo).",
        )
    try:
        with open(digest_path) as f:
            text = f.read()
        apps, num_nodes = _parse_legacy_graph_digest(text)
        num_apps = len(apps)
        summary = (
            f"Survey Phase: Indexed legacy source code with wicked-estate. "
            f"Discovered {num_nodes} nodes across {num_apps} applications."
        )
        details = f"- Applications Scanned: {', '.join(apps) if apps else '(none)'}\n"
        details += f"- Total Code/Data Nodes: {num_nodes}\n"
        return summary, details
    except Exception as e:
        return f"Survey Phase: Error parsing graph digest: {e}", f"- Error: {e}"

def analyze_planner(manifest, workspace_dir="."):
    config_path = os.path.join(workspace_dir, ".anti-legacy", "config.json")
    task_path = None
    strategy = "bottom-up"
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
                strategy = cfg.get("traversal_strategy", "bottom-up")
                task_path = cfg.get("paths", {}).get("task_plan")
        except:
            pass
            
    if not task_path:
        task_path = os.path.join(workspace_dir, ".anti-legacy", "task.md")
    elif not os.path.isabs(task_path):
        task_path = os.path.join(workspace_dir, task_path)
            
    if not os.path.exists(task_path):
        return f"Planner Phase: Task list not found. Strategy: {strategy}.", "- No task.md checklist file exists."
        
    try:
        with open(task_path) as f:
            lines = f.readlines()
        tasks = [l for l in lines if "- [" in l]
        total_tasks = len(tasks)
        summary = f"Planner Phase: Generated build plan with {total_tasks} tasks using strategy '{strategy}'."
        details = f"- Traversal Strategy: {strategy}\n"
        details += f"- Total Swarm Tasks: {total_tasks}\n"
        return summary, details
    except Exception as e:
        return f"Planner Phase: Error: {e}", f"- Error: {e}"

def analyze_swarm(manifest, workspace_dir="."):
    config_path = os.path.join(workspace_dir, ".anti-legacy", "config.json")
    task_path = None
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                cfg = json.load(f)
                task_path = cfg.get("paths", {}).get("task_plan")
        except:
            pass
            
    if not task_path:
        task_path = os.path.join(workspace_dir, ".anti-legacy", "task.md")
    elif not os.path.isabs(task_path):
        task_path = os.path.join(workspace_dir, task_path)
        
    if not os.path.exists(task_path):
        return "Swarm Phase: Task list not found.", "- No task.md checklist file exists."
    try:
        with open(task_path) as f:
            lines = f.readlines()
        tasks = [l for l in lines if "- [" in l]
        completed = [t for t in tasks if "[x]" in t or "[X]" in t]
        summary = f"Swarm Build Phase: Completed {len(completed)}/{len(tasks)} translation tasks."
        details = f"- Completed Tasks: {len(completed)}\n"
        details += f"- Total Tasks: {len(tasks)}\n"
        details += f"- Pending Tasks: {len(tasks) - len(completed)}\n"
        return summary, details
    except Exception as e:
        return f"Swarm Phase: Error: {e}", f"- Error: {e}"

def analyze_target_review(manifest, workspace_dir="."):
    integrity_path = os.path.join(workspace_dir, ".anti-legacy", "evidence", "build-integrity.json")
    report_path = os.path.join(workspace_dir, ".anti-legacy", "evidence", "functional-test-report.json")
    
    status = "unknown"
    test_summary = "no test evidence"
    
    if os.path.exists(integrity_path):
        try:
            with open(integrity_path) as f:
                data = json.load(f)
                status = data.get("status", "unknown")
        except:
            pass
            
    if os.path.exists(report_path):
        try:
            with open(report_path) as f:
                rep = json.load(f)
                test_summary = f"{rep.get('passed', 0)} passed, {rep.get('failed', 0)} failed"
        except:
            pass
            
    summary = f"Target Review Phase: Build status is '{status}'. Parity testing: {test_summary}."
    details = f"- Build Integrity: {status}\n"
    details += f"- Functional Test Summary: {test_summary}\n"
    return summary, details

def analyze_semantic_validation(manifest, workspace_dir="."):
    report_path = os.path.join(workspace_dir, ".anti-legacy", "evidence", "semantic-validation-report.json")
    if not os.path.exists(report_path):
        return "Semantic Validation Phase: Report not found.", "- No validation report file exists."
    try:
        with open(report_path) as f:
            data = json.load(f)
        total_gaps = data.get("total_gaps", 0)
        sevs = ", ".join(f"{k}: {v}" for k, v in data.get("gaps_by_severity", {}).items())
        summary = f"Semantic Validation Phase: Reviewed dependency chains. Detected {total_gaps} semantic gaps ({sevs or 'no gaps'})."
        details = f"- Total Detected Gaps: {total_gaps}\n"
        details += f"- Gaps Breakdown: {sevs or 'None'}\n"
        return summary, details
    except Exception as e:
        return f"Semantic Validation Phase: Error: {e}", f"- Error: {e}"

def analyze_uat(manifest, workspace_dir="."):
    # Check uat summary if exists
    summary_path = os.path.join(workspace_dir, ".anti-legacy", "evidence", "uat-summary.md")
    verdict = "unknown"
    if os.path.exists(summary_path):
        try:
            with open(summary_path) as f:
                content = f.read()
            if "verdict: PASS" in content or "VERDICT: PASS" in content or "PASS" in content:
                verdict = "PASS"
            elif "FAIL" in content:
                verdict = "FAIL"
        except:
            pass
    summary = f"UAT Crew Phase: Independent validation completed. Overall verdict: {verdict}."
    details = f"- Verdict: {verdict}\n"
    return summary, details

def analyze_phase(phase, workspace_dir="."):
    manifest_path = os.path.join(workspace_dir, ".anti-legacy", "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"Error: Manifest not found at {manifest_path}", file=sys.stderr)
        return None, None
        
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception as e:
        print(f"Error parsing manifest: {e}", file=sys.stderr)
        return None, None
        
    phase = phase.lower().strip()
    
    if phase == "setup":
        return analyze_setup(manifest, workspace_dir)
    elif phase in ["survey", "survey-modern"]:
        return analyze_survey(manifest, workspace_dir)
    elif phase == "planner":
        return analyze_planner(manifest, workspace_dir)
    elif phase == "swarm":
        return analyze_swarm(manifest, workspace_dir)
    elif phase == "target-review":
        return analyze_target_review(manifest, workspace_dir)
    elif phase == "semantic-validation":
        return analyze_semantic_validation(manifest, workspace_dir)
    elif phase in ["uat", "uat-crew"]:
        return analyze_uat(manifest, workspace_dir)
    else:
        # Fallback summary
        return f"Completed Phase: {phase}.", f"- Phase Name: {phase}\n- Date: {datetime.now(timezone.utc).isoformat()}"

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Compile and save phase execution memories.")
    parser.add_argument("--phase", required=True, help="Name of the phase to review")
    parser.add_argument("--workspace", default=".", help="Workspace root directory")
    args = parser.parse_args()
    
    manifest_path = os.path.join(args.workspace, ".anti-legacy", "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"Warning: Manifest not found at {manifest_path}. Skipping learn coordinator.")
        sys.exit(0)
        
    try:
        with open(manifest_path) as f:
            m = json.load(f)
        project_name = m.get("project", {}).get("name") or "unnamed-project"
    except Exception:
        project_name = "unnamed-project"

    summary, details = analyze_phase(args.phase, args.workspace)
    if not summary:
        sys.exit(1)
        
    timestamp = datetime.now(timezone.utc).isoformat()
    markdown_content = f"""# Phase Learning: {args.phase}
**Project**: {project_name}
**Timestamp**: {timestamp}

## Summary
{summary}

## Details
{details}
"""
    
    # Store in git-brain
    tags = f"learning,phase-{args.phase},{project_name}"
    success = run_git_brain_store(markdown_content, tags, category="learnings")
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
