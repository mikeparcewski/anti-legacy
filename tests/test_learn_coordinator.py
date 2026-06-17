#!/usr/bin/env python3
"""
Unit tests for scripts/learn_coordinator.py.
Verifies parsing of manifests and reports, summary formatting, and brain storage logic.
"""
import json
import os
import shutil
import tempfile
import unittest
from learn_coordinator import (
    analyze_phase,
    analyze_setup,
    analyze_survey,
    analyze_planner,
    analyze_swarm,
    analyze_target_review,
    analyze_semantic_validation,
    analyze_uat
)

class TestLearnCoordinator(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="learn-coord-test-")
        self.manifest_path = os.path.join(self.test_dir, ".anti-legacy", "manifest.json")
        os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
        
        self.mock_manifest = {
            "project": {
                "name": "billing-system",
                "target_stack": "java",
                "target_path": "./target/billing",
                "deployment_target": "kubernetes"
            },
            "phase": {
                "current": "setup",
                "completed": []
            }
        }
        with open(self.manifest_path, 'w') as f:
            json.dump(self.mock_manifest, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_analyze_setup(self):
        summary, details = analyze_setup(self.mock_manifest, self.test_dir)
        self.assertIn("billing-system", summary)
        self.assertIn("java", summary)
        self.assertIn("kubernetes", details)

    def test_analyze_survey(self):
        # Survey now writes a deterministic wicked-estate stats digest
        # (.anti-legacy/legacy-graph.digest.txt) instead of legacy_graph.json.
        # One stats_digest block per app, delineated by a "# app:" header.
        digest_path = os.path.join(
            self.test_dir, ".anti-legacy", "legacy-graph.digest.txt"
        )
        with open(digest_path, 'w') as f:
            f.write("# app: app1\nnodes=2 edges=1 files=2\n")

        summary, details = analyze_survey(self.mock_manifest, self.test_dir)
        self.assertIn("Discovered 2 nodes across 1 applications", summary)
        self.assertIn("app1", details)

    def test_analyze_survey_multi_app_digest(self):
        # A multi-repo digest sums node counts and lists every app block.
        digest_path = os.path.join(
            self.test_dir, ".anti-legacy", "legacy-graph.digest.txt"
        )
        with open(digest_path, 'w') as f:
            f.write(
                "# app: carddemo\nnodes=10 edges=12 files=4\n\n"
                "# app: creditcard\nnodes=5 edges=3 files=2\n"
            )

        summary, details = analyze_survey(self.mock_manifest, self.test_dir)
        self.assertIn("Discovered 15 nodes across 2 applications", summary)
        self.assertIn("carddemo", details)
        self.assertIn("creditcard", details)

    def test_analyze_survey_single_block_no_header(self):
        # A header-less single-app digest still parses (counts as 1 app).
        digest_path = os.path.join(
            self.test_dir, ".anti-legacy", "legacy-graph.digest.txt"
        )
        with open(digest_path, 'w') as f:
            f.write("nodes=7 edges=4 files=3\nrepo:\n")

        summary, details = analyze_survey(self.mock_manifest, self.test_dir)
        self.assertIn("Discovered 7 nodes across 1 applications", summary)

    def test_analyze_survey_missing_digest(self):
        # No digest file -> graceful, non-crashing message.
        summary, details = analyze_survey(self.mock_manifest, self.test_dir)
        self.assertIn("not found", summary.lower())

    def test_analyze_planner(self):
        # Write mock task.md
        task_path = os.path.join(self.test_dir, ".anti-legacy", "task.md")
        with open(task_path, 'w') as f:
            f.write("# Tasks\n\n- [ ] TASK-001\n- [ ] TASK-002\n")
            
        summary, details = analyze_planner(self.mock_manifest, self.test_dir)
        self.assertIn("Generated build plan with 2 tasks", summary)

    def test_analyze_swarm(self):
        # Write mock task.md
        task_path = os.path.join(self.test_dir, ".anti-legacy", "task.md")
        with open(task_path, 'w') as f:
            f.write("# Tasks\n\n- [x] TASK-001\n- [ ] TASK-002\n")
            
        summary, details = analyze_swarm(self.mock_manifest, self.test_dir)
        self.assertIn("Completed 1/2 translation tasks", summary)

    def test_analyze_target_review(self):
        # Write mock integrity/report files
        evidence_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        
        with open(os.path.join(evidence_dir, "build-integrity.json"), 'w') as f:
            json.dump({"status": "PASS"}, f)
            
        with open(os.path.join(evidence_dir, "functional-test-report.json"), 'w') as f:
            json.dump({"passed": 5, "failed": 0}, f)
            
        summary, details = analyze_target_review(self.mock_manifest, self.test_dir)
        self.assertIn("Build status is 'PASS'", summary)
        self.assertIn("5 passed, 0 failed", summary)

    def test_analyze_semantic_validation(self):
        evidence_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        
        with open(os.path.join(evidence_dir, "semantic-validation-report.json"), 'w') as f:
            json.dump({
                "total_gaps": 2,
                "gaps_by_severity": {"HIGH": 1, "LOW": 1}
            }, f)
            
        summary, details = analyze_semantic_validation(self.mock_manifest, self.test_dir)
        self.assertIn("Detected 2 semantic gaps", summary)

    def test_analyze_uat(self):
        evidence_dir = os.path.join(self.test_dir, ".anti-legacy", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        
        with open(os.path.join(evidence_dir, "uat-summary.md"), 'w') as f:
            f.write("# UAT Summary\n**verdict**: PASS\n")
            
        summary, details = analyze_uat(self.mock_manifest, self.test_dir)
        self.assertIn("Overall verdict: PASS", summary)

    def test_analyze_phase_routing(self):
        # Test wrapper method routing
        summary, details = analyze_phase("setup", self.test_dir)
        self.assertIn("Setup Phase", summary)

    def test_survey_modern_falls_through_to_generic_not_survey(self):
        # ISS-17: 'survey-modern' is retired — it produces no graph evidence, so
        # it must NOT be summarized as a survey; it falls through to the generic
        # handler rather than being routed to analyze_survey.
        summary, _ = analyze_phase("survey-modern", self.test_dir)
        self.assertIn("Completed Phase: survey-modern", summary)
        self.assertNotIn("Survey Phase", summary)

if __name__ == "__main__":
    unittest.main()
