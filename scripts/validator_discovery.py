#!/usr/bin/env python3
"""
Validator Discovery & Gate Execution Orchestrator.
Discovers compilation, code quality, and security tools on the host environment,
executes them based on target stack requirements, and enforces gate blockers.
"""
import os
import sys
import json
import shutil
import subprocess
import argparse
from datetime import datetime, timezone

# Add parent directory to sys.path so we can import other scripts
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Default tool mappings per target stack
DEFAULT_TOOLS = {
    "python": {
        "required": ["python3"],
        "optional": ["flake8", "bandit"]
    },
    "go": {
        "required": ["go"],
        "optional": ["go vet", "gosec"]
    },
    "java": {
        "required": ["javac"],
        "optional": ["checkstyle", "spotbugs"]
    },
    "dotnet": {
        "required": ["dotnet"],
        "optional": ["dotnet-format"]
    },
    "csharp": {
        "required": ["dotnet"],
        "optional": ["dotnet-format"]
    }
}

class ValidatorDiscovery:
    def __init__(self, workspace_path, config_path=".anti-legacy/config.json"):
        self.workspace_path = workspace_path
        self.config_path = config_path
        self.config = self._load_config()
        self.stack = self.config.get("target_stack", "python").lower()

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def get_tool_definition(self):
        """Get required and optional tools for the stack, prioritizing user configuration."""
        user_validators = self.config.get("validators", {})
        default = DEFAULT_TOOLS.get(self.stack, {"required": [], "optional": []})
        
        required = user_validators.get("required", default["required"])
        optional = user_validators.get("optional", default["optional"])
        return required, optional

    def discover_tools(self):
        """Check availability of tools in the system PATH."""
        required, optional = self.get_tool_definition()
        results = {}
        
        for tool in required + optional:
            # Check the first word of the command (e.g. 'go' in 'go vet')
            cmd_name = tool.split()[0]
            path = shutil.which(cmd_name)
            is_installed = path is not None
            
            is_required = tool in required
            results[tool] = {
                "installed": is_installed,
                "required": is_required,
                "path": path
            }
            
        return results

    def print_discovery_report(self, results):
        """Print status of all discovered tools and return True if all required tools exist."""
        print(f"\n--- Validator Discovery Report (Target Stack: {self.stack}) ---")
        all_required_present = True
        
        for tool, info in results.items():
            status = "INSTALLED" if info["installed"] else "MISSING"
            req_str = "Required" if info["required"] else "Optional"
            path_str = f" ({info['path']})" if info["installed"] else ""
            print(f"  [{status:<9}] {tool:<15} ({req_str}){path_str}")
            
            if info["required"] and not info["installed"]:
                all_required_present = False
                print(f"    [ALERT] Required tool '{tool}' is not installed! Gate validations using this tool will fail.", file=sys.stderr)
                
        print("-----------------------------------------------------------\n")
        return all_required_present


class ValidatorRunner:
    def __init__(self, workspace, config_path=".anti-legacy/config.json", manifest_path=".anti-legacy/manifest.json"):
        self.workspace = workspace
        self.config_path = config_path
        self.manifest_path = manifest_path
        self.discovery = ValidatorDiscovery(workspace, config_path)
        self.stack = self.discovery.stack

    def run_gate(self, gate_id):
        """Execute validation checks for the specified gate."""
        gate_id = gate_id.upper().strip()
        print(f"Starting deterministic validation for gate: {gate_id}...")
        
        # Discover tools first
        tool_results = self.discovery.discover_tools()
        
        if gate_id == "GATE_0_DISCOVERY":
            return self._run_gate_0_discovery()
        elif gate_id == "GATE_1_DESIGN":
            return self._run_gate_1_design()
        elif gate_id == "GATE_2_PLAN":
            return self._run_gate_2_plan()
        elif gate_id == "GATE_3_BUILD":
            return self._run_gate_3_build(tool_results)
        elif gate_id == "GATE_3B_SEMANTIC":
            return self._run_gate_3b_semantic()
        elif gate_id == "GATE_4_UAT":
            return self._run_gate_4_uat()
        else:
            print(f"Error: Unknown gate '{gate_id}'", file=sys.stderr)
            return False

    def _run_gate_1_design(self):
        """GATE_1_DESIGN: Checks blueprint and requirements graph consistency."""
        req_graph_path = os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json")
        blueprint_path = os.path.join(self.workspace, ".anti-legacy", "requirements", "blueprint.json")
        
        nfr_path = self.discovery.config.get("paths", {}).get("nfrs")
        if nfr_path:
            if not os.path.isabs(nfr_path):
                nfr_path = os.path.join(self.workspace, nfr_path)
        else:
            nfr_path = os.path.join(self.workspace, ".anti-legacy", "requirements", "nfrs.md")
        
        errors = []
        
        # 1. Requirements Graph checks
        if not os.path.exists(req_graph_path):
            errors.append("Missing requirements_graph.json")
        else:
            try:
                with open(req_graph_path) as f:
                    rg = json.load(f)
                
                # Fast, dependency-free pre-check: traceability to legacy components.
                for domain, d_data in rg.get("domains", {}).items():
                    reqs = d_data.get("requirements", {})
                    for req_id, req in reqs.items():
                        if not req.get("legacy_components"):
                            errors.append(f"Design Compliance: Requirement '{req_id}' has no legacy component traceability.")

                # Real schema gate: validate the whole graph against the enriched
                # profile. This replaces the weak `if not req.get("business_rules")`
                # truthiness check (which passed string-form/malformed rules) with a
                # proper Draft7 validation. If jsonschema is unavailable, append an
                # ERROR rather than silently skipping so the residual is surfaced.
                try:
                    import jsonschema
                    from jsonschema import Draft7Validator, RefResolver
                except ImportError:
                    errors.append(
                        "Design Compliance: 'jsonschema' is not installed; cannot validate "
                        "requirements_graph.json against the enriched profile "
                        "(pip install -r requirements.txt)."
                    )
                else:
                    enriched_path = os.path.join(self.workspace, "schemas", "requirements-graph.enriched.schema.json")
                    base_path = os.path.join(self.workspace, "schemas", "requirements-graph.schema.json")
                    # fall back to plugin-relative schemas if not under workspace
                    if not os.path.exists(enriched_path):
                        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        enriched_path = os.path.join(here, "schemas", "requirements-graph.enriched.schema.json")
                        base_path = os.path.join(here, "schemas", "requirements-graph.schema.json")
                    with open(enriched_path) as ef:
                        enriched_schema = json.load(ef)
                    with open(base_path) as bf:
                        base_schema = json.load(bf)
                    store = {"requirements-graph.schema.json": base_schema,
                             base_schema.get("$id", "requirements-graph.schema.json"): base_schema}
                    resolver = RefResolver(base_uri="", referrer=enriched_schema, store=store)
                    validator = Draft7Validator(enriched_schema, resolver=resolver)
                    schema_errors = sorted(validator.iter_errors(rg), key=lambda e: list(e.path))
                    for e in schema_errors[:50]:   # cap noise; 0 expected post-migration
                        loc = "/".join(str(p) for p in e.path) or "<root>"
                        errors.append(f"Design Compliance: requirements_graph.json fails enriched schema at '{loc}': {e.message}")
                    if len(schema_errors) > 50:
                        errors.append(f"Design Compliance: ...and {len(schema_errors) - 50} more enriched-schema errors.")
            except Exception as e:
                errors.append(f"Failed to parse requirements_graph.json: {e}")

        # 2. Blueprint checks
        if os.path.exists(blueprint_path):
            try:
                with open(blueprint_path) as f:
                    bp = json.load(f)
                
                # Ensure no money fields map to float
                components = bp.get("components", {})
                for comp_id, comp in components.items():
                    for field in comp.get("fields", []):
                        name = field.get("name", "").lower()
                        ftype = field.get("type", "").lower()
                        if any(k in name for k in ["money", "balance", "amount", "salary", "tax", "price"]):
                            if ftype in ["float", "double"]:
                                errors.append(f"Design Compliance: Component '{comp_id}' field '{field.get('name')}' is currency-related but typed as '{ftype}' instead of DECIMAL/BigDecimal.")
            except Exception as e:
                errors.append(f"Failed to parse blueprint.json: {e}")

        # 3. NFR checks
        if not os.path.exists(nfr_path):
            errors.append("Design Compliance: Non-Functional Requirements document (nfrs.md) is missing.")

        # Print result
        if errors:
            print("GATE_1_DESIGN: Compliance errors found:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return False
            
        print("GATE_1_DESIGN: Compliance checks passed. ✓")
        return True

    def _run_gate_2_plan(self):
        """GATE_2_PLAN: Checks task order strategy compliance."""
        task_file = self.discovery.config.get("paths", {}).get("task_plan")
        if task_file:
            if not os.path.isabs(task_file):
                task_file = os.path.join(self.workspace, task_file)
        else:
            task_file = os.path.join(self.workspace, ".anti-legacy", "task.md")
        req_graph_path = os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json")
        
        if not os.path.exists(task_file):
            print("GATE_2_PLAN Error: task.md build plan not found.", file=sys.stderr)
            return False
        if not os.path.exists(req_graph_path):
            print("GATE_2_PLAN Error: requirements_graph.json not found.", file=sys.stderr)
            return False
            
        try:
            from scripts.planner_utils import verify_order
            with open(req_graph_path) as f:
                rg = json.load(f)
                
            strategy = self.discovery.config.get("traversal_strategy", "bottom-up")
            success, errors = verify_order(task_file, rg, strategy)
            if not success:
                print(f"GATE_2_PLAN: Traversal order check failed for strategy '{strategy}':", file=sys.stderr)
                for err in errors:
                    print(f"  - {err}", file=sys.stderr)
                return False
                
            print(f"GATE_2_PLAN: Traversal order checks passed (Strategy: {strategy}). ✓")
            return True
        except Exception as e:
            print(f"GATE_2_PLAN Error during traversal ordering check: {e}", file=sys.stderr)
            return False

    def _run_gate_3_build(self, tool_results):
        """GATE_3_BUILD: Runs compiler, quality, and security tools."""
        success = True
        
        # 1. Compilation check
        # B5 (compiler tier): the compiler is REQUIRED. Overall GATE_3_BUILD
        # success requires build_result['status'] == 'PASS' exactly. A WARNING
        # on the compiler tier (e.g. an "optional" compiler that is missing)
        # must NOT pass build integrity — any non-PASS build_result fails the
        # gate. (_run_compiler already returns FAIL for a missing required
        # compiler, so no change is needed there.)
        build_result = self._run_compiler(tool_results)
        self._record_evidence("build-integrity", build_result)
        if build_result["status"] != "PASS":
            print(f"GATE_3_BUILD: Target compilation check FAILED (status={build_result['status']}).", file=sys.stderr)
            success = False
        else:
            print("GATE_3_BUILD: Target compilation check PASSED. ✓")

        # 2. Linter / Code Quality check
        quality_result = self._run_linter(tool_results)
        self._record_evidence("code-quality", quality_result)
        if quality_result["status"] == "FAIL":
            print("GATE_3_BUILD: Target code quality check FAILED.", file=sys.stderr)
            success = False
        elif quality_result["status"] == "WARNING":
            print("GATE_3_BUILD: Target code quality linter tool is missing (optional warning).")
        else:
            print("GATE_3_BUILD: Target code quality check PASSED. ✓")

        # 3. Security Scan check
        security_result = self._run_security_scan(tool_results)
        self._record_evidence("security-scan", security_result)
        if security_result["status"] == "FAIL":
            print("GATE_3_BUILD: Target security scan FAILED.", file=sys.stderr)
            success = False
        elif security_result["status"] == "WARNING":
            print("GATE_3_BUILD: Target security scanner tool is missing (optional warning).")
        else:
            print("GATE_3_BUILD: Target security scan PASSED. ✓")

        # 4. Round-trip rule-coverage check (M1).
        # The 'functional_comparison_report: 0 FAIL, coverage>=1.0' rule used to
        # live only in skill prose. Enforce it deterministically here so an
        # incomplete or failing round-trip blocks build integrity. Runs alongside
        # (does not replace) the compiler/quality/security tiers.
        if not self._check_round_trip_coverage():
            success = False

        return success

    def _check_round_trip_coverage(self):
        """M1: deterministic round-trip rule-coverage gate.

        Reads .anti-legacy/evidence/functional_comparison_report.json under the
        workspace and BLOCKS (returns False) when the report is missing, any
        requirement FAILs, or aggregate rule coverage < 1.0. Returns True only
        when the report is present, has 0 FAILs, and rule_coverage >= 1.0.
        """
        report_path = os.path.join(
            self.workspace, ".anti-legacy", "evidence",
            "functional_comparison_report.json"
        )
        if not os.path.exists(report_path):
            print(
                "GATE_3_BUILD: BLOCK - round-trip functional_comparison_report.json "
                f"is missing ({report_path}); the legacy->target round-trip rule "
                "coverage was never produced. Run compare_graphs to generate it.",
                file=sys.stderr,
            )
            return False

        try:
            with open(report_path) as f:
                report = json.load(f)
        except Exception as e:
            print(
                f"GATE_3_BUILD: BLOCK - could not parse functional_comparison_report.json: {e}",
                file=sys.stderr,
            )
            return False

        # The aggregate block carries the canonical numbers; fall back to the
        # whole report if no aggregate is present.
        agg = report.get("aggregate", report)

        # Prefer the canonical fail_count, accept 'fail' as a synonym (compare_graphs
        # emits both; older consumers may key on either). When neither is present,
        # fall back to counting requirements[*].status == 'FAIL'.
        fails = agg.get("fail_count", agg.get("fail"))
        if fails is None:
            reqs = report.get("requirements", [])
            if isinstance(reqs, list) and reqs:
                fails = sum(
                    1 for r in reqs
                    if str(r.get("status", "")).upper() == "FAIL"
                )
            else:
                fails = None  # truly unknown -> block below

        cov = agg.get("rule_coverage")

        if fails is None:
            print(
                "GATE_3_BUILD: BLOCK - functional_comparison_report.json has no "
                "fail_count/fail and no requirements[] to count; cannot confirm "
                "0 failing round-trip requirements.",
                file=sys.stderr,
            )
            return False
        if fails > 0:
            print(
                f"GATE_3_BUILD: BLOCK - round-trip comparison has {fails} FAILing "
                "requirement(s); the round-trip must have 0 FAIL.",
                file=sys.stderr,
            )
            return False
        if cov is None:
            print(
                "GATE_3_BUILD: BLOCK - functional_comparison_report.json has no "
                "rule_coverage; cannot confirm full (>=1.0) round-trip coverage.",
                file=sys.stderr,
            )
            return False
        try:
            cov_f = float(cov)
        except (TypeError, ValueError):
            print(
                f"GATE_3_BUILD: BLOCK - rule_coverage value {cov!r} is not numeric.",
                file=sys.stderr,
            )
            return False
        if cov_f < 1.0:
            print(
                f"GATE_3_BUILD: BLOCK - round-trip rule coverage {cov_f:.4f} < 1.0; "
                "every business rule must be covered.",
                file=sys.stderr,
            )
            return False

        print("GATE_3_BUILD: Round-trip rule-coverage check PASSED (0 FAIL, coverage>=1.0). ✓")
        return True

    def _run_gate_3b_semantic(self):
        """GATE_3B_SEMANTIC: Checks for semantic gaps in requirements graph."""
        req_graph_path = os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json")
        if not os.path.exists(req_graph_path):
            print("GATE_3B_SEMANTIC Error: requirements_graph.json not found.", file=sys.stderr)
            return False
            
        try:
            with open(req_graph_path) as f:
                rg = json.load(f)

            def _is_unresolved(g):
                # record_gap writes neither 'status' nor 'resolved', so a freshly
                # recorded gap is unresolved by default. It is resolved only if it
                # explicitly carries status=='resolved' or resolved is True.
                if str(g.get("status", "")).lower() == "resolved":
                    return False
                if g.get("resolved") is True:
                    return False
                return True

            # Each entry: (severity, identifier, requirement_id, description)
            unresolved_high = []

            # MINOR (non-vacuous): track whether ANY semantic_gaps array was found
            # anywhere in the graph (nested per-requirement OR legacy top-level).
            # If semantic validation never recorded a single gaps array, the gate
            # must NOT pass vacuously on an empty graph — we require evidence that
            # semantic validation actually executed.
            saw_gaps_array = False

            # B6: read gaps NESTED per-requirement at
            # rg['domains'][d]['requirements'][req_id]['semantic_gaps'] — this is
            # what semantic_validator.record_gap writes (keys: id/severity/
            # description/legacy_location/target_location/remediation/detected_at).
            for domain, d_data in rg.get("domains", {}).items():
                reqs = d_data.get("requirements", {})
                for req_id, req in reqs.items():
                    if "semantic_gaps" in req:
                        saw_gaps_array = True
                    for gap in req.get("semantic_gaps", []) or []:
                        severity = str(gap.get("severity", "")).upper()
                        if _is_unresolved(gap) and severity in ("HIGH", "CRITICAL"):
                            unresolved_high.append((
                                severity,
                                gap.get("id"),
                                req_id,
                                gap.get("description"),
                            ))

            # Back-compat: still scan a legacy top-level rg['semantic_gaps'] list
            # (keys: gap_id/requirement_id/status) so the legacy shape still blocks.
            if "semantic_gaps" in rg:
                saw_gaps_array = True
            for gap in rg.get("semantic_gaps", []) or []:
                severity = str(gap.get("severity", "")).upper()
                if _is_unresolved(gap) and severity in ("HIGH", "CRITICAL"):
                    unresolved_high.append((
                        severity,
                        gap.get("gap_id"),
                        gap.get("requirement_id"),
                        gap.get("description"),
                    ))

            if not saw_gaps_array:
                print(
                    "GATE_3B_SEMANTIC: Validation failed. No semantic_gaps array "
                    "was recorded anywhere in the requirements graph — semantic "
                    "validation never ran/never recorded its result. The gate "
                    "cannot pass vacuously on a graph that has no evidence of "
                    "semantic validation having executed.",
                    file=sys.stderr,
                )
                return False

            if unresolved_high:
                print("GATE_3B_SEMANTIC: Validation failed. Unresolved HIGH/CRITICAL semantic gaps exist:", file=sys.stderr)
                for severity, ident, req_id, description in unresolved_high:
                    print(f"  - [{severity}] {ident} on {req_id}: {description}", file=sys.stderr)
                return False

            print("GATE_3B_SEMANTIC: Semantic validation checks passed. ✓")
            return True
        except Exception as e:
            print(f"GATE_3B_SEMANTIC Error during checks: {e}", file=sys.stderr)
            return False

    def _run_gate_4_uat(self):
        """GATE_4_UAT: Checks UAT verdicts and coverage."""
        uat_evidence_dir = os.path.join(self.workspace, ".anti-legacy", "evidence", "uat")
        manifest_path = self.manifest_path
        
        errors = []
        if not os.path.exists(manifest_path):
            errors.append("manifest.json not found")
        else:
            try:
                # 1. Verify all test verdicts are PASS in evidence
                if os.path.exists(uat_evidence_dir):
                    evidence_files = [f for f in os.listdir(uat_evidence_dir) if f.endswith('.json')]
                    if not evidence_files:
                        errors.append("UAT Compliance: No UAT domain verdict files found in evidence/uat/.")
                    for ef in evidence_files:
                        with open(os.path.join(uat_evidence_dir, ef)) as f:
                            ev = json.load(f)
                        # B6: uat-crew writes verdict files keyed `verdict`; read it
                        # first, fall back to `status`, uppercase-compare to 'PASS'.
                        v = (ev.get("verdict") or ev.get("status") or "").upper()
                        if v != "PASS":
                            errors.append(f"UAT Compliance: UAT evidence '{ef}' has non-passing verdict: {ev.get('verdict') or ev.get('status')}")

                        # B6: machine-enforce the anti-rubber-stamp rules so a
                        # rubber-stamped PASS cannot clear the gate. Fail if any
                        # finding is CRITICAL/MAJOR, the overall_rationale is
                        # empty/missing, or any finding lacks a target_file_line.
                        # MINOR: coerce a dict-shaped findings into a list of its
                        # values so a dict (rather than a list) cannot crash the
                        # gate when we iterate.
                        findings = ev.get("findings", []) or []
                        if isinstance(findings, dict):
                            findings = list(findings.values())
                        for finding in findings:
                            sev = str(finding.get("severity", "")).upper()
                            if sev in ("CRITICAL", "MAJOR"):
                                errors.append(f"UAT Compliance: UAT evidence '{ef}' has a {sev} finding: {finding.get('description') or finding.get('id') or finding}")
                            if not finding.get("target_file_line"):
                                errors.append(f"UAT Compliance: UAT evidence '{ef}' has a finding without target_file_line: {finding.get('description') or finding.get('id') or finding}")
                        if not ev.get("overall_rationale"):
                            errors.append(f"UAT Compliance: UAT evidence '{ef}' is missing a non-empty overall_rationale (anti-rubber-stamp).")
                else:
                    errors.append("UAT Compliance: UAT evidence directory evidence/uat/ does not exist.")

                # M2: reviewer-independence. The UAT evaluator must NOT be the
                # same person who signed GATE_1_DESIGN (the architect). This was
                # claimed machine-enforced in the docs but no code did it; enforce
                # it deterministically here. The check is vacuous-safe: it only
                # fires when the relevant names are present and non-empty, so the
                # existing tests (which leave roles/evaluators empty) stay green.
                indep_errors = self._check_reviewer_independence(manifest_path)
                errors.extend(indep_errors)
            except Exception as e:
                errors.append(f"Failed during UAT checks: {e}")
                
        if errors:
            print("GATE_4_UAT: UAT compliance errors found:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return False
            
        print("GATE_4_UAT: UAT compliance checks passed. ✓")
        return True

    def _check_reviewer_independence(self, manifest_path):
        """M2: enforce UAT reviewer-independence.

        The GATE_4_UAT evaluator must be a different person from the architect
        who owns GATE_1_DESIGN, otherwise the UAT is a self-review. We compare
        the GATE_4_UAT evaluator (from the manifest gate) against BOTH:
          * config.json roles.architect, and
          * the recorded GATE_1_DESIGN signer (manifest gate evaluator, plus the
            audit.jsonl 'anti-legacy:gate-signed-off' GATE_1_DESIGN evaluator).
        Returns a list of error strings (empty when independent / vacuous). The
        check is vacuous-safe: it only fires when both sides are present and
        non-empty, so a config/manifest with empty roles cannot trip it.
        """
        errors = []

        def _norm(name):
            return str(name or "").strip().lower()

        # 1. UAT evaluator from the manifest gate.
        uat_evaluator = ""
        gate1_signer = ""
        try:
            with open(manifest_path) as f:
                m = json.load(f)
            gates = m.get("gates", {}) or {}
            uat_evaluator = (gates.get("GATE_4_UAT", {}) or {}).get("evaluator", "")
            gate1_signer = (gates.get("GATE_1_DESIGN", {}) or {}).get("evaluator", "")
        except Exception:
            # If the manifest cannot be read we have nothing to compare;
            # stay vacuous rather than crashing the gate.
            return errors

        uat_n = _norm(uat_evaluator)
        if not uat_n:
            # No recorded UAT evaluator -> nothing to compare; vacuous-safe.
            return errors

        # 2. Architect role from config.json.
        architect = ""
        try:
            cfg = self.discovery.config or {}
            architect = (cfg.get("roles", {}) or {}).get("architect", "")
        except Exception:
            architect = ""

        # 3. Latest GATE_1_DESIGN signer from audit.jsonl (sibling of the manifest).
        audit_path = os.path.join(os.path.dirname(manifest_path), "audit.jsonl")
        audit_gate1_signer = ""
        if os.path.exists(audit_path):
            try:
                with open(audit_path) as af:
                    for line in af:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("event") != "anti-legacy:gate-signed-off":
                            continue
                        details = rec.get("details", {}) or {}
                        if details.get("gate_id") == "GATE_1_DESIGN":
                            # last writer wins (most recent sign-off)
                            audit_gate1_signer = details.get("evaluator", "") or audit_gate1_signer
            except Exception:
                audit_gate1_signer = ""

        # Compare against every non-empty architect/signer identity.
        for label, other in (
            ("config roles.architect", architect),
            ("GATE_1_DESIGN signer (manifest)", gate1_signer),
            ("GATE_1_DESIGN signer (audit.jsonl)", audit_gate1_signer),
        ):
            other_n = _norm(other)
            if other_n and other_n == uat_n:
                errors.append(
                    "UAT Compliance: reviewer-independence violated - the "
                    f"GATE_4_UAT evaluator '{uat_evaluator}' is the same person as "
                    f"the {label} ('{other}'). UAT must be reviewed by someone "
                    "other than the GATE_1 architect."
                )

        return errors

    def _run_gate_0_discovery(self):
        """GATE_0_DISCOVERY: Checks that project has been registered and language scanning completed."""
        manifest_path = self.manifest_path
        if not os.path.exists(manifest_path):
            print("GATE_0_DISCOVERY Error: manifest.json not found.", file=sys.stderr)
            return False
        
        try:
            with open(manifest_path) as f:
                m = json.load(f)
            
            # Verify name and stack are initialized
            name = m.get("project", {}).get("name")
            stack = m.get("project", {}).get("target_stack")
            if not name or not stack:
                print("GATE_0_DISCOVERY Error: Project name or target stack is not initialized.", file=sys.stderr)
                return False
                
            # Verify that we have some imports
            imports_dir = os.path.join(self.workspace, ".anti-legacy", "imports")
            if not os.path.exists(imports_dir) or len(os.listdir(imports_dir)) == 0:
                print("GATE_0_DISCOVERY Error: No imported source code repositories found.", file=sys.stderr)
                return False
                
            print("GATE_0_DISCOVERY: Discovery checks passed. ✓")
            return True
        except Exception as e:
            print(f"GATE_0_DISCOVERY Error during checks: {e}", file=sys.stderr)
            return False

    def _run_compiler(self, tool_results):
        """Compile codebase based on target stack."""
        cmd = []
        if self.stack == "python":
            cmd = ["python3", "-m", "compileall", "."]
        elif self.stack in ["go", "golang"]:
            cmd = ["go", "build", "./..."]
        elif self.stack in ["java", "maven"]:
            if os.path.exists(os.path.join(self.workspace, "pom.xml")):
                cmd = ["mvn", "clean", "compile"]
            elif os.path.exists(os.path.join(self.workspace, "gradlew")):
                cmd = ["./gradlew", "compileJava"]
            else:
                cmd = ["javac", "-sourcepath", "src", "src/**/*.java"]
        elif self.stack in ["dotnet", "csharp"]:
            cmd = ["dotnet", "build"]
        else:
            # B3: NO silent-PASS fallback for an unknown/unsupported target_stack.
            # Previously this branch returned PASS unconditionally, letting build
            # integrity phantom-pass for any stack with no compiler defined.
            # Overall GATE_3_BUILD success requires build_result['status']=='PASS',
            # so returning FAIL here blocks the gate. The operator must either
            # define validators.required in config.json (so a real tool runs) or
            # explicitly waive the gate.
            return {
                "status": "FAIL",
                "command": "unsupported-stack",
                "exit_code": -1,
                "stdout": "",
                "stderr": (
                    f"Unsupported/unknown target_stack '{self.stack}' for build "
                    "integrity; no compiler defined. Define validators.required "
                    "in config.json or waive the gate."
                ),
            }

        # Discover check
        tool_name = cmd[0]
        tool_info = tool_results.get(tool_name, {"installed": shutil.which(tool_name) is not None, "required": True})
        if not tool_info["installed"]:
            if tool_info["required"]:
                return {"status": "FAIL", "command": " ".join(cmd), "exit_code": -1, "stdout": "", "stderr": f"Required compiler '{tool_name}' not installed."}
            else:
                return {"status": "WARNING", "command": " ".join(cmd), "exit_code": 0, "stdout": "", "stderr": f"Optional compiler '{tool_name}' not installed."}

        # Run command
        print(f"Running build compiler command: {' '.join(cmd)}")
        return self._exec_command(cmd, timeout=self._timeouts()["build"])

    def _run_linter(self, tool_results):
        """Run linter based on target stack."""
        cmd = []
        if self.stack == "python":
            cmd = ["flake8", "."]
        elif self.stack in ["go", "golang"]:
            cmd = ["go", "vet", "./..."]
        elif self.stack in ["java", "maven"]:
            # Checkstyle check
            cmd = ["checkstyle", "-c", "/sun_checks.xml", "src/"]
        elif self.stack in ["dotnet", "csharp"]:
            cmd = ["dotnet", "format", "--verify-no-changes"]
        else:
            return {"status": "PASS", "command": "none", "exit_code": 0, "stdout": "", "stderr": ""}

        tool_name = "go" if cmd[0] == "go" else cmd[0]
        tool_info = tool_results.get(tool_name, {"installed": shutil.which(tool_name) is not None, "required": False})
        
        if not tool_info["installed"]:
            if tool_info["required"]:
                return {"status": "FAIL", "command": " ".join(cmd), "exit_code": -1, "stdout": "", "stderr": f"Required linter '{tool_name}' not installed."}
            else:
                return {"status": "WARNING", "command": " ".join(cmd), "exit_code": 0, "stdout": "", "stderr": f"Optional linter '{tool_name}' not installed."}

        print(f"Running linter command: {' '.join(cmd)}")
        return self._exec_command(cmd, timeout=self._timeouts()["lint"])

    def _run_security_scan(self, tool_results):
        """Run security scanner based on target stack."""
        cmd = []
        if self.stack == "python":
            cmd = ["bandit", "-r", "."]
        elif self.stack in ["go", "golang"]:
            cmd = ["gosec", "./..."]
        elif self.stack in ["java", "maven"]:
            cmd = ["spotbugs", "-textui", "target/"]
        elif self.stack in ["dotnet", "csharp"]:
            cmd = ["dotnet", "list", "package", "--vulnerable"]
        else:
            return {"status": "PASS", "command": "none", "exit_code": 0, "stdout": "", "stderr": ""}

        tool_name = cmd[0]
        tool_info = tool_results.get(tool_name, {"installed": shutil.which(tool_name) is not None, "required": False})
        
        if not tool_info["installed"]:
            if tool_info["required"]:
                return {"status": "FAIL", "command": " ".join(cmd), "exit_code": -1, "stdout": "", "stderr": f"Required security scanner '{tool_name}' not installed."}
            else:
                return {"status": "WARNING", "command": " ".join(cmd), "exit_code": 0, "stdout": "", "stderr": f"Optional security scanner '{tool_name}' not installed."}

        print(f"Running security scan command: {' '.join(cmd)}")
        return self._exec_command(cmd, timeout=self._timeouts()["scan"])

    def _timeouts(self):
        """Configurable subprocess timeouts (seconds). Defaults apply when
        config.json has no `timeouts` block. F7: a hung tool must FAIL the
        gate, never hang the pipeline."""
        cfg = (self.discovery.config or {}).get("timeouts", {})
        return {
            "build": cfg.get("build", 300),
            "lint": cfg.get("lint", 60),
            "scan": cfg.get("scan", 120),
        }

    def _exec_command(self, cmd, timeout=None):
        try:
            result = subprocess.run(
                cmd,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout
            )
            # B5: status is computed purely from the real returncode. A missing
            # JRE (or any non-zero exit) must FAIL, never be mocked into a PASS.
            returncode = result.returncode
            stderr = result.stderr
            status = "PASS" if returncode == 0 else "FAIL"
            return {
                "status": status,
                "command": " ".join(cmd),
                "exit_code": returncode,
                "stdout": result.stdout,
                "stderr": stderr
            }
        except subprocess.TimeoutExpired as e:
            # F7: treat a hung tool as a gate FAILURE, not a pipeline hang.
            return {
                "status": "FAIL",
                "command": " ".join(cmd),
                "exit_code": -1,
                "stdout": "",
                "stderr": f"timeout after {e.timeout}s"
            }
        except Exception as e:
            return {
                "status": "FAIL",
                "command": " ".join(cmd),
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Execution failed: {e}"
            }

    def _record_evidence(self, artifact_id, result):
        evidence_path = os.path.join(self.workspace, ".anti-legacy", "evidence", f"{artifact_id}.json")
        os.makedirs(os.path.dirname(evidence_path), exist_ok=True)
        
        evidence = {
            "scope": "build",
            "phase": "validation",
            "claim": f"target-{artifact_id}",
            "status": result["status"],
            "evidence": {
                "command": result["command"],
                "exit_code": result.get("exit_code", 0),
                "stdout_snippet": result.get("stdout", "")[-2000:],
                "stderr_snippet": result.get("stderr", "")[-2000:]
            }
        }
        
        with open(evidence_path, 'w') as f:
            json.dump(evidence, f, indent=2)
            
        print(f"Recorded evidence to: {evidence_path} (Status: {result['status']})")


def main():
    parser = argparse.ArgumentParser(description="Deterministic validator tool discovery and run orchestrator.")
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # subcommand: discover
    discover_p = subparsers.add_parser("discover", help="Discover available validators")
    discover_p.add_argument("--workspace", default=".", help="Path to target codebase")
    discover_p.add_argument("--config", default=".anti-legacy/config.json", help="Path to config.json")
    discover_p.add_argument("--stack", help="Override stack type (python, go, java, dotnet)")
    discover_p.add_argument("--fail-on-missing", action="store_true", help="Fail if any required tool is missing")

    # subcommand: run
    run_p = subparsers.add_parser("run", help="Run validators for a specific gate")
    run_p.add_argument("--gate", required=True, help="Gate to run validators for (GATE_1_DESIGN, GATE_2_PLAN, GATE_3_BUILD, GATE_3B_SEMANTIC, GATE_4_UAT)")
    run_p.add_argument("--workspace", default=".", help="Path to target codebase")
    run_p.add_argument("--config", default=".anti-legacy/config.json", help="Path to config.json")

    args = parser.parse_args()
    
    if args.command == "discover":
        discovery = ValidatorDiscovery(args.workspace, args.config)
        if args.stack:
            discovery.stack = args.stack.lower()
            
        results = discovery.discover_tools()
        success = discovery.print_discovery_report(results)
        
        if not success and args.fail_on_missing:
            sys.exit(1)
        sys.exit(0)

    elif args.command == "run":
        runner = ValidatorRunner(args.workspace, args.config)
        success = runner.run_gate(args.gate)
        if not success:
            print(f"Gatekeeper check: FAILED validation for {args.gate}.", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Gatekeeper check: PASSED validation for {args.gate}.")
            sys.exit(0)
            
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
