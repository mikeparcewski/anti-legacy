import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure the scripts directory is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "anti-legacy-expert", "scripts"))

from antilegacy_core import graph_validator


class TestGraphValidator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = self.temp_dir.name
        
        # Create .anti-legacy structure
        os.makedirs(os.path.join(self.workspace, ".anti-legacy", "requirements"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace, ".anti-legacy", "validation"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace, ".anti-legacy", "graphs"), exist_ok=True)
        
        # Default config
        self.config = {
            "project_name": "test_project",
            "source_apps": [{"name": "app1"}]
        }
        with open(os.path.join(self.workspace, ".anti-legacy", "config.json"), "w") as f:
            json.dump(self.config, f)
            
        # Default manifest
        self.manifest = {
            "phase": {"current": "gate-design-review", "completed": ["setup", "survey", "extraction"]},
            "gates": {}
        }
        with open(os.path.join(self.workspace, ".anti-legacy", "manifest.json"), "w") as f:
            json.dump(self.manifest, f)
            
        # Default requirements graph
        self.rg = {
            "domains": {
                "DomainA": {
                    "requirements": {
                        "REQ001": {
                            "legacy": "PGM1",
                            "description": "Updates bank accounts and writes files",
                            "status": "active"
                        },
                        "REQ002": {
                            "legacy": "JCLSTEP1",
                            "description": "Calculates daily totals",
                            "status": "active"
                        }
                    }
                }
            }
        }
        with open(os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json"), "w") as f:
            json.dump(self.rg, f)

        # Mock DB file presence
        open(os.path.join(self.workspace, ".anti-legacy", "graphs", "app1.db"), "w").close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prerequisites_check_missing_manifest(self):
        os.remove(os.path.join(self.workspace, ".anti-legacy", "manifest.json"))
        report, err = graph_validator.run_validation(self.workspace)
        self.assertIsNone(report)
        self.assertIn("manifest.json not found", err)

    def test_prerequisites_check_incomplete_phase(self):
        manifest = {
            "phase": {"current": "survey", "completed": ["setup"]},
            "gates": {}
        }
        with open(os.path.join(self.workspace, ".anti-legacy", "manifest.json"), "w") as f:
            json.dump(manifest, f)
            
        report, err = graph_validator.run_validation(self.workspace)
        self.assertIsNone(report)
        self.assertIn("extraction phase not complete", err)

    @patch("antilegacy_core.wicked_estate.list_nodes")
    @patch("antilegacy_core.coverage.is_behavior_bearing")
    def test_pass1_validation_errors(self, mock_is_behavior, mock_list_nodes):
        mock_is_behavior.return_value = True
        mock_list_nodes.return_value = [
            {"name": "PGM1", "kind": "module", "file": "src/PGM1.cbl", "symbol_id": "sym1"},
            {"name": "JCLSTEP1", "kind": "step", "file": "jcl/job.jcl", "symbol_id": "sym2"}
        ]
        
        report, err = graph_validator.run_validation(self.workspace)
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["type"], "JCL_NOT_PROGRAM")
        self.assertEqual(report["errors"][0]["req_id"], "REQ002")

    @patch("antilegacy_core.wicked_estate.list_nodes")
    @patch("antilegacy_core.coverage.is_behavior_bearing")
    def test_jcl_auto_fix(self, mock_is_behavior, mock_list_nodes):
        mock_is_behavior.return_value = True
        
        # Mock JCL file with PGM name
        jcl_dir = os.path.join(self.workspace, "jcl")
        os.makedirs(jcl_dir, exist_ok=True)
        jcl_file = os.path.join(jcl_dir, "job.jcl")
        with open(jcl_file, "w") as f:
            f.write("//STEP1 EXEC PGM=REALPGM\n")
            
        mock_list_nodes.return_value = [
            {"name": "PGM1", "kind": "module", "file": "src/PGM1.cbl", "symbol_id": "sym1"},
            {"name": "JCLSTEP1", "kind": "step", "file": "jcl/job.jcl", "symbol_id": "sym2"},
            {"name": "REALPGM", "kind": "module", "file": "src/REALPGM.cbl", "symbol_id": "sym3"}
        ]
        
        # Run validation with auto_fix=True
        report, err = graph_validator.run_validation(self.workspace, auto_fix=True)
        self.assertIsNotNone(report)
        
        # Verify the requirement legacy field was remapped to REALPGM
        with open(os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json")) as f:
            updated_rg = json.load(f)
        
        req2 = updated_rg["domains"]["DomainA"]["requirements"]["REQ002"]
        self.assertEqual(req2["legacy"], "REALPGM")
        self.assertEqual(report["status"], "CLEAN")

    @patch("antilegacy_core.wicked_estate.list_nodes")
    @patch("antilegacy_core.coverage.is_behavior_bearing")
    def test_pass2_content_spot_check_mismatch(self, mock_is_behavior, mock_list_nodes):
        mock_is_behavior.return_value = True
        
        # Mock program header indicating read-only report but requirement says "updates"
        os.makedirs(os.path.join(self.workspace, "src"), exist_ok=True)
        with open(os.path.join(self.workspace, "src", "PGM1.cbl"), "w") as f:
            f.write("IDENTIFICATION DIVISION.\nPROGRAM-ID. PGM1.\n* Print transactions report\n")
            
        mock_list_nodes.return_value = [
            {"name": "PGM1", "kind": "module", "file": "src/PGM1.cbl", "symbol_id": "sym1"}
        ]
        
        # Remove REQ002 to simplify
        del self.rg["domains"]["DomainA"]["requirements"]["REQ002"]
        with open(os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json"), "w") as f:
            json.dump(self.rg, f)
            
        report, err = graph_validator.run_validation(self.workspace)
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["type"], "CONTENT_MISMATCH")
        self.assertEqual(report["errors"][0]["req_id"], "REQ001")

    @patch("antilegacy_core.wicked_estate.list_nodes")
    @patch("antilegacy_core.coverage.is_behavior_bearing")
    def test_pass3_uncaptured_heuristics(self, mock_is_behavior, mock_list_nodes):
        mock_is_behavior.return_value = True
        
        os.makedirs(os.path.join(self.workspace, "src"), exist_ok=True)
        with open(os.path.join(self.workspace, "src", "COBSWAIT.cbl"), "w") as f:
            f.write("CALL 'MVSWAIT'")
            
        # PGM1 is covered. UNRESOLVED_PGM and COBSWAIT are uncovered
        mock_list_nodes.return_value = [
            {"name": "PGM1", "kind": "module", "file": "src/PGM1.cbl", "symbol_id": "sym1"},
            {"name": "UNRESOLVED_PGM", "kind": "module", "file": "src/UNRESOLVED.cbl", "symbol_id": "sym3"},
            {"name": "COBSWAIT", "kind": "module", "file": "src/COBSWAIT.cbl", "symbol_id": "sym4"}
        ]
        
        # Simplify requirements: only REQ001 is active
        del self.rg["domains"]["DomainA"]["requirements"]["REQ002"]
        with open(os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json"), "w") as f:
            json.dump(self.rg, f)
            
        report, err = graph_validator.run_validation(self.workspace)
        self.assertIsNotNone(report)
        
        # UNRESOLVED_PGM should be NEEDS_REQUIREMENT (gap)
        # COBSWAIT should be UTILITY_OMIT
        self.assertEqual(report["status"], "GAPS")
        self.assertEqual(report["summary"]["gaps"], 1)
        self.assertEqual(report["summary"]["omissions"], 1)
        
        self.assertEqual(report["gaps"][0]["name"], "UNRESOLVED_PGM")
        self.assertEqual(report["gaps"][0]["classification"], "NEEDS_REQUIREMENT")
        
        self.assertEqual(report["omissions"][0]["name"], "COBSWAIT")
        self.assertEqual(report["omissions"][0]["classification"], "UTILITY_OMIT")


    @patch("antilegacy_core.wicked_estate.list_nodes")
    @patch("antilegacy_core.coverage.is_behavior_bearing")
    def test_pass1_detects_jcl_step_with_raw_estate_kind(self, mock_is_behavior, mock_list_nodes):
        """list_nodes returns kind VERBATIM from the DB.

        For JCL steps the raw value is '{"other":"step"}', not "step".  The
        validator must normalize it via cov.normalize_kind (not we._dekind, which
        only strips JSON quotes and leaves estate-kind JSON objects unchanged).
        This test proves the normalization path works on the actual raw DB form.
        """
        mock_is_behavior.return_value = True
        mock_list_nodes.return_value = [
            {"name": "PGM1", "kind": '"module"', "file": "src/PGM1.cbl", "symbol_id": "sym1"},
            {"name": "JCLSTEP1", "kind": '{"other":"step"}', "file": "jcl/job.jcl", "symbol_id": "sym2"},
        ]

        # Only REQ002 (legacy=JCLSTEP1) remains so we get exactly one error
        del self.rg["domains"]["DomainA"]["requirements"]["REQ002"]
        rg = {
            "domains": {"DomainA": {"requirements": {
                "REQ002": {"legacy": "JCLSTEP1", "description": "Calculates totals", "status": "active"}
            }}}
        }
        with open(
            os.path.join(self.workspace, ".anti-legacy", "requirements", "requirements_graph.json"), "w"
        ) as f:
            json.dump(rg, f)

        report, err = graph_validator.run_validation(self.workspace)
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "BLOCKED")
        jcl_errors = [e for e in report["errors"] if e["type"] == "JCL_NOT_PROGRAM"]
        self.assertEqual(len(jcl_errors), 1, "Expected JCL_NOT_PROGRAM error for raw estate kind")
        self.assertEqual(jcl_errors[0]["req_id"], "REQ002")


if __name__ == "__main__":
    unittest.main()
