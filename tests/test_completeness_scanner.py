#!/usr/bin/env python3
"""
Hermetic tests for scripts/completeness_scanner.py.

Builds two synthetic TARGET trees in a tempdir:
  * a DIRTY tree with planted stubs/markers across all four dimensions, and
  * a CLEAN tree with none.

Asserts FAIL-on-HIGH for the dirty tree, PASS for the clean one, the report
shape, per-dimension targeting, and the CLI's exit-code contract. No network,
no real build tooling, no repo state mutated.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.completeness_scanner import (
    build_report,
    scan_tree,
    write_report,
    main,
)

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "completeness_scanner.py",
)


def _write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestCompletenessScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="completeness-test-")
        self.dirty = os.path.join(self.tmp, "dirty")
        self.clean = os.path.join(self.tmp, "clean")
        os.makedirs(self.dirty)
        os.makedirs(self.clean)
        self._plant_dirty()
        self._plant_clean()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ trees
    def _plant_dirty(self):
        r = self.dirty

        # CODE: TODO marker + UnsupportedOperationException + trivial body.
        _write(r, "src/main/java/com/acme/PaymentService.java",
               "package com.acme;\n"
               "public class PaymentService {\n"
               "    // TODO: implement interest accrual\n"
               "    public BigDecimal computeInterest(Account a) {\n"
               "        throw new UnsupportedOperationException(\"not done\");\n"
               "    }\n"
               "    public List<Txn> recent() { return Collections.emptyList(); }\n"
               "}\n")

        # CODE: Python stub with NotImplementedError + trivial pass body.
        _write(r, "src/svc/ledger.py",
               "def post_entry(account, amount):\n"
               "    raise NotImplementedError\n"
               "\n"
               "def reconcile(batch):\n"
               "    pass\n")

        # CODE in a NON-test Go file: panic TODO.
        _write(r, "internal/billing/cycle.go",
               "package billing\n"
               "func RunCycle() error {\n"
               "    panic(\"TODO: wire up cycle\")\n"
               "}\n")

        # DOCS: README with no setup/run guidance + a TODO section.
        _write(r, "README.md",
               "# Credit Card Service\n\n"
               "A modernized credit card platform.\n\n"
               "## Architecture\n\n"
               "TODO: write this up.\n\n"
               "## Roadmap\n")

        # CONFIG: real .env with a placeholder secret and empty sensitive key.
        _write(r, ".env",
               "DB_PASSWORD=changeme\n"
               "API_KEY=\n"
               "SERVICE_URL=http://localhost:8080\n")

        # CONFIG: application.properties with hardcoded test datasource.
        _write(r, "src/main/resources/application.properties",
               "spring.datasource.url=jdbc:h2:mem:testdb\n"
               "app.name=credit-card-service\n")

        # BUILD: pom.xml configured to skip tests.
        _write(r, "pom.xml",
               "<project>\n"
               "  <properties>\n"
               "    <skipTests>true</skipTests>\n"
               "  </properties>\n"
               "</project>\n")

        # BUILD: a disabled JUnit test living in a CODE (test) file.
        _write(r, "src/test/java/com/acme/PaymentServiceTest.java",
               "package com.acme;\n"
               "import org.junit.jupiter.api.Disabled;\n"
               "import org.junit.jupiter.api.Test;\n"
               "public class PaymentServiceTest {\n"
               "    @Disabled(\"flaky\")\n"
               "    @Test void interestAccrues() { }\n"
               "}\n")

    def _plant_clean(self):
        r = self.clean

        # CODE: a real implementation, no markers, non-trivial body.
        _write(r, "src/main/java/com/acme/PaymentService.java",
               "package com.acme;\n"
               "public class PaymentService {\n"
               "    public BigDecimal computeInterest(Account a) {\n"
               "        BigDecimal rate = a.getApr().divide(BIG_365, MC);\n"
               "        return a.getBalance().multiply(rate);\n"
               "    }\n"
               "}\n")

        # CODE: a clean Python module.
        _write(r, "src/svc/ledger.py",
               "def post_entry(account, amount):\n"
               "    account.balance += amount\n"
               "    return account.balance\n")

        # DOCS: README WITH setup + run instructions.
        _write(r, "README.md",
               "# Credit Card Service\n\n"
               "## Setup\n\n"
               "Install JDK 17 and Maven.\n\n"
               "## Running\n\n"
               "Run `mvn spring-boot:run` to start the service.\n")

        # CONFIG: a real env example (placeholders allowed → downgraded LOW).
        _write(r, ".env.example",
               "DB_PASSWORD=your-password-here\n")

        # CONFIG: production-shaped properties, no test values.
        _write(r, "src/main/resources/application.properties",
               "spring.datasource.url=${DATABASE_URL}\n"
               "app.name=credit-card-service\n")

        # BUILD: a healthy pom that runs tests.
        _write(r, "pom.xml",
               "<project>\n"
               "  <build><plugins></plugins></build>\n"
               "</project>\n")

        # BUILD: an enabled test.
        _write(r, "src/test/java/com/acme/PaymentServiceTest.java",
               "package com.acme;\n"
               "import org.junit.jupiter.api.Test;\n"
               "public class PaymentServiceTest {\n"
               "    @Test void interestAccrues() {\n"
               "        assertEquals(expected, svc.computeInterest(a));\n"
               "    }\n"
               "}\n")

    # ------------------------------------------------------------- assertions
    def test_dirty_tree_fails_with_high_findings(self):
        report = build_report(self.dirty)
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["counts"]["HIGH"], 0)
        # Every dimension produced at least one finding.
        for dim in ("CODE", "DOCS", "CONFIG", "BUILD"):
            self.assertGreater(
                report["dimension_counts"][dim], 0,
                "expected at least one %s finding in dirty tree" % dim,
            )

    def test_clean_tree_passes(self):
        report = build_report(self.clean)
        self.assertEqual(
            report["status"], "PASS",
            "clean tree produced HIGH findings: %s"
            % [f for f in report["findings"] if f["severity"] == "HIGH"],
        )
        self.assertEqual(report["counts"]["HIGH"], 0)

    def test_report_shape(self):
        report = build_report(self.dirty)
        for key in ("status", "scanned_root", "generated_at",
                    "counts", "dimension_counts", "findings"):
            self.assertIn(key, report)
        self.assertTrue(os.path.isabs(report["scanned_root"]))
        for f in report["findings"]:
            self.assertIn(f["dimension"], ("CODE", "DOCS", "CONFIG", "BUILD"))
            self.assertIn(f["severity"], ("HIGH", "MEDIUM", "LOW"))
            self.assertIn("path", f)
            self.assertIn("what", f)
            # path is RELATIVE to the scanned root.
            self.assertFalse(os.path.isabs(f["path"]))

    def test_code_dimension_detects_specific_markers(self):
        findings, _ = scan_tree(self.dirty, dimensions=["CODE"])
        whats = " ".join(f["what"].lower() for f in findings)
        self.assertIn("todo", whats)
        self.assertIn("unimplemented", whats)
        self.assertIn("trivial", whats)  # the emptyList() body + python pass
        self.assertIn("notimplementederror", whats.replace(" ", ""))

    def test_config_dimension_flags_placeholder_and_empty_secret(self):
        findings, _ = scan_tree(self.dirty, dimensions=["CONFIG"])
        # placeholder DB_PASSWORD=changeme → HIGH (sensitive key + placeholder).
        self.assertTrue(any(
            f["severity"] == "HIGH" and "DB_PASSWORD" in f["what"]
            for f in findings
        ), "expected HIGH placeholder finding for DB_PASSWORD")
        # empty API_KEY → HIGH (sensitive key, empty value).
        self.assertTrue(any(
            "API_KEY" in f["what"] for f in findings
        ), "expected a finding for empty API_KEY")

    def test_build_dimension_flags_skiptests_and_disabled(self):
        findings, _ = scan_tree(self.dirty, dimensions=["BUILD"])
        whats = " ".join(f["what"].lower() for f in findings)
        self.assertIn("skip tests", whats)        # pom <skipTests>
        self.assertIn("disabled test", whats)     # @Disabled in test file

    def test_docs_dimension_flags_missing_setup_and_todo(self):
        findings, _ = scan_tree(self.dirty, dimensions=["DOCS"])
        # README with no setup/run → HIGH.
        self.assertTrue(any(
            f["severity"] == "HIGH" and "setup" in f["what"].lower()
            for f in findings
        ))
        # TODO in docs → MEDIUM placeholder.
        self.assertTrue(any("todo" in f["what"].lower() for f in findings))
        # Empty 'Roadmap' section.
        self.assertTrue(any("empty doc section" in f["what"].lower() for f in findings))

    def test_dimension_targeting_isolates(self):
        # Asking for CONFIG only must not return CODE/DOCS/BUILD findings.
        findings, _ = scan_tree(self.dirty, dimensions=["CONFIG"])
        self.assertTrue(findings)
        self.assertTrue(all(f["dimension"] == "CONFIG" for f in findings))

    def test_skip_dirs_not_descended(self):
        # Plant a stub inside a build-output dir that must be skipped.
        _write(self.clean, "target/classes/Generated.java",
               "class G { void x() { throw new UnsupportedOperationException(); } }\n")
        _write(self.clean, "node_modules/dep/index.js",
               "// TODO leftover from a dependency\n")
        report = build_report(self.clean)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(any(
            "target" in f["path"] or "node_modules" in f["path"]
            for f in report["findings"]
        ))

    def test_write_report_roundtrip(self):
        out = os.path.join(self.tmp, "evidence", "completeness-report.json")
        report = build_report(self.dirty)
        write_report(report, out)
        self.assertTrue(os.path.exists(out))
        with open(out, encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["status"], "FAIL")
        self.assertEqual(loaded["counts"], report["counts"])

    # -------------------------------------------------------------- CLI / main
    def test_main_returns_nonzero_on_fail(self):
        out = os.path.join(self.tmp, "ev-dirty.json")
        rc = main(["--workspace", self.dirty, "--output", out, "--quiet"])
        self.assertEqual(rc, 1)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["status"], "FAIL")

    def test_main_returns_zero_on_pass(self):
        out = os.path.join(self.tmp, "ev-clean.json")
        rc = main(["--workspace", self.clean, "--output", out, "--quiet"])
        self.assertEqual(rc, 0)

    def test_main_resolves_target_path_from_config(self):
        cfg = os.path.join(self.tmp, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"target_path": self.clean}, f)
        out = os.path.join(self.tmp, "ev-cfg.json")
        rc = main(["--config", cfg, "--output", out, "--quiet"])
        self.assertEqual(rc, 0)

    def test_main_errors_on_missing_target(self):
        rc = main(["--workspace", os.path.join(self.tmp, "nope"),
                   "--output", os.path.join(self.tmp, "x.json"), "--quiet"])
        self.assertEqual(rc, 2)

    def test_cli_subprocess_exit_code(self):
        out = os.path.join(self.tmp, "ev-sub.json")
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--workspace", self.dirty,
             "--output", out, "--quiet"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertTrue(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
