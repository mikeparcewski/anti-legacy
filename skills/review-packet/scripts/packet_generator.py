#!/usr/bin/env python3
import json
import sys
import os
import argparse

class ReviewPacketGenerator:
    def __init__(self, requirements_path):
        self.requirements_path = requirements_path
        try:
            with open(requirements_path, 'r') as f:
                self.requirements = json.load(f)
        except Exception as e:
            print(f"Error reading Requirements JSON: {e}", file=sys.stderr)
            sys.exit(1)

    def generate_markdown(self):
        md = []
        md.append("# Digital Review Packet & Modernization Blueprint")
        mode = self.requirements.get("metadata", {}).get("migration_mode", "structural").upper()
        md.append(f"\n> **Generated from Requirements Graph**: `{os.path.basename(self.requirements_path)}`  ")
        md.append(f"> **Collaboration Mode**: Git & Fileshare (Serverless)  ")
        md.append(f"> **Migration Paradigm**: **{mode}** (Match {'Functionality' if mode == 'FUNCTIONAL' else 'Code'})")
        
        md.append("\n## Table of Contents\n")
        md.append("1. [Migration Paradigm](#migration-paradigm)")
        md.append("2. [Architecture Overview](#architecture-overview)")
        idx = 3
        for domain in self.requirements.get("domains", {}):
            md.append(f"{idx}. [Domain: {domain}](#domain-{domain.lower()})")
            idx += 1
        md.append(f"{idx}. [Rigid Sign-off Gate Checklist](#rigid-sign-off-gate-checklist)")

        # Migration Paradigm Details
        md.append("\n## Migration Paradigm\n")
        if mode == "FUNCTIONAL":
            md.append("This project is executing under **Match Functionality** mode. Legacy code files are merged into higher-level business capabilities to improve architecture and reduce technical debt. For details, see [CODE_VS_FUNCTIONAL.md](CODE_VS_FUNCTIONAL.md).")
        else:
            md.append("This project is executing under **Match Code** mode. Legacy modules are migrated 1-to-1 to maintain structural equivalence and minimize conversion risk. For details, see [CODE_VS_FUNCTIONAL.md](CODE_VS_FUNCTIONAL.md).")

        # 1. Architecture Overview (Mermaid call-graph)
        md.append("\n## Architecture Overview")
        md.append("\nBelow is the logical relationship flow between requirements and domains:\n")
        md.append("```mermaid")
        md.append("flowchart TD")
        
        for domain, domain_data in self.requirements.get("domains", {}).items():
            md.append(f"  subgraph {domain} [\"Domain: {domain}\"]")
            for req_id, req in domain_data.get("requirements", {}).items():
                md.append(f"    {req_id}[\"{req['title']}\"]")
            md.append("  end")

        # Edges
        for domain, domain_data in self.requirements.get("domains", {}).items():
            for req_id, req in domain_data.get("requirements", {}).items():
                for dep in req.get("dependencies", []):
                    md.append(f"  {dep} --> {req_id}")
        md.append("```\n")

        # 2. Domain Details
        for domain, domain_data in self.requirements.get("domains", {}).items():
            md.append(f"---")
            md.append(f"\n## Domain: {domain}")
            
            # Entities
            entities = domain_data.get("entities", {})
            if entities:
                md.append("\n### Logical Data Entities")
                for entity_name, entity in entities.items():
                    md.append(f"\n#### Entity: {entity_name}")
                    md.append(f"*{entity.get('description', '')}*")
                    md.append("\n| Field | Type | Description |")
                    md.append("|---|---|---|")
                    for field in entity.get("fields", []):
                        md.append(f"| {field['name']} | {field['type']} | {field['description']} |")
            
            # Requirements
            requirements = domain_data.get("requirements", {})
            if requirements:
                md.append("\n### Functional Requirements")
                for req_id, req in requirements.items():
                    md.append(f"\n#### [{req_id}] {req['title']}")
                    md.append(f"\n**Business Logic Description**:\n{req['description']}\n")
                    
                    md.append(f"**Data Entities Accessed**:")
                    if req.get("data_access"):
                        md.append(", ".join([f"`{d}`" for d in req["data_access"]]))
                    else:
                        md.append("*None*")
                    md.append("\n")

                    md.append(f"**Legacy Source Components**:")
                    md.append(", ".join([f"`{c}`" for c in req.get("legacy_components", [])]))
                    md.append("\n")

                    md.append(f"**Dependencies**:")
                    if req.get("dependencies"):
                        md.append(", ".join([f"`{d}`" for d in req["dependencies"]]))
                    else:
                        md.append("*None*")
                    md.append("\n")

        # 3. Sign-off Gate Checklist
        md.append("\n---")
        md.append("\n## Rigid Sign-off Gate Checklist")
        md.append("\nTo record your approval, run the `anti-legacy:gatekeeper` command or record an attestation in `.anti-legacy/evidence/` via git.")
        md.append("\n| Gate ID | Description | Required Roles | Status |")
        md.append("|---|---|---|---|")
        md.append("| **GATE_1_DESIGN** | Review and sign-off on target Requirements Graph | Lead Architect, Lead Developer | `PENDING` |")
        md.append("| **GATE_2_PLAN** | Review and sign-off on the generated execution plan | Product Manager, Tech Lead | `PENDING` |")
        md.append("| **GATE_3_BUILD** | Automated verification of test-parity and LSP syntax | Deterministic (Compiler) | `PENDING` |")
        md.append("| **GATE_4_UAT** | Independent UAT Reviewer validation of target requirements | UAT Lead, Business Analyst | `PENDING` |")

        return "\n".join(md)

    def write_packet(self, output_path):
        content = self.generate_markdown()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(content)

def main():
    parser = argparse.ArgumentParser(description="Compiles Requirements Graph JSON into a Markdown Review Packet.")
    parser.add_argument('--input', required=True, help='Path to Requirements Graph JSON')
    parser.add_argument('--output', required=True, help='Path to output Markdown Review Packet')
    parser.add_argument('--blueprint', help='Path to blueprint.json (optional — appends blueprint summary)')
    parser.add_argument('--test-strategy', help='Path to test-strategy.md (optional — appends test summary)')

    args = parser.parse_args()

    generator = ReviewPacketGenerator(args.input)

    # Append blueprint section if provided
    if args.blueprint and os.path.exists(args.blueprint):
        try:
            with open(args.blueprint, 'r') as f:
                bp = json.load(f)
            generator.blueprint = bp
        except Exception:
            pass

    # Append test strategy section if provided
    if args.test_strategy and os.path.exists(args.test_strategy):
        try:
            with open(args.test_strategy, 'r') as f:
                generator.test_strategy_md = f.read()
        except Exception:
            pass

    generator.write_packet(args.output)
    print(f"Review Packet successfully compiled to: {args.output}")

if __name__ == '__main__':
    main()
