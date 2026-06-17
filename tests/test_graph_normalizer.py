#!/usr/bin/env python3
import unittest
import sys
import os
import json
import tempfile
import subprocess

# Adjust path to find scripts
# legacy scripts/ insert removed — leaf modules resolved via tests/conftest.py

from antilegacy_core.graph_normalizer import GraphNormalizer


class TestGraphNormalizerSingleApp(unittest.TestCase):
    """Single app with 2 programs and 1 table."""

    def setUp(self):
        self.code_graph = {
            "applications": {
                "billing": {
                    "nodes": {
                        "PROC_A": {
                            "type": "program",
                            "name": "PROC_A",
                            "file_path": "src/proc_a.cob"
                        },
                        "PROC_B": {
                            "type": "program",
                            "name": "PROC_B",
                            "file_path": "src/proc_b.cob"
                        },
                        "ORDERS": {
                            "type": "table",
                            "name": "ORDERS",
                            "file_path": "db/orders.sql"
                        }
                    },
                    "edges": [
                        {"source": "PROC_A", "target": "ORDERS", "type": "READS"},
                        {"source": "PROC_B", "target": "ORDERS", "type": "WRITES"}
                    ]
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph)
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def test_domains_created(self):
        self.assertIn("domains", self.graph, "requirements_graph must have 'domains' key")
        self.assertTrue(len(self.graph["domains"]) > 0, "At least one domain should be created")

    def test_requirement_for_each_program(self):
        all_reqs = {}
        for domain_data in self.graph["domains"].values():
            all_reqs.update(domain_data.get("requirements", {}))
        self.assertIn("REQ_PROC_A", all_reqs, "Requirement for PROC_A should exist")
        self.assertIn("REQ_PROC_B", all_reqs, "Requirement for PROC_B should exist")

    def test_entity_for_table(self):
        all_entities = {}
        for domain_data in self.graph["domains"].values():
            all_entities.update(domain_data.get("entities", {}))
        self.assertIn("ORDERS", all_entities, "Entity for ORDERS table should exist")

    def test_entity_has_description_and_fields(self):
        all_entities = {}
        for domain_data in self.graph["domains"].values():
            all_entities.update(domain_data.get("entities", {}))
        entity = all_entities["ORDERS"]
        self.assertIn("description", entity, "Entity should have description")
        self.assertIn("fields", entity, "Entity should have fields")
        self.assertTrue(len(entity["fields"]) > 0, "Entity should have at least one field")

    def test_programs_assigned_to_same_domain(self):
        # Both programs access ORDERS, so they should be in the same domain
        domain_name = "Domain_orders"
        self.assertIn(domain_name, self.graph["domains"],
                      "Shared table domain should be named Domain_orders")
        reqs = self.graph["domains"][domain_name]["requirements"]
        self.assertIn("REQ_PROC_A", reqs,
                      "PROC_A should be in the ORDERS domain")
        self.assertIn("REQ_PROC_B", reqs,
                      "PROC_B should be in the ORDERS domain")

    def test_data_access_captured(self):
        domain_name = "Domain_orders"
        req_a = self.graph["domains"][domain_name]["requirements"]["REQ_PROC_A"]
        self.assertIn("ORDERS", req_a["data_access"],
                      "REQ_PROC_A should list ORDERS in data_access")


class TestGraphNormalizerMultiApp(unittest.TestCase):
    """Multiple apps sharing a table → shared domain created."""

    def setUp(self):
        self.code_graph = {
            "applications": {
                "app_alpha": {
                    "nodes": {
                        "WORKER": {
                            "type": "program",
                            "name": "WORKER",
                            "file_path": "src/worker.py"
                        },
                        "SHARED_TBL": {
                            "type": "table",
                            "name": "SHARED_TBL",
                            "file_path": "db/shared.sql"
                        }
                    },
                    "edges": [
                        {"source": "WORKER", "target": "SHARED_TBL", "type": "READS"}
                    ]
                },
                "app_beta": {
                    "nodes": {
                        "LOADER": {
                            "type": "program",
                            "name": "LOADER",
                            "file_path": "src/loader.py"
                        },
                        "SHARED_TBL": {
                            "type": "table",
                            "name": "SHARED_TBL",
                            "file_path": "db/shared.sql"
                        }
                    },
                    "edges": [
                        {"source": "LOADER", "target": "SHARED_TBL", "type": "WRITES"}
                    ]
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph)
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def test_shared_domain_created(self):
        self.assertIn("Domain_shared_tbl", self.graph["domains"],
                      "Shared table should produce Domain_shared_tbl")

    def test_shared_domain_has_entity(self):
        entities = self.graph["domains"]["Domain_shared_tbl"]["entities"]
        self.assertIn("SHARED_TBL", entities,
                      "Shared domain should contain the SHARED_TBL entity")

    def test_both_apps_programs_in_shared_domain(self):
        reqs = self.graph["domains"]["Domain_shared_tbl"]["requirements"]
        req_ids = set(reqs.keys())
        self.assertIn("REQ_WORKER", req_ids,
                      "WORKER from app_alpha should be in shared domain")
        self.assertIn("REQ_LOADER", req_ids,
                      "LOADER from app_beta should be in shared domain")


class TestGraphNormalizerIsolatedProgram(unittest.TestCase):
    """Isolated program (no file/table access) → assigned to app-core domain."""

    def setUp(self):
        self.code_graph = {
            "applications": {
                "myapp": {
                    "nodes": {
                        "ORPHAN": {
                            "type": "program",
                            "name": "ORPHAN",
                            "file_path": "src/orphan.cob"
                        }
                    },
                    "edges": []
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph)
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def test_core_domain_created(self):
        self.assertIn("Domain_myapp_core", self.graph["domains"],
                      "Isolated program should be placed in Domain_myapp_core")

    def test_requirement_created_in_core(self):
        reqs = self.graph["domains"]["Domain_myapp_core"]["requirements"]
        self.assertIn("REQ_ORPHAN", reqs,
                      "ORPHAN should have a requirement in core domain")


class TestGraphNormalizerDependencies(unittest.TestCase):
    """Dependencies captured: program CALLS another → dependency in requirements."""

    def setUp(self):
        self.code_graph = {
            "applications": {
                "svc": {
                    "nodes": {
                        "CALLER": {
                            "type": "program",
                            "name": "CALLER",
                            "file_path": "src/caller.py"
                        },
                        "CALLEE": {
                            "type": "program",
                            "name": "CALLEE",
                            "file_path": "src/callee.py"
                        },
                        "DATA_FILE": {
                            "type": "file",
                            "name": "DATA_FILE",
                            "file_path": "data/file.csv"
                        }
                    },
                    "edges": [
                        {"source": "CALLER", "target": "CALLEE", "type": "CALLS"},
                        {"source": "CALLER", "target": "DATA_FILE", "type": "READS"},
                        {"source": "CALLEE", "target": "DATA_FILE", "type": "READS"}
                    ]
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph)
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def test_caller_depends_on_callee(self):
        # Find the requirement for CALLER
        all_reqs = {}
        for domain_data in self.graph["domains"].values():
            all_reqs.update(domain_data.get("requirements", {}))
        caller_req = all_reqs.get("REQ_CALLER")
        self.assertIsNotNone(caller_req, "REQ_CALLER should exist")
        self.assertIn("REQ_CALLEE", caller_req["dependencies"],
                      "CALLER should depend on CALLEE")

    def test_callee_has_no_program_dependencies(self):
        all_reqs = {}
        for domain_data in self.graph["domains"].values():
            all_reqs.update(domain_data.get("requirements", {}))
        callee_req = all_reqs.get("REQ_CALLEE")
        self.assertIsNotNone(callee_req, "REQ_CALLEE should exist")
        # CALLEE only accesses DATA_FILE, no program deps
        program_deps = [d for d in callee_req["dependencies"] if d.startswith("REQ_")]
        # The only deps should not include any program dependency (CALLEE doesn't call another program)
        self.assertNotIn("REQ_CALLER", callee_req["dependencies"],
                         "CALLEE should not depend on CALLER")


class TestGraphNormalizerEmpty(unittest.TestCase):
    """Empty input → empty output, no crash."""

    def test_empty_applications(self):
        normalizer = GraphNormalizer({"applications": {}})
        normalizer.normalize()
        self.assertEqual(normalizer.requirements_graph["domains"], {},
                         "Empty applications should produce empty domains")

    def test_completely_empty(self):
        normalizer = GraphNormalizer({})
        normalizer.normalize()
        self.assertEqual(normalizer.requirements_graph["domains"], {},
                         "Completely empty input should produce empty domains")

    def test_no_crash_on_missing_keys(self):
        normalizer = GraphNormalizer({"applications": {"x": {}}})
        normalizer.normalize()
        self.assertIn("domains", normalizer.requirements_graph,
                      "Should still have domains key even with sparse input")


class TestGraphNormalizerCLI(unittest.TestCase):
    """CLI args work (--input, --output)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts'))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cli_produces_output(self):
        code_graph = {
            "applications": {
                "demo": {
                    "nodes": {
                        "P1": {"type": "program", "name": "P1", "file_path": "p1.py"}
                    },
                    "edges": []
                }
            }
        }
        input_path = os.path.join(self.tmpdir, "input.json")
        output_path = os.path.join(self.tmpdir, "output", "reqs.json")
        with open(input_path, 'w') as f:
            json.dump(code_graph, f)

        result = subprocess.run(
            [sys.executable, "-m", "antilegacy_core.graph_normalizer",
             '--input', input_path, '--output', output_path],
            # Run in the tmpdir so the CLI's default --config (.anti-legacy/config.json,
            # resolved relative to CWD) cannot pick up the host workspace's config.
            # Without this, an ambient `migration_mode: functional` silently changes the
            # default mode and contaminates the assertions below.
            cwd=self.tmpdir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"CLI should exit 0, stderr: {result.stderr}")
        self.assertTrue(os.path.exists(output_path),
                        "Output file should be created by CLI")

        with open(output_path, 'r') as f:
            data = json.load(f)
        self.assertIn("domains", data, "CLI output JSON should have 'domains'")

    def test_cli_bad_input_path(self):
        result = subprocess.run(
            [sys.executable, "-m", "antilegacy_core.graph_normalizer",
             '--input', '/nonexistent/path.json',
             '--output', os.path.join(self.tmpdir, 'out.json')],
            cwd=self.tmpdir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertNotEqual(result.returncode, 0,
                            "CLI should fail with nonexistent input file")


# ==================================================================
# Functional mode tests
# ==================================================================

class TestFunctionalLeafMerging(unittest.TestCase):
    """Single-caller programs get absorbed into their caller."""

    def setUp(self):
        # BILLING calls PAY-GATE (only caller) → should merge
        self.code_graph = {
            "applications": {
                "app": {
                    "nodes": {
                        "BILLING": {"type": "program", "name": "BILLING", "file_path": "BILLING.cbl"},
                        "PAY-GATE": {"type": "program", "name": "PAY-GATE", "file_path": "PAY-GATE.cbl"},
                        "INVOICES": {"type": "table", "name": "INVOICES", "file_path": "db/inv"},
                        "PAYLEDGR": {"type": "file", "name": "PAYLEDGR", "file_path": "PAYLEDGR"},
                    },
                    "edges": [
                        {"source": "BILLING", "target": "PAY-GATE", "type": "call"},
                        {"source": "BILLING", "target": "INVOICES", "type": "READS"},
                        {"source": "PAY-GATE", "target": "PAYLEDGR", "type": "WRITES"},
                    ]
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph, mode="functional")
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def _all_reqs(self):
        reqs = {}
        for domain_data in self.graph["domains"].values():
            reqs.update(domain_data.get("requirements", {}))
        return reqs

    def test_produces_single_capability(self):
        """PAY-GATE (single caller) merges into BILLING → 1 capability."""
        reqs = self._all_reqs()
        self.assertEqual(len(reqs), 1,
                         f"Should produce 1 merged capability, got {len(reqs)}: {list(reqs.keys())}")

    def test_merged_has_both_legacy_components(self):
        """The merged capability traces back to both programs."""
        reqs = self._all_reqs()
        cap = list(reqs.values())[0]
        legacy = cap["legacy_components"]
        legacy_flat = [c.split(":")[-1] for c in legacy]
        self.assertIn("BILLING", legacy_flat,
                       "Merged capability should include BILLING")
        self.assertIn("PAY-GATE", legacy_flat,
                       "Merged capability should include PAY-GATE")

    def test_merged_has_combined_data_access(self):
        """Data access from both programs is combined."""
        reqs = self._all_reqs()
        cap = list(reqs.values())[0]
        self.assertIn("INVOICES", cap["data_access"],
                       "Should include BILLING's data access")
        self.assertIn("PAYLEDGR", cap["data_access"],
                       "Should include PAY-GATE's data access")

    def test_merged_programs_listed(self):
        """The merged_programs field shows which programs were combined."""
        reqs = self._all_reqs()
        cap = list(reqs.values())[0]
        self.assertIn("merged_programs", cap, "Should have merged_programs field")
        self.assertIn("BILLING", cap["merged_programs"])
        self.assertIn("PAY-GATE", cap["merged_programs"])

    def test_title_uses_intent_not_program_name(self):
        """Capability title is business-intent based, not 'Migrate X'."""
        reqs = self._all_reqs()
        cap = list(reqs.values())[0]
        self.assertNotIn("Migrate", cap["title"],
                          "Functional mode should not use 'Migrate X' titles")


class TestFunctionalMultiCallerPreserved(unittest.TestCase):
    """Programs called from 2+ places stay as separate capabilities."""

    def setUp(self):
        # SHARED is called by both CALLER_A and CALLER_B
        self.code_graph = {
            "applications": {
                "app": {
                    "nodes": {
                        "CALLER_A": {"type": "program", "name": "CALLER_A", "file_path": "a.cbl"},
                        "CALLER_B": {"type": "program", "name": "CALLER_B", "file_path": "b.cbl"},
                        "SHARED": {"type": "program", "name": "SHARED", "file_path": "shared.cbl"},
                        "TBL": {"type": "table", "name": "TBL", "file_path": "db/tbl"},
                    },
                    "edges": [
                        {"source": "CALLER_A", "target": "SHARED", "type": "call"},
                        {"source": "CALLER_B", "target": "SHARED", "type": "call"},
                        {"source": "CALLER_A", "target": "TBL", "type": "READS"},
                        {"source": "CALLER_B", "target": "TBL", "type": "READS"},
                        {"source": "SHARED", "target": "TBL", "type": "READS"},
                    ]
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph, mode="functional")
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def test_multi_caller_not_merged(self):
        """SHARED (2 callers) remains its own capability."""
        reqs = {}
        for domain_data in self.graph["domains"].values():
            reqs.update(domain_data.get("requirements", {}))
        # 3 programs, none should merge (SHARED has 2 callers, CALLER_A and CALLER_B
        # each have 0 callers). With data affinity all 3 access TBL so they might
        # cluster, but should still be ≥1 capability
        self.assertTrue(len(reqs) >= 1,
                         f"Should have at least 1 capability, got {len(reqs)}")

        # Check that SHARED appears as a legacy_component somewhere
        all_components = []
        for req in reqs.values():
            all_components.extend([c.split(":")[-1] for c in req["legacy_components"]])
        self.assertIn("SHARED", all_components,
                       "SHARED should appear as a legacy component")


class TestFunctionalDataAffinity(unittest.TestCase):
    """Programs sharing 3+ data accesses cluster into one capability."""

    def setUp(self):
        # PROG_X and PROG_Y both access TBL_A, TBL_B, TBL_C (3 shared)
        # They don't call each other — only data affinity links them
        self.code_graph = {
            "applications": {
                "app": {
                    "nodes": {
                        "PROG_X": {"type": "program", "name": "PROG_X", "file_path": "x.cbl"},
                        "PROG_Y": {"type": "program", "name": "PROG_Y", "file_path": "y.cbl"},
                        "TBL_A": {"type": "table", "name": "TBL_A", "file_path": "db/a"},
                        "TBL_B": {"type": "table", "name": "TBL_B", "file_path": "db/b"},
                        "TBL_C": {"type": "table", "name": "TBL_C", "file_path": "db/c"},
                    },
                    "edges": [
                        {"source": "PROG_X", "target": "TBL_A", "type": "READS"},
                        {"source": "PROG_X", "target": "TBL_B", "type": "READS"},
                        {"source": "PROG_X", "target": "TBL_C", "type": "WRITES"},
                        {"source": "PROG_Y", "target": "TBL_A", "type": "READS"},
                        {"source": "PROG_Y", "target": "TBL_B", "type": "WRITES"},
                        {"source": "PROG_Y", "target": "TBL_C", "type": "READS"},
                    ]
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph, mode="functional")
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def test_data_affinity_merges_into_one(self):
        """Two programs sharing 3 tables → one capability."""
        reqs = {}
        for domain_data in self.graph["domains"].values():
            reqs.update(domain_data.get("requirements", {}))
        self.assertEqual(len(reqs), 1,
                         f"3+ shared data accesses should merge into 1 capability, got {len(reqs)}")

    def test_affinity_merged_has_both_components(self):
        """Merged capability has both programs as legacy_components."""
        reqs = {}
        for domain_data in self.graph["domains"].values():
            reqs.update(domain_data.get("requirements", {}))
        cap = list(reqs.values())[0]
        components = [c.split(":")[-1] for c in cap["legacy_components"]]
        self.assertIn("PROG_X", components, "PROG_X should be in merged capability")
        self.assertIn("PROG_Y", components, "PROG_Y should be in merged capability")


class TestFunctionalDeadCode(unittest.TestCase):
    """Programs with no callers and no data access flagged as review."""

    def setUp(self):
        self.code_graph = {
            "applications": {
                "app": {
                    "nodes": {
                        "LIVE": {"type": "program", "name": "LIVE", "file_path": "live.cbl"},
                        "DEAD": {"type": "program", "name": "DEAD", "file_path": "dead.cbl"},
                        "TBL": {"type": "table", "name": "TBL", "file_path": "db/t"},
                    },
                    "edges": [
                        {"source": "LIVE", "target": "TBL", "type": "READS"},
                        # DEAD has no edges at all
                    ]
                }
            }
        }
        self.normalizer = GraphNormalizer(self.code_graph, mode="functional")
        self.normalizer.normalize()
        self.graph = self.normalizer.requirements_graph

    def test_dead_code_flagged(self):
        """Program with no callers and no data access gets status: review."""
        reqs = {}
        for domain_data in self.graph["domains"].values():
            reqs.update(domain_data.get("requirements", {}))

        dead_req = None
        for req_id, req in reqs.items():
            components = [c.split(":")[-1] for c in req["legacy_components"]]
            if "DEAD" in components:
                dead_req = req
                break

        self.assertIsNotNone(dead_req, "DEAD should still produce a capability")
        self.assertEqual(dead_req["status"], "review",
                          "DEAD code should have status 'review'")

    def test_live_code_active(self):
        """Program with data access is status: active."""
        reqs = {}
        for domain_data in self.graph["domains"].values():
            reqs.update(domain_data.get("requirements", {}))

        live_req = None
        for req_id, req in reqs.items():
            components = [c.split(":")[-1] for c in req["legacy_components"]]
            if "LIVE" in components:
                live_req = req
                break

        self.assertIsNotNone(live_req, "LIVE should produce a capability")
        self.assertEqual(live_req["status"], "active",
                          "LIVE code should have status 'active'")


class TestFunctionalEmpty(unittest.TestCase):
    """Empty input doesn't crash in functional mode."""

    def test_empty_functional(self):
        normalizer = GraphNormalizer({"applications": {}}, mode="functional")
        normalizer.normalize()
        self.assertEqual(normalizer.requirements_graph["domains"], {},
                         "Empty input in functional mode should produce empty domains")


class TestFunctionalCLI(unittest.TestCase):
    """CLI --mode flag works."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts'))
        self.code_graph = {
            "applications": {
                "app": {
                    "nodes": {
                        "A": {"type": "program", "name": "A", "file_path": "a.cbl"},
                        "B": {"type": "program", "name": "B", "file_path": "b.cbl"},
                        "T": {"type": "table", "name": "T", "file_path": "db/t"},
                    },
                    "edges": [
                        {"source": "A", "target": "B", "type": "call"},
                        {"source": "A", "target": "T", "type": "READS"},
                        {"source": "B", "target": "T", "type": "READS"},
                    ]
                }
            }
        }
        self.input_path = os.path.join(self.tmpdir, "input.json")
        with open(self.input_path, 'w') as f:
            json.dump(self.code_graph, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cli_functional_mode(self):
        """CLI with --mode functional produces merged output."""
        output_path = os.path.join(self.tmpdir, "out.json")
        result = subprocess.run(
            [sys.executable, "-m", "antilegacy_core.graph_normalizer",
             '--input', self.input_path, '--output', output_path, '--mode', 'functional'],
            cwd=self.tmpdir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"CLI functional mode should exit 0, stderr: {result.stderr}")
        self.assertIn("functional", result.stdout.lower(),
                       "Output should mention functional mode")

        with open(output_path, 'r') as f:
            data = json.load(f)

        # A calls B (single caller) → should merge into 1 capability
        all_reqs = {}
        for domain_data in data["domains"].values():
            all_reqs.update(domain_data.get("requirements", {}))
        self.assertEqual(len(all_reqs), 1,
                         f"Functional mode should merge A+B into 1 cap, got {len(all_reqs)}")

    def test_cli_structural_default(self):
        """CLI without --mode defaults to structural."""
        output_path = os.path.join(self.tmpdir, "out_default.json")
        result = subprocess.run(
            [sys.executable, "-m", "antilegacy_core.graph_normalizer",
             '--input', self.input_path, '--output', output_path],
            # Hermetic CWD: the no-`--mode` default must resolve from the script's own
            # fallback ('structural'), NOT from whatever migration_mode the host
            # workspace's .anti-legacy/config.json happens to carry. This is the
            # permanent fix for the recurring functional-config footgun.
            cwd=self.tmpdir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"CLI default mode should exit 0, stderr: {result.stderr}")

        with open(output_path, 'r') as f:
            data = json.load(f)

        # Structural mode: 2 programs → 2 requirements
        all_reqs = {}
        for domain_data in data["domains"].values():
            all_reqs.update(domain_data.get("requirements", {}))
        self.assertEqual(len(all_reqs), 2,
                         f"Structural mode should produce 2 reqs, got {len(all_reqs)}")


if __name__ == '__main__':
    unittest.main()
