#!/usr/bin/env python3
"""
Unit tests for scripts/semantic_validator.py.
Verifies dependency chain grouping, gap recording, and report generation.
"""
import json
import os
import shutil
import tempfile
import unittest
from semantic_validator import find_dependency_chains, record_gap, generate_reports

class TestSemanticValidator(unittest.TestCase):
    def setUp(self):
        # Create temp folder for files
        self.test_dir = tempfile.mkdtemp(prefix="semantic-val-test-")
        self.graph_path = os.path.join(self.test_dir, "requirements_graph.json")
        self.blueprint_path = os.path.join(self.test_dir, "blueprint.json")
        self.report_json = os.path.join(self.test_dir, "report.json")
        self.report_md = os.path.join(self.test_dir, "report.md")

        # Mock requirements graph with two disjoint dependency subgraphs:
        # Chain 1: REQ_A1 -> REQ_A2 (A2 depends on A1)
        # Chain 2: REQ_B1 (independent)
        self.mock_rg = {
            "domains": {
                "Domain_A": {
                    "requirements": {
                        "REQ_A1": {
                            "title": "A1 Requirement",
                            "legacy_components": ["cobol:A1"],
                            "dependencies": []
                        },
                        "REQ_A2": {
                            "title": "A2 Requirement",
                            "legacy_components": ["cobol:A2"],
                            "dependencies": ["REQ_A1"]
                        }
                    }
                },
                "Domain_B": {
                    "requirements": {
                        "REQ_B1": {
                            "title": "B1 Requirement",
                            "legacy_components": ["cobol:B1"],
                            "dependencies": []
                        }
                    }
                }
            }
        }
        with open(self.graph_path, 'w') as f:
            json.dump(self.mock_rg, f, indent=2)

        # Mock blueprint
        self.mock_bp = {
            "target_path": "./target",
            "domains": {
                "Domain_A": {
                    "components": {
                        "REQ_A1": {"class_name": "ServiceA1"},
                        "REQ_A2": {"class_name": "ServiceA2"}
                    }
                },
                "Domain_B": {
                    "components": {
                        "REQ_B1": {"class_name": "ServiceB1"}
                    }
                }
            }
        }
        with open(self.blueprint_path, 'w') as f:
            json.dump(self.mock_bp, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_find_dependency_chains(self):
        chains = find_dependency_chains(self.mock_rg)
        # Should produce two chains:
        # Chain A: ["REQ_A1", "REQ_A2"]
        # Chain B: ["REQ_B1"]
        self.assertEqual(len(chains), 2)
        
        # Sort chains by size/keys to assert deterministically
        sorted_chains = sorted(chains, key=lambda c: len(c))
        self.assertEqual(sorted_chains[0], ["REQ_B1"])
        self.assertEqual(sorted_chains[1], ["REQ_A1", "REQ_A2"])

    def test_record_gap(self):
        # Record a gap on REQ_A2
        success = record_gap(
            self.graph_path,
            req_id="REQ_A2",
            gap_id="GAP-001",
            severity="HIGH",
            description="Rounding error",
            legacy_loc="A2.cbl:L10",
            target_loc="ServiceA2.java:L25",
            remediation="Use BigDecimal"
        )
        self.assertTrue(success)
        
        # Reload graph and verify gap is saved
        with open(self.graph_path, 'r') as f:
            data = json.load(f)
            
        req_a2 = data["domains"]["Domain_A"]["requirements"]["REQ_A2"]
        self.assertIn("semantic_gaps", req_a2)
        self.assertEqual(len(req_a2["semantic_gaps"]), 1)
        self.assertEqual(req_a2["semantic_gaps"][0]["id"], "GAP-001")
        self.assertEqual(req_a2["semantic_gaps"][0]["severity"], "HIGH")

    def test_generate_reports(self):
        # Record a gap first so reports have content
        record_gap(
            self.graph_path,
            req_id="REQ_A1",
            gap_id="GAP-002",
            severity="LOW",
            description="Minor discrepancy",
            legacy_loc="A1.cbl:L5",
            target_loc="ServiceA1.java:L12",
            remediation="None needed"
        )
        
        success = generate_reports(
            self.graph_path,
            self.blueprint_path,
            self.report_json,
            self.report_md
        )
        self.assertTrue(success)
        
        # Check files exist and have content
        self.assertTrue(os.path.exists(self.report_json))
        self.assertTrue(os.path.exists(self.report_md))
        
        with open(self.report_json, 'r') as f:
            rep = json.load(f)
            self.assertEqual(rep["total_gaps"], 1)
            self.assertEqual(rep["gaps"][0]["id"], "GAP-002")
            
        with open(self.report_md, 'r') as f:
            content = f.read()
            self.assertIn("## 1. Application Dependency Chains", content)
            self.assertIn("## 2. Identified Semantic Gaps", content)
            self.assertIn("GAP-002", content)

    def test_record_gap_critical_severity(self):
        # CRITICAL is a valid severity that GATE_3B_SEMANTIC blocks on.
        success = record_gap(
            self.graph_path,
            req_id="REQ_A1",
            gap_id="GAP-CRIT",
            severity="CRITICAL",
            description="Data corruption",
            legacy_loc="A1.cbl:L1",
            target_loc="ServiceA1.java:L1",
            remediation="Block release"
        )
        self.assertTrue(success)
        with open(self.graph_path, 'r') as f:
            data = json.load(f)
        req_a1 = data["domains"]["Domain_A"]["requirements"]["REQ_A1"]
        self.assertEqual(req_a1["semantic_gaps"][0]["severity"], "CRITICAL")

    def test_generate_reports_top_level_blueprint_components(self):
        # Live blueprint shape: components at the TOP level (not nested in domains).
        top_level_bp = {
            "target_path": "./target",
            "components": {
                "REQ_A1": {"class_name": "ServiceA1Top"},
                "REQ_A2": {"class_name": "ServiceA2Top"},
                "REQ_B1": {"class_name": "ServiceB1Top"}
            }
        }
        with open(self.blueprint_path, 'w') as f:
            json.dump(top_level_bp, f, indent=2)

        record_gap(
            self.graph_path,
            req_id="REQ_A1",
            gap_id="GAP-TOP",
            severity="HIGH",
            description="discrepancy",
            legacy_loc="A1.cbl:L5",
            target_loc="ServiceA1Top.java:L12",
            remediation="fix"
        )
        success = generate_reports(
            self.graph_path,
            self.blueprint_path,
            self.report_json,
            self.report_md
        )
        self.assertTrue(success)
        with open(self.report_md, 'r') as f:
            content = f.read()
        # Target class must resolve from the top-level components, not be 'unknown'.
        self.assertIn("ServiceA1Top", content)
        self.assertNotIn("Target Component: `unknown`", content)


if __name__ == "__main__":
    unittest.main()
