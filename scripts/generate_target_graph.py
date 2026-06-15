#!/usr/bin/env python3
import os
import sys
import re
import json
import argparse
from datetime import datetime, timezone

# Rule-id token shared across all languages. A rule_id from the requirements
# graph (RULE-/VAL-/ERR-NNN) is the join key compare_graphs.py uses to verify
# rule-level coverage instead of mere class-name existence.
_RULE_ID_RE = r'(?:RULE|VAL|ERR)-\d+'

# Per-language configuration. Each entry describes how to find source files,
# how to derive a symbol/class name, the source roots to probe under
# target_path, and the WEAK-tier rule-id evidence anchors (annotation/attribute
# form + line-comment form) the scanner extracts. NOTHING here assumes a
# particular package layout (no com/carddemo): the actual directory tree under
# the discovered source root drives domain_name and package/namespace.
#
# `annotation`: regex whose group(1) is a rule_id, recorded with source
#   "annotation" (Java/Kotlin @ImplementsRule/@SatisfiesRule, C# attribute).
# `comment`:    regex whose group(1) is a rule_id, recorded with source
#   "marker_comment" (language line-comment prefix carrying a bare rule_id).
_LANG_CONFIG = {
    "java": {
        "exts": (".java",),
        "src_roots": ("src/main/java",),
        "skip_files": ("Application.java",),
        "annotation": re.compile(
            r'@(?:ImplementsRule|SatisfiesRule)\(\s*"(' + _RULE_ID_RE + r')"'
        ),
        "comment": re.compile(r'//\s*(' + _RULE_ID_RE + r')\b'),
    },
    "kotlin": {
        "exts": (".kt",),
        "src_roots": ("src/main/kotlin",),
        "skip_files": ("Application.kt",),
        "annotation": re.compile(
            r'@(?:ImplementsRule|SatisfiesRule)\(\s*"(' + _RULE_ID_RE + r')"'
        ),
        "comment": re.compile(r'//\s*(' + _RULE_ID_RE + r')\b'),
    },
    "python": {
        "exts": (".py",),
        "src_roots": ("src", "."),
        "skip_files": ("__init__.py", "setup.py"),
        "annotation": None,
        "comment": re.compile(r'#\s*(' + _RULE_ID_RE + r')\b'),
    },
    "go": {
        "exts": (".go",),
        "src_roots": (".", "cmd", "internal", "pkg"),
        "skip_files": (),
        "annotation": None,
        "comment": re.compile(r'//\s*(' + _RULE_ID_RE + r')\b'),
    },
    "typescript": {
        "exts": (".ts",),
        "src_roots": ("src", "."),
        "skip_files": (),
        "annotation": None,
        "comment": re.compile(r'//\s*(' + _RULE_ID_RE + r')\b'),
    },
    "csharp": {
        "exts": (".cs",),
        "src_roots": ("src", "."),
        "skip_files": (),
        "annotation": re.compile(
            r'\[\s*(?:ImplementsRule|SatisfiesRule)\(\s*"(' + _RULE_ID_RE + r')"'
        ),
        "comment": re.compile(r'//\s*(' + _RULE_ID_RE + r')\b'),
    },
}

# Aliases so a config target_stack value maps onto a canonical language key.
_STACK_ALIASES = {
    "java": "java",
    "spring": "java",
    "springboot": "java",
    "spring-boot": "java",
    "kotlin": "kotlin",
    "python": "python",
    "py": "python",
    "fastapi": "python",
    "django": "python",
    "flask": "python",
    "go": "go",
    "golang": "go",
    "typescript": "typescript",
    "ts": "typescript",
    "node": "typescript",
    "nestjs": "typescript",
    "csharp": "csharp",
    "c#": "csharp",
    "cs": "csharp",
    "dotnet": "csharp",
    ".net": "csharp",
}


def _resolve_language(target_stack):
    """Map a free-form config target_stack onto a canonical _LANG_CONFIG key."""
    if not target_stack:
        return None
    key = str(target_stack).strip().lower()
    if key in _LANG_CONFIG:
        return key
    return _STACK_ALIASES.get(key)


class TargetGraphGenerator:
    def __init__(self, target_dir, target_stack="java"):
        self.target_dir = os.path.abspath(target_dir)
        self.lang = _resolve_language(target_stack)
        self.target_stack = target_stack
        self.cfg = _LANG_CONFIG.get(self.lang) if self.lang else None
        self.domains = {}

    def generate(self, output_path):
        if self.cfg is None:
            print(
                f"Error: unsupported target_stack '{self.target_stack}' "
                f"(known: {', '.join(sorted(_LANG_CONFIG))})",
                file=sys.stderr,
            )
            return False

        src_dir = self._discover_source_root()
        if src_dir is None:
            roots = ", ".join(self.cfg["src_roots"])
            print(
                f"Error: no {self.lang} source files (*{', *'.join(self.cfg['exts'])}) "
                f"found under {self.target_dir} (probed roots: {roots})",
                file=sys.stderr,
            )
            return False

        # AUTO-DETECT layout: derive domains from the directory tree actually
        # present under the source root rather than assuming any fixed package
        # (no com/carddemo). Immediate subdirectories of the source root that
        # contain matching files become domains; matching files directly under
        # the source root fall back to a domain named after the source dir.
        domain_dirs = self._discover_domains(src_dir)
        for domain_name, domain_path in domain_dirs:
            self._parse_domain(domain_path, domain_name, src_dir)

        graph_data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target_path": self.target_dir,
            "domains": self.domains,
        }

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(graph_data, f, indent=2)
        print(f"Target state graph written to {output_path}")
        return True

    def _matches(self, filename):
        if filename in self.cfg["skip_files"]:
            return False
        return filename.endswith(self.cfg["exts"])

    def _has_matching_file(self, directory):
        for _, _, files in os.walk(directory):
            for f in files:
                if self._matches(f):
                    return True
        return False

    def _discover_source_root(self):
        """Probe the per-language candidate source roots under target_dir and
        return the first that actually contains matching files. Falls back to
        target_dir itself so a flat repo (Python/Go/TS at root) still works."""
        for candidate in self.cfg["src_roots"]:
            cand_path = (
                self.target_dir if candidate == "."
                else os.path.join(self.target_dir, candidate)
            )
            if os.path.isdir(cand_path) and self._has_matching_file(cand_path):
                return cand_path
        if self._has_matching_file(self.target_dir):
            return self.target_dir
        return None

    def _discover_domains(self, src_dir):
        """Walk the source root one level at a time to find the layer at which
        domain folders live, WITHOUT assuming any package prefix. We descend
        through single-child wrapper dirs (e.g. a language package root such as
        com/<org>) until we reach a directory whose subdirectories hold the
        source files; those subdirectories are the domains. Returns a list of
        (domain_name, domain_path)."""
        base = src_dir
        # Descend through pure pass-through package dirs (only subdirs, no
        # matching files of their own) while there is exactly one subdir -- this
        # transparently skips a com/<org> namespace prefix for any org.
        while True:
            try:
                entries = [
                    e for e in sorted(os.listdir(base))
                    if os.path.isdir(os.path.join(base, e))
                ]
            except OSError:
                break
            files_here = any(
                self._matches(f) for f in os.listdir(base)
                if os.path.isfile(os.path.join(base, f))
            )
            if files_here or len(entries) != 1:
                break
            base = os.path.join(base, entries[0])

        domains = []
        try:
            subdirs = [
                e for e in sorted(os.listdir(base))
                if os.path.isdir(os.path.join(base, e))
            ]
        except OSError:
            subdirs = []

        for sub in subdirs:
            sub_path = os.path.join(base, sub)
            if self._has_matching_file(sub_path):
                domains.append((sub, sub_path))

        # If no subdirectory carries source (flat layout), treat the base dir
        # itself as a single domain named after that directory.
        if not domains and self._has_matching_file(base):
            domains.append((os.path.basename(base.rstrip(os.sep)) or "app", base))

        return domains

    def _derive_package(self, domain_path, src_dir):
        """Derive a package/namespace string from the discovered path relative
        to the source root -- e.g. src/main/java/com/acme/billing -> the part
        after the language src root, dotted: com.acme.billing. No fixed prefix
        is assumed; whatever directory tree exists becomes the package."""
        rel = os.path.relpath(domain_path, src_dir)
        if rel in (".", ""):
            return os.path.basename(domain_path.rstrip(os.sep))
        parts = [p for p in rel.split(os.sep) if p and p != "."]
        return ".".join(parts)

    def _parse_domain(self, domain_path, domain_name, src_dir):
        components = {}
        entities = {}
        package_name = self._derive_package(domain_path, src_dir)

        for root, _, files in os.walk(domain_path):
            for file in files:
                if self._matches(file):
                    file_path = os.path.join(root, file)
                    self._parse_file(file_path, components, entities)

        if components or entities:
            self.domains[domain_name] = {
                "package": package_name,
                "components": components,
                "entities": entities,
            }

    def _extract_rule_evidence(self, content, file_path):
        """Best-effort, machine-checkable rule-implementation evidence.

        Scans the source for the per-language WEAK-tier anchors and returns a
        list of {rule_id, source, evidence_strength, file_path, line_range}
        dicts (the item shape compare_graphs.py reads from each component's
        `implemented_rules` array). De-duplicated per (rule_id, source).

        WEAK (structural) tier only -- the presence of an annotation / marker
        comment proves the developer CLAIMED to implement the rule, not that a
        passing test exercises it. The STRONG tier (source:"test_ledger") and
        the MEDIUM tier (source:"mapped_method" / semantic concurrence) are
        joined in later by compare_graphs.py, not produced here.

        LIMITATION: this only detects developer-supplied anchors. It does NOT
        statically prove the rule's logic exists -- that intentionally yields
        at most PARTIAL in compare_graphs, pushing teams toward the STRONG
        (passing per-rule test) tier for a true PASS.
        """
        evidence = []
        seen = set()

        def _record(rule_id, source, line_no):
            key = (rule_id, source)
            if key in seen:
                return
            seen.add(key)
            evidence.append({
                "rule_id": rule_id,
                "source": source,
                "evidence_strength": "weak",
                "file_path": file_path,
                "line_range": f"{line_no}-{line_no}",
            })

        ann_re = self.cfg.get("annotation")
        com_re = self.cfg.get("comment")
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            if ann_re is not None:
                for m in ann_re.finditer(line):
                    _record(m.group(1), "annotation", idx)
            if com_re is not None:
                for m in com_re.finditer(line):
                    _record(m.group(1), "marker_comment", idx)

        return evidence

    def _symbol_name(self, file_path):
        """Derive a class/symbol name from the file name, stripping the
        language extension (replaces the old hardcoded '.java' strip)."""
        name = os.path.basename(file_path)
        for ext in self.cfg["exts"]:
            if name.endswith(ext):
                return name[: -len(ext)]
        return name

    def _parse_file(self, file_path, components, entities):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except OSError:
            return

        class_name = self._symbol_name(file_path)

        # Rule-implementation evidence is independent of the component KIND --
        # a controller, service, batch job, repository OR entity may all carry
        # rule anchors. Extract once and attach to whatever record we emit.
        rule_evidence = self._extract_rule_evidence(content, file_path)

        # Check if JPA Entity. We NO LONGER short-circuit here: an entity that
        # also carries rule markers yields BOTH its entity record AND its rule
        # evidence, and parsing continues so the same file could also surface a
        # component kind below if it carries those signals.
        if "@Entity" in content:
            table_name = class_name.lower()
            table_match = re.search(r'@Table\(name\s*=\s*"([^"]+)"\)', content)
            if table_match:
                table_name = table_match.group(1)

            columns = []
            fields = re.findall(
                r'private\s+([A-Za-z0-9\._]+)\s+([A-Za-z0-9_]+);', content
            )
            for f_type, f_name in fields:
                columns.append({"name": f_name, "type": f_type})
            entity = {
                "table_name": table_name,
                "columns": columns,
                "file_path": file_path,
            }
            if rule_evidence:
                entity["implemented_rules"] = rule_evidence
            entities[class_name] = entity

        # Check if JpaRepository / repository
        if "interface" in content and "extends JpaRepository" in content:
            comp = {"type": "repository", "file_path": file_path}
            if rule_evidence:
                comp["implemented_rules"] = rule_evidence
            components[class_name] = comp
            return

        # Check if RestController
        if "@RestController" in content:
            endpoints = []
            mappings = re.findall(
                r'@RequestMapping\(method\s*=\s*RequestMethod\.([A-Z]+),\s*value\s*=\s*"([^"]+)"\)',
                content,
            )
            for method, path in mappings:
                endpoints.append({"method": method, "path": path})
            shorthand = re.findall(
                r'@(Get|Post|Put|Delete|Patch)Mapping\(\s*(?:value\s*=\s*)?"([^"]+)"\)',
                content,
            )
            for verb, path in shorthand:
                endpoints.append({"method": verb.upper(), "path": path})
            comp = {
                "type": "controller",
                "endpoints": endpoints,
                "file_path": file_path,
            }
            if rule_evidence:
                comp["implemented_rules"] = rule_evidence
            components[class_name] = comp
            return

        # Check if Scheduled Component
        if "@Component" in content:
            schedules = []
            sched_matches = re.findall(r'@Scheduled\(cron\s*=\s*"([^"]+)"\)', content)
            for cron in sched_matches:
                schedules.append({"cron": cron})
            comp = {
                "type": "batch_job" if schedules else "component",
                "schedules": schedules,
                "file_path": file_path,
            }
            if rule_evidence:
                comp["implemented_rules"] = rule_evidence
            components[class_name] = comp
            return

        # Check if Service
        if "@Service" in content:
            comp = {"type": "service", "file_path": file_path}
            if rule_evidence:
                comp["implemented_rules"] = rule_evidence
            components[class_name] = comp
            return

        # No component KIND signal but the file carried rule evidence and was
        # not classified as an entity above -- record a generic component so the
        # weak-tier evidence is not silently dropped.
        if class_name not in entities and rule_evidence:
            components[class_name] = {
                "type": "component",
                "file_path": file_path,
                "implemented_rules": rule_evidence,
            }


def _load_config(config_path):
    """Load config.json, returning (target_path, target_stack) or (None, None)
    on any failure. config wins over CLI defaults when present."""
    try:
        with open(config_path) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return None, None
    return config.get("target_path"), config.get("target_stack")


def main():
    parser = argparse.ArgumentParser(
        description="Scans the modernized codebase and generates a target graph."
    )
    parser.add_argument(
        "--config",
        default=".anti-legacy/config.json",
        help="Path to config.json (provides target_path + target_stack; wins over --workspace)",
    )
    parser.add_argument(
        "--workspace",
        default="./target/credit-card-service",
        help="Fallback path to target codebase when config.json is absent",
    )
    parser.add_argument(
        "--stack",
        default=None,
        help="Override target_stack (else taken from config.json, else 'java')",
    )
    parser.add_argument(
        "--output",
        default=".anti-legacy/target_graph.json",
        help="Path to write target graph JSON",
    )
    args = parser.parse_args()

    cfg_target_path, cfg_target_stack = _load_config(args.config)

    # config wins; CLI flags are fallbacks/overrides.
    target_path = cfg_target_path or args.workspace
    target_stack = args.stack or cfg_target_stack or "java"

    generator = TargetGraphGenerator(target_path, target_stack)
    success = generator.generate(args.output)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
