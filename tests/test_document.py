#!/usr/bin/env python3
"""Hermetic tests for scripts/document.py (B2 — DOCUMENT phase).

Every test builds a small synthetic config / blueprint / requirements / target
graph fixture in a tmpdir, runs the synthesizer, and asserts the four docs are
derived (not coined) from those artifacts and registered in the manifest. No
network, no real workspace, no LLM.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts')))

import document  # noqa: E402


SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../scripts'))


def _config(target_path):
    return {
        "project_name": "demo-card-service",
        "source_apps": [
            {"name": "legacy-carddemo", "path": "x", "language": "cobol"},
            {"name": "legacy-ccps", "path": "y", "language": "java"},
        ],
        "target_stack": "java",
        "target_path": target_path,
        "deployment_target": "Kubernetes (GKE)",
        "database": "PostgreSQL",
        "embeddings": True,
    }


def _blueprint(target_path):
    return {
        "project": "demo-card-service",
        "target_stack": "java",
        "target_path": target_path,
        "style": "hexagonal",
        "domains": {
            "billing": {
                "package": "com.demo.billing",
                "components": {
                    "REQ_BILL_PROC": {
                        "class_name": "BillingService",
                        "component_type": "service",
                        "api": {"method": "POST", "path": "/billing/process"},
                        "dependencies": ["REQ_ACCT_LOOKUP"],
                    },
                    "REQ_ACCT_LOOKUP": {
                        "class_name": "AccountRepository",
                        "component_type": "repository",
                    },
                },
                "schema": {
                    "billing_run": {
                        "columns": [
                            {"name": "id", "type": "BIGINT", "pk": True},
                            {"name": "amount", "type": "DECIMAL(11,2)"},
                        ]
                    }
                },
            },
            "account": {
                "package": "com.demo.account",
                "components": {
                    "REQ_ACCT_VIEW": {
                        "class_name": "AccountController",
                        "component_type": "controller",
                        "api": {"method": "GET", "path": "/accounts/{id}"},
                    }
                },
                "schema": {},
            },
        },
        "build_order": ["REQ_ACCT_LOOKUP", "REQ_BILL_PROC", "REQ_ACCT_VIEW"],
    }


def _requirements():
    return {
        "metadata": {"migration_mode": "functional"},
        "domains": {
            "billing": {
                "requirements": {
                    "REQ_BILL_PROC": {
                        "title": "Process billing run",
                        "description": "Computes statements for a cycle.",
                        "legacy_components": ["legacy-carddemo:CBSTM03B"],
                        "data_access": ["ACCTFILE", "TRANFILE"],
                        "dependencies": ["REQ_ACCT_LOOKUP"],
                    }
                },
                "entities": {
                    "ACCTFILE": {
                        "description": "Account master",
                        "fields": [
                            {"name": "id", "type": "string", "description": "key"},
                        ],
                    }
                },
            },
            "account": {
                "requirements": {
                    "REQ_ACCT_LOOKUP": {
                        "title": "Look up account",
                        "description": "Reads the account master.",
                        "legacy_components": ["legacy-carddemo:COACTVWC"],
                        "data_access": ["ACCTFILE"],
                        "dependencies": [],
                    }
                },
                "entities": {},
            },
        },
    }


def _target_graph(target_path):
    return {
        "generated_at": "2026-06-14T00:00:00Z",
        "target_path": target_path,
        "domains": {
            "billing": {
                "package": "com.demo.billing",
                "components": {"BillingService": {"type": "service"}},
                "entities": {"BillingRun": {"table_name": "billing_run", "columns": []}},
            }
        },
    }


def _write_fixture(root, target_path, manifest=True):
    """Write config/blueprint/requirements/target_graph (+ optional manifest)."""
    cfg_dir = os.path.join(root, ".anti-legacy")
    req_dir = os.path.join(cfg_dir, "requirements")
    os.makedirs(req_dir, exist_ok=True)

    paths = {
        "config": os.path.join(cfg_dir, "config.json"),
        "blueprint": os.path.join(req_dir, "blueprint.json"),
        "requirements": os.path.join(req_dir, "requirements_graph.json"),
        "target_graph": os.path.join(cfg_dir, "target_graph.json"),
        "manifest": os.path.join(cfg_dir, "manifest.json"),
    }
    with open(paths["config"], "w") as f:
        json.dump(_config(target_path), f)
    with open(paths["blueprint"], "w") as f:
        json.dump(_blueprint(target_path), f)
    with open(paths["requirements"], "w") as f:
        json.dump(_requirements(), f)
    with open(paths["target_graph"], "w") as f:
        json.dump(_target_graph(target_path), f)
    if manifest:
        with open(paths["manifest"], "w") as f:
            json.dump({
                "project": {"name": "demo-card-service"},
                "phase": {"current": "uat", "completed": []},
                "gates": {},
                "artifacts": {},
                "learnings": [],
            }, f)
        # audit log so _append_audit has somewhere to write.
        open(os.path.join(cfg_dir, "audit.jsonl"), "a").close()
    return paths


class TestSynthesizeApi(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.target = os.path.join(self.root, "target", "app")
        self.paths = _write_fixture(self.root, self.target)
        self.target_dir, self.written = document.synthesize(
            config_path=self.paths["config"],
            blueprint_path=self.paths["blueprint"],
            requirements_path=self.paths["requirements"],
            target_graph_path=self.paths["target_graph"],
            manifest_path=self.paths["manifest"],
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _read(self, name):
        with open(os.path.join(self.target, name)) as f:
            return f.read()

    def test_all_four_docs_written(self):
        for name in ("README.md", "ARCHITECTURE.md", "DEPENDENCIES.md", "ENVIRONMENTS.md"):
            self.assertTrue(os.path.exists(os.path.join(self.target, name)),
                            "{0} should be written under the target dir".format(name))

    def test_returns_four_artifact_ids(self):
        self.assertEqual(
            set(self.written),
            {"doc-readme", "doc-architecture", "doc-dependencies", "doc-environments"},
        )

    # -- README -- #

    def test_readme_project_and_stack(self):
        r = self._read("README.md")
        self.assertIn("demo-card-service", r)
        self.assertIn("java", r)

    def test_readme_lists_domains_from_requirements(self):
        r = self._read("README.md")
        self.assertIn("billing", r)
        self.assertIn("account", r)

    def test_readme_migration_mode_from_requirements(self):
        self.assertIn("functional", self._read("README.md"))

    def test_readme_setup_mentions_database(self):
        self.assertIn("PostgreSQL", self._read("README.md"))

    def test_readme_links_siblings(self):
        r = self._read("README.md")
        self.assertIn("DEPENDENCIES.md", r)
        self.assertIn("ENVIRONMENTS.md", r)
        self.assertIn("ARCHITECTURE.md", r)

    # -- ARCHITECTURE -- #

    def test_architecture_from_blueprint(self):
        a = self._read("ARCHITECTURE.md")
        self.assertIn("blueprint.json", a)
        self.assertIn("hexagonal", a)  # style from blueprint

    def test_architecture_lists_packages(self):
        a = self._read("ARCHITECTURE.md")
        self.assertIn("com.demo.billing", a)
        self.assertIn("com.demo.account", a)

    def test_architecture_lists_components(self):
        a = self._read("ARCHITECTURE.md")
        self.assertIn("REQ_BILL_PROC", a)
        self.assertIn("REQ_ACCT_VIEW", a)

    def test_architecture_api_surface(self):
        a = self._read("ARCHITECTURE.md")
        self.assertIn("POST /billing/process", a)
        self.assertIn("GET /accounts/{id}", a)

    def test_architecture_boundaries(self):
        # REQ_BILL_PROC declares a dependency on REQ_ACCT_LOOKUP.
        a = self._read("ARCHITECTURE.md")
        self.assertIn("Boundaries", a)
        self.assertIn("REQ_BILL_PROC", a)
        self.assertIn("REQ_ACCT_LOOKUP", a)

    def test_architecture_build_order(self):
        a = self._read("ARCHITECTURE.md")
        self.assertIn("Build order", a)

    # -- DEPENDENCIES -- #

    def test_dependencies_database(self):
        self.assertIn("PostgreSQL", self._read("DEPENDENCIES.md"))

    def test_dependencies_data_access_assets(self):
        d = self._read("DEPENDENCIES.md")
        self.assertIn("ACCTFILE", d)
        self.assertIn("TRANFILE", d)

    def test_dependencies_maps_asset_to_requirements(self):
        # ACCTFILE is touched by both requirements; assert at least one mapped.
        d = self._read("DEPENDENCIES.md")
        self.assertIn("REQ_BILL_PROC", d)
        self.assertIn("REQ_ACCT_LOOKUP", d)

    def test_dependencies_service_deps(self):
        # REQ_BILL_PROC depends on REQ_ACCT_LOOKUP at the requirement level.
        d = self._read("DEPENDENCIES.md")
        self.assertIn("Service dependencies", d)
        self.assertIn("REQ_ACCT_LOOKUP", d)

    def test_dependencies_source_provenance(self):
        d = self._read("DEPENDENCIES.md")
        self.assertIn("legacy-carddemo", d)
        self.assertIn("legacy-ccps", d)

    def test_dependencies_not_a_callgraph(self):
        # Infra-level: must not leak code-level class names from the blueprint.
        d = self._read("DEPENDENCIES.md")
        self.assertNotIn("BillingService", d)
        self.assertNotIn("AccountRepository", d)

    # -- ENVIRONMENTS -- #

    def test_environments_deployment_target(self):
        self.assertIn("Kubernetes (GKE)", self._read("ENVIRONMENTS.md"))

    def test_environments_ladder(self):
        e = self._read("ENVIRONMENTS.md")
        for env in ("local", "staging", "production"):
            self.assertIn(env, e)

    def test_environments_config_keys(self):
        e = self._read("ENVIRONMENTS.md")
        self.assertIn("Database connection", e)
        # embeddings True -> embedding endpoint key present.
        self.assertIn("Embedding service endpoint", e)

    # -- registration -- #

    def test_artifacts_registered_in_manifest(self):
        with open(self.paths["manifest"]) as f:
            m = json.load(f)
        for aid in ("doc-readme", "doc-architecture", "doc-dependencies", "doc-environments"):
            self.assertIn(aid, m["artifacts"], "{0} should be registered".format(aid))
            self.assertEqual(m["artifacts"][aid]["status"], "final")
            self.assertEqual(m["artifacts"][aid]["format"], "markdown")
            self.assertIn("checksum", m["artifacts"][aid])

    def test_registered_path_resolves_under_anti_legacy_anchor(self):
        """The stored path must resolve to the real file via manifest's anchor rule."""
        import manifest as mf
        with open(self.paths["manifest"]) as f:
            m = json.load(f)
        anti_legacy_dir = os.path.dirname(os.path.abspath(self.paths["manifest"]))
        for aid in ("doc-readme", "doc-architecture", "doc-dependencies", "doc-environments"):
            stored = m["artifacts"][aid]["path"]
            # Replicate manifest._artifact_full_path anchoring, relative to the
            # manifest's own directory (the test workspace is not the cwd).
            if stored.startswith(".anti-legacy"):
                resolved = os.path.join(os.path.dirname(anti_legacy_dir), stored)
            else:
                resolved = os.path.join(anti_legacy_dir, stored)
            self.assertTrue(os.path.exists(resolved),
                            "stored path {0} should resolve to a real file".format(stored))
            # Checksum recorded must match the file the path resolves to.
            self.assertEqual(mf.file_checksum(resolved), m["artifacts"][aid]["checksum"])

    def test_audit_appended(self):
        with open(os.path.join(self.root, ".anti-legacy", "audit.jsonl")) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        registered = [e for e in lines if e.get("event") == "anti-legacy:artifact-registered"]
        self.assertEqual(len(registered), 4)


class TestNoRegister(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.target = os.path.join(self.root, "out")
        self.paths = _write_fixture(self.root, self.target)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_register_leaves_manifest_untouched(self):
        document.synthesize(
            config_path=self.paths["config"],
            blueprint_path=self.paths["blueprint"],
            requirements_path=self.paths["requirements"],
            target_graph_path=self.paths["target_graph"],
            manifest_path=self.paths["manifest"],
            register=False,
        )
        with open(self.paths["manifest"]) as f:
            m = json.load(f)
        self.assertEqual(m["artifacts"], {})
        self.assertTrue(os.path.exists(os.path.join(self.target, "README.md")))


class TestFallbacksAndDegradation(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_missing_blueprint_falls_back_to_target_graph(self):
        target = os.path.join(self.root, "tgt")
        paths = _write_fixture(self.root, target)
        # Empty the blueprint domains entirely.
        with open(paths["blueprint"], "w") as f:
            json.dump({"project": "demo", "target_stack": "java"}, f)
        document.synthesize(
            config_path=paths["config"],
            blueprint_path=paths["blueprint"],
            requirements_path=paths["requirements"],
            target_graph_path=paths["target_graph"],
            manifest_path=paths["manifest"],
        )
        with open(os.path.join(target, "ARCHITECTURE.md")) as f:
            a = f.read()
        self.assertIn("target_graph.json", a)  # fallback source noted
        self.assertIn("com.demo.billing", a)

    def test_empty_requirements_degrades_dependencies(self):
        target = os.path.join(self.root, "tgt")
        paths = _write_fixture(self.root, target)
        with open(paths["requirements"], "w") as f:
            json.dump({"domains": {}}, f)
        document.synthesize(
            config_path=paths["config"],
            blueprint_path=paths["blueprint"],
            requirements_path=paths["requirements"],
            target_graph_path=paths["target_graph"],
            manifest_path=paths["manifest"],
        )
        with open(os.path.join(target, "DEPENDENCIES.md")) as f:
            d = f.read()
        # Degrades gracefully — no crash, asset section says none.
        self.assertIn("No data-access assets", d)
        # Database section still present from config.
        self.assertIn("PostgreSQL", d)

    def test_no_target_path_anywhere_raises(self):
        target = os.path.join(self.root, "tgt")
        paths = _write_fixture(self.root, target)
        # Strip target_path from config and target graph; blueprint already lacks
        # one once we overwrite it.
        with open(paths["config"], "w") as f:
            json.dump({"project_name": "x", "target_stack": "java"}, f)
        with open(paths["blueprint"], "w") as f:
            json.dump({"project": "x"}, f)
        with open(paths["target_graph"], "w") as f:
            json.dump({"domains": {}}, f)
        with self.assertRaises(ValueError):
            document.synthesize(
                config_path=paths["config"],
                blueprint_path=paths["blueprint"],
                requirements_path=paths["requirements"],
                target_graph_path=paths["target_graph"],
                manifest_path=paths["manifest"],
            )

    def test_target_dir_override_wins(self):
        target = os.path.join(self.root, "tgt")
        override = os.path.join(self.root, "elsewhere")
        paths = _write_fixture(self.root, target)
        out_dir, _ = document.synthesize(
            config_path=paths["config"],
            blueprint_path=paths["blueprint"],
            requirements_path=paths["requirements"],
            target_graph_path=paths["target_graph"],
            target_dir_override=override,
            manifest_path=paths["manifest"],
        )
        self.assertEqual(os.path.abspath(out_dir), os.path.abspath(override))
        self.assertTrue(os.path.exists(os.path.join(override, "README.md")))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.target = os.path.join(self.root, "target", "app")
        self.paths = _write_fixture(self.root, self.target)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cli_writes_and_registers(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "document.py"),
             "--config", self.paths["config"],
             "--blueprint", self.paths["blueprint"],
             "--requirements", self.paths["requirements"],
             "--target-graph", self.paths["target_graph"],
             "--manifest", self.paths["manifest"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(result.returncode, 0, "stderr: {0}".format(result.stderr))
        for name in ("README.md", "ARCHITECTURE.md", "DEPENDENCIES.md", "ENVIRONMENTS.md"):
            self.assertTrue(os.path.exists(os.path.join(self.target, name)))
        with open(self.paths["manifest"]) as f:
            m = json.load(f)
        self.assertEqual(len(m["artifacts"]), 4)

    def test_cli_no_register_flag(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "document.py"),
             "--config", self.paths["config"],
             "--blueprint", self.paths["blueprint"],
             "--requirements", self.paths["requirements"],
             "--target-graph", self.paths["target_graph"],
             "--manifest", self.paths["manifest"],
             "--no-register"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(result.returncode, 0, "stderr: {0}".format(result.stderr))
        with open(self.paths["manifest"]) as f:
            m = json.load(f)
        self.assertEqual(m["artifacts"], {})

    def test_cli_missing_target_path_exits_nonzero(self):
        # Config without target_path, and override target graph to empty.
        with open(self.paths["config"], "w") as f:
            json.dump({"project_name": "x", "target_stack": "java"}, f)
        with open(self.paths["blueprint"], "w") as f:
            json.dump({"project": "x"}, f)
        with open(self.paths["target_graph"], "w") as f:
            json.dump({"domains": {}}, f)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "document.py"),
             "--config", self.paths["config"],
             "--blueprint", self.paths["blueprint"],
             "--requirements", self.paths["requirements"],
             "--target-graph", self.paths["target_graph"],
             "--manifest", self.paths["manifest"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No target directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
