#!/usr/bin/env python3
"""
Unit tests for Semantic Join Analyzer.
"""
import unittest
import os
import json
import tempfile
import shutil
from scripts.semantic_join import normalize_path, analyze_project

class TestSemanticJoin(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        
        # Create mock directories for two applications
        self.app1_dir = os.path.join(self.test_dir, "app1")
        self.app2_dir = os.path.join(self.test_dir, "app2")
        os.makedirs(self.app1_dir, exist_ok=True)
        os.makedirs(self.app2_dir, exist_ok=True)
        
        # 1. Write Python client files in app1 (calling app2 and a dangling endpoint)
        python_code = """
import requests

def call_users():
    # Matched call
    res = requests.get("http://localhost:8080/api/v2/users")
    return res.json()

def call_missing():
    # Dangling call
    res = requests.post("http://localhost:8080/api/v3/missing")
    return res.status_code
"""
        with open(os.path.join(self.app1_dir, "client.py"), 'w') as f:
            f.write(python_code)
            
        # 2. Write Java controllers in app2 (defining the /api/v2/users endpoint)
        java_code = """
package com.example.demo;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class UserController {
    @GetMapping("/api/v2/users")
    public List<User> getUsers() {
        return userService.findAll();
    }
}
"""
        with open(os.path.join(self.app2_dir, "UserController.java"), 'w') as f:
            f.write(java_code)
            
        # 3. Create config.json in temporary directory
        self.reqs_dir = os.path.join(self.test_dir, "requirements")
        self.report_path = os.path.join(self.reqs_dir, "semantic_join_report.md")
        self.graph_path = os.path.join(self.reqs_dir, "semantic_join_graph.json")

        self.config_path = os.path.join(self.test_dir, "config.json")
        self.config_data = {
            "project_name": "test-join",
            "source_apps": [
                {"name": "app1", "path": self.app1_dir, "language": "python"},
                {"name": "app2", "path": self.app2_dir, "language": "java"}
            ],
            "target_stack": "python",
            "target_path": "./target/test-join",
            "paths": {
                "requirements_dir": self.reqs_dir,
                "semantic_join_report": self.report_path
            }
        }
        with open(self.config_path, 'w') as f:
            json.dump(self.config_data, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_normalize_path(self):
        self.assertEqual(normalize_path("/users/{id}"), "/users/*")
        self.assertEqual(normalize_path("/users/<id>/profile"), "/users/*/profile")
        self.assertEqual(normalize_path("/users/:id"), "/users/*")
        self.assertEqual(normalize_path("/api/v1/data?page=2"), "/api/v1/data")
        self.assertEqual(normalize_path("no-slash"), "/no-slash")

    def test_analyze_project_semantic_join(self):
        # Run analyzer
        analyze_project(self.config_path)
        
        self.assertTrue(os.path.exists(self.graph_path))
        self.assertTrue(os.path.exists(self.report_path))
        
        with open(self.graph_path) as f:
            graph = json.load(f)
            
        # Verify apps scanned
        self.assertIn("app1", graph["services"])
        self.assertIn("app2", graph["services"])
        
        # Verify endpoints discovered in app2
        app2_eps = [ep["path"] for ep in graph["services"]["app2"]["endpoints"]]
        self.assertIn("/api/v2/users", app2_eps)
        
        # Verify relations mapped (app1 calls app2 /api/v2/users)
        relations = graph["relations"]
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]["source"], "app1")
        self.assertEqual(relations[0]["target"], "app2")
        self.assertEqual(relations[0]["path"], "/api/v2/users")
        
        # Verify dangling call detected (app1 calling /api/v3/missing)
        dangling = graph["dangling_calls"]
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0]["source"], "app1")
        self.assertIn("/api/v3/missing", dangling[0]["path"])

    def test_analyze_project_schema_match(self):
        # Create temp dirs for Java and COBOL apps
        java_dir = os.path.join(self.test_dir, "java_app")
        cobol_dir = os.path.join(self.test_dir, "cobol_app")
        os.makedirs(java_dir, exist_ok=True)
        os.makedirs(cobol_dir, exist_ok=True)
        
        # Write Java file
        java_code = """
        package com.example.model;
        public class CreditCardEntry {
            private String cardNumber;
            private String expirationDate;
            private String cardHolderName;
        }
        """
        # Ensure directories exist
        java_src_dir = os.path.join(java_dir, "src", "main", "java", "com", "example", "model")
        os.makedirs(java_src_dir, exist_ok=True)
        with open(os.path.join(java_src_dir, "CreditCardEntry.java"), 'w') as f:
            f.write(java_code)
            
        # Write COBOL copybook file
        cobol_code = """
        01  CARD-RECORD.
            05  CARD-NUM                          PIC X(16).
            05  CARD-EXPIRAION-DATE               PIC X(10).
            05  CARD-EMBOSSED-NAME                PIC X(50).
        """
        with open(os.path.join(cobol_dir, "CVACT02Y.cpy"), 'w') as f:
            f.write(cobol_code)
            
        # Create config.json
        config_path = os.path.join(self.test_dir, "config_schema.json")
        reqs_dir = os.path.join(self.test_dir, "requirements_schema")
        report_path = os.path.join(reqs_dir, "semantic_join_report.md")
        graph_path = os.path.join(reqs_dir, "semantic_join_graph.json")
        
        config_data = {
            "project_name": "test-schema",
            "source_apps": [
                {"name": "java_app", "path": java_dir, "language": "java"},
                {"name": "cobol_app", "path": cobol_dir, "language": "cobol"}
            ],
            "target_stack": "java",
            "target_path": "./target/test-schema",
            "paths": {
                "requirements_dir": reqs_dir,
                "semantic_join_report": report_path
            }
        }
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
            
        # Run analyzer
        analyze_project(config_path)
        
        # Verify graph and report exist
        self.assertTrue(os.path.exists(graph_path))
        self.assertTrue(os.path.exists(report_path))
        
        with open(graph_path) as f:
            graph = json.load(f)
        # Verify schema match is in relations
        relations = graph["relations"]
        schema_matches = [r for r in relations if r.get("type") == "schema_match"]
        self.assertEqual(len(schema_matches), 1)
        
        match = schema_matches[0]
        self.assertEqual(match["source"], "cobol_app")
        self.assertEqual(match["target"], "java_app")
        self.assertIn("CARD-NUM <-> cardNumber", match["details"])
        self.assertIn("CARD-EXPIRAION-DATE <-> expirationDate", match["details"])
        self.assertIn("CARD-EMBOSSED-NAME <-> cardHolderName", match["details"])

if __name__ == "__main__":
    unittest.main()
