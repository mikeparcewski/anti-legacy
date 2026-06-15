#!/usr/bin/env python3
import unittest
import sys
import os
import json
import tempfile
import shutil
import subprocess

# Adjust path to find scripts
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts')))


class TestPacketGeneratorValid(unittest.TestCase):
    """Valid requirements graph → produces Markdown with all domains, requirements, entities."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.requirements = {
            "domains": {
                "Domain_orders": {
                    "requirements": {
                        "REQ_PROC_A": {
                            "title": "Migrate PROC_A",
                            "description": "Business logic for PROC_A",
                            "legacy_components": ["billing:PROC_A"],
                            "data_access": ["ORDERS"],
                            "dependencies": []
                        },
                        "REQ_PROC_B": {
                            "title": "Migrate PROC_B",
                            "description": "Business logic for PROC_B",
                            "legacy_components": ["billing:PROC_B"],
                            "data_access": ["ORDERS"],
                            "dependencies": ["REQ_PROC_A"]
                        }
                    },
                    "entities": {
                        "ORDERS": {
                            "description": "Logical entity derived from legacy asset: ORDERS",
                            "fields": [
                                {"name": "id", "type": "string", "description": "Primary identifier"},
                                {"name": "amount", "type": "decimal", "description": "Order amount"}
                            ]
                        }
                    }
                },
                "Domain_users_core": {
                    "requirements": {
                        "REQ_AUTH": {
                            "title": "Migrate AUTH",
                            "description": "Auth logic",
                            "legacy_components": ["users:AUTH"],
                            "data_access": [],
                            "dependencies": []
                        }
                    },
                    "entities": {}
                }
            }
        }
        self.input_path = os.path.join(self.tmpdir, "requirements.json")
        with open(self.input_path, 'w') as f:
            json.dump(self.requirements, f)

        # Import here to use the class directly
        from packet_generator import ReviewPacketGenerator
        self.generator = ReviewPacketGenerator(self.input_path)
        self.md = self.generator.generate_markdown()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_title_present(self):
        self.assertIn("# Digital Review Packet & Modernization Blueprint", self.md,
                      "Markdown should contain the main title")

    def test_all_domains_present(self):
        self.assertIn("Domain: Domain_orders", self.md,
                      "Markdown should reference Domain_orders")
        self.assertIn("Domain: Domain_users_core", self.md,
                      "Markdown should reference Domain_users_core")

    def test_all_requirements_present(self):
        self.assertIn("REQ_PROC_A", self.md, "REQ_PROC_A should appear in Markdown")
        self.assertIn("REQ_PROC_B", self.md, "REQ_PROC_B should appear in Markdown")
        self.assertIn("REQ_AUTH", self.md, "REQ_AUTH should appear in Markdown")

    def test_all_entities_present(self):
        self.assertIn("Entity: ORDERS", self.md, "ORDERS entity should appear in Markdown")

    def test_entity_fields_in_table(self):
        self.assertIn("| id | string |", self.md,
                      "Entity field 'id' should appear in table")
        self.assertIn("| amount | decimal |", self.md,
                      "Entity field 'amount' should appear in table")

    def test_requirement_titles(self):
        self.assertIn("Migrate PROC_A", self.md, "Requirement title should be present")
        self.assertIn("Migrate PROC_B", self.md, "Requirement title should be present")
        self.assertIn("Migrate AUTH", self.md, "Requirement title should be present")

    def test_architecture_overview_section(self):
        self.assertIn("## Architecture Overview", self.md,
                      "Architecture Overview section should be present")

    def test_table_of_contents(self):
        self.assertIn("## Table of Contents", self.md,
                      "Table of Contents should be present")
        self.assertIn("Domain: Domain_orders", self.md,
                      "TOC should list Domain_orders")


class TestPacketGeneratorMermaid(unittest.TestCase):
    """Mermaid diagram generated with correct nodes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.requirements = {
            "domains": {
                "Domain_data": {
                    "requirements": {
                        "REQ_EXTRACT": {
                            "title": "Migrate EXTRACT",
                            "description": "Extract logic",
                            "legacy_components": ["app:EXTRACT"],
                            "data_access": [],
                            "dependencies": []
                        },
                        "REQ_TRANSFORM": {
                            "title": "Migrate TRANSFORM",
                            "description": "Transform logic",
                            "legacy_components": ["app:TRANSFORM"],
                            "data_access": [],
                            "dependencies": ["REQ_EXTRACT"]
                        }
                    },
                    "entities": {}
                }
            }
        }
        self.input_path = os.path.join(self.tmpdir, "requirements.json")
        with open(self.input_path, 'w') as f:
            json.dump(self.requirements, f)

        from packet_generator import ReviewPacketGenerator
        self.generator = ReviewPacketGenerator(self.input_path)
        self.md = self.generator.generate_markdown()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mermaid_block_present(self):
        self.assertIn("```mermaid", self.md, "Mermaid code block should be present")
        self.assertIn("flowchart TD", self.md, "Mermaid flowchart directive should be present")

    def test_mermaid_contains_nodes(self):
        self.assertIn('REQ_EXTRACT["Migrate EXTRACT"]', self.md,
                      "Mermaid should contain REQ_EXTRACT node")
        self.assertIn('REQ_TRANSFORM["Migrate TRANSFORM"]', self.md,
                      "Mermaid should contain REQ_TRANSFORM node")

    def test_mermaid_contains_subgraph(self):
        self.assertIn("subgraph Domain_data", self.md,
                      "Mermaid should have subgraph for Domain_data")

    def test_dependency_arrows(self):
        self.assertIn("REQ_EXTRACT --> REQ_TRANSFORM", self.md,
                      "Dependency arrow from EXTRACT to TRANSFORM should be in Mermaid")


class TestPacketGeneratorGateChecklist(unittest.TestCase):
    """Gate checklist present with all 4 gates."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.requirements = {"domains": {}}
        self.input_path = os.path.join(self.tmpdir, "requirements.json")
        with open(self.input_path, 'w') as f:
            json.dump(self.requirements, f)

        from packet_generator import ReviewPacketGenerator
        self.generator = ReviewPacketGenerator(self.input_path)
        self.md = self.generator.generate_markdown()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_gate_section_present(self):
        self.assertIn("## Rigid Sign-off Gate Checklist", self.md,
                      "Gate checklist section should be present")

    def test_gate_1(self):
        self.assertIn("GATE_1_DESIGN", self.md,
                      "GATE_1_DESIGN should be in the checklist")

    def test_gate_2(self):
        self.assertIn("GATE_2_PLAN", self.md,
                      "GATE_2_PLAN should be in the checklist")

    def test_gate_3(self):
        self.assertIn("GATE_3_BUILD", self.md,
                      "GATE_3_BUILD should be in the checklist")

    def test_gate_4(self):
        self.assertIn("GATE_4_UAT", self.md,
                      "GATE_4_UAT should be in the checklist")

    def test_all_gates_pending(self):
        count = self.md.count("`PENDING`")
        self.assertEqual(count, 4,
                         f"All 4 gates should be PENDING, found {count}")


class TestPacketGeneratorEmptyDomains(unittest.TestCase):
    """Empty domains → doesn't crash."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.requirements = {"domains": {}}
        self.input_path = os.path.join(self.tmpdir, "requirements.json")
        with open(self.input_path, 'w') as f:
            json.dump(self.requirements, f)

        from packet_generator import ReviewPacketGenerator
        self.generator = ReviewPacketGenerator(self.input_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_crash(self):
        md = self.generator.generate_markdown()
        self.assertIsInstance(md, str, "Should return a string even with empty domains")

    def test_still_has_title(self):
        md = self.generator.generate_markdown()
        self.assertIn("# Digital Review Packet", md,
                      "Title should still be present with empty domains")

    def test_write_packet_works(self):
        output_path = os.path.join(self.tmpdir, "out", "packet.md")
        self.generator.write_packet(output_path)
        self.assertTrue(os.path.exists(output_path),
                        "write_packet should create file even with empty domains")


class TestPacketGeneratorCLI(unittest.TestCase):
    """CLI args work (--input, --output)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts'))
        self.requirements = {
            "domains": {
                "Domain_test": {
                    "requirements": {
                        "REQ_X": {
                            "title": "Migrate X",
                            "description": "X logic",
                            "legacy_components": ["a:X"],
                            "data_access": [],
                            "dependencies": []
                        }
                    },
                    "entities": {}
                }
            }
        }
        self.input_path = os.path.join(self.tmpdir, "input.json")
        with open(self.input_path, 'w') as f:
            json.dump(self.requirements, f)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cli_produces_markdown(self):
        output_path = os.path.join(self.tmpdir, "output", "packet.md")
        result = subprocess.run(
            [sys.executable, os.path.join(self.scripts_dir, 'packet_generator.py'),
             '--input', self.input_path, '--output', output_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"CLI should exit 0, stderr: {result.stderr}")
        self.assertTrue(os.path.exists(output_path),
                        "CLI should create the output Markdown file")

        with open(output_path, 'r') as f:
            content = f.read()
        self.assertIn("# Digital Review Packet", content,
                      "Output file should contain the review packet title")
        self.assertIn("REQ_X", content,
                      "Output should contain the requirement from input")

    def test_cli_bad_input(self):
        result = subprocess.run(
            [sys.executable, os.path.join(self.scripts_dir, 'packet_generator.py'),
             '--input', '/nonexistent/path.json',
             '--output', os.path.join(self.tmpdir, 'out.md')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertNotEqual(result.returncode, 0,
                            "CLI should fail with nonexistent input file")

    def test_cli_missing_args(self):
        result = subprocess.run(
            [sys.executable, os.path.join(self.scripts_dir, 'packet_generator.py')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertNotEqual(result.returncode, 0,
                            "CLI should fail when required args are missing")


if __name__ == '__main__':
    unittest.main()
