#!/usr/bin/env python3
"""Tests for skills/uat-crew/scripts/uat_crew.py (Fix 2 — CLI batch runner)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

# Add the uat-crew scripts dir to the path.
_UAT_CREW_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "uat-crew", "scripts",
)
sys.path.insert(0, _UAT_CREW_SCRIPTS)

from uat_crew import main


def _write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(content, dict):
            json.dump(content, f, indent=2)
        else:
            f.write(content)
    return path


class TestUatCrewAssemble(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uat-crew-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_graph(self, reqs):
        """Write a minimal requirements_graph.json with given {req_id: domain} map."""
        domains = {}
        for req_id, domain in reqs.items():
            domains.setdefault(domain, {"requirements": {}})
            domains[domain]["requirements"][req_id] = {
                "status": "active",
                "legacy_components": ["SomeModule"],
                "business_rules": [{"id": "BR-1", "statement": "Do the thing", "confidence": 0.9}],
            }
        _write(self.tmp, ".anti-legacy/requirements/requirements_graph.json",
               {"domains": domains})

    def _make_contract(self, domain, req_id):
        _write(self.tmp, f".anti-legacy/contracts/{domain}/{req_id}.contract.json",
               {"req_id": req_id, "domain": domain, "test_cases": []})

    def test_assemble_creates_manifest(self):
        self._make_graph({"BILLING-001": "billing", "BILLING-002": "billing"})
        self._make_contract("billing", "BILLING-001")
        self._make_contract("billing", "BILLING-002")
        rc = main(["assemble", "--workspace", self.tmp])
        self.assertEqual(rc, 0)
        out = os.path.join(self.tmp, ".anti-legacy", "evidence", "uat-dispatch-manifest.json")
        self.assertTrue(os.path.exists(out))
        with open(out) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["total_jobs"], 2)
        req_ids = {j["req_id"] for j in manifest["jobs"]}
        self.assertIn("BILLING-001", req_ids)
        self.assertIn("BILLING-002", req_ids)

    def test_assemble_skips_unresolvable(self):
        domains = {"billing": {"requirements": {
            "BILLING-001": {"status": "active", "legacy_components": ["M"]},
            "BILLING-999": {"status": "unresolvable", "legacy_components": ["M"]},
        }}}
        _write(self.tmp, ".anti-legacy/requirements/requirements_graph.json",
               {"domains": domains})
        self._make_contract("billing", "BILLING-001")
        rc = main(["assemble", "--workspace", self.tmp, "--allow-missing-contracts"])
        self.assertEqual(rc, 0)
        out = os.path.join(self.tmp, ".anti-legacy", "evidence", "uat-dispatch-manifest.json")
        with open(out) as f:
            manifest = json.load(f)
        req_ids = {j["req_id"] for j in manifest["jobs"]}
        self.assertIn("BILLING-001", req_ids)
        self.assertNotIn("BILLING-999", req_ids, "unresolvable requirements must be skipped")

    def test_assemble_fails_on_missing_contracts(self):
        self._make_graph({"BILLING-001": "billing"})
        # No contract file written.
        rc = main(["assemble", "--workspace", self.tmp])
        self.assertNotEqual(rc, 0, "assemble must fail when contracts are missing")

    def test_assemble_allow_missing_contracts_flag(self):
        self._make_graph({"BILLING-001": "billing"})
        rc = main(["assemble", "--workspace", self.tmp, "--allow-missing-contracts"])
        self.assertEqual(rc, 0)
        out = os.path.join(self.tmp, ".anti-legacy", "evidence", "uat-dispatch-manifest.json")
        with open(out) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["total_jobs"], 1)
        self.assertFalse(manifest["jobs"][0]["contract_exists"])


class TestUatCrewCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uat-crew-collect-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_verdict(self, req_id, domain, verdict):
        _write(self.tmp, f".anti-legacy/evidence/uat/{req_id}-verdict.json", {
            "req_id": req_id,
            "domain": domain,
            "verdict": verdict,
            "findings": [],
            "overall_rationale": "Verified all rules." if verdict == "PASS" else "Failures found.",
        })

    def test_collect_all_pass(self):
        self._write_verdict("BILLING-001", "billing", "PASS")
        self._write_verdict("BILLING-002", "billing", "PASS")
        rc = main(["collect", "--workspace", self.tmp])
        self.assertEqual(rc, 0)
        report_path = os.path.join(
            self.tmp, ".anti-legacy", "evidence", "uat-dispatch-report.json"
        )
        with open(report_path) as f:
            report = json.load(f)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["pass_count"], 2)
        self.assertEqual(report["fail_count"], 0)

    def test_collect_with_fail(self):
        self._write_verdict("BILLING-001", "billing", "PASS")
        self._write_verdict("BILLING-002", "billing", "FAIL")
        rc = main(["collect", "--workspace", self.tmp])
        self.assertNotEqual(rc, 0)
        report_path = os.path.join(
            self.tmp, ".anti-legacy", "evidence", "uat-dispatch-report.json"
        )
        with open(report_path) as f:
            report = json.load(f)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["fail_count"], 1)

    def test_collect_empty_dir_fails(self):
        os.makedirs(os.path.join(self.tmp, ".anti-legacy", "evidence", "uat"))
        rc = main(["collect", "--workspace", self.tmp])
        self.assertNotEqual(rc, 0)


class TestUatCrewStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uat-crew-status-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_without_manifest_fails(self):
        rc = main(["status", "--workspace", self.tmp])
        self.assertNotEqual(rc, 0)

    def test_status_with_partial_verdicts(self):
        manifest = {
            "total_jobs": 3,
            "jobs": [
                {"req_id": "B-001", "domain": "billing"},
                {"req_id": "B-002", "domain": "billing"},
                {"req_id": "B-003", "domain": "billing"},
            ],
        }
        _write(self.tmp, ".anti-legacy/evidence/uat-dispatch-manifest.json", manifest)
        # Only one verdict present.
        uat_dir = os.path.join(self.tmp, ".anti-legacy", "evidence", "uat")
        os.makedirs(uat_dir)
        _write(self.tmp, ".anti-legacy/evidence/uat/B-001-verdict.json",
               {"verdict": "PASS", "overall_rationale": "ok"})
        rc = main(["status", "--workspace", self.tmp])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
