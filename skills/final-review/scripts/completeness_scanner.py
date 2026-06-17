#!/usr/bin/env python3
"""
Completeness Scanner — the FINAL completeness-review pass (B1b).

Scans a built TARGET application tree for mocked / half-done / incomplete work
across four dimensions, and emits a single evidence envelope:

    .anti-legacy/evidence/completeness-report.json
    {
      "status": "PASS" | "FAIL",
      "scanned_root": "<abs path>",
      "generated_at": "<iso8601>",
      "counts": {"HIGH": n, "MEDIUM": n, "LOW": n},
      "dimension_counts": {"CODE": n, "DOCS": n, "CONFIG": n, "BUILD": n},
      "findings": [
        {"dimension": "CODE|DOCS|CONFIG|BUILD",
         "path": "<rel path>", "line": <int|null>,
         "severity": "HIGH|MEDIUM|LOW", "what": "<human-readable>"}
      ]
    }

status is FAIL if ANY finding has severity HIGH. The four dimensions:

  CODE   — TODO/FIXME/XXX/HACK/stub/mock markers; trivially-short method bodies
           that just `return null/0/""/empty`; `throw new
           UnsupportedOperationException` / `NotImplementedError` / `panic("TODO")`.
  DOCS   — empty or TODO sections in README/docs; missing setup/run steps.
  CONFIG — hardcoded test values, placeholder / empty env vars in config files.
  BUILD  — skipped / disabled tests (@Disabled, @Ignore, it.skip, t.Skip,
           @pytest.mark.skip), commented-out build/test steps.

This script is intentionally language-agnostic and dependency-free: it reads
text files and applies portable regex heuristics. It is the deterministic core
that the `anti-legacy:final-review` reviewer swarm runs and then reasons over.

Cross-platform: pure Python (no shell-isms), os.path everywhere, UTF-8 reads
with errors ignored so binary/odd-encoding files never crash the scan.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# What we never descend into (build output, vendored deps, VCS metadata).
# ---------------------------------------------------------------------------
SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "vendor", "venv", ".venv", "env",
    "target", "build", "dist", "out", "bin", "obj",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".gradle", ".idea",
    "site-packages",
}

# Extensions we treat as source code for the CODE dimension.
CODE_EXTS = {
    ".java", ".kt", ".go", ".cs", ".py", ".ts", ".tsx", ".js", ".jsx",
    ".rs", ".rb", ".scala", ".groovy", ".php", ".swift", ".c", ".h",
    ".cpp", ".hpp", ".cc",
}

# Doc files for the DOCS dimension.
DOC_EXTS = {".md", ".rst", ".adoc", ".txt"}

# Config files for the CONFIG dimension.
CONFIG_EXTS = {
    ".env", ".properties", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".json", ".conf", ".xml",
}
# Config files matched by full name (no useful extension).
CONFIG_NAMES = {
    ".env", ".env.local", ".env.example", ".env.sample",
    "application.properties", "application.yaml", "application.yml",
}

# Build / test definition files for the BUILD dimension.
BUILD_NAMES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "go.mod", "makefile", "Makefile", "package.json", "pyproject.toml",
    "tox.ini", "pytest.ini", "setup.cfg", "Dockerfile",
}
BUILD_CI_DIR_HINTS = (".github", ".gitlab-ci", ".circleci")

# How many bytes to read per file (guards against pathological huge files).
MAX_BYTES = 2_000_000

# ---------------------------------------------------------------------------
# CODE dimension patterns.
# ---------------------------------------------------------------------------
# Marker comments. We require word boundaries / comment-ish context to avoid
# matching substrings inside identifiers (e.g. "FIXMEUP" or "mockingbird").
_MARKER_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_])"            # boundary before the marker
    r"(TODO|FIXME|XXX|HACK)\b",
    re.IGNORECASE,
)
# Explicit "not implemented" signals across languages.
_NOT_IMPL_RE = re.compile(
    r"throw\s+new\s+UnsupportedOperationException"      # Java/Kotlin
    r"|throw\s+new\s+NotImplementedException"           # C#/JS
    r"|raise\s+NotImplementedError"                     # Python
    r"|NotImplementedError"                             # Python (bare ref)
    r"|panic\(\s*[\"'](?:TODO|not\s+implemented|unimplemented)" # Go
    r"|TODO\(\)"                                         # Rust/Kotlin todo()
    r"|unimplemented!\("                                # Rust
    r"|todo!\(",                                        # Rust
    re.IGNORECASE,
)
# "stub" / "mock" / "placeholder" called out in a comment or string.
_STUB_WORD_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_])(stub|mock(?:ed)?|placeholder|dummy|fake|stubbed)\b",
    re.IGNORECASE,
)
# Comment-line detector (line begins, after whitespace, with a comment opener).
_COMMENT_LINE_RE = re.compile(r"^\s*(//|#|\*|/\*|<!--|--)")

# Single-statement trivial bodies: a method/function whose entire body is just a
# return of a no-op value. We match an opening brace immediately followed by a
# bare `return null/0/0.0/""/''/false/[]/{}/Collections.emptyList()...` then a
# closing brace, allowing whitespace/newlines between.
_TRIVIAL_BODY_RE = re.compile(
    r"\{\s*return\s+"
    r"(?:null|nil|None|0|0\.0|0L|false|true"
    r"|\"\"|''|\[\]|\{\}"
    r"|Collections\.empty\w+\(\)"
    r"|new\s+ArrayList<>\(\)|new\s+HashMap<>\(\)"
    r"|Optional\.empty\(\)"
    r"|Mono\.empty\(\)|Flux\.empty\(\))"
    r"\s*;?\s*\}",
)
# Python trivial body: a def whose suite is a single `return <noop>` or `pass`,
# or a one-line `...`/`pass`. Detected line-wise (see scan_code).
_PY_DEF_RE = re.compile(r"^(\s*)def\s+\w+\s*\(")
_PY_TRIVIAL_RETURN_RE = re.compile(
    r"^\s*(?:return\s+(?:None|0|0\.0|False|True|\"\"|''|\[\]|\{\}|\(\))?\s*"
    r"|pass|\.\.\.|raise\s+NotImplementedError)\s*$"
)

# ---------------------------------------------------------------------------
# DOCS dimension patterns.
# ---------------------------------------------------------------------------
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_DOC_TODO_RE = re.compile(r"\b(TODO|TBD|FIXME|WIP|coming\s+soon|to\s+be\s+(?:done|written|added))\b",
                          re.IGNORECASE)
# Headings that we expect a runnable target's README to actually fill in.
_SETUP_HEADING_HINTS = ("setup", "install", "getting started", "usage",
                        "running", "run", "build", "quick start", "quickstart",
                        "how to run")

# ---------------------------------------------------------------------------
# CONFIG dimension patterns.
# ---------------------------------------------------------------------------
# key = value style (.env / .properties / .ini).
_KV_RE = re.compile(r"^\s*([A-Za-z_][\w.\-]*)\s*[:=]\s*(.*?)\s*$")
# Placeholder values that should never ship in a built target.
_PLACEHOLDER_VAL_RE = re.compile(
    r"^(?:changeme|change_me|xxx+|todo|tbd|your[_-].+|<.*>|\$\{?placeholder\}?"
    r"|placeholder|example\.com|secret|password"
    r"|admin|test|testtest|foo|bar|baz|dummy|fixme|insert[_-].+[_-]here)$",
    re.IGNORECASE,
)
# Keys whose value being empty is a real "fill me in" smell (credentials/urls).
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret|url|uri|host|endpoint|dsn"
    r"|connection[_-]?string)",
    re.IGNORECASE,
)
# Hardcoded test-y values inside config (a key referencing test + literal value).
_HARDCODED_TEST_RE = re.compile(
    r"(localhost|127\.0\.0\.1|example\.com|test[_-]?(?:user|password|key|db|token)"
    r"|dummy|sandbox\.|h2:mem|:memory:)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# BUILD dimension patterns.
# ---------------------------------------------------------------------------
_SKIP_TEST_RE = re.compile(
    r"@Disabled\b"                          # JUnit 5
    r"|@Ignore\b"                           # JUnit 4 / TestNG
    r"|@pytest\.mark\.skip\b"               # pytest
    r"|@unittest\.skip\b"                   # unittest
    r"|\bit\.skip\s*\("                     # jest/mocha
    r"|\bdescribe\.skip\s*\("               # jest/mocha
    r"|\bxit\s*\(|\bxdescribe\s*\("         # jasmine
    r"|\bt\.Skip\s*\(|\bt\.SkipNow\s*\("    # Go
    r"|\[Ignore\b",                         # NUnit/MSTest C#
    re.IGNORECASE,
)
# A test step / goal commented out in a build file.
_COMMENTED_BUILD_STEP_RE = re.compile(
    r"^\s*(#|//|<!--)\s*.*(test|mvn|gradle|npm\s+test|go\s+test|pytest|build)\b",
    re.IGNORECASE,
)
# Maven skipTests / Gradle test.enabled = false.
_BUILD_SKIP_FLAG_RE = re.compile(
    r"<skipTests>\s*true\s*</skipTests>"
    r"|-DskipTests"
    r"|maven\.test\.skip\s*=\s*true"
    r"|test\.enabled\s*=\s*false"
    r"|tasks\.test\s*\{[^}]*enabled\s*=\s*false",
    re.IGNORECASE,
)


def _classify(path):
    """Return the dimension this file belongs to, or None to skip it."""
    name = os.path.basename(path)
    lower = name.lower()
    ext = os.path.splitext(name)[1].lower()

    if name in BUILD_NAMES or lower in {b.lower() for b in BUILD_NAMES}:
        return "BUILD"
    if name in CONFIG_NAMES:
        return "CONFIG"
    if ext in CODE_EXTS:
        return "CODE"
    if ext in DOC_EXTS:
        return "DOCS"
    if ext in CONFIG_EXTS:
        return "CONFIG"
    # dot-env variants like .env.production
    if lower.startswith(".env"):
        return "CONFIG"
    return None


def _read_lines(path):
    """Read a text file as a list of lines, robust to encoding/size."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > MAX_BYTES:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().splitlines()
    except (OSError, UnicodeError):
        return None


def _is_test_file(rel_path):
    p = rel_path.replace("\\", "/").lower()
    base = os.path.basename(p)
    return (
        "/test/" in p or "/tests/" in p or "__tests__" in p
        or base.startswith("test_") or base.endswith("_test.py")
        or base.endswith("test.java") or base.endswith("tests.java")
        or base.endswith(".test.ts") or base.endswith(".test.js")
        or base.endswith(".spec.ts") or base.endswith(".spec.js")
        or base.endswith("_test.go")
    )


def scan_code(rel_path, lines):
    """CODE dimension. Returns a list of finding dicts (no path/dimension)."""
    findings = []
    is_test = _is_test_file(rel_path)
    is_python = rel_path.lower().endswith(".py")

    for i, line in enumerate(lines, start=1):
        # Marker comments — HIGH for real source, MEDIUM inside test scaffolding.
        m = _MARKER_RE.search(line)
        if m:
            findings.append({
                "line": i,
                "severity": "MEDIUM" if is_test else "HIGH",
                "what": "%s marker in source: %s" % (m.group(1).upper(), line.strip()[:120]),
            })
        # Explicit not-implemented.
        if _NOT_IMPL_RE.search(line):
            findings.append({
                "line": i,
                "severity": "MEDIUM" if is_test else "HIGH",
                "what": "unimplemented stub: %s" % line.strip()[:120],
            })
        # stub/mock/placeholder words in a COMMENT line only (avoid flagging
        # legitimate mock-library imports/usage in test code).
        if _COMMENT_LINE_RE.match(line):
            sm = _STUB_WORD_RE.search(line)
            if sm:
                findings.append({
                    "line": i,
                    "severity": "MEDIUM" if is_test else "HIGH",
                    "what": "'%s' called out in comment: %s" % (sm.group(1).lower(), line.strip()[:120]),
                })

    # Trivial brace-bodies (Java/Kotlin/Go/C#/TS/JS): scan the joined text so a
    # body split across lines is still caught.
    if not is_python and not is_test:
        text = "\n".join(lines)
        for m in _TRIVIAL_BODY_RE.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            findings.append({
                "line": line_no,
                "severity": "HIGH",
                "what": "trivial method body returns a no-op value: %s"
                        % re.sub(r"\s+", " ", m.group(0)).strip()[:120],
            })

    # Python trivial bodies: a def whose entire suite is a single trivial return
    # / pass / ... / raise NotImplementedError.
    if is_python and not is_test:
        n = len(lines)
        for i, line in enumerate(lines):
            dm = _PY_DEF_RE.match(line)
            if not dm:
                continue
            indent = dm.group(1)
            # The def header may span multiple lines (multi-line signature).
            # Find the line that ends the signature (ends with ':').
            j = i
            while j < n and not lines[j].rstrip().endswith(":"):
                j += 1
            if j >= n:
                continue
            # Collect body lines: subsequent non-blank lines more-indented than def.
            body = []
            k = j + 1
            while k < n:
                bl = lines[k]
                if bl.strip() == "":
                    k += 1
                    continue
                cur_indent = len(bl) - len(bl.lstrip())
                if cur_indent <= len(indent):
                    break
                # skip a leading docstring line
                stripped = bl.strip()
                if stripped.startswith(('"""', "'''")):
                    k += 1
                    # consume to closing triple-quote if not single-line
                    quote = stripped[:3]
                    if not (len(stripped) > 3 and stripped.endswith(quote)):
                        while k < n and quote not in lines[k]:
                            k += 1
                        k += 1
                    continue
                body.append((k + 1, bl))
                k += 1
            if len(body) == 1 and _PY_TRIVIAL_RETURN_RE.match(body[0][1]):
                findings.append({
                    "line": body[0][0],
                    "severity": "HIGH",
                    "what": "trivial function body (no-op): %s" % body[0][1].strip()[:120],
                })
    return findings


def scan_docs(rel_path, lines):
    """DOCS dimension. Empty/TODO sections, missing setup/run steps."""
    findings = []
    base = os.path.basename(rel_path).lower()
    is_readme = base.startswith("readme")

    # Walk markdown headings; flag a heading with no content body before the
    # next heading, and any TODO/TBD inside the doc.
    headings = []  # (line_no, level, title)
    for i, line in enumerate(lines, start=1):
        hm = _MD_HEADING_RE.match(line)
        if hm:
            headings.append((i, len(hm.group(1)), hm.group(2).strip()))
        if _DOC_TODO_RE.search(line):
            findings.append({
                "line": i,
                "severity": "MEDIUM",
                "what": "TODO/TBD placeholder in docs: %s" % line.strip()[:120],
            })

    # Empty sections: a heading immediately followed (ignoring blanks) by another
    # heading or EOF with no body text.
    for idx, (line_no, level, title) in enumerate(headings):
        next_line = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines) + 1
        body_text = "".join(
            lines[k].strip() for k in range(line_no, min(next_line - 1, len(lines)))
        )
        if not body_text:
            findings.append({
                "line": line_no,
                "severity": "MEDIUM",
                "what": "empty doc section: '%s' has no content" % title[:80],
            })

    # README without any setup/run guidance is a HIGH smell for a deliverable.
    if is_readme and lines:
        joined = "\n".join(lines).lower()
        has_setup_heading = any(
            any(h in title.lower() for h in _SETUP_HEADING_HINTS)
            for _, _, title in headings
        )
        has_run_command = bool(
            re.search(r"(mvn |gradle |gradlew|npm (run |start|test)|go run|go test"
                      r"|python |pytest|docker (run|compose)|\./run)", joined)
        )
        if not has_setup_heading and not has_run_command:
            findings.append({
                "line": 1,
                "severity": "HIGH",
                "what": "README has no setup/run instructions (no setup heading, no run command)",
            })
    return findings


def scan_config(rel_path, lines):
    """CONFIG dimension. Placeholder/empty env vars, hardcoded test values."""
    findings = []
    base = os.path.basename(rel_path).lower()
    # Example/sample env files are SUPPOSED to carry placeholders — downgrade.
    is_example = ("example" in base or "sample" in base or "template" in base)

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", ";", "<!--")):
            continue
        kv = _KV_RE.match(line)
        if not kv:
            # Non key=value config (yaml/json/xml): still flag obvious hardcoded
            # test endpoints in value position.
            if _HARDCODED_TEST_RE.search(line) and ("=" in line or ":" in line):
                findings.append({
                    "line": i,
                    "severity": "LOW" if is_example else "MEDIUM",
                    "what": "hardcoded test/placeholder value in config: %s" % stripped[:120],
                })
            continue
        key, val = kv.group(1), kv.group(2)
        val_clean = val.strip().strip('"').strip("'")

        # Empty value for a sensitive key → fill-me-in smell.
        if val_clean == "" and _SENSITIVE_KEY_RE.search(key):
            findings.append({
                "line": i,
                "severity": "LOW" if is_example else "HIGH",
                "what": "empty value for sensitive config key '%s'" % key,
            })
            continue
        # Placeholder value.
        if _PLACEHOLDER_VAL_RE.match(val_clean):
            sev = "LOW" if is_example else ("HIGH" if _SENSITIVE_KEY_RE.search(key) else "MEDIUM")
            findings.append({
                "line": i,
                "severity": sev,
                "what": "placeholder value for '%s': %s" % (key, val_clean[:80]),
            })
            continue
        # Hardcoded test value.
        if _HARDCODED_TEST_RE.search(val_clean):
            findings.append({
                "line": i,
                "severity": "LOW" if is_example else "MEDIUM",
                "what": "hardcoded test value for '%s': %s" % (key, val_clean[:80]),
            })
    return findings


def scan_build(rel_path, lines):
    """BUILD dimension. Skipped/disabled tests, commented-out steps."""
    findings = []
    in_build_file = (
        os.path.basename(rel_path) in BUILD_NAMES
        or os.path.basename(rel_path).lower() in {b.lower() for b in BUILD_NAMES}
        or any(h in rel_path.replace("\\", "/") for h in BUILD_CI_DIR_HINTS)
    )
    for i, line in enumerate(lines, start=1):
        if _SKIP_TEST_RE.search(line):
            findings.append({
                "line": i,
                "severity": "HIGH",
                "what": "skipped/disabled test: %s" % line.strip()[:120],
            })
        if in_build_file and _BUILD_SKIP_FLAG_RE.search(line):
            findings.append({
                "line": i,
                "severity": "HIGH",
                "what": "build configured to skip tests: %s" % line.strip()[:120],
            })
        if in_build_file and _COMMENTED_BUILD_STEP_RE.match(line):
            findings.append({
                "line": i,
                "severity": "MEDIUM",
                "what": "commented-out build/test step: %s" % line.strip()[:120],
            })
    return findings


_SCANNERS = {
    "CODE": scan_code,
    "DOCS": scan_docs,
    "CONFIG": scan_config,
    "BUILD": scan_build,
}


def scan_tree(root, dimensions=None):
    """
    Walk `root` and return (findings, summary).

    `dimensions`: optional iterable restricting which dimensions run (used by the
    per-dimension reviewer swarm to parallelize). None = all four.
    """
    wanted = set(d.upper() for d in dimensions) if dimensions else set(_SCANNERS)
    findings = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in place so os.walk does not descend.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            abspath = os.path.join(dirpath, fname)
            dimension = _classify(abspath)
            if dimension is None:
                continue
            rel = os.path.relpath(abspath, root)
            lines = _read_lines(abspath)
            if lines is None:
                continue

            # Run the file's own dimension scanner.
            if dimension in wanted and dimension in _SCANNERS:
                for f in _SCANNERS[dimension](rel, lines):
                    f["dimension"] = dimension
                    f["path"] = rel
                    findings.append(f)
            # Cross-dimension: BUILD markers (skipped tests) can live in CODE
            # files (e.g. @Disabled in a .java test). Run the BUILD scanner on
            # source files too when BUILD is wanted, so disabled tests are caught
            # regardless of which file they live in.
            if dimension == "CODE" and "BUILD" in wanted:
                for f in scan_build(rel, lines):
                    f["dimension"] = "BUILD"
                    f["path"] = rel
                    findings.append(f)

    # Stable ordering: dimension, path, line.
    findings.sort(key=lambda f: (f["dimension"], f["path"], f.get("line") or 0))

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    dim_counts = {"CODE": 0, "DOCS": 0, "CONFIG": 0, "BUILD": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        dim_counts[f["dimension"]] = dim_counts.get(f["dimension"], 0) + 1

    status = "FAIL" if counts["HIGH"] > 0 else "PASS"
    summary = {
        "status": status,
        "counts": counts,
        "dimension_counts": dim_counts,
    }
    return findings, summary


def build_report(root, dimensions=None):
    """Build the full report dict for `root`."""
    abs_root = os.path.abspath(root)
    findings, summary = scan_tree(abs_root, dimensions=dimensions)
    return {
        "status": summary["status"],
        "scanned_root": abs_root,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dimensions": sorted(set(d.upper() for d in dimensions)) if dimensions
                      else sorted(_SCANNERS),
        "counts": summary["counts"],
        "dimension_counts": summary["dimension_counts"],
        "findings": findings,
    }


def write_report(report, output_path):
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def _load_target_path(config_path):
    """Resolve the target tree from config.json (target_path)."""
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    return cfg.get("target_path")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Final completeness review — scan a built target tree for "
                    "mocked / half-done / incomplete work across CODE/DOCS/CONFIG/BUILD."
    )
    parser.add_argument(
        "--workspace",
        help="Path to the built target tree to scan. Defaults to target_path from --config.",
    )
    parser.add_argument(
        "--config",
        default=".anti-legacy/config.json",
        help="Path to config.json (used to resolve target_path when --workspace is omitted).",
    )
    parser.add_argument(
        "--dimension",
        action="append",
        choices=["CODE", "DOCS", "CONFIG", "BUILD"],
        help="Restrict to one or more dimensions (repeatable). Default: all four. "
             "Used by the per-dimension reviewer swarm to parallelize.",
    )
    parser.add_argument(
        "--output",
        default=".anti-legacy/evidence/completeness-report.json",
        help="Where to write the JSON evidence report.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable summary on stdout.",
    )
    args = parser.parse_args(argv)

    root = args.workspace or _load_target_path(args.config)
    if not root:
        print("Error: no --workspace given and target_path missing from config "
              "(%s)." % args.config, file=sys.stderr)
        return 2
    if not os.path.isdir(root):
        print("Error: target tree does not exist: %s" % root, file=sys.stderr)
        return 2

    report = build_report(root, dimensions=args.dimension)
    write_report(report, args.output)

    if not args.quiet:
        c = report["counts"]
        print("Completeness scan of %s" % report["scanned_root"])
        print("  status: %s" % report["status"])
        print("  HIGH=%d MEDIUM=%d LOW=%d  (CODE=%d DOCS=%d CONFIG=%d BUILD=%d)" % (
            c["HIGH"], c["MEDIUM"], c["LOW"],
            report["dimension_counts"]["CODE"],
            report["dimension_counts"]["DOCS"],
            report["dimension_counts"]["CONFIG"],
            report["dimension_counts"]["BUILD"],
        ))
        print("  report: %s" % args.output)
        if report["status"] == "FAIL":
            print("  --- HIGH findings ---")
            for f in report["findings"]:
                if f["severity"] == "HIGH":
                    loc = ("%s:%s" % (f["path"], f["line"])) if f.get("line") else f["path"]
                    print("  [%s] %s — %s" % (f["dimension"], loc, f["what"]))

    # Exit non-zero on FAIL so the gate/orchestrator can branch on it.
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
