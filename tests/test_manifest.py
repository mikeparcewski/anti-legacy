#!/usr/bin/env python3
import unittest
import sys
import os
import json
import subprocess
import tempfile
import shutil

class TestManifestManager(unittest.TestCase):
    def setUp(self):
        """Create a temp directory to simulate a project root."""
        self.project_dir = tempfile.mkdtemp(prefix="anti-legacy-test-")
        self.scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts'))
        self.manifest_script = os.path.join(self.scripts_dir, 'manifest.py')

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def _run(self, *args):
        """Run manifest.py with args in the temp project directory."""
        cmd = [sys.executable, self.manifest_script] + list(args)
        result = subprocess.run(cmd, cwd=self.project_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result

    def _load_manifest(self):
        manifest_path = os.path.join(self.project_dir, ".anti-legacy", "manifest.json")
        with open(manifest_path, 'r') as f:
            return json.load(f)

    def test_init_creates_workspace(self):
        """Init creates .anti-legacy/ with manifest, audit trail, and subdirectories."""
        res = self._run("init", "--name", "test-project", "--target-stack", "java")
        self.assertEqual(res.returncode, 0, f"Init failed: {res.stderr}")

        # Verify directory structure
        base = os.path.join(self.project_dir, ".anti-legacy")
        self.assertTrue(os.path.isdir(base))
        self.assertTrue(os.path.isdir(os.path.join(base, "evidence")))
        self.assertTrue(os.path.isdir(os.path.join(base, "contracts")))
        self.assertTrue(os.path.isdir(os.path.join(base, "requirements")))
        self.assertTrue(os.path.isdir(os.path.join(base, "patterns", "learnings")))
        self.assertTrue(os.path.isfile(os.path.join(base, "manifest.json")))
        self.assertTrue(os.path.isfile(os.path.join(base, "audit.jsonl")))

        # Verify manifest content
        m = self._load_manifest()
        self.assertEqual(m["project"]["name"], "test-project")
        self.assertEqual(m["project"]["target_stack"], "java")
        self.assertEqual(m["phase"]["current"], "uninitialized")
        self.assertEqual(len(m["phase"]["completed"]), 0)
        self.assertEqual(m["gates"]["GATE_1_DESIGN"]["status"], "pending")

    def test_init_refuses_overwrite_without_force(self):
        """Init won't overwrite existing manifest without --force."""
        self._run("init", "--name", "first")
        res = self._run("init", "--name", "second")
        self.assertNotEqual(res.returncode, 0)

        # Original is preserved
        m = self._load_manifest()
        self.assertEqual(m["project"]["name"], "first")

    def test_init_force_overwrites(self):
        """Init --force replaces the manifest."""
        self._run("init", "--name", "first")
        self._run("init", "--name", "second", "--force")
        m = self._load_manifest()
        self.assertEqual(m["project"]["name"], "second")

    def test_advance_phase(self):
        """Advance moves the current phase and records completed."""
        self._run("init", "--name", "test")
        self._run("advance", "survey")
        m = self._load_manifest()
        self.assertEqual(m["phase"]["current"], "survey")
        self.assertNotIn("uninitialized", m["phase"]["completed"])  # uninitialized is never "completed"

        self._run("advance", "analyze")
        m = self._load_manifest()
        self.assertEqual(m["phase"]["current"], "analyze")
        self.assertIn("survey", m["phase"]["completed"])

    def test_advance_rejects_illegal_phase(self):
        """Advancing to a phase outside the enum exits 2 and does not mutate phase."""
        self._run("init", "--name", "test")
        res = self._run("advance", "not-a-real-phase")
        self.assertEqual(res.returncode, 2, f"Illegal phase should exit 2: {res.stderr}")
        # Phase is unchanged (still uninitialized).
        self.assertEqual(self._load_manifest()["phase"]["current"], "uninitialized")

    def test_advance_blocked_leaving_gate_phase_until_signed(self):
        """B2: cannot advance OUT of gate-design-review while GATE_1_DESIGN is pending.

        Advancing INTO a gate phase is always free; the precondition fires only on EXIT.
        Once the gate is signed with registered + present evidence, the exit succeeds.
        """
        self._run("init", "--name", "test")

        # Advancing INTO the gate phase is unconditionally allowed.
        res = self._run("advance", "gate-design-review")
        self.assertEqual(res.returncode, 0, f"Entering gate phase should be free: {res.stderr}")
        self.assertEqual(self._load_manifest()["phase"]["current"], "gate-design-review")

        # Leaving the gate phase while GATE_1_DESIGN is pending is blocked (exit 2),
        # and the phase must NOT change.
        res = self._run("advance", "planning")
        self.assertEqual(res.returncode, 2, f"Leaving with pending gate should exit 2: {res.stderr}")
        self.assertEqual(self._load_manifest()["phase"]["current"], "gate-design-review")

        # Sign GATE_1_DESIGN with real, registered, checksum-matching evidence.
        self._write_evidence("review_packet.md", "# review packet\n")
        self._run("register", "review-packet", "--path", "review_packet.md",
                  "--format", "markdown", "--produced-by", "test", "--status", "final")
        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "passed",
                        "--evaluator", "architect", "--evidence", "review-packet")
        self.assertEqual(res.returncode, 0, f"Gate sign-off failed: {res.stderr}")

        # Now leaving the gate phase succeeds.
        res = self._run("advance", "planning")
        self.assertEqual(res.returncode, 0, f"Leaving after sign-off should succeed: {res.stderr}")
        m = self._load_manifest()
        self.assertEqual(m["phase"]["current"], "planning")
        self.assertIn("gate-design-review", m["phase"]["completed"])

    def test_advance_blocked_leaving_gate_phase_until_waived(self):
        """B2: a WAIVED gate also satisfies the precondition for leaving a gate phase."""
        self._run("init", "--name", "test")
        self._run("advance", "gate-plan-review")

        # Pending -> blocked.
        res = self._run("advance", "build")
        self.assertEqual(res.returncode, 2, f"Pending gate should block exit: {res.stderr}")
        self.assertEqual(self._load_manifest()["phase"]["current"], "gate-plan-review")

        # Waiving satisfies the precondition (explicit human override).
        res = self._run("gate", "GATE_2_PLAN", "--opinion", "waived",
                        "--evaluator", "human", "--rationale", "accepted")
        self.assertEqual(res.returncode, 0, f"Waive failed: {res.stderr}")
        res = self._run("advance", "build")
        self.assertEqual(res.returncode, 0, f"Leaving after waive should succeed: {res.stderr}")
        self.assertEqual(self._load_manifest()["phase"]["current"], "build")

    def test_advance_build_integrity_requires_both_gates(self):
        """B2: leaving gate-build-integrity requires BOTH GATE_3_BUILD and GATE_3B_SEMANTIC."""
        self._run("init", "--name", "test")
        self._run("advance", "gate-build-integrity")

        # Both pending -> blocked.
        res = self._run("advance", "uat")
        self.assertEqual(res.returncode, 2, f"Both pending should block: {res.stderr}")
        self.assertEqual(self._load_manifest()["phase"]["current"], "gate-build-integrity")

        # Only one satisfied -> still blocked.
        self._run("gate", "GATE_3_BUILD", "--opinion", "waived",
                  "--evaluator", "human", "--rationale", "ok")
        res = self._run("advance", "uat")
        self.assertEqual(res.returncode, 2, f"One gate still pending should block: {res.stderr}")
        self.assertEqual(self._load_manifest()["phase"]["current"], "gate-build-integrity")

        # Both satisfied -> unblocked.
        self._run("gate", "GATE_3B_SEMANTIC", "--opinion", "waived",
                  "--evaluator", "human", "--rationale", "ok")
        res = self._run("advance", "uat")
        self.assertEqual(res.returncode, 0, f"Both gates satisfied should unblock: {res.stderr}")
        self.assertEqual(self._load_manifest()["phase"]["current"], "uat")

    def test_register_artifact(self):
        """Register records artifact metadata and checksum.

        Models the WF1 survey flow: the legacy-graph evidence is the deterministic
        wicked-estate `stats` digest written to a checksummable text file
        (legacy-graph.digest.txt), registered with --format text and no schema.
        """
        self._run("init", "--name", "test")

        # Create a dummy digest artifact (the stripped, deterministic stats block).
        artifact_path = os.path.join(self.project_dir, ".anti-legacy", "legacy-graph.digest.txt")
        with open(artifact_path, 'w') as f:
            f.write("nodes: 10307\nedges: 10989\n")

        res = self._run(
            "register", "legacy-graph",
            "--path", "legacy-graph.digest.txt",
            "--format", "text",
            "--produced-by", "anti-legacy:survey",
            "--status", "final"
        )
        self.assertEqual(res.returncode, 0, f"Register failed: {res.stderr}")

        m = self._load_manifest()
        self.assertIn("legacy-graph", m["artifacts"])
        art = m["artifacts"]["legacy-graph"]
        self.assertEqual(art["path"], "legacy-graph.digest.txt")
        self.assertEqual(art["format"], "text")
        self.assertEqual(art["produced_by"], "anti-legacy:survey")
        self.assertEqual(art["status"], "final")
        self.assertIn("checksum", art)
        self.assertIn("produced_at", art)

    def test_register_with_dependencies(self):
        """Register records artifact dependencies.

        legacy-graph is the wicked-estate stats digest (text); requirements-graph is
        anti-legacy's own JSON graph that depends on it.
        """
        self._run("init", "--name", "test")

        # Create files: the legacy digest (text) and the requirements graph (json).
        for name, body in [("legacy-graph.digest.txt", "nodes: 1\nedges: 0\n"),
                           ("requirements_graph.json", "{}")]:
            path = os.path.join(self.project_dir, ".anti-legacy", name)
            with open(path, 'w') as f:
                f.write(body)

        self._run("register", "legacy-graph", "--path", "legacy-graph.digest.txt",
                   "--format", "text", "--produced-by", "survey")
        self._run("register", "requirements-graph", "--path", "requirements_graph.json",
                   "--format", "json", "--produced-by", "extraction",
                   "--depends-on", "legacy-graph")

        m = self._load_manifest()
        self.assertEqual(m["artifacts"]["requirements-graph"]["depends_on"], ["legacy-graph"])

    def test_gate_decision(self):
        """Gate records sign-off and writes audit event."""
        self._run("init", "--name", "test")

        # Register the evidence the gate cites — a gate may only be recorded
        # PASSED when its cited evidence artifacts are registered. legacy-graph is
        # the wicked-estate stats digest (text), per the WF1 survey flow.
        for art_id, fname, fmt, body in [
            ("review-packet", "review_packet.md", "markdown", "# packet"),
            ("legacy-graph", "legacy-graph.digest.txt", "text", "nodes: 1\nedges: 0\n"),
        ]:
            with open(os.path.join(self.project_dir, ".anti-legacy", fname), 'w') as f:
                f.write(body)
            self._run("register", art_id, "--path", fname, "--format", fmt, "--produced-by", "test")

        res = self._run(
            "gate", "GATE_1_DESIGN",
            "--opinion", "passed",
            "--evaluator", "lead-architect",
            "--rationale", "Blueprint approved",
            "--evidence", "review-packet,legacy-graph"
        )
        self.assertEqual(res.returncode, 0, f"Gate failed: {res.stderr}")

        m = self._load_manifest()
        gate = m["gates"]["GATE_1_DESIGN"]
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["evaluator"], "lead-architect")
        self.assertEqual(gate["evidence_artifacts"], ["review-packet", "legacy-graph"])

        # Verify audit trail
        audit_path = os.path.join(self.project_dir, ".anti-legacy", "audit.jsonl")
        with open(audit_path, 'r') as f:
            events = [json.loads(line) for line in f if line.strip()]
        gate_events = [e for e in events if e["event"] == "anti-legacy:gate-signed-off"]
        self.assertEqual(len(gate_events), 1)
        self.assertEqual(gate_events[0]["details"]["gate_id"], "GATE_1_DESIGN")

    def test_gate_rejects_unknown_gate(self):
        """Gate command rejects unknown gate IDs."""
        self._run("init", "--name", "test")
        res = self._run("gate", "GATE_99_FAKE", "--opinion", "passed", "--evaluator", "nobody")
        self.assertNotEqual(res.returncode, 0)

    def test_gate_rejects_passed_with_unregistered_evidence(self):
        """A gate cannot be recorded PASSED citing evidence that isn't registered."""
        self._run("init", "--name", "test")

        # PASSED with no evidence at all -> rejected.
        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "passed", "--evaluator", "x")
        self.assertNotEqual(res.returncode, 0)

        # PASSED citing an unregistered artifact -> rejected.
        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "passed",
                        "--evaluator", "x", "--evidence", "ghost-artifact")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotEqual(self._load_manifest()["gates"]["GATE_1_DESIGN"]["status"], "passed")

        # WAIVED is an explicit human override and bypasses the evidence check.
        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "waived",
                        "--evaluator", "human", "--rationale", "accepted risk")
        self.assertEqual(res.returncode, 0, f"Waive failed: {res.stderr}")
        self.assertEqual(self._load_manifest()["gates"]["GATE_1_DESIGN"]["status"], "waived")

    def _write_evidence(self, fname, content):
        """Create an evidence file under .anti-legacy/ and return its relative path."""
        full = os.path.join(self.project_dir, ".anti-legacy", fname)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
        return full

    def test_gate_content_verify_missing_file_rejected(self):
        """PASSED citing a registered artifact whose file was deleted -> rejected (B1)."""
        self._run("init", "--name", "test")

        # Register a real file (checksum captured), then delete it so the file is gone.
        full = self._write_evidence("ev_missing.json", '{"ok": true}')
        res = self._run("register", "ev-missing", "--path", "ev_missing.json",
                        "--format", "json", "--produced-by", "test")
        self.assertEqual(res.returncode, 0, f"Register failed: {res.stderr}")
        os.remove(full)

        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "passed",
                        "--evaluator", "x", "--evidence", "ev-missing")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotEqual(self._load_manifest()["gates"]["GATE_1_DESIGN"]["status"], "passed")

    def test_gate_content_verify_drifted_file_rejected(self):
        """PASSED citing a registered artifact whose file content drifted -> rejected (B1)."""
        self._run("init", "--name", "test")

        full = self._write_evidence("ev_drift.json", '{"original": true}')
        res = self._run("register", "ev-drift", "--path", "ev_drift.json",
                        "--format", "json", "--produced-by", "test")
        self.assertEqual(res.returncode, 0, f"Register failed: {res.stderr}")

        # Mutate the file after registration so the recorded checksum no longer matches.
        with open(full, 'w') as f:
            f.write('{"original": false, "tampered": true}')

        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "passed",
                        "--evaluator", "x", "--evidence", "ev-drift")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotEqual(self._load_manifest()["gates"]["GATE_1_DESIGN"]["status"], "passed")

    def test_gate_content_verify_bad_status_rejected(self):
        """PASSED citing evidence with status failed or pending -> rejected (B1)."""
        self._run("init", "--name", "test")

        for art_id, fname, status in [("ev-failed", "ev_failed.json", "failed"),
                                      ("ev-pending", "ev_pending.json", "pending")]:
            self._write_evidence(fname, '{"ok": true}')
            res = self._run("register", art_id, "--path", fname, "--format", "json",
                            "--produced-by", "test", "--status", status)
            self.assertEqual(res.returncode, 0, f"Register failed: {res.stderr}")

            res = self._run("gate", "GATE_1_DESIGN", "--opinion", "passed",
                            "--evaluator", "x", "--evidence", art_id)
            self.assertNotEqual(res.returncode, 0,
                                f"Expected reject for status={status}, got: {res.stdout}{res.stderr}")
            self.assertNotEqual(self._load_manifest()["gates"]["GATE_1_DESIGN"]["status"], "passed")

    def test_gate_content_verify_clean_passes(self):
        """PASSED citing registered + present + checksum-matching evidence -> accepted (B1)."""
        self._run("init", "--name", "test")

        self._write_evidence("ev_clean.json", '{"clean": true}')
        res = self._run("register", "ev-clean", "--path", "ev_clean.json",
                        "--format", "json", "--produced-by", "test", "--status", "final")
        self.assertEqual(res.returncode, 0, f"Register failed: {res.stderr}")

        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "passed",
                        "--evaluator", "architect", "--evidence", "ev-clean")
        self.assertEqual(res.returncode, 0, f"Clean gate should pass: {res.stderr}")
        self.assertEqual(self._load_manifest()["gates"]["GATE_1_DESIGN"]["status"], "passed")

    def test_gate_content_verify_waived_bypasses(self):
        """WAIVED bypasses content-verify even when evidence is missing/unregistered (B1)."""
        self._run("init", "--name", "test")

        # No evidence registered at all; WAIVED is an audited human override.
        res = self._run("gate", "GATE_1_DESIGN", "--opinion", "waived",
                        "--evaluator", "human", "--rationale", "accepted risk",
                        "--evidence", "ghost-artifact")
        self.assertEqual(res.returncode, 0, f"Waive should bypass content-verify: {res.stderr}")
        self.assertEqual(self._load_manifest()["gates"]["GATE_1_DESIGN"]["status"], "waived")

    def test_learn_indexes_note(self):
        """Learn command indexes a learning note."""
        self._run("init", "--name", "test")
        res = self._run(
            "learn", "comp3-mapping",
            "--path", "patterns/learnings/comp3-to-bigdecimal.md",
            "--tags", "cobol,comp-3,bigdecimal,java"
        )
        self.assertEqual(res.returncode, 0, f"Learn failed: {res.stderr}")

        m = self._load_manifest()
        self.assertEqual(len(m["learnings"]), 1)
        self.assertEqual(m["learnings"][0]["id"], "comp3-mapping")
        self.assertEqual(m["learnings"][0]["tags"], ["cobol", "comp-3", "bigdecimal", "java"])

    def test_check_detects_missing_artifact(self):
        """Check command detects missing files."""
        self._run("init", "--name", "test")

        # Register artifact for a file that doesn't exist
        self._run("register", "ghost", "--path", "does_not_exist.json",
                   "--format", "json", "--produced-by", "nobody")

        res = self._run("check")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("MISSING", res.stdout)

    def test_check_detects_drift(self):
        """Check command detects when file content changes after registration."""
        self._run("init", "--name", "test")

        # Create, register, then modify
        artifact_path = os.path.join(self.project_dir, ".anti-legacy", "graph.json")
        with open(artifact_path, 'w') as f:
            f.write('{"original": true}')

        self._run("register", "graph", "--path", "graph.json",
                   "--format", "json", "--produced-by", "builder")

        # Mutate the file
        with open(artifact_path, 'w') as f:
            f.write('{"original": false, "tampered": true}')

        res = self._run("check")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("DRIFTED", res.stdout)

    def test_check_passes_clean(self):
        """Check passes when all artifacts are intact."""
        self._run("init", "--name", "test")

        artifact_path = os.path.join(self.project_dir, ".anti-legacy", "clean.json")
        with open(artifact_path, 'w') as f:
            f.write('{"clean": true}')

        self._run("register", "clean", "--path", "clean.json",
                   "--format", "json", "--produced-by", "builder")

        res = self._run("check")
        self.assertEqual(res.returncode, 0)
        self.assertIn("verified", res.stdout)

    def test_check_positional_single_artifact(self):
        """`check <id>` verifies just that artifact, and errors on an unknown id."""
        self._run("init", "--name", "test")

        # Register two artifacts: one clean, one whose file is missing.
        self._write_evidence("good.json", '{"good": true}')
        self._run("register", "good", "--path", "good.json",
                  "--format", "json", "--produced-by", "builder")
        self._run("register", "ghost", "--path", "missing.json",
                  "--format", "json", "--produced-by", "builder")

        # Checking only the clean artifact passes even though 'ghost' is broken.
        res = self._run("check", "good")
        self.assertEqual(res.returncode, 0, f"Single clean check should pass: {res.stderr}{res.stdout}")
        self.assertIn("verified", res.stdout)

        # Checking the broken artifact by id fails.
        res = self._run("check", "ghost")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("MISSING", res.stdout)

        # Checking an unknown id errors out.
        res = self._run("check", "no-such-artifact")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Unknown artifact", res.stderr)

    def test_status_prints_summary(self):
        """Status command prints a readable summary."""
        self._run("init", "--name", "billing-modernization", "--target-stack", "java")
        self._run("advance", "survey")

        res = self._run("status")
        self.assertEqual(res.returncode, 0)
        self.assertIn("billing-modernization", res.stdout)
        self.assertIn("java", res.stdout)
        self.assertIn("survey", res.stdout)

    def test_full_lifecycle(self):
        """Simulate a full pipeline lifecycle: init → survey → register → gate → learn."""
        self._run("init", "--name", "e2e-test", "--target-stack", "go")

        # Survey phase: wicked-estate index + register its stats digest as the
        # checksummable legacy-graph evidence (deterministic text artifact).
        self._run("advance", "survey")
        graph_path = os.path.join(self.project_dir, ".anti-legacy", "legacy-graph.digest.txt")
        with open(graph_path, 'w') as f:
            f.write("nodes: 10307\nedges: 10989\n")
        self._run("register", "legacy-graph", "--path", "legacy-graph.digest.txt",
                   "--format", "text", "--produced-by", "anti-legacy:survey", "--status", "final")

        # Graph translate: the requirements graph is anti-legacy IP, depends on legacy-graph.
        self._run("advance", "graph-translate")
        reqs_path = os.path.join(self.project_dir, ".anti-legacy", "requirements_graph.json")
        with open(reqs_path, 'w') as f:
            json.dump({"domains": {}}, f)
        self._run("register", "requirements-graph", "--path", "requirements_graph.json",
                   "--format", "json", "--produced-by", "anti-legacy:extraction",
                   "--depends-on", "legacy-graph", "--status", "final")

        # Gate
        self._run("advance", "gate-design-review")
        self._run("gate", "GATE_1_DESIGN", "--opinion", "passed",
                   "--evaluator", "architect", "--evidence", "requirements-graph")

        # Learn
        learning_path = os.path.join(self.project_dir, ".anti-legacy", "patterns", "learnings", "test-note.md")
        with open(learning_path, 'w') as f:
            f.write("# Learned: test patterns work\n")
        self._run("learn", "test-learning", "--path", "patterns/learnings/test-note.md",
                   "--tags", "test,e2e")

        # Verify final state
        m = self._load_manifest()
        self.assertEqual(m["phase"]["current"], "gate-design-review")
        self.assertIn("survey", m["phase"]["completed"])
        self.assertIn("graph-translate", m["phase"]["completed"])
        self.assertEqual(len(m["artifacts"]), 2)
        self.assertEqual(m["gates"]["GATE_1_DESIGN"]["status"], "passed")
        self.assertEqual(len(m["learnings"]), 1)

        # Integrity check should pass
        res = self._run("check")
        self.assertEqual(res.returncode, 0)

        # Audit trail should have events
        audit_path = os.path.join(self.project_dir, ".anti-legacy", "audit.jsonl")
        with open(audit_path, 'r') as f:
            events = [json.loads(line) for line in f if line.strip()]
        self.assertGreaterEqual(len(events), 4)  # 2 phase advances + 1 register + 1 gate + more


if __name__ == '__main__':
    unittest.main()
