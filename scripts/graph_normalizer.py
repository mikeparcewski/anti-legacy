#!/usr/bin/env python3
"""
Graph normalizer — converts a legacy code graph into a requirements graph.

Two modes:
  structural  — 1 legacy program = 1 requirement node (code-equivalent)
  functional  — merges programs into business capabilities (functional-equivalent)

The functional mode applies four transforms:
  1. Leaf merging   — programs called from exactly one place become part of the caller
  2. Data affinity  — programs sharing 3+ data accesses cluster into one capability
  3. Dead code      — programs with no callers and no entry indicators get flagged
  4. Intent naming  — requirements named by business purpose, not program name
"""
import json
import sys
import os
import argparse
from collections import defaultdict


class GraphNormalizer:
    def __init__(self, code_graph, mode="structural"):
        self.code_graph = code_graph
        self.mode = mode
        self.requirements_graph = {
            "metadata": {
                "migration_mode": self.mode
            },
            "domains": {}
        }

    # ------------------------------------------------------------------
    # Shared: build the unified node + edge index
    # ------------------------------------------------------------------
    def _gather_nodes_and_edges(self):
        """Collect all nodes and edges across applications into flat dicts."""
        all_nodes = {}
        all_edges = []
        for app_name, app_data in self.code_graph.get("applications", {}).items():
            for node_id, node_data in app_data.get("nodes", {}).items():
                qualified_id = f"{app_name}:{node_id}"
                all_nodes[qualified_id] = {
                    **node_data,
                    "app": app_name
                }
            for edge in app_data.get("edges", []):
                all_edges.append({
                    "source": f"{app_name}:{edge['source']}",
                    "target": f"{app_name}:{edge['target']}" if ":" not in edge['target'] else edge['target'],
                    "type": edge['type']
                })
        return all_nodes, all_edges

    def _identify_shared_assets(self, all_nodes, all_edges):
        """Find tables/files and which programs access them."""
        shared_assets = {}
        for node_id, node in all_nodes.items():
            if node["type"] in ["table", "file", "table_model"]:
                shared_assets[node["name"]] = {
                    "legacy_nodes": [],
                    "accessing_apps": set()
                }

        for edge in all_edges:
            target_name = edge["target"].split(":")[-1]
            if target_name in shared_assets:
                shared_assets[target_name]["legacy_nodes"].append(edge["source"])
                source_app = all_nodes.get(edge["source"], {}).get("app", "unknown")
                shared_assets[target_name]["accessing_apps"].add(source_app)

        return shared_assets

    def _program_data_access(self, node_id, all_nodes, all_edges):
        """Return set of data asset names a program accesses."""
        access = set()
        for edge in all_edges:
            if edge["source"] == node_id:
                target_node = all_nodes.get(edge["target"])
                if target_node and target_node["type"] in ["table", "file", "table_model"]:
                    access.add(target_node["name"])
        return access

    def _program_call_targets(self, node_id, all_nodes, all_edges):
        """Return list of program node_ids this program calls."""
        targets = []
        for edge in all_edges:
            if edge["source"] == node_id:
                target_node = all_nodes.get(edge["target"])
                if target_node and target_node["type"] in ["program", "class"]:
                    targets.append(edge["target"])
        return targets

    @staticmethod
    def _entity_object(asset_name):
        """Canonical entity object for a legacy data asset.

        Kept identical to the per-asset entity dicts emitted in step 1 of each
        mode so co-location copies are structurally identical (T2).
        """
        return {
            "description": f"Logical entity derived from legacy asset: {asset_name}",
            "fields": [
                {"name": "id", "type": "string", "description": "Primary identifier"}
            ]
        }

    def _build_asset_entities(self, all_nodes):
        """Map asset_name -> entity object for every table/file/table_model node.

        Used to CO-LOCATE the entities a requirement accesses inside the
        requirement's own domain, so the invariant `req.data_access ⊆
        req_domain.entities` holds (T2). Returns a fresh dict per call.
        """
        asset_entities = {}
        for node in all_nodes.values():
            if node["type"] in ["table", "file", "table_model"]:
                asset_entities[node["name"]] = self._entity_object(node["name"])
        return asset_entities

    # ------------------------------------------------------------------
    # Mode dispatch
    # ------------------------------------------------------------------
    def normalize(self):
        if self.mode == "functional":
            self._normalize_functional()
        else:
            self._normalize_structural()

    # ------------------------------------------------------------------
    # Structural mode (original behavior — 1 program = 1 requirement)
    # ------------------------------------------------------------------
    def _normalize_structural(self):
        all_nodes, all_edges = self._gather_nodes_and_edges()
        shared_assets = self._identify_shared_assets(all_nodes, all_edges)

        # 1. Assign programs to domains based on shared data access
        program_to_domain = {}
        for asset_name, asset_info in shared_assets.items():
            if len(asset_info["legacy_nodes"]) > 0:
                domain_name = f"Domain_{asset_name.lower().replace('.', '_').replace('-', '_')}"
                if domain_name not in self.requirements_graph["domains"]:
                    self.requirements_graph["domains"][domain_name] = {
                        "requirements": {},
                        "entities": {}
                    }
                self.requirements_graph["domains"][domain_name]["entities"][asset_name] = \
                    self._entity_object(asset_name)
                for prog_id in asset_info["legacy_nodes"]:
                    program_to_domain[prog_id] = domain_name

        # Assign remaining isolated programs to app-core domains
        for node_id, node in all_nodes.items():
            if node["type"] in ["program", "class"] and node_id not in program_to_domain:
                app_domain = f"Domain_{node['app'].lower()}_core"
                if app_domain not in self.requirements_graph["domains"]:
                    self.requirements_graph["domains"][app_domain] = {
                        "requirements": {},
                        "entities": {}
                    }
                program_to_domain[node_id] = app_domain

        # Build a lookup of asset_name -> entity object (for co-location)
        asset_entities = self._build_asset_entities(all_nodes)

        # 2. Generate 1:1 requirement nodes
        for node_id, node in all_nodes.items():
            if node["type"] in ["program", "class"]:
                domain = program_to_domain.get(node_id, "Domain_general")
                if domain not in self.requirements_graph["domains"]:
                    self.requirements_graph["domains"][domain] = {
                        "requirements": {},
                        "entities": {}
                    }

                req_id = f"REQ_{node['name'].upper().replace('.', '_')}"
                dependencies = []
                data_access = []
                for edge in all_edges:
                    if edge["source"] == node_id:
                        target_node = all_nodes.get(edge["target"])
                        if target_node:
                            if target_node["type"] in ["program", "class"]:
                                target_req = f"REQ_{target_node['name'].upper().replace('.', '_')}"
                                dependencies.append(target_req)
                            elif target_node["type"] in ["table", "file", "table_model"]:
                                data_access.append(target_node["name"])

                # T2: de-dup data_access (fixes repeated-asset bug) and keep it stable
                data_access = sorted(set(data_access))

                # T2 co-location: copy every accessed asset's entity into THIS
                # requirement's own domain so `data_access ⊆ domain.entities`.
                # This is additive — the per-asset Domain_{asset} entity domains
                # built in step 1 still exist (backward-compat for test_integration).
                req_domain_entities = self.requirements_graph["domains"][domain]["entities"]
                for asset_name in data_access:
                    if asset_name in asset_entities and asset_name not in req_domain_entities:
                        req_domain_entities[asset_name] = asset_entities[asset_name]

                self.requirements_graph["domains"][domain]["requirements"][req_id] = {
                    "title": f"Migrate {node['name']}",
                    "description": f"Functional rules and processing logic for {node['name']} (Source: {node['app']} - {node['file_path']}). [TBD: Extract business rules via LLM]",
                    "legacy_components": [node_id],
                    "data_access": data_access,
                    "dependencies": sorted(set(dependencies)),
                    # T3: rule slots are first-class (empty in the raw draft profile;
                    # populated later by enrich_requirements.py).
                    "business_rules": [],
                    "validations": [],
                    "error_paths": []
                }

    # ------------------------------------------------------------------
    # Functional mode — capability-based grouping
    # ------------------------------------------------------------------
    def _normalize_functional(self):
        all_nodes, all_edges = self._gather_nodes_and_edges()
        shared_assets = self._identify_shared_assets(all_nodes, all_edges)

        # Collect only program/class nodes
        programs = {nid: n for nid, n in all_nodes.items()
                    if n["type"] in ["program", "class"]}

        # ----------------------------------------------------------
        # Step 1: Build call graph
        # ----------------------------------------------------------
        callers_of = defaultdict(set)   # node_id → set of callers
        callees_of = defaultdict(set)   # node_id → set of callees
        for edge in all_edges:
            src, tgt = edge["source"], edge["target"]
            if src in programs and tgt in programs:
                callers_of[tgt].add(src)
                callees_of[src].add(tgt)

        # ----------------------------------------------------------
        # Step 2: Leaf merging — single-caller programs are absorbed
        # ----------------------------------------------------------
        merged_into = {}   # child → parent
        for node_id in list(programs.keys()):
            callers = callers_of.get(node_id, set())
            if len(callers) == 1:
                parent = next(iter(callers))
                # Don't merge if it would create a cycle (parent already merged into child)
                if merged_into.get(parent) != node_id:
                    merged_into[node_id] = parent

        # Resolve transitive merges (A→B→C means A and B both merge into C)
        def resolve_root(nid):
            visited = set()
            while nid in merged_into:
                if nid in visited:
                    break  # cycle guard
                visited.add(nid)
                nid = merged_into[nid]
            return nid

        # Build capability groups: root → [members]
        capability_groups = defaultdict(list)
        for node_id in programs:
            root = resolve_root(node_id)
            capability_groups[root].append(node_id)

        # ----------------------------------------------------------
        # Step 3: Data affinity — merge groups sharing 3+ data assets
        # ----------------------------------------------------------
        # Compute data access per capability group
        group_data = {}
        for root, members in capability_groups.items():
            combined_access = set()
            for member in members:
                combined_access |= self._program_data_access(member, all_nodes, all_edges)
            group_data[root] = combined_access

        # Find pairs with high overlap and merge
        roots = list(group_data.keys())
        union_find = {r: r for r in roots}

        def find(x):
            while union_find[x] != x:
                union_find[x] = union_find[union_find[x]]
                x = union_find[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                union_find[rb] = ra

        for i in range(len(roots)):
            for j in range(i + 1, len(roots)):
                shared = group_data[roots[i]] & group_data[roots[j]]
                if len(shared) >= 3:
                    union(roots[i], roots[j])

        # Rebuild groups after data-affinity merging
        affinity_groups = defaultdict(list)
        for root in roots:
            affinity_groups[find(root)].extend(capability_groups[root])

        # ----------------------------------------------------------
        # Step 4: Dead code detection
        # ----------------------------------------------------------
        # Programs with no incoming calls at all (not called by anyone, not a root)
        # and that also don't access any data — flag as review candidates
        dead_candidates = set()
        for node_id in programs:
            if not callers_of.get(node_id) and not self._program_data_access(node_id, all_nodes, all_edges):
                # No callers AND no data access — likely dead
                dead_candidates.add(node_id)

        # Asset_name -> entity object lookup (for co-location in Step 6).
        asset_entities = self._build_asset_entities(all_nodes)

        # ----------------------------------------------------------
        # Step 5: Build domains from shared assets (same as structural)
        #
        # T2: In functional mode the requirement (capability) is co-located with
        # its entities in Step 6, so these per-asset Domain_{asset} domains are
        # only PROVISIONAL. Any that end up entity-only (no requirements) are
        # suppressed at the end of this method so functional output has no
        # entity-only domain — the capability-plan intent. (Structural mode
        # keeps them for backward-compat with test_integration.)
        # ----------------------------------------------------------
        for asset_name, asset_info in shared_assets.items():
            if len(asset_info["legacy_nodes"]) > 0:
                domain_name = f"Domain_{asset_name.lower().replace('.', '_').replace('-', '_')}"
                if domain_name not in self.requirements_graph["domains"]:
                    self.requirements_graph["domains"][domain_name] = {
                        "requirements": {},
                        "entities": {}
                    }
                self.requirements_graph["domains"][domain_name]["entities"][asset_name] = \
                    self._entity_object(asset_name)

        # ----------------------------------------------------------
        # Step 6: Assign groups to domains & generate capability reqs
        # ----------------------------------------------------------
        for group_root, members in affinity_groups.items():
            # Determine domain by data access
            combined_access = set()
            for member in members:
                combined_access |= self._program_data_access(member, all_nodes, all_edges)

            # Pick domain: use the most-accessed asset's domain
            if combined_access:
                # Find the asset accessed by the most members
                asset_counts = defaultdict(int)
                for member in members:
                    for asset in self._program_data_access(member, all_nodes, all_edges):
                        asset_counts[asset] += 1
                primary_asset = max(asset_counts, key=asset_counts.get)
                domain_name = f"Domain_{primary_asset.lower().replace('.', '_').replace('-', '_')}"
            else:
                # Isolated group — use app-based domain
                first_node = all_nodes[members[0]]
                domain_name = f"Domain_{first_node['app'].lower()}_core"

            if domain_name not in self.requirements_graph["domains"]:
                self.requirements_graph["domains"][domain_name] = {
                    "requirements": {},
                    "entities": {}
                }

            # T2 co-location: insert every accessed asset's entity INTO the
            # capability's chosen domain, so `data_access ⊆ domain.entities`
            # for the capability that owns those accesses.
            cap_domain_entities = self.requirements_graph["domains"][domain_name]["entities"]
            for asset_name in combined_access:
                if asset_name in asset_entities and asset_name not in cap_domain_entities:
                    cap_domain_entities[asset_name] = asset_entities[asset_name]

            # Generate capability name from intent
            cap_id, title = self._capability_name(members, all_nodes, all_edges, combined_access)

            # Determine status
            all_dead = all(m in dead_candidates for m in members)
            status = "review" if all_dead else "active"

            # Collect dependencies: calls from this group to programs outside this group
            member_set = set(members)
            external_deps = set()
            for member in members:
                for callee in self._program_call_targets(member, all_nodes, all_edges):
                    root_of_callee = find(resolve_root(callee)) if callee in programs else None
                    if root_of_callee and root_of_callee != find(group_root):
                        # This callee is in a different capability group — it's an external dependency
                        callee_group_members = affinity_groups.get(root_of_callee, [callee])
                        callee_cap_id, _ = self._capability_name(
                            callee_group_members, all_nodes, all_edges,
                            set().union(*(self._program_data_access(m, all_nodes, all_edges) for m in callee_group_members))
                        )
                        external_deps.add(callee_cap_id)

            # Build description showing which programs were merged
            member_names = sorted(all_nodes[m]["name"] for m in members)
            if len(members) == 1:
                desc_prefix = f"Business capability from {member_names[0]}"
            else:
                desc_prefix = f"Business capability merged from {', '.join(member_names)}"

            source_paths = [f"{all_nodes[m]['app']}:{all_nodes[m]['file_path']}" for m in members]

            self.requirements_graph["domains"][domain_name]["requirements"][cap_id] = {
                "title": title,
                "description": f"{desc_prefix}. Sources: {'; '.join(source_paths)}. [TBD: Extract business rules via LLM]",
                "legacy_components": sorted(members),
                "data_access": sorted(combined_access),
                "dependencies": sorted(external_deps),
                "status": status,
                "merged_programs": member_names,
                # T3: rule slots are first-class (empty in the raw draft profile;
                # populated later by enrich_requirements.py).
                "business_rules": [],
                "validations": [],
                "error_paths": []
            }

        # ----------------------------------------------------------
        # Step 7 (functional only): suppress entity-only domains.
        #
        # After co-location, every accessed entity also lives in the domain of
        # the capability that uses it. Any provisional Domain_{asset} left with
        # entities but ZERO requirements is a 1:1 file-mirror artifact, not a
        # business capability — drop it so functional output has no entity-only
        # (and no empty) domain. Structural mode intentionally keeps these.
        # ----------------------------------------------------------
        self._drop_entity_only_domains()

    def _drop_entity_only_domains(self):
        """Remove domains that carry no requirements (entity-only or empty)."""
        for domain_name in list(self.requirements_graph["domains"].keys()):
            domain = self.requirements_graph["domains"][domain_name]
            if not domain.get("requirements"):
                del self.requirements_graph["domains"][domain_name]

    def _capability_name(self, members, all_nodes, all_edges, data_access):
        """Generate a business-intent name from the programs and data they touch."""
        member_names = [all_nodes[m]["name"] for m in members]

        if not data_access:
            # No data access — name from the primary program
            primary = sorted(member_names)[0]
            cap_id = f"CAP_{primary.upper().replace('.', '_').replace('-', '_')}"
            return cap_id, f"{primary} Processing"

        # Name from the primary data entity
        # Heuristic: the entity accessed by the most members is the "primary" entity
        entity_counts = defaultdict(int)
        for member in members:
            for asset in self._program_data_access(member, all_nodes, all_edges):
                entity_counts[asset] += 1

        primary_entity = max(entity_counts, key=entity_counts.get)
        entity_clean = primary_entity.replace(".", "_").replace("-", "_")

        # Verb from edge types: more WRITES = "Management", more READS = "Processing"
        write_count = 0
        read_count = 0
        for member in members:
            for edge in all_edges:
                if edge["source"] == member:
                    if edge["type"].upper() in ["WRITES", "WRITE", "REWRITE", "INSERT", "UPDATE"]:
                        write_count += 1
                    elif edge["type"].upper() in ["READS", "READ", "SELECT", "QUERY"]:
                        read_count += 1

        if write_count > read_count:
            verb = "Management"
        elif len(members) > 1:
            verb = "Processing"
        else:
            verb = "Processing"

        cap_id = f"CAP_{entity_clean.upper()}_{verb.upper()}"

        # Deduplicate by appending member count if needed
        if len(members) > 1:
            title = f"{primary_entity} {verb} ({len(members)} programs)"
        else:
            title = f"{primary_entity} {verb}"

        return cap_id, title

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def save_requirements(self, output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(self.requirements_graph, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Normalizes a legacy code graph into a draft Requirements Graph."
    )
    parser.add_argument('--input', required=True, help='Path to legacy code graph JSON')
    parser.add_argument('--output', required=True, help='Path to output Requirements Graph JSON')
    parser.add_argument('--mode', choices=['structural', 'functional'], default=None,
                        help='structural = 1 program per requirement (code-equivalent). '
                             'functional = merge into business capabilities (functional-equivalent). '
                             'If omitted, falls back to config.json migration_mode, then "structural".')
    parser.add_argument('--config', default='.anti-legacy/config.json',
                        help='Path to project config.json (read for migration_mode when --mode is omitted).')

    args = parser.parse_args()

    try:
        with open(args.input, 'r') as f:
            code_graph = json.load(f)
    except Exception as e:
        print(f"Error reading input graph file: {e}", file=sys.stderr)
        sys.exit(1)

    # T1: resolve mode with a 3-tier precedence: explicit --mode flag wins,
    # then config.json migration_mode, then the legacy default 'structural'.
    # The config load is best-effort and NEVER fatal — CLI/test invocations
    # often run in tmpdirs with no config; a missing/unreadable/invalid config
    # must silently degrade to {} so the default applies.
    cfg = {}
    try:
        with open(args.config, 'r') as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}

    config_mode = cfg.get('migration_mode')
    if config_mode not in ('structural', 'functional'):
        config_mode = None
    mode = args.mode or config_mode or 'structural'

    normalizer = GraphNormalizer(code_graph, mode=mode)
    normalizer.normalize()
    normalizer.save_requirements(args.output)
    print(f"Draft Requirements Graph ({mode} mode) successfully saved to: {args.output}")


if __name__ == '__main__':
    main()
