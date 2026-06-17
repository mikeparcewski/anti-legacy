#!/usr/bin/env python3
"""
Semantic Validator — Groups requirements by dependency chains, generates validation slices,
and manages the record and reporting of semantic/behavioral gaps.
"""
import json
import os
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone

def find_dependency_chains(requirements_graph):
    """
    Find connected components (dependency chains) in the requirements graph.
    Returns a list of lists of requirement IDs, each sorted topologically.
    """
    # 1. Gather all requirement IDs and their dependencies
    deps = {}
    req_to_domain = {}
    for domain, domain_data in requirements_graph.get('domains', {}).items():
        reqs = domain_data.get('requirements', {})
        for req_id, req in reqs.items():
            deps[req_id] = req.get('dependencies', [])
            req_to_domain[req_id] = domain
            
    if not deps:
        return []

    # 2. Build undirected graph to find connected components
    undirected = defaultdict(set)
    for node, node_deps in deps.items():
        for dep in node_deps:
            if dep in deps:
                undirected[node].add(dep)
                undirected[dep].add(node)
                
    # 3. Find connected components (undirected BFS/DFS)
    visited = set()
    components = []
    for req_id in sorted(deps.keys()):
        if req_id not in visited:
            component = []
            queue = deque([req_id])
            visited.add(req_id)
            while queue:
                curr = queue.popleft()
                component.append(curr)
                # Walk neighbors in undirected graph
                for neighbor in sorted(undirected[curr]):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(component)
            
    # 4. Topologically sort each component based on original dependencies
    sorted_components = []
    for comp in components:
        # Construct sub-dependencies mapping
        comp_set = set(comp)
        sub_deps = {}
        for node in comp:
            sub_deps[node] = [d for d in deps[node] if d in comp_set]
            
        # Kahn's algorithm for this component
        in_degree = {n: 0 for n in sub_deps}
        adj = defaultdict(list)
        for node, node_deps in sub_deps.items():
            for dep in node_deps:
                adj[dep].append(node)
                in_degree[node] += 1
                
        queue = deque(sorted([n for n in in_degree if in_degree[n] == 0]))
        topo_order = []
        while queue:
            curr = queue.popleft()
            topo_order.append(curr)
            for neighbor in sorted(adj[curr]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    
        # Append any remainder (cycles)
        if len(topo_order) < len(comp):
            remaining = comp_set - set(topo_order)
            topo_order.extend(sorted(list(remaining)))
            
        sorted_components.append(topo_order)
        
    return sorted_components

def record_gap(graph_path, req_id, gap_id, severity, description, legacy_loc, target_loc, remediation):
    """Record or update a semantic gap in the requirements graph."""
    if not os.path.exists(graph_path):
        print(f"Error: Requirements graph not found at {graph_path}", file=sys.stderr)
        return False
        
    try:
        with open(graph_path, 'r') as f:
            rg = json.load(f)
    except Exception as e:
        print(f"Error reading requirements graph: {e}", file=sys.stderr)
        return False
        
    # Find the node in domains
    found = False
    for domain, domain_data in rg.get('domains', {}).items():
        reqs = domain_data.get('requirements', {})
        if req_id in reqs:
            req = reqs[req_id]
            gaps = req.setdefault('semantic_gaps', [])
            
            # Remove existing gap with same ID if present
            gaps = [g for g in gaps if g.get('id') != gap_id]
            
            gap = {
                "id": gap_id,
                "severity": severity.upper(),
                "description": description,
                "legacy_location": legacy_loc,
                "target_location": target_loc,
                "remediation": remediation,
                "status": "unresolved",
                "detected_at": datetime.now(timezone.utc).isoformat()
            }
            gaps.append(gap)
            req['semantic_gaps'] = gaps
            found = True
            break
            
    if not found:
        print(f"Error: Requirement '{req_id}' not found in requirements graph.", file=sys.stderr)
        return False
        
    try:
        with open(graph_path, 'w') as f:
            json.dump(rg, f, indent=2)
        print(f"Gap '{gap_id}' successfully recorded on requirement '{req_id}'.")
        return True
    except Exception as e:
        print(f"Error saving requirements graph: {e}", file=sys.stderr)
        return False

def resolve_gap(graph_path, req_id, gap_id):
    """Mark a nested semantic gap as resolved so GATE_3B no longer blocks on it."""
    if not os.path.exists(graph_path):
        print(f"Error: Requirements graph not found at {graph_path}", file=sys.stderr)
        return False

    try:
        with open(graph_path, 'r') as f:
            rg = json.load(f)
    except Exception as e:
        print(f"Error reading requirements graph: {e}", file=sys.stderr)
        return False

    # Find the requirement node and the matching nested gap
    req_found = False
    gap_found = False
    for domain, domain_data in rg.get('domains', {}).items():
        reqs = domain_data.get('requirements', {})
        if req_id in reqs:
            req_found = True
            gaps = reqs[req_id].get('semantic_gaps', [])
            for g in gaps:
                if g.get('id') == gap_id:
                    g['status'] = 'resolved'
                    gap_found = True
                    break
            break

    if not req_found:
        print(f"Error: Requirement '{req_id}' not found in requirements graph.", file=sys.stderr)
        return False
    if not gap_found:
        print(f"Error: Gap '{gap_id}' not found on requirement '{req_id}'.", file=sys.stderr)
        return False

    try:
        with open(graph_path, 'w') as f:
            json.dump(rg, f, indent=2)
        print(f"Gap '{gap_id}' on requirement '{req_id}' marked as resolved.")
        return True
    except Exception as e:
        print(f"Error saving requirements graph: {e}", file=sys.stderr)
        return False

def generate_reports(graph_path, blueprint_path, output_json, output_md):
    """Compile validation reports from the requirements graph and blueprint."""
    if not os.path.exists(graph_path):
        print(f"Error: Requirements graph not found at {graph_path}", file=sys.stderr)
        return False
        
    try:
        with open(graph_path, 'r') as f:
            rg = json.load(f)
    except Exception as e:
        print(f"Error reading requirements graph: {e}", file=sys.stderr)
        return False
        
    bp = {}
    if os.path.exists(blueprint_path):
        try:
            with open(blueprint_path, 'r') as f:
                bp = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to read blueprint: {e}", file=sys.stderr)

    # 1. Extract dependency chains
    chains = find_dependency_chains(rg)
    
    # Map requirements to their domain and blueprint component details
    req_to_domain = {}
    req_to_details = {}
    for domain, domain_data in rg.get('domains', {}).items():
        for req_id, req in domain_data.get('requirements', {}).items():
            req_to_domain[req_id] = domain
            # Trace target details from blueprint if available
            target_class = "unknown"
            target_path = "unknown"
            if bp:
                comp = {}
                # Live blueprint carries components at the TOP level
                # (blueprint['components'][req_id]); prefer that shape and fall
                # back to the nested domains[*].components form for older blueprints.
                top_components = bp.get("components", {})
                if req_id in top_components:
                    comp = top_components[req_id]
                else:
                    for bp_dom, bp_dom_data in bp.get("domains", {}).items():
                        if req_id in bp_dom_data.get("components", {}):
                            comp = bp_dom_data["components"][req_id]
                            break
                if comp:
                    target_class = comp.get("class_name", "unknown")
                    target_path = bp.get("target_path", "") + "/" + comp.get("class_name", "") # approximate
            
            req_to_details[req_id] = {
                "title": req.get("title", ""),
                "legacy_components": req.get("legacy_components", []),
                "target_class": target_class,
                "target_path": target_path
            }

    # 2. Gather gaps
    all_gaps = []
    gaps_by_severity = defaultdict(int)
    for domain, domain_data in rg.get('domains', {}).items():
        for req_id, req in domain_data.get('requirements', {}).items():
            gaps = req.get('semantic_gaps', [])
            for g in gaps:
                gap_info = {
                    "req_id": req_id,
                    "domain": domain,
                    **g
                }
                all_gaps.append(gap_info)
                gaps_by_severity[g.get("severity", "UNKNOWN").upper()] += 1

    # 3. Write JSON evidence
    validation_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_requirements": len(req_to_domain),
        "total_gaps": len(all_gaps),
        "gaps_by_severity": dict(gaps_by_severity),
        "dependency_chains": [
            {
                "chain_id": f"CHAIN-{i:03d}",
                "requirements": chain,
                "domains": list(set(req_to_domain.get(r, "unknown") for r in chain))
            }
            for i, chain in enumerate(chains, 1)
        ],
        "gaps": all_gaps
    }
    
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    try:
        with open(output_json, 'w') as f:
            json.dump(validation_data, f, indent=2)
        print(f"JSON validation evidence written to {output_json}")
    except Exception as e:
        print(f"Error saving JSON report: {e}", file=sys.stderr)
        return False
        
    # 4. Compile Markdown report
    os.makedirs(os.path.dirname(output_md), exist_ok=True)
    try:
        with open(output_md, 'w') as f:
            f.write("# Semantic Validation Report\n\n")
            f.write(f"**Generated at**: {validation_data['generated_at']}  \n")
            f.write(f"**Total Gaps**: {validation_data['total_gaps']} (")
            sevs = [f"{k}: {v}" for k, v in gaps_by_severity.items()]
            f.write(", ".join(sevs) if sevs else "None")
            f.write(")  \n\n")
            
            f.write("## 1. Application Dependency Chains\n\n")
            f.write("Semantic review tasks are partitioned across the following dependency chains:\n\n")
            
            for chain_obj in validation_data["dependency_chains"]:
                f.write(f"### {chain_obj['chain_id']}\n")
                f.write(f"**Domains**: {', '.join(chain_obj['domains'])}  \n")
                f.write("**Traversal Order**:\n")
                for r in chain_obj["requirements"]:
                    det = req_to_details.get(r, {})
                    f.write(f"- `{r}`: **{det.get('title', 'Untitled')}**\n")
                    f.write(f"  - Legacy: `{', '.join(det.get('legacy_components', []))}`\n")
                    f.write(f"  - Target Component: `{det.get('target_class', 'unknown')}`\n")
                f.write("\n")
                
            f.write("## 2. Identified Semantic Gaps\n\n")
            if not all_gaps:
                f.write("✓ **No semantic gaps detected.** All implementations align with legacy behavior.\n")
            else:
                f.write("| Gap ID | Req ID | Severity | Description | Legacy Loc | Target Loc | Remediation |\n")
                f.write("|---|---|---|---|---|---|---|\n")
                for g in all_gaps:
                    desc = g.get('description', '').replace("\n", " ")
                    rem = g.get('remediation', '').replace("\n", " ")
                    f.write(f"| `{g['id']}` | `{g['req_id']}` | **{g['severity']}** | {desc} | `{g['legacy_location']}` | `{g['target_location']}` | {rem} |\n")
                f.write("\n")
                
            f.write("## 3. Resolution Gatekeeper checklist\n\n")
            f.write("- [ ] **Zero High Severity Gaps**: Verify no HIGH severity gaps remain unresolved.\n")
            f.write("- [ ] **Medium/Low Severity Approvals**: Verify that any minor/medium differences are accepted by the Tech Lead.\n")
            
        print(f"Markdown validation report written to {output_md}")
        return True
    except Exception as e:
        print(f"Error saving Markdown report: {e}", file=sys.stderr)
        return False

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Manage semantic validation, connected components, and gap recording.")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")
    
    # Subcommand: list-chains
    parser_list = subparsers.add_parser("list-chains", help="List dependency chains in graph")
    parser_list.add_argument("--requirements-graph", default=".anti-legacy/requirements/requirements_graph.json")
    
    # Subcommand: record-gap
    parser_record = subparsers.add_parser("record-gap", help="Record a semantic validation gap")
    parser_record.add_argument("--requirements-graph", default=".anti-legacy/requirements/requirements_graph.json")
    parser_record.add_argument("--req-id", required=True, help="Affected requirement node ID")
    parser_record.add_argument("--gap-id", required=True, help="Unique gap ID (e.g. GAP-001)")
    parser_record.add_argument("--severity", required=True, choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"], help="Gap severity level")
    parser_record.add_argument("--description", required=True, help="Description of the behavioral discrepancy")
    parser_record.add_argument("--legacy-loc", required=True, help="Legacy code filename and lines (e.g., BILLING.cbl:L150)")
    parser_record.add_argument("--target-loc", required=True, help="Target code filename and lines (e.g., BillingService.go:L45)")
    parser_record.add_argument("--remediation", required=True, help="Suggested fix")
    parser_record.add_argument("--blueprint", default=".anti-legacy/requirements/blueprint.json")
    parser_record.add_argument("--output-json", default=".anti-legacy/evidence/semantic-validation-report.json")
    parser_record.add_argument("--output-md", default=".anti-legacy/evidence/semantic_validation_report.md")
    
    # Subcommand: resolve-gap
    parser_resolve = subparsers.add_parser("resolve-gap", help="Mark a recorded semantic gap as resolved")
    parser_resolve.add_argument("--requirements-graph", default=".anti-legacy/requirements/requirements_graph.json")
    parser_resolve.add_argument("--req-id", required=True, help="Affected requirement node ID")
    parser_resolve.add_argument("--gap-id", required=True, help="Gap ID to resolve (e.g. GAP-001)")
    parser_resolve.add_argument("--status", choices=["resolved", "unresolved"], default="unresolved",
                                help="Status to set on the gap (default: unresolved)")
    parser_resolve.add_argument("--blueprint", default=".anti-legacy/requirements/blueprint.json")
    parser_resolve.add_argument("--output-json", default=".anti-legacy/evidence/semantic-validation-report.json")
    parser_resolve.add_argument("--output-md", default=".anti-legacy/evidence/semantic_validation_report.md")

    # Subcommand: generate-report
    parser_report = subparsers.add_parser("generate-report", help="Generate semantic validation reports")
    parser_report.add_argument("--requirements-graph", default=".anti-legacy/requirements/requirements_graph.json")
    parser_report.add_argument("--blueprint", default=".anti-legacy/requirements/blueprint.json")
    parser_report.add_argument("--output-json", default=".anti-legacy/evidence/semantic-validation-report.json")
    parser_report.add_argument("--output-md", default=".anti-legacy/evidence/semantic_validation_report.md")
    
    args = parser.parse_args()
    
    if args.command == "list-chains":
        if not os.path.exists(args.requirements_graph):
            print(f"Error: Requirements graph not found at {args.requirements_graph}", file=sys.stderr)
            sys.exit(1)
        with open(args.requirements_graph) as f:
            rg = json.load(f)
        chains = find_dependency_chains(rg)
        print(f"Found {len(chains)} connected dependency chains:")
        for i, chain in enumerate(chains, 1):
            print(f"  Chain {i}: {', '.join(chain)}")
            
    elif args.command == "record-gap":
        success = record_gap(
            args.requirements_graph,
            args.req_id,
            args.gap_id,
            args.severity,
            args.description,
            args.legacy_loc,
            args.target_loc,
            args.remediation
        )
        if success:
            generate_reports(args.requirements_graph, args.blueprint, args.output_json, args.output_md)
        else:
            sys.exit(1)
            
    elif args.command == "resolve-gap":
        success = resolve_gap(
            args.requirements_graph,
            args.req_id,
            args.gap_id
        )
        if success:
            generate_reports(args.requirements_graph, args.blueprint, args.output_json, args.output_md)
        else:
            sys.exit(1)

    elif args.command == "generate-report":
        success = generate_reports(args.requirements_graph, args.blueprint, args.output_json, args.output_md)
        if not success:
            sys.exit(1)
            
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
