"""Regression for evidence_log GATE_3C golden-confidence surfacing (ISS-25).

The differential-equivalence report carries `golden_confidence`, but a consumer that reads only
the gate STATUS can drop the caveat and present a low/medium PASS as proven parity. evidence_log
must read the report from disk and ride the confidence ALONGSIDE the GATE_3C opinion. Hermetic:
each test runs in a tmp workspace (os.chdir) because D.load_json resolves relative to the CWD.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts")))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "skills", "evidence-log", "scripts")))

import evidence_log as el  # noqa: E402


class _Workspace(unittest.TestCase):
    """Base: a tmp workspace with a .anti-legacy/ dir, chdir'd in for the test."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="al-evlog-g3c-"))
        self.anti = os.path.join(self.dir, ".anti-legacy")
        self.ev = os.path.join(self.anti, "evidence")
        os.makedirs(self.ev, exist_ok=True)
        self._cwd = os.getcwd()
        os.chdir(self.anti)  # workspace_root() == CWD; the deliverable runs from .anti-legacy/

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_report(self, payload):
        with open(os.path.join(self.ev, "differential-equivalence-report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(payload, f)

    def _manifest_with_gate(self, opinion="passed"):
        return {"version": "1.0.0", "project": {"name": "demo"},
                "phase": {"current": "target-review", "completed": []},
                "gates": {"GATE_3C_DIFFERENTIAL": {"status": opinion,
                                                   "evaluator": "anti-legacy:differential-equivalence",
                                                   "rationale": "posture=PASS",
                                                   "evidence_artifacts": ["differential-equivalence-report"]}},
                "artifacts": {}}


class ReadConfidenceTest(_Workspace):
    def test_reads_confidence_from_report(self):
        self._write_report({"status": "PASS", "golden_confidence": "low",
                            "gate_posture": "PASS", "warnings": ["heads up"]})
        conf, posture, warns = el._read_differential_confidence()
        self.assertEqual(conf, "low")
        self.assertEqual(posture, "PASS")
        self.assertEqual(warns, ["heads up"])

    def test_absent_report_is_none(self):
        conf, posture, warns = el._read_differential_confidence()
        self.assertIsNone(conf)
        self.assertIsNone(posture)
        self.assertEqual(warns, [])

    def test_suffix_for_low(self):
        s = el._gate3c_confidence_suffix("low")
        self.assertIn("golden confidence: low", s)
        self.assertIn("not captured legacy", s)

    def test_suffix_empty_when_none(self):
        self.assertEqual(el._gate3c_confidence_suffix(None), "")


class GateLedgerSurfacingTest(_Workspace):
    def test_low_confidence_pass_is_not_rendered_as_bare_passed(self):
        # The anti-oversell case: PASS at low confidence must carry the caveat in the ledger row.
        self._write_report({"status": "PASS", "golden_confidence": "low", "gate_posture": "PASS",
                            "warnings": ["PASS proves agreement with assumed behavior, not legacy"]})
        md = "\n".join(el._render_gate_ledger(self._manifest_with_gate("passed"), []))
        # GATE_3C row no longer reads as a clean 'passed' — confidence rides along.
        self.assertIn("golden confidence: low", md)
        self.assertIn("assumed behavior, not captured legacy", md)
        # The dedicated caveat section + the epistemic limit are spelled out.
        self.assertIn("GATE_3C_DIFFERENTIAL — golden confidence", md)
        self.assertIn("epistemic", md)
        # And the report's own warning is surfaced.
        self.assertIn("assumed behavior", md)

    def test_high_confidence_is_surfaced_too(self):
        self._write_report({"status": "PASS", "golden_confidence": "high", "gate_posture": "PASS",
                            "warnings": []})
        md = "\n".join(el._render_gate_ledger(self._manifest_with_gate("passed"), []))
        self.assertIn("golden confidence: high", md)
        self.assertIn("captured legacy", md)

    def test_no_report_renders_plain_gate_row_without_crash(self):
        # No differential report at all -> the GATE_3C row is plain, no caveat section, no error.
        md = "\n".join(el._render_gate_ledger(self._manifest_with_gate("pending"), []))
        self.assertIn("GATE_3C_DIFFERENTIAL", md)
        self.assertNotIn("golden confidence:", md)
        self.assertNotIn("GATE_3C_DIFFERENTIAL — golden confidence", md)

    def test_full_render_includes_confidence(self):
        # End-to-end through render_evidence_log (the assembled document), not just the section.
        self._write_report({"status": "FAIL", "golden_confidence": "medium", "gate_posture": "WARN",
                            "warnings": ["divergence against a medium-confidence golden"]})
        content, _receipts = el.render_evidence_log(self._manifest_with_gate("passed"), [])
        self.assertIn("golden confidence: medium", content)
        self.assertIn("source oracle", content)


if __name__ == "__main__":
    unittest.main()
