#!/usr/bin/env python3
"""
Unit tests for the Validator Discovery & Gate Execution Orchestrator.
"""
import unittest
import json
import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock
from antilegacy_core.validator_discovery import ValidatorDiscovery, ValidatorRunner

class TestValidatorDiscovery(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")
        
    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch('shutil.which')
    def test_tool_discovery_python_default(self, mock_which):
        # Mock which to say python3 is installed, but flake8 and bandit are missing
        mock_which.side_effect = lambda cmd: "/usr/bin/python3" if cmd == "python3" else None
        
        with open(self.config_path, 'w') as f:
            json.dump({"target_stack": "python"}, f)
            
        discovery = ValidatorDiscovery(self.test_dir, self.config_path)
        results = discovery.discover_tools()
        
        self.assertTrue(results["python3"]["installed"])
        self.assertTrue(results["python3"]["required"])
        
        self.assertFalse(results["flake8"]["installed"])
        self.assertFalse(results["flake8"]["required"])
        
        self.assertFalse(results["bandit"]["installed"])
        self.assertFalse(results["bandit"]["required"])

    @patch('shutil.which')
    def test_tool_discovery_custom_config(self, mock_which):
        # Override config to require flake8
        mock_which.side_effect = lambda cmd: "/usr/bin/flake8" if cmd == "flake8" else None
        
        with open(self.config_path, 'w') as f:
            json.dump({
                "target_stack": "python",
                "validators": {
                    "required": ["flake8"],
                    "optional": ["bandit"]
                }
            }, f)
            
        discovery = ValidatorDiscovery(self.test_dir, self.config_path)
        results = discovery.discover_tools()
        
        self.assertTrue(results["flake8"]["installed"])
        self.assertTrue(results["flake8"]["required"])
        
        self.assertFalse(results["bandit"]["installed"])
        self.assertFalse(results["bandit"]["required"])


class TestValidatorRunner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")
        self.manifest_path = os.path.join(self.test_dir, "manifest.json")
        
        with open(self.config_path, 'w') as f:
            json.dump({"target_stack": "python"}, f)
            
        with open(self.manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "project": {"name": "test"}, "artifacts": {}}, f)
            
        # Create folder structure
        os.makedirs(os.path.join(self.test_dir, ".anti-legacy", "requirements"), exist_ok=True)
        os.makedirs(os.path.join(self.test_dir, ".anti-legacy", "evidence"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_functional_report(self, fail_count=0, rule_coverage=1.0,
                                 requirements=None, omit_fail_count=False,
                                 omit_coverage=False, use_fail_synonym=False):
        """Write a functional_comparison_report.json under the workspace evidence
        dir mirroring what compare_graphs emits (top-level 'requirements' +
        'aggregate' block). Knobs let individual tests drive the M1 gate paths."""
        ev_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence")
        os.makedirs(ev_dir, exist_ok=True)
        agg = {"total_reqs": 1, "rules_total": 1, "rules_covered": 1}
        if not omit_fail_count:
            if use_fail_synonym:
                agg["fail"] = fail_count
            else:
                agg["fail_count"] = fail_count
        if not omit_coverage:
            agg["rule_coverage"] = rule_coverage
        payload = {"aggregate": agg}
        if requirements is not None:
            payload["requirements"] = requirements
        report_path = os.path.join(ev_dir, "functional_comparison_report.json")
        with open(report_path, 'w') as f:
            json.dump(payload, f)
        return report_path

    def test_run_gate_1_design_missing_files(self):
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_1_DESIGN")
        self.assertFalse(success, "Should fail when requirements_graph.json is missing")

    def test_run_gate_1_design_invalid_currency_type(self):
        # Write requirements graph and blueprint with float type for money
        rg_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "requirements_graph.json")
        bp_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "blueprint.json")
        nfr_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "nfrs.md")
        
        with open(rg_path, 'w') as f:
            json.dump({
                "domains": {
                    "Domain_A": {
                        "requirements": {
                            "REQ_1": {
                                "legacy_components": ["billing.cbl"],
                                "business_rules": ["money must beDecimal"]
                            }
                        }
                    }
                }
            }, f)
            
        with open(bp_path, 'w') as f:
            json.dump({
                "components": {
                    "BillingComponent": {
                        "fields": [
                            {"name": "salary_amount", "type": "float"}
                        ]
                    }
                }
            }, f)
            
        with open(nfr_path, 'w') as f:
            f.write("# NFRs")
            
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_1_DESIGN")
        self.assertFalse(success, "Should fail when currency field has type 'float'")

    def test_run_gate_1_design_valid(self):
        rg_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "requirements_graph.json")
        bp_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "blueprint.json")
        nfr_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "nfrs.md")
        
        with open(rg_path, 'w') as f:
            json.dump({
                "domains": {
                    "Domain_A": {
                        "entities": {},
                        "requirements": {
                            "REQ_1": {
                                "title": "Billing calculation",
                                "description": "Compute billing for an account.",
                                "legacy_components": ["billing.cbl"],
                                "data_access": [],
                                "dependencies": [],
                                "business_rules": [
                                    {"id": "RULE-001", "statement": "Compute billing amount from balance."}
                                ],
                                "validations": [],
                                "error_paths": [],
                                "parity_hints": [
                                    {"kind": "money", "field": "billing_amount"}
                                ]
                            }
                        }
                    }
                }
            }, f)
            
        with open(bp_path, 'w') as f:
            json.dump({
                "components": {
                    "BillingComponent": {
                        "fields": [
                            {"name": "salary_amount", "type": "DECIMAL(11,2)"}
                        ]
                    }
                }
            }, f)
            
        with open(nfr_path, 'w') as f:
            f.write("# NFRs")
            
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_1_DESIGN")
        self.assertTrue(success)

    def test_run_gate_1_design_parity_hints_missing(self):
        # ISS-06: a numeric/money requirement with NO parity_hints must FAIL
        # GATE_1 — else COMP-3 precision loss ships with no parity_rules signal.
        import io
        import contextlib
        rg_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "requirements_graph.json")
        bp_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "blueprint.json")
        nfr_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "nfrs.md")
        with open(rg_path, 'w') as f:
            json.dump({"domains": {"Domain_A": {"entities": {}, "requirements": {
                "REQ_1": {
                    "title": "Interest calc", "description": "Compute interest.",
                    "legacy_components": ["x.cbl"], "data_access": [], "dependencies": [],
                    "business_rules": [{"id": "RULE-001",
                                        "statement": "Compute the interest amount on the balance."}],
                    "validations": [], "error_paths": [], "status": "active"
                    # NO parity_hints — the gap the parity gate must catch.
                }}}}}, f)
        with open(bp_path, 'w') as f:
            json.dump({"components": {}}, f)
        with open(nfr_path, 'w') as f:
            f.write("# NFRs")
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            success = runner.run_gate("GATE_1_DESIGN")
        self.assertFalse(success, "a money rule without parity_hints must fail GATE_1")
        self.assertIn("parity_hints", buf.getvalue())

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_run_gate_3_build_pass(self, mock_which, mock_run):
        # Mock all tools installed
        mock_which.return_value = "/usr/bin/mocked-path"
        
        # Mock compile, lint, and security subprocess exit code 0
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")

        # M1: GATE_3_BUILD now requires a round-trip functional_comparison_report
        # with 0 FAIL and rule_coverage >= 1.0. Write a clean passing report so
        # the gate clears.
        self._write_functional_report(fail_count=0, rule_coverage=1.0)

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_3_BUILD")

        self.assertTrue(success)
        
        # Verify evidence files created
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, ".anti-legacy", "evidence", "build-integrity.json")))
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, ".anti-legacy", "evidence", "code-quality.json")))
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, ".anti-legacy", "evidence", "security-scan.json")))

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_run_gate_3_build_unsupported_stack_fails(self, mock_which, mock_run):
        # B3: an unknown/unsupported target_stack must FAIL build integrity, never
        # silently PASS via the old unconditional compiler `else` fallback.
        mock_which.return_value = "/usr/bin/mocked-path"
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")

        # Configure an unsupported stack (no compiler defined for 'rust').
        with open(self.config_path, 'w') as f:
            json.dump({"target_stack": "rust"}, f)

        # Even with a clean passing round-trip report (M1 satisfied), the
        # unsupported-stack compiler FAIL must still block the gate.
        self._write_functional_report(fail_count=0, rule_coverage=1.0)

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_3_BUILD")
        self.assertFalse(success, "Unsupported/unknown target_stack must FAIL GATE_3_BUILD, never silent PASS")

        # The build-integrity evidence must record the unsupported-stack FAIL,
        # not a phantom PASS.
        build_ev_path = os.path.join(self.test_dir, ".anti-legacy", "evidence", "build-integrity.json")
        self.assertTrue(os.path.exists(build_ev_path))
        with open(build_ev_path) as f:
            build_ev = json.load(f)
        self.assertEqual(build_ev["status"], "FAIL")

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_run_gate_3_build_round_trip_report_missing_fails(self, mock_which, mock_run):
        # M1: GATE_3_BUILD must FAIL when the round-trip
        # functional_comparison_report.json is missing entirely, even when the
        # compiler/quality/security tiers all pass (mocked exit 0).
        mock_which.return_value = "/usr/bin/mocked-path"
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")

        # Deliberately do NOT write functional_comparison_report.json.
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_3_BUILD")
        self.assertFalse(success, "Missing functional_comparison_report.json must FAIL GATE_3_BUILD")

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_run_gate_3_build_round_trip_fail_count_blocks(self, mock_which, mock_run):
        # M1: a round-trip report with fail_count > 0 must block GATE_3_BUILD.
        mock_which.return_value = "/usr/bin/mocked-path"
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")

        self._write_functional_report(fail_count=2, rule_coverage=1.0)

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_3_BUILD")
        self.assertFalse(success, "fail_count > 0 in the round-trip report must FAIL GATE_3_BUILD")

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_run_gate_3_build_round_trip_low_coverage_blocks(self, mock_which, mock_run):
        # M1: a round-trip report with rule_coverage < 1.0 must block GATE_3_BUILD,
        # even with 0 fails.
        mock_which.return_value = "/usr/bin/mocked-path"
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")

        self._write_functional_report(fail_count=0, rule_coverage=0.75)

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_3_BUILD")
        self.assertFalse(success, "rule_coverage < 1.0 in the round-trip report must FAIL GATE_3_BUILD")

    def test_run_gate_3b_semantic_gaps(self):
        rg_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "requirements_graph.json")

        # 1. Fail with an unresolved HIGH gap recorded NESTED per-requirement
        #    (the shape semantic_validator.record_gap writes:
        #     rg['domains'][D]['requirements'][REQ]['semantic_gaps'][*] with
        #     keys id/severity/description; record_gap writes no status, so a
        #     freshly recorded gap is unresolved by default).
        with open(rg_path, 'w') as f:
            json.dump({
                "domains": {
                    "D": {
                        "requirements": {
                            "REQ_A": {
                                "semantic_gaps": [
                                    {"id": "GAP-001", "severity": "HIGH", "status": "unresolved", "description": "missing calculation"}
                                ]
                            }
                        }
                    }
                }
            }, f)

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_3B_SEMANTIC")
        self.assertFalse(success, "Should fail when a nested HIGH semantic gap is unresolved")

        # 2. Pass once that nested gap is flipped to status 'resolved'.
        with open(rg_path, 'w') as f:
            json.dump({
                "domains": {
                    "D": {
                        "requirements": {
                            "REQ_A": {
                                "semantic_gaps": [
                                    {"id": "GAP-001", "severity": "HIGH", "status": "resolved", "description": "missing calculation"}
                                ]
                            }
                        }
                    }
                }
            }, f)

        success = runner.run_gate("GATE_3B_SEMANTIC")
        self.assertTrue(success, "Should pass when the nested HIGH gap is resolved")

        # 3. A nested gap with severity below HIGH/CRITICAL does not block,
        #    even when unresolved.
        with open(rg_path, 'w') as f:
            json.dump({
                "domains": {
                    "D": {
                        "requirements": {
                            "REQ_A": {
                                "semantic_gaps": [
                                    {"id": "GAP-002", "severity": "LOW", "description": "cosmetic"}
                                ]
                            }
                        }
                    }
                }
            }, f)

        success = runner.run_gate("GATE_3B_SEMANTIC")
        self.assertTrue(success, "A LOW-severity unresolved gap must not block the gate")

        # 4. Back-compat: a legacy top-level `semantic_gaps` list (keys
        #    gap_id/requirement_id/status) with an unresolved HIGH gap still blocks
        #    if the dual-read is retained.
        with open(rg_path, 'w') as f:
            json.dump({
                "semantic_gaps": [
                    {"gap_id": "GAP-003", "requirement_id": "REQ_A", "severity": "HIGH", "status": "unresolved", "description": "legacy-shape gap"}
                ]
            }, f)

        success = runner.run_gate("GATE_3B_SEMANTIC")
        self.assertFalse(success, "Legacy top-level semantic_gaps shape must still block on unresolved HIGH gap")

    def test_run_gate_3b_semantic_vacuous_empty_graph_fails(self):
        # MINOR (non-vacuous): a requirements graph with NO semantic_gaps array
        # anywhere (semantic validation never ran / never recorded a result) must
        # NOT pass vacuously. The gate requires evidence that semantic validation
        # actually executed.
        rg_path = os.path.join(self.test_dir, ".anti-legacy", "requirements", "requirements_graph.json")
        with open(rg_path, 'w') as f:
            json.dump({
                "domains": {
                    "D": {
                        "requirements": {
                            # A requirement with no `semantic_gaps` key at all.
                            "REQ_A": {"title": "Some requirement"}
                        }
                    }
                }
            }, f)

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_3B_SEMANTIC")
        self.assertFalse(
            success,
            "An empty graph with no semantic_gaps array anywhere must FAIL (no vacuous pass)",
        )

        # Sanity: recording even a single (resolved/LOW) gaps array is evidence
        # that semantic validation ran, so the gate clears again.
        with open(rg_path, 'w') as f:
            json.dump({
                "domains": {
                    "D": {
                        "requirements": {
                            "REQ_A": {
                                "semantic_gaps": []
                            }
                        }
                    }
                }
            }, f)
        success = runner.run_gate("GATE_3B_SEMANTIC")
        self.assertTrue(
            success,
            "An (empty) semantic_gaps array is evidence validation ran with no unresolved HIGH/CRITICAL gaps -> pass",
        )

    def test_run_gate_4_uat(self):
        uat_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence", "uat")
        os.makedirs(uat_dir, exist_ok=True)
        verdict_path = os.path.join(uat_dir, "domain_a.json")

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)

        # 1. Fail on a non-PASS verdict. uat-crew writes verdict files keyed
        #    `verdict`, not `status`.
        with open(verdict_path, 'w') as f:
            json.dump({"verdict": "FAIL", "overall_rationale": "did not pass", "findings": []}, f)
        self.assertFalse(runner.run_gate("GATE_4_UAT"), "Should fail on a FAIL verdict")

        # 2. Pass on a clean PASS verdict: non-empty overall_rationale, no findings.
        with open(verdict_path, 'w') as f:
            json.dump({"verdict": "PASS", "overall_rationale": "r", "findings": []}, f)
        self.assertTrue(runner.run_gate("GATE_4_UAT"), "Should pass on a clean PASS verdict")

        # 3. Anti-rubber-stamp: a PASS verdict carrying a CRITICAL finding FAILs.
        with open(verdict_path, 'w') as f:
            json.dump({
                "verdict": "PASS",
                "overall_rationale": "r",
                "findings": [
                    {"id": "F-1", "severity": "CRITICAL", "description": "broken calc", "target_file_line": "Foo.java:42"}
                ],
            }, f)
        self.assertFalse(runner.run_gate("GATE_4_UAT"), "PASS with a CRITICAL finding must fail")

        # 3b. A MAJOR finding on a PASS verdict also FAILs.
        with open(verdict_path, 'w') as f:
            json.dump({
                "verdict": "PASS",
                "overall_rationale": "r",
                "findings": [
                    {"id": "F-2", "severity": "MAJOR", "description": "wrong rounding", "target_file_line": "Bar.java:7"}
                ],
            }, f)
        self.assertFalse(runner.run_gate("GATE_4_UAT"), "PASS with a MAJOR finding must fail")

        # 4. Anti-rubber-stamp: a PASS verdict missing overall_rationale FAILs.
        with open(verdict_path, 'w') as f:
            json.dump({"verdict": "PASS", "findings": []}, f)
        self.assertFalse(runner.run_gate("GATE_4_UAT"), "PASS missing overall_rationale must fail")

        # 5. Anti-rubber-stamp: a finding lacking target_file_line FAILs even when
        #    the finding severity is low.
        with open(verdict_path, 'w') as f:
            json.dump({
                "verdict": "PASS",
                "overall_rationale": "r",
                "findings": [
                    {"id": "F-3", "severity": "MINOR", "description": "nit"}
                ],
            }, f)
        self.assertFalse(runner.run_gate("GATE_4_UAT"), "A finding without target_file_line must fail the gate")

        # 6. Back-compat: a legacy top-level `status` key (no `verdict`) still works
        #    via the dual-read — PASS clears, the rest of the verdict being clean.
        with open(verdict_path, 'w') as f:
            json.dump({"status": "PASS", "overall_rationale": "r", "findings": []}, f)
        self.assertTrue(runner.run_gate("GATE_4_UAT"), "Legacy status=PASS key must still clear the gate")

    def _write_clean_uat_verdict(self):
        """Write a single clean PASS verdict so the verdict tier of GATE_4_UAT is
        satisfied and the reviewer-independence (M2) check is what's under test."""
        uat_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence", "uat")
        os.makedirs(uat_dir, exist_ok=True)
        with open(os.path.join(uat_dir, "domain_a.json"), 'w') as f:
            json.dump({"verdict": "PASS", "overall_rationale": "r", "findings": []}, f)

    def _write_manifest_with_gates(self, gate4_evaluator=None, gate1_evaluator=None):
        """Rewrite the manifest with a gates block carrying the given evaluators."""
        gates = {}
        if gate1_evaluator is not None:
            gates["GATE_1_DESIGN"] = {"status": "passed", "evaluator": gate1_evaluator}
        if gate4_evaluator is not None:
            gates["GATE_4_UAT"] = {"status": "passed", "evaluator": gate4_evaluator}
        with open(self.manifest_path, 'w') as f:
            json.dump({
                "version": "1.0.0",
                "project": {"name": "test", "target_stack": "python"},
                "artifacts": {},
                "gates": gates,
            }, f)

    def _write_audit_gate1_signoff(self, evaluator):
        """Append a GATE_1_DESIGN gate-signed-off event to audit.jsonl, sibling of
        the manifest (where _check_reviewer_independence looks)."""
        audit_path = os.path.join(os.path.dirname(self.manifest_path), "audit.jsonl")
        rec = {
            "event": "anti-legacy:gate-signed-off",
            "timestamp": "2026-06-14T00:00:00+00:00",
            "details": {
                "gate_id": "GATE_1_DESIGN",
                "opinion": "PASSED",
                "evaluator": evaluator,
                "rationale": "",
            },
        }
        with open(audit_path, 'a') as f:
            f.write(json.dumps(rec) + "\n")

    def test_run_gate_4_uat_reviewer_independence_architect(self):
        # M2: the GATE_4_UAT evaluator must NOT be the config roles.architect.
        # config.json roles.architect = Alice; GATE_4_UAT evaluator = Alice -> FAIL.
        with open(self.config_path, 'w') as f:
            json.dump({"target_stack": "python", "roles": {"architect": "Alice"}}, f)
        self._write_clean_uat_verdict()
        self._write_manifest_with_gates(gate4_evaluator="Alice")

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        self.assertFalse(
            runner.run_gate("GATE_4_UAT"),
            "UAT reviewer == config architect (Alice) must FAIL reviewer-independence",
        )

        # A distinct UAT evaluator (Bob) is independent -> PASS.
        self._write_manifest_with_gates(gate4_evaluator="Bob")
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        self.assertTrue(
            runner.run_gate("GATE_4_UAT"),
            "A UAT evaluator distinct from the architect must clear reviewer-independence",
        )

    def test_run_gate_4_uat_reviewer_independence_gate1_signer(self):
        # M2: the GATE_4_UAT evaluator must NOT be the recorded GATE_1_DESIGN
        # signer from audit.jsonl. Audit GATE_1_DESIGN signed by Alice; GATE_4_UAT
        # evaluator = Alice -> FAIL.
        with open(self.config_path, 'w') as f:
            json.dump({"target_stack": "python"}, f)
        self._write_clean_uat_verdict()
        self._write_audit_gate1_signoff("Alice")
        self._write_manifest_with_gates(gate4_evaluator="Alice")

        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        self.assertFalse(
            runner.run_gate("GATE_4_UAT"),
            "UAT reviewer == GATE_1_DESIGN signer (Alice, from audit.jsonl) must FAIL reviewer-independence",
        )

        # A distinct UAT evaluator (Carol) is independent of the GATE_1 signer -> PASS.
        self._write_manifest_with_gates(gate4_evaluator="Carol")
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        self.assertTrue(
            runner.run_gate("GATE_4_UAT"),
            "A UAT evaluator distinct from the GATE_1_DESIGN signer must clear the gate",
        )

    def test_run_gate_4_uat_reviewer_independence_vacuous(self):
        # M2 is vacuous-safe: with no roles/evaluators recorded (the default
        # manifest/config), the independence check never fires and a clean PASS
        # verdict clears the gate.
        self._write_clean_uat_verdict()
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        self.assertTrue(
            runner.run_gate("GATE_4_UAT"),
            "With no architect/evaluator names, reviewer-independence must not fire (vacuous-safe)",
        )

    def test_run_gate_4_uat_findings_as_dict_does_not_crash(self):
        # MINOR: a verdict whose `findings` is a dict (not a list) must be coerced
        # to its values and evaluated, not crash the gate.
        uat_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence", "uat")
        os.makedirs(uat_dir, exist_ok=True)
        verdict_path = os.path.join(uat_dir, "domain_a.json")
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)

        # (a) dict-shaped findings carrying a CRITICAL finding -> evaluated -> FAIL
        #     (no crash).
        with open(verdict_path, 'w') as f:
            json.dump({
                "verdict": "PASS",
                "overall_rationale": "r",
                "findings": {
                    "F-1": {"id": "F-1", "severity": "CRITICAL", "description": "broken", "target_file_line": "Foo.java:1"}
                },
            }, f)
        self.assertFalse(
            runner.run_gate("GATE_4_UAT"),
            "A dict-shaped findings with a CRITICAL value must be evaluated (and FAIL), not crash",
        )

        # (b) dict-shaped findings that is empty -> evaluated -> clean PASS clears.
        with open(verdict_path, 'w') as f:
            json.dump({
                "verdict": "PASS",
                "overall_rationale": "r",
                "findings": {},
            }, f)
        self.assertTrue(
            runner.run_gate("GATE_4_UAT"),
            "An empty dict-shaped findings must not crash and a clean PASS verdict clears the gate",
        )

    def test_run_gate_0_discovery(self):
        imports_dir = os.path.join(self.test_dir, ".anti-legacy", "imports")
        os.makedirs(imports_dir, exist_ok=True)
        
        # Create a dummy cloned repository directory to pass imports length check
        os.makedirs(os.path.join(imports_dir, "dummy-repo"), exist_ok=True)
        
        with open(self.manifest_path, 'w') as f:
            json.dump({
                "version": "1.0.0",
                "project": {
                    "name": "test",
                    "target_stack": "python"
                },
                "artifacts": {}
            }, f)
        
        runner = ValidatorRunner(self.test_dir, self.config_path, self.manifest_path)
        success = runner.run_gate("GATE_0_DISCOVERY")
        self.assertTrue(success)

if __name__ == "__main__":
    unittest.main()
