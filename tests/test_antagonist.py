"""Tests for antilegacy_core.antagonist — tier registry, phase categories,
context assembler, and CLI entry-point."""
import io
import json
import os
import sys
import tempfile
import unittest

# Ensure antilegacy_core package is importable from the scripts directory.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts"),
)
from antilegacy_core import antagonist


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _write_manifest(tmpdir, phase="extraction"):
    os.makedirs(os.path.join(tmpdir, ".anti-legacy"), exist_ok=True)
    m = {
        "phase": {"current": phase},
        "gates": {"GATE_0_DISCOVERY": {"status": "passed"}},
    }
    with open(os.path.join(tmpdir, ".anti-legacy", "manifest.json"), "w") as f:
        json.dump(m, f)


# ---------------------------------------------------------------------------
# TestPhaseTiers
# ---------------------------------------------------------------------------

class TestPhaseTiers(unittest.TestCase):
    """Tests for the tier registry (PHASE_TIERS)."""

    def test_full_pep_phases(self):
        for phase in ("extraction", "blueprint", "build"):
            with self.subTest(phase=phase):
                self.assertEqual(antagonist.PHASE_TIERS[phase], "full")

    def test_minus_ant_phases(self):
        for phase in ("analyze", "document", "review-packet"):
            with self.subTest(phase=phase):
                self.assertEqual(antagonist.PHASE_TIERS[phase], "minus-ant")

    def test_lite_phases(self):
        for phase in ("setup", "survey", "deploy"):
            with self.subTest(phase=phase):
                self.assertEqual(antagonist.PHASE_TIERS[phase], "lite")

    def test_unknown_phase_defaults_full(self):
        # An unknown phase should fall back to the safe default: 'full'.
        tier = antagonist.PHASE_TIERS.get("totally-unknown-phase", "full")
        self.assertEqual(tier, "full")


# ---------------------------------------------------------------------------
# TestPhaseCategories
# ---------------------------------------------------------------------------

class TestPhaseCategories(unittest.TestCase):
    """Tests for threat category assignment per phase (PHASE_CATEGORIES)."""

    def test_extraction_has_design_cats(self):
        cats = antagonist.PHASE_CATEGORIES["extraction"]
        for expected in ("confidence-laundering", "silent-drop", "ring-depth-insufficient"):
            self.assertIn(expected, cats)

    def test_build_has_build_cats(self):
        cats = antagonist.PHASE_CATEGORIES["build"]
        for expected in ("annotation-stacking", "reflection-test", "weak-evidence"):
            self.assertIn(expected, cats)

    def test_uat_has_validation_cats(self):
        cats = antagonist.PHASE_CATEGORIES["uat"]
        for expected in ("reviewer-conflict", "missing-contract"):
            self.assertIn(expected, cats)

    def test_all_phases_have_universal_cats(self):
        universal = ("gate-bypass", "precheck-skip", "forced-override-abuse")
        lite_phases = {"setup", "survey", "deploy"}
        for phase, cats in antagonist.PHASE_CATEGORIES.items():
            if phase in lite_phases:
                continue
            for u in universal:
                with self.subTest(phase=phase, cat=u):
                    self.assertIn(u, cats)

    def test_lite_phases_have_no_cats(self):
        for phase in ("setup", "survey", "deploy"):
            with self.subTest(phase=phase):
                self.assertEqual(antagonist.PHASE_CATEGORIES[phase], [])


# ---------------------------------------------------------------------------
# TestContextAssembler
# ---------------------------------------------------------------------------

class TestContextAssembler(unittest.TestCase):
    """Tests for assemble_context()."""

    def test_missing_manifest_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx, err = antagonist.assemble_context("extraction", workspace=tmpdir)
            self.assertIsNone(ctx)
            self.assertIsNotNone(err)
            self.assertIn("manifest", err.lower())

    def test_minimal_manifest_assembles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="extraction")
            ctx, err = antagonist.assemble_context("extraction", workspace=tmpdir)
            self.assertIsNone(err)
            self.assertIsNotNone(ctx)
            self.assertEqual(ctx["phase"], "extraction")
            self.assertIn("tier", ctx)
            self.assertIn("applicable_categories", ctx)
            self.assertIn("manifest_status", ctx)
            self.assertIn("assembled_at", ctx)

    def test_extraction_includes_coverage_signal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="extraction")
            # Write a minimal coverage-report.json
            cov = {
                "coverage": 0.85,
                "resolved": 17,
                "risk_flagged": 2,
                "unaccounted": 1,
                "total_behavior_bearing": 20,
            }
            with open(os.path.join(tmpdir, ".anti-legacy", "coverage-report.json"), "w") as f:
                json.dump(cov, f)

            ctx, err = antagonist.assemble_context("extraction", workspace=tmpdir)
            self.assertIsNone(err)
            self.assertIn("coverage_report", ctx["phase_specific_signals"])

    def test_blueprint_includes_graph_signal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="blueprint")
            # Write a minimal requirements_graph.json
            reqs_dir = os.path.join(tmpdir, ".anti-legacy", "requirements")
            os.makedirs(reqs_dir, exist_ok=True)
            rg = {
                "domains": {
                    "Billing": {
                        "requirements": {
                            "REQ_001": {
                                "title": "Compute bill",
                                "business_rules": [
                                    {"id": "RULE-001", "statement": "Add interest", "confidence": 0.9}
                                ],
                            }
                        }
                    }
                }
            }
            with open(os.path.join(reqs_dir, "requirements_graph.json"), "w") as f:
                json.dump(rg, f)

            ctx, err = antagonist.assemble_context("blueprint", workspace=tmpdir)
            self.assertIsNone(err)
            self.assertIn("requirements_graph", ctx["phase_specific_signals"])

    def test_uat_includes_reserved_identities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="uat")
            # Write a config.json with roles.architect set
            cfg = {"roles": {"architect": "alice"}}
            with open(os.path.join(tmpdir, ".anti-legacy", "config.json"), "w") as f:
                json.dump(cfg, f)

            ctx, err = antagonist.assemble_context("uat", workspace=tmpdir)
            self.assertIsNone(err)
            identities = ctx["phase_specific_signals"]["uat_reserved_identities"]
            self.assertEqual(identities["architect"], "alice")

    def test_lite_phase_has_no_signals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="setup")
            ctx, err = antagonist.assemble_context("setup", workspace=tmpdir)
            self.assertIsNone(err)
            self.assertEqual(ctx["phase_specific_signals"], {})

    def test_graph_summary_detects_low_confidence_rules(self):
        """_graph_summary should count rules with confidence < 0.75 (H1 signal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="blueprint")
            reqs_dir = os.path.join(tmpdir, ".anti-legacy", "requirements")
            os.makedirs(reqs_dir, exist_ok=True)
            rg = {
                "domains": {
                    "Billing": {
                        "requirements": {
                            "REQ_001": {
                                "title": "Compute bill",
                                "business_rules": [
                                    {"id": "RULE-001", "statement": "Apply rate", "confidence": 0.5},
                                    {"id": "RULE-002", "statement": "Round result", "confidence": 0.9},
                                ],
                            }
                        }
                    }
                }
            }
            with open(os.path.join(reqs_dir, "requirements_graph.json"), "w") as f:
                json.dump(rg, f)
            ctx, err = antagonist.assemble_context("blueprint", workspace=tmpdir)
            self.assertIsNone(err)
            gs = ctx["phase_specific_signals"]["requirements_graph"]
            self.assertEqual(gs["low_confidence_rules"], 1)

    def test_graph_summary_detects_placeholder_rules(self):
        """_graph_summary should count rules whose statement contains placeholder text."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="blueprint")
            reqs_dir = os.path.join(tmpdir, ".anti-legacy", "requirements")
            os.makedirs(reqs_dir, exist_ok=True)
            rg = {
                "domains": {
                    "Billing": {
                        "requirements": {
                            "REQ_001": {
                                "title": "Compute bill",
                                "business_rules": [
                                    {"id": "RULE-001", "statement": "REVIEW REQUIRED", "confidence": 0.9},
                                    {"id": "RULE-002", "statement": "Apply rate", "confidence": 0.9},
                                ],
                            }
                        }
                    }
                }
            }
            with open(os.path.join(reqs_dir, "requirements_graph.json"), "w") as f:
                json.dump(rg, f)
            ctx, err = antagonist.assemble_context("blueprint", workspace=tmpdir)
            self.assertIsNone(err)
            gs = ctx["phase_specific_signals"]["requirements_graph"]
            self.assertEqual(gs["placeholder_rules"], 1)

    def test_uat_reserved_identities_reads_audit_jsonl(self):
        """_uat_reserved_identities should read GATE_1_DESIGN signers from audit.jsonl."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="uat")
            cfg = {"roles": {"architect": "alice"}}
            with open(os.path.join(tmpdir, ".anti-legacy", "config.json"), "w") as f:
                json.dump(cfg, f)
            # Write an audit.jsonl with a GATE_1_DESIGN sign-off
            audit_line = json.dumps({
                "event": "anti-legacy:gate-signed-off",
                "details": {"gate_id": "GATE_1_DESIGN", "evaluator": "bob"},
            })
            with open(os.path.join(tmpdir, ".anti-legacy", "audit.jsonl"), "w") as f:
                f.write(audit_line + "\n")
            ctx, err = antagonist.assemble_context("uat", workspace=tmpdir)
            self.assertIsNone(err)
            identities = ctx["phase_specific_signals"]["uat_reserved_identities"]
            self.assertIn("bob", identities["gate1_audit_signers"])


# ---------------------------------------------------------------------------
# TestCLIMain
# ---------------------------------------------------------------------------

class TestCLIMain(unittest.TestCase):
    """Tests for the CLI entry point (antagonist.main)."""

    def _capture_stdout(self, argv):
        """Run main(argv) and capture stdout; return (exit_code, captured_text)."""
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            code = antagonist.main(argv)
        finally:
            sys.stdout = old_stdout
        return code, buf.getvalue()

    def test_tier_subcommand(self):
        code, out = self._capture_stdout(["tier", "--phase", "extraction"])
        self.assertEqual(code, 0)
        self.assertIn("extraction", out)

    def test_context_subcommand_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.StringIO()
            old_stderr = sys.stderr
            sys.stderr = buf
            try:
                code = antagonist.main(
                    ["context", "--phase", "extraction", "--workspace", tmpdir]
                )
            finally:
                sys.stderr = old_stderr
            self.assertEqual(code, 1)

    def test_context_subcommand_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_manifest(tmpdir, phase="extraction")
            code, out = self._capture_stdout(
                ["context", "--phase", "extraction", "--workspace", tmpdir, "--json"]
            )
            self.assertEqual(code, 0)
            # Output must be valid JSON
            parsed = json.loads(out)
            self.assertEqual(parsed["phase"], "extraction")


if __name__ == "__main__":
    unittest.main()
