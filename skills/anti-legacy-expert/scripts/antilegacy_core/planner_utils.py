#!/usr/bin/env python3
"""
Planner utilities for dependency graph traversal and task sorting.
Supports Bottom-Up, Top-Down, and Vertical Slice traversal strategies.
Includes automated verification checkers for strategy compliance.
"""
import json
import os
import re
import sys
from collections import defaultdict, deque

def get_dependencies_and_domains(requirements_graph):
    """Extract requirements dependencies and their domain mappings."""
    deps = {}
    req_to_domain = {}
    for domain, domain_data in requirements_graph.get('domains', {}).items():
        reqs = domain_data.get('requirements', {})
        for req_id, req in reqs.items():
            deps[req_id] = req.get('dependencies', [])
            req_to_domain[req_id] = domain
    return deps, req_to_domain

def topological_sort(deps):
    """Standard Kahn's algorithm for topological sorting."""
    in_degree = {n: 0 for n in deps}
    adj = defaultdict(list)
    for node, node_deps in deps.items():
        for dep in node_deps:
            if dep in in_degree:
                adj[dep].append(node)
                in_degree[node] += 1
    
    # Sort keys to ensure deterministic ordering of nodes with 0 in-degree
    queue = deque(sorted([n for n in in_degree if in_degree[n] == 0]))
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(adj[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
                
    if len(order) < len(deps):
        # Cycle detected or orphan nodes, append remaining in sorted order
        remaining = set(deps.keys()) - set(order)
        order.extend(sorted(list(remaining)))
        
    return order

def sort_requirements(requirements_graph, strategy="bottom-up"):
    """Sort requirements based on the chosen strategy: bottom-up, top-down, or vertical-slice."""
    deps, req_to_domain = get_dependencies_and_domains(requirements_graph)
    
    if not deps:
        return []

    strategy = strategy.lower().strip()
    
    if strategy == "bottom-up":
        return topological_sort(deps)
        
    elif strategy == "top-down":
        order = topological_sort(deps)
        order.reverse()
        return order
        
    elif strategy == "vertical-slice":
        # 1. Group requirements by domain
        domain_to_reqs = defaultdict(list)
        for req_id, domain in req_to_domain.items():
            domain_to_reqs[domain].append(req_id)
            
        # 2. Build domain-level dependency graph
        domain_deps = defaultdict(set)
        for req_id, r_deps in deps.items():
            req_domain = req_to_domain[req_id]
            for dep in r_deps:
                dep_domain = req_to_domain.get(dep)
                if dep_domain and dep_domain != req_domain:
                    domain_deps[req_domain].add(dep_domain)
                    
        # 3. Topologically sort the domains
        domain_deps_list = {d: list(domain_deps[d]) for d in domain_to_reqs.keys()}
        sorted_domains = topological_sort(domain_deps_list)
        
        final_order = []
        for domain in sorted_domains:
            # Sort topologically within each domain/slice
            sub_deps = {}
            for req_id in domain_to_reqs[domain]:
                sub_deps[req_id] = [d for d in deps[req_id] if req_to_domain.get(d) == domain]
            domain_order = topological_sort(sub_deps)
            final_order.extend(domain_order)
            
        return final_order
        
    else:
        raise ValueError(f"Unknown traversal strategy: {strategy}")

def verify_order(task_file, requirements_graph, strategy="bottom-up"):
    """
    Verify that task.md ordering complies with the specified strategy.
    Returns (True, []) or (False, [error_messages]).
    """
    if not os.path.exists(task_file):
        return False, [f"Task list file not found: {task_file}"]
        
    # 1. Parse requirements and domains from graph
    deps, req_to_domain = get_dependencies_and_domains(requirements_graph)
    
    # 2. Extract tasks and req_ids in order from task.md
    with open(task_file) as f:
        content = f.read()
        
    chunks = content.split("- [")
    parsed_tasks = []
    
    for chunk in chunks[1:]:
        m_task = re.search(r'^[^\]]*\]\s*\*\*([A-Za-z0-9\-]+)\*\*', chunk)
        if not m_task:
            continue
        task_id = m_task.group(1)
        
        m_req = re.search(r'Requirement:\s*([A-Za-z0-9\_]+)', chunk, re.IGNORECASE)
        if not m_req:
            m_req = re.search(r'requirements_graph\.json:\s*([A-Za-z0-9\_]+)', chunk, re.IGNORECASE)
            
        req_id = m_req.group(1) if m_req else None
        if req_id and req_id in deps:
            parsed_tasks.append((task_id, req_id))
            
    if not parsed_tasks:
        return True, []
        
    # Positions of requirements in task list
    req_positions = {}
    for idx, (task_id, req_id) in enumerate(parsed_tasks):
        req_positions.setdefault(req_id, []).append(idx)
        
    errors = []
    strategy = strategy.lower().strip()
    
    if strategy == "bottom-up":
        # Dependent must be built AFTER dependency
        # For each req, its dependencies must be positioned BEFORE it
        for idx, (task_id, req_id) in enumerate(parsed_tasks):
            for dep in deps.get(req_id, []):
                if dep in req_positions:
                    for dep_pos in req_positions[dep]:
                        if dep_pos > idx:
                            errors.append(f"Bottom-Up Violation: Task {task_id} ({req_id}) is scheduled before its dependency {dep} (at task index {dep_pos}).")
                            
    elif strategy == "top-down":
        # Dependent must be built BEFORE dependency
        # For each req, its dependencies must be positioned AFTER it
        for idx, (task_id, req_id) in enumerate(parsed_tasks):
            for dep in deps.get(req_id, []):
                if dep in req_positions:
                    for dep_pos in req_positions[dep]:
                        if dep_pos < idx:
                            errors.append(f"Top-Down Violation: Task {task_id} ({req_id}) is scheduled after its dependency {dep} (at task index {dep_pos}).")
                            
    elif strategy == "vertical-slice":
        # 1. Grouping check: domains must be contiguous/grouped
        domain_sequence = []
        for _, req_id in parsed_tasks:
            dom = req_to_domain.get(req_id)
            if dom:
                domain_sequence.append(dom)
                
        if domain_sequence:
            compressed_domains = [domain_sequence[0]]
            for d in domain_sequence[1:]:
                if d != compressed_domains[-1]:
                    compressed_domains.append(d)
            if len(compressed_domains) != len(set(compressed_domains)):
                seen = set()
                duplicated = []
                for d in compressed_domains:
                    if d in seen:
                        duplicated.append(d)
                    seen.add(d)
                errors.append(f"Vertical Slice Violation: Domain slices are not contiguous. Non-contiguous domains: {', '.join(duplicated)}.")
                
        # 2. Dependency precedence check within each domain (must be bottom-up within the domain)
        for idx, (task_id, req_id) in enumerate(parsed_tasks):
            req_dom = req_to_domain.get(req_id)
            for dep in deps.get(req_id, []):
                dep_dom = req_to_domain.get(dep)
                if dep_dom == req_dom and dep in req_positions:
                    for dep_pos in req_positions[dep]:
                        if dep_pos > idx:
                            errors.append(f"Vertical Slice Violation: Task {task_id} ({req_id}) is scheduled before its intra-domain dependency {dep} (at task index {dep_pos}).")
                            
    else:
        return False, [f"Unknown strategy: {strategy}"]
        
    return len(errors) == 0, errors

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sort or verify requirements graph nodes by strategy.")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")
    
    # Subcommand: sort (default)
    parser_sort = subparsers.add_parser("sort", help="Sort requirements graph nodes (default)")
    parser_sort.add_argument("--requirements-graph", default=".anti-legacy/requirements/requirements_graph.json")
    parser_sort.add_argument("--config", default=".anti-legacy/config.json")
    parser_sort.add_argument("--strategy", default=None)
    
    # Subcommand: verify-order
    parser_verify = subparsers.add_parser("verify-order", help="Verify task list compliance with strategy")
    parser_verify.add_argument("--task-file", default=".anti-legacy/task.md")
    parser_verify.add_argument("--requirements-graph", default=".anti-legacy/requirements/requirements_graph.json")
    parser_verify.add_argument("--config", default=".anti-legacy/config.json")
    parser_verify.add_argument("--strategy", default=None)
    
    # Fallback to sort if no subcommand is provided
    if len(sys.argv) > 1 and sys.argv[1] not in ["sort", "verify-order", "-h", "--help"]:
        sys.argv.insert(1, "sort")
    elif len(sys.argv) == 1:
        sys.argv.append("sort")
        
    args = parser.parse_args()
    
    if args.command == "sort":
        strategy = args.strategy
        if not strategy and os.path.exists(args.config):
            try:
                with open(args.config) as f:
                    cfg = json.load(f)
                    strategy = cfg.get("traversal_strategy", "bottom-up")
            except Exception as e:
                print(f"Warning: Failed to read config: {e}", file=sys.stderr)
                
        if not strategy:
            strategy = "bottom-up"
            
        if not os.path.exists(args.requirements_graph):
            print(f"Error: Requirements graph not found at {args.requirements_graph}", file=sys.stderr)
            sys.exit(1)
            
        try:
            with open(args.requirements_graph) as f:
                rg = json.load(f)
        except Exception as e:
            print(f"Error: Failed to parse requirements graph: {e}", file=sys.stderr)
            sys.exit(1)
            
        try:
            order = sort_requirements(rg, strategy)
            print(f"Strategy: {strategy}")
            print("Build order:")
            for i, req_id in enumerate(order, 1):
                domain = get_dependencies_and_domains(rg)[1].get(req_id, "unknown")
                print(f"  {i}. {req_id} ({domain})")
        except Exception as e:
            print(f"Error sorting requirements: {e}", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == "verify-order":
        strategy = args.strategy
        if not strategy and os.path.exists(args.config):
            try:
                with open(args.config) as f:
                    cfg = json.load(f)
                    strategy = cfg.get("traversal_strategy", "bottom-up")
            except Exception as e:
                print(f"Warning: Failed to read config: {e}", file=sys.stderr)
                
        if not strategy:
            strategy = "bottom-up"
            
        if not os.path.exists(args.requirements_graph):
            print(f"Error: Requirements graph not found at {args.requirements_graph}", file=sys.stderr)
            sys.exit(1)
            
        try:
            with open(args.requirements_graph) as f:
                rg = json.load(f)
        except Exception as e:
            print(f"Error: Failed to parse requirements graph: {e}", file=sys.stderr)
            sys.exit(1)
            
        success, errors = verify_order(args.task_file, rg, strategy)
        if success:
            print(f"Traversal Checklist: Verified {strategy} ordering (0 violations). ✓")
            sys.exit(0)
        else:
            print(f"Traversal Checklist: FAILED verification for strategy '{strategy}':", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
