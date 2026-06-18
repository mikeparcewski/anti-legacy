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

from completeness_scanner import (
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
            [sys.executable, "-m", "completeness_scanner", "--workspace", self.dirty,
             "--output", out, "--quiet"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertTrue(os.path.exists(out))


class TestFix3CommentWordSeverity(unittest.TestCase):
    """Fix 3: stub/mock/placeholder in a comment line must be MEDIUM, never HIGH."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fix3-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_dummy_in_comment_is_medium_not_high_production_file(self):
        self._write("src/main/java/Repo.java",
                    "public class Repo {\n"
                    "    // initialized with dummy seed data for local dev\n"
                    "    private List<Item> items = loadSeedData();\n"
                    "    public List<Item> findAll() {\n"
                    "        return items.stream().filter(Item::isActive).collect(Collectors.toList());\n"
                    "    }\n"
                    "}\n")
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        comment_findings = [f for f in findings if "dummy" in f["what"].lower()]
        self.assertTrue(comment_findings, "expected a 'dummy' finding")
        for f in comment_findings:
            self.assertEqual(f["severity"], "MEDIUM",
                             f"'dummy' in comment should be MEDIUM, got {f['severity']}: {f['what']}")

    def test_mock_in_comment_is_medium_for_production_file(self):
        self._write("src/service/OrderService.java",
                    "public class OrderService {\n"
                    "    // mock data removed after integration — real adapter wired\n"
                    "    private final OrderRepo repo;\n"
                    "    public Order findById(long id) { return repo.findById(id); }\n"
                    "}\n")
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        comment_findings = [f for f in findings if "mock" in f["what"].lower()]
        for f in comment_findings:
            self.assertNotEqual(f["severity"], "HIGH",
                                f"'mock' in comment must not be HIGH: {f['what']}")

    def test_production_file_with_only_dummy_comment_passes_gate(self):
        """A fully-implemented file with one 'dummy' comment must not be GATE_5 FAIL."""
        self._write("src/main/java/HealthCheck.java",
                    "public class HealthCheck {\n"
                    "    // dummy endpoint response shape — matches spec v2\n"
                    "    public HealthStatus check() {\n"
                    "        return new HealthStatus(db.ping(), cache.ping());\n"
                    "    }\n"
                    "}\n")
        report = build_report(self.tmp)
        high_findings = [f for f in report["findings"] if f["severity"] == "HIGH"]
        self.assertEqual(high_findings, [],
                         f"Production file with 'dummy' comment caused HIGH finding: {high_findings}")

    def test_todo_marker_in_code_still_high(self):
        """Regression: TODO/FIXME markers in code body must still be HIGH."""
        self._write("src/Processor.java",
                    "public class Processor {\n"
                    "    // TODO: finish implementing\n"
                    "    public void process() {}\n"
                    "}\n")
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        todo_findings = [f for f in findings if "todo" in f["what"].lower()]
        self.assertTrue(todo_findings, "TODO in code must still fire")
        self.assertTrue(any(f["severity"] == "HIGH" for f in todo_findings),
                        "TODO marker must still be HIGH")


class TestFix4ConfigDevProfile(unittest.TestCase):
    """Fix 4: dev-profile configs and in-memory DB configs must not cause HIGH findings."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fix4-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_application_local_yml_empty_password_not_high(self):
        self._write("src/main/resources/application-local.yml",
                    "spring:\n"
                    "  datasource:\n"
                    "    password: \n"
                    "    url: jdbc:h2:mem:testdb\n")
        findings, _ = scan_tree(self.tmp, dimensions=["CONFIG"])
        password_findings = [f for f in findings if "password" in f["what"].lower()]
        for f in password_findings:
            self.assertNotEqual(f["severity"], "HIGH",
                                f"empty password in -local. config must not be HIGH: {f['what']}")

    def test_application_dev_yml_empty_password_not_high(self):
        self._write("src/main/resources/application-dev.yml",
                    "spring:\n"
                    "  datasource:\n"
                    "    password: \n")
        findings, _ = scan_tree(self.tmp, dimensions=["CONFIG"])
        password_findings = [f for f in findings if "password" in f["what"].lower()]
        for f in password_findings:
            self.assertNotEqual(f["severity"], "HIGH",
                                f"empty password in -dev. config must not be HIGH: {f['what']}")

    def test_h2_mem_config_empty_password_downgraded(self):
        """application.properties with h2:mem URL + empty password must not be HIGH."""
        self._write("src/main/resources/application.properties",
                    "spring.datasource.url=jdbc:h2:mem:testdb\n"
                    "spring.datasource.username=sa\n"
                    "spring.datasource.password=\n")
        findings, _ = scan_tree(self.tmp, dimensions=["CONFIG"])
        password_findings = [f for f in findings
                             if "password" in f["what"].lower() and f["severity"] == "HIGH"]
        self.assertEqual(password_findings, [],
                         "H2 in-memory config with empty password must not fire HIGH")

    def test_production_config_empty_password_still_high(self):
        """Real prod .env with no h2:mem and empty PASSWORD must remain HIGH."""
        self._write(".env",
                    "DATABASE_URL=jdbc:postgresql://prod-db:5432/myapp\n"
                    "DATABASE_PASSWORD=\n")
        findings, _ = scan_tree(self.tmp, dimensions=["CONFIG"])
        self.assertTrue(
            any("DATABASE_PASSWORD" in f["what"] and f["severity"] == "HIGH"
                for f in findings),
            "empty DATABASE_PASSWORD in production .env must still be HIGH",
        )


class TestH4AnnotationStacking(unittest.TestCase):
    """H4: >=8 @ImplementsRule annotations on one class → HIGH (non-test) or MEDIUM (test)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h4-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _make_stacked_class(self, n_annotations, classname="BillingService"):
        annos = "\n".join(f'@ImplementsRule("RULE-{i:03d}")' for i in range(n_annotations))
        return (
            f"package com.acme;\n"
            f"{annos}\n"
            f"public class {classname} {{\n"
            f"    public void run() {{}}\n"
            f"}}\n"
        )

    def test_eight_annotations_fires_high_on_production_file(self):
        self._write("src/main/java/BillingService.java",
                    self._make_stacked_class(8))
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        stacking = [f for f in findings if "annotation stacking" in f["what"].lower()]
        self.assertTrue(stacking, "expected annotation stacking finding with 8 annotations")
        self.assertTrue(any(f["severity"] == "HIGH" for f in stacking),
                        "non-test file with 8 annotations must be HIGH")

    def test_seven_annotations_does_not_fire(self):
        self._write("src/main/java/BillingService.java",
                    self._make_stacked_class(7))
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        stacking = [f for f in findings if "annotation stacking" in f["what"].lower()]
        self.assertEqual(stacking, [], "7 annotations is under the limit — must not fire")

    def test_annotation_stacking_in_test_file_is_medium_not_high(self):
        self._write("src/test/java/BillingServiceTest.java",
                    self._make_stacked_class(10, "BillingServiceTest"))
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        stacking = [f for f in findings if "annotation stacking" in f["what"].lower()]
        self.assertTrue(stacking, "expected annotation stacking finding in test file")
        for f in stacking:
            self.assertNotEqual(f["severity"], "HIGH",
                                "annotation stacking in test file must not be HIGH")

    def test_two_classes_independent_counts(self):
        """Stacking on one class must not pollute the count of a neighbouring class."""
        content = (
            "package com.acme;\n"
            + "\n".join(f'@ImplementsRule("RULE-{i:03d}")' for i in range(10))
            + "\npublic class HeavyService {\n    public void run() {}\n}\n"
            + "public class LightService {\n    @ImplementsRule(\"RULE-900\")\n    public void run() {}\n}\n"
        )
        self._write("src/main/java/Mixed.java", content)
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        stacking = [f for f in findings if "annotation stacking" in f["what"].lower()]
        # HeavyService fires, LightService must not appear in stacking findings.
        names = " ".join(f["what"] for f in stacking)
        self.assertIn("HeavyService", names)
        self.assertNotIn("LightService", names)


class TestH5ReflectionOnlyTests(unittest.TestCase):
    """H5: Java test files with >=3 Class.forName + >80% assertNotNull + no domain calls → HIGH."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h5-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _reflection_only_test(self, n_classes=4):
        """Returns Java test source with n_classes Class.forName calls and only assertNotNull."""
        lines = ["package com.acme;", "import org.junit.jupiter.api.Test;",
                 "public class ExistenceTest {"]
        for i in range(n_classes):
            lines += [
                f"    @Test void class{i}Exists() throws Exception {{",
                f'        Object c = Class.forName("com.acme.domain.Class{i}");',
                f"        assertNotNull(c);",
                "    }",
            ]
        lines.append("}")
        return "\n".join(lines) + "\n"

    def test_reflection_only_test_fires_high(self):
        self._write("src/test/java/ExistenceTest.java",
                    self._reflection_only_test(n_classes=4))
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        reflection = [f for f in findings if "reflection-only" in f["what"].lower()]
        self.assertTrue(reflection, "expected reflection-only-test HIGH finding")
        self.assertTrue(any(f["severity"] == "HIGH" for f in reflection))

    def test_test_with_domain_calls_does_not_fire(self):
        """A test that calls a real domain method must not trigger the reflection detector."""
        content = (
            "package com.acme;\n"
            "import org.junit.jupiter.api.Test;\n"
            "public class BillingTest {\n"
            "    @Test void chargesCorrectly() throws Exception {\n"
            "        Class<?> c = Class.forName(\"com.acme.BillingService\");\n"
            "        assertNotNull(c);\n"
            "        BillingService svc = new BillingService();\n"
            "        BigDecimal result = svc.calculateCharge(account);\n"
            "        assertEquals(expected, result);\n"
            "    }\n"
            "}\n"
        )
        self._write("src/test/java/BillingTest.java", content)
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        reflection = [f for f in findings if "reflection-only" in f["what"].lower()]
        self.assertEqual(reflection, [],
                         "test file with real domain calls must not fire reflection-only detector")

    def test_fewer_than_threshold_class_for_name_does_not_fire(self):
        """Two Class.forName calls is under the threshold (3) — must not fire."""
        content = (
            "package com.acme;\n"
            "public class TwoReflectTest {\n"
            "    @Test void a() throws Exception {\n"
            "        Object c1 = Class.forName(\"com.acme.Foo\"); assertNotNull(c1);\n"
            "    }\n"
            "    @Test void b() throws Exception {\n"
            "        Object c2 = Class.forName(\"com.acme.Bar\"); assertNotNull(c2);\n"
            "    }\n"
            "}\n"
        )
        self._write("src/test/java/TwoReflectTest.java", content)
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        reflection = [f for f in findings if "reflection-only" in f["what"].lower()]
        self.assertEqual(reflection, [], "< threshold Class.forName calls must not fire")

    def test_production_java_file_not_flagged(self):
        """H5 must never fire on a non-test Java file, even with Class.forName usage."""
        content = (
            "package com.acme;\n"
            "public class PluginLoader {\n"
            "    public Object load(String cls) throws Exception {\n"
            "        Object o = Class.forName(cls); assertNotNull(o);\n"
            "        return Class.forName(cls).getDeclaredConstructor().newInstance();\n"
            "    }\n"
            "    public void run() {\n"
            "        Object p = Class.forName(\"com.Plugin\"); assertNotNull(p);\n"
            "        Object q = Class.forName(\"com.Plugin2\"); assertNotNull(q);\n"
            "    }\n"
            "}\n"
        )
        self._write("src/main/java/PluginLoader.java", content)
        findings, _ = scan_tree(self.tmp, dimensions=["CODE"])
        reflection = [f for f in findings if "reflection-only" in f["what"].lower()]
        self.assertEqual(reflection, [],
                         "H5 must not fire on production (non-test) files")


if __name__ == "__main__":
    unittest.main()
