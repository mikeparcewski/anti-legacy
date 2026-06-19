#!/usr/bin/env python3
"""antilegacy_core.stack_discovery — probes workspace to determine legacy + target stack conventions.

Runs after survey (graph built) for full results, or before the graph for target-only detection.
Produces .anti-legacy/stack-profile.json registered as the 'stack-profile' artifact.

Downstream consumers (graph_validator, functional_tests, blueprint) call load_profile(workspace)
to get verified, proven stack facts rather than guessing from a static table.

Priority chain for stack-sensitive decisions:
  1. config.json explicit override (e.g. coverage.utility_name_patterns)
  2. stack-profile.json discovered values (this script)
  3. Hardcoded defaults (COBOL-safe fallback)

Usage: python3 .anti-legacy/run.py stack_discovery [--workspace .]
"""
import argparse
import glob as _g
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Build manifest signals: (filename, stack, build_system, confidence_weight)
# Ordered highest-weight first so the first match wins ties.
# ---------------------------------------------------------------------------
_BUILD_SIGNALS = [
    ("pom.xml",          "java",       "maven",      1.0),
    ("build.gradle",     "java",       "gradle",     0.9),
    ("build.gradle.kts", "java",       "gradle",     0.9),
    ("go.mod",           "go",         "go-modules", 1.0),
    ("Cargo.toml",       "rust",       "cargo",      1.0),
    ("tsconfig.json",    "typescript", "typescript", 1.0),
    ("pyproject.toml",   "python",     "pyproject",  1.0),
    ("setup.py",         "python",     "setuptools", 0.8),
    ("setup.cfg",        "python",     "setuptools", 0.7),
    ("requirements.txt", "python",     "pip",        0.6),
    ("package.json",     "typescript", "npm",        0.65),
]

# Test root candidates per stack (priority order — first existing wins)
_TEST_ROOT_CANDIDATES = {
    "java":       ["src/test/java", "src/test/kotlin", "test"],
    "python":     ["tests", "test", "src/tests", "src/test"],
    "go":         [".", "cmd"],       # Go tests live alongside source
    "rust":       ["tests", "src"],
    "typescript": ["__tests__", "src/__tests__", "test", "tests"],
    "csharp":     ["tests", "Tests", "test"],
}

# Acceptance test sub-directory within the test root
_ACCEPTANCE_SUBPATH = {
    "java":       "acceptance",
    "python":     "acceptance",
    "go":         "acceptance_test",
    "rust":       "acceptance",
    "typescript": "acceptance",
    "csharp":     "Acceptance",
}

# Known infrastructure suffixes used for naming-convention derivation
_INFRA_SUFFIXES = frozenset({
    "UTIL", "UTILS", "HELPER", "HELPERS", "ADAPTER", "BRIDGE",
    "SORT", "COPY", "LOG", "LOGGER", "GEN", "DUMP", "WAIT", "SLEEP",
    "CONFIG", "CONST", "CONSTANT", "FACTORY", "BUILDER",
})


def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Target stack probing
# ---------------------------------------------------------------------------

def _refine_maven(pom_path, finding):
    """Read pom.xml with regex to extract Java version and framework hints.

    Deliberately avoids xml.etree (XXE / billion-laughs risk on untrusted XML)
    in favour of targeted regex on the raw text — we only need two values and
    pom.xml is a well-structured, predictable format for these fields.
    """
    try:
        with open(pom_path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for pattern in (
            r"<java\.version>\s*([\d.]+)\s*</java\.version>",
            r"<maven\.compiler\.source>\s*([\d.]+)\s*</maven\.compiler\.source>",
            r"<maven\.compiler\.release>\s*([\d.]+)\s*</maven\.compiler\.release>",
        ):
            m = re.search(pattern, content)
            if m:
                finding["details"]["language_version"] = m.group(1)
                finding["evidence"].append(f"Java version: {m.group(1)}")
                break
        if re.search(r"spring-boot[^<]*starter[^<]*parent", content, re.IGNORECASE):
            finding["details"]["framework"] = "spring-boot"
            finding["evidence"].append("Spring Boot parent POM detected")
    except Exception:
        pass


def _refine_go_mod(path, finding):
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"^go\s+([\d.]+)", content, re.MULTILINE)
        if m:
            finding["details"]["language_version"] = m.group(1)
            finding["evidence"].append(f"Go version: {m.group(1)}")
    except Exception:
        pass


def _refine_pyproject(path, finding):
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'requires-python\s*=\s*["\']([^"\']+)', content)
        if m:
            finding["details"]["language_version"] = m.group(1)
            finding["evidence"].append(f"Python requires: {m.group(1)}")
        if "pytest" in content:
            finding["details"]["test_framework"] = "pytest"
            finding["evidence"].append("pytest detected in pyproject.toml")
    except Exception:
        pass


def _refine_package_json(path, finding):
    try:
        pkg = _read_json(path)
        if not pkg:
            return
        all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "typescript" in all_deps:
            finding["stack"] = "typescript"
            finding["weight"] = 1.0
            finding["evidence"].append("typescript in package.json dependencies")
        if "jest" in all_deps:
            finding["details"]["test_framework"] = "jest"
        elif "vitest" in all_deps:
            finding["details"]["test_framework"] = "vitest"
    except Exception:
        pass


def _probe_target_stack(target_path):
    """Inspect build manifests in target_path. Returns a findings dict with proven evidence."""
    hits = []
    for filename, stack, build_sys, weight in _BUILD_SIGNALS:
        fpath = os.path.join(target_path, filename)
        if os.path.isfile(fpath):
            finding = {
                "file": filename, "stack": stack, "build_system": build_sys,
                "weight": weight, "evidence": [f"{filename} found"], "details": {},
            }
            if filename == "pom.xml":
                _refine_maven(fpath, finding)
            elif filename == "go.mod":
                _refine_go_mod(fpath, finding)
            elif filename == "pyproject.toml":
                _refine_pyproject(fpath, finding)
            elif filename == "package.json":
                _refine_package_json(fpath, finding)
            hits.append(finding)

    # Glob for .csproj / .sln (dotnet — no fixed filename)
    for pat in ("**/*.csproj", "**/*.sln"):
        matches = _g.glob(os.path.join(target_path, pat), recursive=True)
        if matches:
            hits.append({
                "file": os.path.basename(matches[0]), "stack": "csharp",
                "build_system": "dotnet", "weight": 1.0,
                "evidence": [f"{os.path.basename(matches[0])} found"],
                "details": {},
            })
            break

    if not hits:
        return {
            "stack": None, "build_system": None, "confidence": 0.0,
            "evidence": [], "details": {}, "proven": False,
            "gap": "No build manifest found — set target_stack manually in config.json",
        }

    best = max(hits, key=lambda h: h["weight"])
    # Bonus confidence when multiple signals agree
    agreeing = sum(1 for h in hits if h["stack"] == best["stack"])
    confidence = min(0.99, best["weight"] + 0.04 * agreeing)

    return {
        "stack": best["stack"], "build_system": best["build_system"],
        "confidence": round(confidence, 2), "evidence": best["evidence"],
        "details": best["details"], "proven": True, "gap": None,
    }


def _find_source_root(target_path, stack):
    candidates = {
        "java":       ["src/main/java", "src/main/kotlin", "src"],
        "python":     ["src", "."],
        "go":         ["cmd", "internal", "."],
        "rust":       ["src"],
        "typescript": ["src"],
        "csharp":     ["src"],
    }
    for rel in candidates.get(stack, []):
        if os.path.isdir(os.path.join(target_path, rel)):
            return rel
    return None


def _probe_test_root(target_path, stack):
    """Find and verify the test root. Returns (rel_path, evidence_list, gap_or_None)."""
    for candidate in _TEST_ROOT_CANDIDATES.get(stack, ["tests"]):
        full = os.path.join(target_path, candidate)
        if os.path.isdir(full):
            file_count = sum(
                1 for f in _g.glob(os.path.join(full, "**", "*"), recursive=True)
                if os.path.isfile(f)
            )
            return (candidate,
                    [f"Test root {candidate!r} verified ({file_count} files)"],
                    None)
    # Not found — target may not be scaffolded yet
    default = (_TEST_ROOT_CANDIDATES.get(stack, ["tests"])[0])
    return (default, [],
            f"Test root not found in target_path — {default!r} assumed. "
            "Verify once target scaffold exists.")


# ---------------------------------------------------------------------------
# Legacy stack detection from graph
# ---------------------------------------------------------------------------

def _analyze_legacy_stack(workspace, config):
    """Count node kinds across all source_app graphs to identify the dominant legacy stack."""
    try:
        from antilegacy_core import wicked_estate as we
        from antilegacy_core.coverage import normalize_kind
    except ImportError:
        return {"dominant_stack": "unknown", "confidence": 0.0,
                "evidence": ["antilegacy_core not importable"], "kind_distribution": {}}

    kind_counts: Counter = Counter()
    apps = config.get("source_apps", [])
    for app in apps:
        db = os.path.join(workspace, ".anti-legacy", "graphs", f"{app['name']}.db")
        if not os.path.exists(db):
            continue
        try:
            for node in we.list_nodes(db):
                kind_counts[normalize_kind(node.get("kind", ""))] += 1
        except Exception:
            continue

    if not kind_counts:
        return {"dominant_stack": "unknown", "confidence": 0.0,
                "evidence": ["No graph DBs found — run survey first"],
                "kind_distribution": {}}

    total = sum(kind_counts.values())
    cobol_count = sum(kind_counts.get(k, 0) for k in ("cics_program", "db2_table", "step"))
    java_oo_count = sum(kind_counts.get(k, 0) for k in ("class", "interface", "method"))

    evidence = [f"{total} graph nodes: {dict(kind_counts.most_common(6))}"]

    cobol_ratio = cobol_count / max(total, 1)
    java_ratio = java_oo_count / max(total, 1)

    if cobol_ratio > 0.15:
        dom, conf = "cobol", min(0.99, 0.65 + cobol_ratio)
        evidence.append(f"COBOL/mainframe: {cobol_count}/{total} nodes ({cobol_ratio:.0%})")
    elif java_ratio > 0.30:
        dom, conf = "java", min(0.99, 0.55 + java_ratio)
        evidence.append(f"Java OO kinds: {java_oo_count}/{total} nodes ({java_ratio:.0%})")
    else:
        dom, conf = "mixed_or_unknown", 0.40
        evidence.append("No dominant language signal — review kind_distribution manually")

    return {
        "dominant_stack": dom, "confidence": round(conf, 2),
        "evidence": evidence,
        "kind_distribution": dict(kind_counts.most_common(20)),
    }


# ---------------------------------------------------------------------------
# Naming convention derivation from graph
# ---------------------------------------------------------------------------

def _derive_naming_patterns(workspace, config):
    """Analyze node names to derive utility name patterns for this specific codebase.

    The goal is to surface patterns like .*Util$, .*Helper$ that actually appear
    in this project's naming conventions — not to impose a language-generic list.
    Only considers module/function/class nodes (the naming-convention space).
    Returns patterns that appear in >= 2% of names AND match a known infra suffix.
    """
    try:
        from antilegacy_core import wicked_estate as we
        from antilegacy_core.coverage import normalize_kind
    except ImportError:
        return {"utility_patterns": [], "confidence": 0.0,
                "evidence": ["not importable"], "node_count": 0}

    names = []
    apps = config.get("source_apps", [])
    for app in apps:
        db = os.path.join(workspace, ".anti-legacy", "graphs", f"{app['name']}.db")
        if not os.path.exists(db):
            continue
        try:
            for node in we.list_nodes(db):
                if normalize_kind(node.get("kind", "")) in ("module", "function", "class"):
                    names.append(node["name"].upper())
        except Exception:
            continue

    if not names:
        return {"utility_patterns": [], "confidence": 0.0,
                "evidence": ["No module/class nodes found"], "node_count": 0}

    total = len(names)
    threshold = max(3, int(total * 0.02))

    # Count exact suffix matches against known infra suffixes
    suffix_hits: Counter = Counter()
    for name in names:
        for suffix in _INFRA_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                suffix_hits[suffix] += 1

    patterns = []
    evidence_parts = [f"Analyzed {total} module/class names"]
    for suffix, count in suffix_hits.most_common():
        if count >= threshold:
            patterns.append(f".*{re.escape(suffix)}$")
            evidence_parts.append(f"  {suffix}: {count}/{total} ({100*count//total}%)")

    conf = 0.82 if len(patterns) >= 3 else (0.55 if patterns else 0.20)
    return {
        "utility_patterns": patterns,
        "confidence": round(conf, 2),
        "evidence": evidence_parts[:10],
        "node_count": total,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover(workspace="."):
    """Run full stack discovery. Writes .anti-legacy/stack-profile.json.

    Returns (profile_dict, error_str). error_str is None on success.
    Can run before the graph exists (target detection only) or after (full profile).
    """
    config = _read_json(os.path.join(workspace, ".anti-legacy", "config.json"))
    if config is None:
        return None, "config.json not found — run setup first"

    target_path_cfg = config.get("target_path", "")
    if target_path_cfg and not os.path.isabs(target_path_cfg):
        target_path = os.path.join(workspace, target_path_cfg)
    else:
        target_path = target_path_cfg

    gaps = []

    # --- Target stack ---
    if target_path and os.path.isdir(target_path):
        tp = _probe_target_stack(target_path)
        if tp["gap"]:
            gaps.append(tp["gap"])
    else:
        config_stack = config.get("target_stack")
        tp = {
            "stack": config_stack, "build_system": None, "proven": False,
            "confidence": 0.50 if config_stack else 0.0,
            "evidence": [f"target_path absent — using config target_stack={config_stack!r}"],
            "details": {},
        }
        if not config_stack:
            gaps.append("target_path not found and target_stack not set — discovery is partial")

    stack = tp["stack"] or config.get("target_stack")

    # --- Test root ---
    if stack and target_path and os.path.isdir(target_path):
        test_root, test_ev, test_gap = _probe_test_root(target_path, stack)
        if test_gap:
            gaps.append(test_gap)
        tp["evidence"].extend(test_ev)
    else:
        test_root = (_TEST_ROOT_CANDIDATES.get(stack or "", ["tests"])[0])
        gaps.append("Test root assumed — target_path not available for filesystem verification")

    source_root = (
        _find_source_root(target_path, stack)
        if (stack and target_path and os.path.isdir(target_path)) else None
    )
    accept_sub = _ACCEPTANCE_SUBPATH.get(stack or "", "acceptance")
    acceptance_path = f"{test_root}/{accept_sub}"

    # --- Legacy stack (needs graph) ---
    legacy = _analyze_legacy_stack(workspace, config)

    # --- Naming conventions (needs graph) ---
    naming = _derive_naming_patterns(workspace, config)

    profile = {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "legacy": legacy,
        "target": {
            "stack": stack,
            "build_system": tp["build_system"],
            "confidence": tp["confidence"],
            "proven": tp.get("proven", False),
            "evidence": tp["evidence"],
            "language_version": tp.get("details", {}).get("language_version"),
            "framework": tp.get("details", {}).get("framework"),
            "source_root": source_root,
            "test_root": test_root,
            "acceptance_test_path": acceptance_path,
        },
        "naming": naming,
        "gaps": gaps,
    }

    out = os.path.join(workspace, ".anti-legacy", "stack-profile.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    return profile, None


def load_profile(workspace="."):
    """Load the stack profile produced by discover(). Returns None if not yet run."""
    return _read_json(os.path.join(workspace, ".anti-legacy", "stack-profile.json"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="stack_discovery",
        description="Probe workspace to discover legacy + target stack conventions",
    )
    ap.add_argument("--workspace", default=".", help="Workspace root (default: .)")
    ap.add_argument("--json", action="store_true", help="Emit full profile as JSON")
    args = ap.parse_args(argv)

    profile, err = discover(args.workspace)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(profile, indent=2))
        return 0

    lg = profile["legacy"]
    tg = profile["target"]
    nm = profile["naming"]

    print(f"Legacy stack : {lg['dominant_stack']} (confidence {lg['confidence']:.0%})")
    for ev in lg["evidence"][:3]:
        print(f"  {ev}")

    print(f"\nTarget stack : {tg['stack']} via {tg['build_system']} "
          f"({'proven' if tg['proven'] else 'assumed'}, {tg['confidence']:.0%})")
    for ev in tg["evidence"][:4]:
        print(f"  {ev}")
    print(f"  source root     : {tg['source_root'] or '(not found)'}")
    print(f"  test root       : {tg['test_root']}")
    print(f"  acceptance path : {tg['acceptance_test_path']}")

    if nm["utility_patterns"]:
        print(f"\nDerived utility patterns (confidence {nm['confidence']:.0%}):")
        for p in nm["utility_patterns"][:8]:
            print(f"  {p}")

    if profile["gaps"]:
        print("\nGaps (need manual input or re-run after scaffold):")
        for g in profile["gaps"]:
            print(f"  ! {g}")

    # Register artifact so precheck and evidence-log can reference it
    try:
        import subprocess
        subprocess.run(
            [sys.executable, ".anti-legacy/run.py", "manifest", "register", "stack-profile",
             "--path", "stack-profile.json", "--format", "json",
             "--produced-by", "anti-legacy:stack-discovery", "--status",
             "draft" if profile["gaps"] else "final"],
            cwd=args.workspace, check=True,
        )
    except Exception as e:
        print(f"Warning: artifact registration failed: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
