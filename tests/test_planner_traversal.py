#!/usr/bin/env python3
"""
Unit tests for planner traversal strategies.
Verifies Bottom-Up, Top-Down, and Vertical Slice ordering.
"""
import unittest
from antilegacy_core.planner_utils import sort_requirements

class TestPlannerTraversal(unittest.TestCase):
    def test_empty_graph(self):
        rg = {"domains": {}}
        self.assertEqual(sort_requirements(rg, "bottom-up"), [])
        self.assertEqual(sort_requirements(rg, "top-down"), [])
        self.assertEqual(sort_requirements(rg, "vertical-slice"), [])

    def test_linear_dependency(self):
        # REQ_C -> REQ_B -> REQ_A
        # Note: B depends on A, C depends on B.
        rg = {
            "domains": {
                "Domain_main": {
                    "requirements": {
                        "REQ_A": {"dependencies": []},
                        "REQ_B": {"dependencies": ["REQ_A"]},
                        "REQ_C": {"dependencies": ["REQ_B"]}
                    }
                }
            }
        }
        
        # Bottom-Up: A, then B, then C
        self.assertEqual(sort_requirements(rg, "bottom-up"), ["REQ_A", "REQ_B", "REQ_C"])
        
        # Top-Down: C, then B, then A
        self.assertEqual(sort_requirements(rg, "top-down"), ["REQ_C", "REQ_B", "REQ_A"])
        
        # Vertical Slice (all in same domain): same as bottom-up
        self.assertEqual(sort_requirements(rg, "vertical-slice"), ["REQ_A", "REQ_B", "REQ_C"])

    def test_cross_domain_dependencies(self):
        # Domain_X: REQ_X1, REQ_X2 (X2 depends on X1)
        # Domain_Y: REQ_Y1 (depends on REQ_X2), REQ_Y2 (depends on REQ_Y1)
        rg = {
            "domains": {
                "Domain_Y": {
                    "requirements": {
                        "REQ_Y1": {"dependencies": ["REQ_X2"]},
                        "REQ_Y2": {"dependencies": ["REQ_Y1"]}
                    }
                },
                "Domain_X": {
                    "requirements": {
                        "REQ_X1": {"dependencies": []},
                        "REQ_X2": {"dependencies": ["REQ_X1"]}
                    }
                }
            }
        }
        
        # Bottom-Up: X1 -> X2 -> Y1 -> Y2
        self.assertEqual(sort_requirements(rg, "bottom-up"), ["REQ_X1", "REQ_X2", "REQ_Y1", "REQ_Y2"])
        
        # Top-Down: Y2 -> Y1 -> X2 -> X1
        self.assertEqual(sort_requirements(rg, "top-down"), ["REQ_Y2", "REQ_Y1", "REQ_X2", "REQ_X1"])
        
        # Vertical Slice: Domain_X (X1, X2) then Domain_Y (Y1, Y2)
        self.assertEqual(sort_requirements(rg, "vertical-slice"), ["REQ_X1", "REQ_X2", "REQ_Y1", "REQ_Y2"])

    def test_disjoint_domains(self):
        # Domain_B: REQ_B1
        # Domain_A: REQ_A1
        rg = {
            "domains": {
                "Domain_B": {
                    "requirements": {
                        "REQ_B1": {"dependencies": []}
                    }
                },
                "Domain_A": {
                    "requirements": {
                        "REQ_A1": {"dependencies": []}
                    }
                }
            }
        }
        
        # Bottom-Up (independent, alphabetical key sorting in topological_sort): A1, B1
        self.assertEqual(sort_requirements(rg, "bottom-up"), ["REQ_A1", "REQ_B1"])
        
        # Top-Down: B1, A1
        self.assertEqual(sort_requirements(rg, "top-down"), ["REQ_B1", "REQ_A1"])
        
        # Vertical Slice: Domain_A (A1) then Domain_B (B1) because Domain_A sorted before Domain_B
        self.assertEqual(sort_requirements(rg, "vertical-slice"), ["REQ_A1", "REQ_B1"])

    def test_invalid_strategy(self):
        rg = {
            "domains": {
                "Domain_A": {
                    "requirements": {
                        "REQ_A1": {"dependencies": []}
                    }
                }
            }
        }
        with self.assertRaises(ValueError):
            sort_requirements(rg, "invalid-strategy")

    def test_verify_order_invariants(self):
        import tempfile, shutil, os
        test_dir = tempfile.mkdtemp()
        task_path = os.path.join(test_dir, "task.md")
        
        rg = {
            "domains": {
                "Domain_X": {
                    "requirements": {
                        "REQ_A": {"dependencies": []},
                        "REQ_B": {"dependencies": ["REQ_A"]}
                    }
                },
                "Domain_Y": {
                    "requirements": {
                        "REQ_C": {"dependencies": []}
                    }
                }
            }
        }
        
        from antilegacy_core.planner_utils import verify_order
        
        # Test 1: Valid Bottom-Up (A scheduled before B)
        with open(task_path, 'w') as f:
            f.write("""# Tasks
- [ ] **TASK-001** A
  - Requirement: REQ_A
- [ ] **TASK-002** B
  - Requirement: REQ_B
- [ ] **TASK-003** C
  - Requirement: REQ_C
""")
        success, errors = verify_order(task_path, rg, "bottom-up")
        self.assertTrue(success)
        self.assertEqual(len(errors), 0)
        
        # Test 2: Invalid Bottom-Up (B scheduled before A)
        with open(task_path, 'w') as f:
            f.write("""# Tasks
- [ ] **TASK-001** B
  - Requirement: REQ_B
- [ ] **TASK-002** A
  - Requirement: REQ_A
""")
        success, errors = verify_order(task_path, rg, "bottom-up")
        self.assertFalse(success)
        self.assertGreater(len(errors), 0)
        self.assertIn("scheduled before its dependency", errors[0])
        
        # Test 3: Valid Top-Down (B scheduled before A)
        success, errors = verify_order(task_path, rg, "top-down")
        self.assertTrue(success)
        
        # Test 4: Invalid Top-Down (A scheduled before B)
        with open(task_path, 'w') as f:
            f.write("""# Tasks
- [ ] **TASK-001** A
  - Requirement: REQ_A
- [ ] **TASK-002** B
  - Requirement: REQ_B
""")
        success, errors = verify_order(task_path, rg, "top-down")
        self.assertFalse(success)
        self.assertGreater(len(errors), 0)
        
        # Test 5: Valid Vertical Slice (contiguous domain, bottom-up within domain)
        with open(task_path, 'w') as f:
            f.write("""# Tasks
- [ ] **TASK-001** A (Domain_X)
  - Requirement: REQ_A
- [ ] **TASK-002** B (Domain_X)
  - Requirement: REQ_B
- [ ] **TASK-003** C (Domain_Y)
  - Requirement: REQ_C
""")
        success, errors = verify_order(task_path, rg, "vertical-slice")
        self.assertTrue(success)
        
        # Test 6: Invalid Vertical Slice (non-contiguous domains: Domain_X, Domain_Y, Domain_X)
        with open(task_path, 'w') as f:
            f.write("""# Tasks
- [ ] **TASK-001** A (Domain_X)
  - Requirement: REQ_A
- [ ] **TASK-002** C (Domain_Y)
  - Requirement: REQ_C
- [ ] **TASK-003** B (Domain_X)
  - Requirement: REQ_B
""")
        success, errors = verify_order(task_path, rg, "vertical-slice")
        self.assertFalse(success)
        self.assertGreater(len(errors), 0)
        self.assertIn("Domain slices are not contiguous", errors[0])
        
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
