#!/usr/bin/env python3
"""
Target-app documentation synthesizer (B2 — DOCUMENT phase).

The DOCUMENT phase runs AFTER GATE_4_UAT is cleared. It is the very last
authoring step before/alongside deploy: it writes the human-facing docs that
ship INSIDE the modernized target application directory so the delivered repo is
self-describing.

These docs are NOT coined by an LLM. They are DERIVED, deterministically, from
the committed pipeline artifacts:

  - config.json             — project name, target stack, deployment target,
                              database engine, target_path, source apps
  - blueprint.json          — domains / packages / components / API surface /
                              schema → ARCHITECTURE.md
  - requirements_graph.json — per-requirement data_access + dependencies →
                              DEPENDENCIES.md (service / database / file deps,
                              NOT code-level callgraph edges)
  - target_graph.json       — the actually-built package/component layout (used
                              as a fallback for ARCHITECTURE when blueprint
                              domains are thin, and to confirm the build root)

Four documents are written under the target app dir:

  README.md        — concise: what the app does, how to set up, how to run.
  ARCHITECTURE.md  — domains / services / package layout + boundaries.
  DEPENDENCIES.md  — service / database / file dependencies (infra-level).
  ENVIRONMENTS.md  — deployment targets + per-environment config / setup.

Every produced doc is registered as a manifest artifact (status=final) so the
gate/audit contract can see it. Registration reuses antilegacy_core.manifest's own
helpers, so there is exactly one definition of how an artifact row is shaped and
checksummed.

Pure standard library. No shell-isms; cross-platform (macOS / Linux / WSL /
Windows) — every path is built with os.path.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# Artifact ids registered by this phase (stable; consumed by manifest/audit).
# --------------------------------------------------------------------------- #
DOC_ARTIFACT_IDS = {
    "README.md": "doc-readme",
    "ARCHITECTURE.md": "doc-architecture",
    "DEPENDENCIES.md": "doc-dependencies",
    "ENVIRONMENTS.md": "doc-environments",
}

# Default per-environment ladder. Deterministic; the actual deployment target
# name comes from config so the rendered table is project-specific.
_DEFAULT_ENVIRONMENTS = ("local", "staging", "production")


def _load_json(path):
    """Load a JSON file, returning {} when absent or unreadable.

    The synthesizer must degrade gracefully: a missing/empty optional artifact
    means the corresponding section is rendered as 'not available' rather than
    crashing the DOCUMENT phase.
    """
    if not path or not os.path.exists(path) or os.path.isdir(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _resolve_target_dir(config, blueprint, target_graph, override=None):
    """Resolve the directory the docs are written into.

    Precedence: explicit --target-dir override, then config.target_path, then
    blueprint.target_path, then target_graph.target_path. Returns None when no
    target path can be determined.
    """
    for candidate in (
        override,
        config.get("target_path"),
        blueprint.get("target_path"),
        target_graph.get("target_path"),
    ):
        if candidate:
            return candidate
    return None


# --------------------------------------------------------------------------- #
# Synthesizer
# --------------------------------------------------------------------------- #
class DocSynthesizer:
    """Derives the four target-app docs from committed pipeline artifacts."""

    def __init__(self, config, blueprint, requirements, target_graph):
        self.config = config or {}
        self.blueprint = blueprint or {}
        self.requirements = requirements or {}
        self.target_graph = target_graph or {}

        self.project = (
            self.config.get("project_name")
            or self.blueprint.get("project")
            or "modernized-application"
        )
        self.stack = (
            self.config.get("target_stack")
            or self.blueprint.get("target_stack")
            or "the target stack"
        )
        self.deployment_target = self.config.get("deployment_target") or "the configured platform"
        self.database = self.config.get("database") or "the configured database"
        self.style = self.blueprint.get("style") or "layered"

    # -- shared derivations ------------------------------------------------- #

    def _blueprint_domains(self):
        """Domains dict from the blueprint, falling back to the target graph.

        The blueprint is the design-time contract (preferred); the target graph
        reflects what was actually built and is used as a fallback when the
        blueprint carries no domains.
        """
        doms = self.blueprint.get("domains")
        if isinstance(doms, dict) and doms:
            return doms, "blueprint.json"
        tg = self.target_graph.get("domains")
        if isinstance(tg, dict) and tg:
            return tg, "target_graph.json"
        return {}, "blueprint.json"

    def _req_domains(self):
        doms = self.requirements.get("domains")
        return doms if isinstance(doms, dict) else {}

    def _iter_requirements(self):
        """Yield (domain_name, req_id, req_dict) over the requirements graph."""
        for dname, ddata in self._req_domains().items():
            reqs = (ddata or {}).get("requirements", {})
            if not isinstance(reqs, dict):
                continue
            for rid, req in reqs.items():
                if isinstance(req, dict):
                    yield dname, rid, req

    def _all_data_access(self):
        """Union of every requirement's data_access targets (logical stores)."""
        acc = set()
        for _, _, req in self._iter_requirements():
            for d in req.get("data_access", []) or []:
                if str(d).strip():
                    acc.add(str(d))
        return acc

    def _all_dependencies(self):
        """Union of every requirement's declared dependencies."""
        deps = set()
        for _, _, req in self._iter_requirements():
            for d in req.get("dependencies", []) or []:
                if str(d).strip():
                    deps.add(str(d))
        return deps

    def _migration_mode(self):
        meta = self.requirements.get("metadata", {})
        return meta.get("migration_mode") if isinstance(meta, dict) else None

    # -- README.md ---------------------------------------------------------- #

    def render_readme(self):
        md = []
        md.append("# {0}".format(self.project))
        md.append("")
        md.append("> Generated by the anti-legacy DOCUMENT phase from the committed "
                  "pipeline artifacts (config, blueprint, requirements graph, target "
                  "graph). Edit the source artifacts and re-run `document`, not this "
                  "file by hand.")
        md.append("")

        # What the app does — derived from the domains it implements.
        md.append("## What this application does")
        md.append("")
        req_domains = self._req_domains()
        if req_domains:
            md.append("`{0}` is the modernized ({1}) replacement for the legacy "
                      "estate. It implements {2} business "
                      "domain{3}:".format(
                          self.project, self.stack, len(req_domains),
                          "" if len(req_domains) == 1 else "s"))
            md.append("")
            for dname in sorted(req_domains):
                ddata = req_domains[dname] or {}
                reqs = ddata.get("requirements", {})
                rcount = len(reqs) if isinstance(reqs, dict) else 0
                md.append("- **{0}** — {1} capabilit{2}".format(
                    dname, rcount, "y" if rcount == 1 else "ies"))
            md.append("")
        else:
            bdoms, _ = self._blueprint_domains()
            if bdoms:
                md.append("`{0}` is the modernized ({1}) application, organized "
                          "into {2} domain{3}: {4}.".format(
                              self.project, self.stack, len(bdoms),
                              "" if len(bdoms) == 1 else "s",
                              ", ".join("`{0}`".format(d) for d in sorted(bdoms))))
            else:
                md.append("`{0}` is the modernized ({1}) application.".format(
                    self.project, self.stack))
            md.append("")

        mode = self._migration_mode()
        if mode:
            md.append("Migration mode: **{0}**.".format(mode))
            md.append("")

        # Setup — derived from stack + database.
        md.append("## Setup")
        md.append("")
        md.append("This is a **{0}** application backed by **{1}**. High-level "
                  "setup:".format(self.stack, self.database))
        md.append("")
        for step in self._setup_steps():
            md.append("1. {0}".format(step))
        md.append("")
        md.append("See [DEPENDENCIES.md](DEPENDENCIES.md) for the external services, "
                  "datastores, and files this application requires, and "
                  "[ENVIRONMENTS.md](ENVIRONMENTS.md) for per-environment "
                  "configuration.")
        md.append("")

        # Run — overview only.
        md.append("## Run")
        md.append("")
        md.append("Overview (exact commands live in the build tooling, not this doc):")
        md.append("")
        for step in self._run_steps():
            md.append("- {0}".format(step))
        md.append("")

        md.append("## Architecture")
        md.append("")
        md.append("See [ARCHITECTURE.md](ARCHITECTURE.md) for the domain / service "
                  "/ package layout and boundaries.")
        md.append("")
        return "\n".join(md)

    def _setup_steps(self):
        stack = (self.stack or "").lower()
        db = self.database
        steps = []
        if "java" in stack or "kotlin" in stack:
            steps.append("Install a JDK matching the target (build tooling declares the exact version).")
            steps.append("Build with the project build tool (Maven/Gradle) to fetch dependencies.")
        elif "go" in stack:
            steps.append("Install the Go toolchain matching the module's `go` directive.")
            steps.append("Run `go mod download` to fetch dependencies.")
        elif "python" in stack:
            steps.append("Create a virtual environment and install dependencies from the lockfile.")
        elif "dotnet" in stack or "csharp" in stack:
            steps.append("Install the .NET SDK and run `dotnet restore`.")
        elif "typescript" in stack or "node" in stack or "javascript" in stack:
            steps.append("Install Node.js and run the package manager install step.")
        else:
            steps.append("Install the {0} toolchain and fetch dependencies.".format(self.stack))
        steps.append("Provision a **{0}** instance and apply the schema (see ARCHITECTURE.md).".format(db))
        steps.append("Set the per-environment configuration (see ENVIRONMENTS.md).")
        return steps

    def _run_steps(self):
        stack = (self.stack or "").lower()
        steps = []
        if "java" in stack or "kotlin" in stack:
            steps.append("Start the service via the build tool's run task or the packaged artifact (JAR).")
        elif "go" in stack:
            steps.append("Build the binary and run it, or `go run` the entrypoint.")
        elif "python" in stack:
            steps.append("Launch the application entrypoint inside the virtual environment.")
        elif "dotnet" in stack or "csharp" in stack:
            steps.append("Run the service with `dotnet run` or the published binary.")
        elif "typescript" in stack or "node" in stack or "javascript" in stack:
            steps.append("Run the package manager start script.")
        else:
            steps.append("Run the application entrypoint produced by the build.")
        steps.append("Confirm connectivity to **{0}** before serving traffic.".format(self.database))
        steps.append("Deploy to **{0}** using the artifacts from the deploy phase.".format(self.deployment_target))
        return steps

    # -- ARCHITECTURE.md ---------------------------------------------------- #

    def render_architecture(self):
        md = []
        md.append("# Architecture — {0}".format(self.project))
        md.append("")
        domains, source = self._blueprint_domains()
        md.append("> Derived from `{0}`. Target stack: **{1}**. Architecture style: "
                  "**{2}**.".format(source, self.stack, self.style))
        md.append("")

        if not domains:
            md.append("_No domains found in the blueprint or target graph — "
                      "architecture cannot be derived._")
            md.append("")
            return "\n".join(md)

        md.append("## Domains and packages")
        md.append("")
        md.append("| Domain | Package | Components | Entities |")
        md.append("|---|---|---|---|")
        for dname in sorted(domains):
            d = domains[dname] or {}
            pkg = d.get("package", "_n/a_")
            comps = d.get("components", {})
            ents = d.get("entities", {}) or d.get("schema", {})
            ccount = len(comps) if isinstance(comps, dict) else 0
            ecount = len(ents) if isinstance(ents, dict) else 0
            md.append("| `{0}` | `{1}` | {2} | {3} |".format(dname, pkg, ccount, ecount))
        md.append("")

        # Per-domain detail: components (with type + API) and boundaries.
        for dname in sorted(domains):
            d = domains[dname] or {}
            md.append("## Domain: {0}".format(dname))
            md.append("")
            md.append("Package: `{0}`".format(d.get("package", "_n/a_")))
            md.append("")

            comps = d.get("components", {})
            if isinstance(comps, dict) and comps:
                md.append("### Components")
                md.append("")
                md.append("| Component | Type | API |")
                md.append("|---|---|---|")
                for cname in sorted(comps):
                    c = comps[cname] or {}
                    ctype = c.get("component_type") or c.get("type") or "_n/a_"
                    api = c.get("api")
                    if isinstance(api, dict) and api:
                        api_str = "`{0} {1}`".format(
                            api.get("method", "?"), api.get("path", "?"))
                    else:
                        api_str = "—"
                    md.append("| `{0}` | {1} | {2} |".format(cname, ctype, api_str))
                md.append("")

                # Boundaries: cross-component dependencies declared in the blueprint.
                edges = []
                for cname in sorted(comps):
                    c = comps[cname] or {}
                    for dep in c.get("dependencies", []) or []:
                        edges.append((cname, str(dep)))
                if edges:
                    md.append("### Boundaries (intra-domain dependencies)")
                    md.append("")
                    for src, dst in edges:
                        md.append("- `{0}` → `{1}`".format(src, dst))
                    md.append("")

            schema = d.get("schema", {})
            if isinstance(schema, dict) and schema:
                md.append("### Persistence")
                md.append("")
                md.append("Tables owned by this domain: {0}.".format(
                    ", ".join("`{0}`".format(t) for t in sorted(schema))))
                md.append("")

        # Build order, if the blueprint declared one.
        build_order = self.blueprint.get("build_order")
        if isinstance(build_order, list) and build_order:
            md.append("## Build order")
            md.append("")
            md.append("Components are built in dependency order:")
            md.append("")
            for i, rid in enumerate(build_order, 1):
                md.append("{0}. `{1}`".format(i, rid))
            md.append("")

        return "\n".join(md)

    # -- DEPENDENCIES.md ---------------------------------------------------- #

    def render_dependencies(self):
        """Infra-level (service / database / file) dependencies.

        Derived from the requirements graph's per-requirement `data_access`
        (the logical stores each capability touches) and `dependencies` (the
        capabilities it calls). This is explicitly NOT a code-level callgraph —
        it answers 'what must exist for this app to run', not 'which method
        calls which'.
        """
        md = []
        md.append("# Dependencies — {0}".format(self.project))
        md.append("")
        md.append("> Service-, database-, and file-level dependencies derived "
                  "from `requirements_graph.json` (`data_access` + "
                  "`dependencies`). Infrastructure-level, not a code callgraph.")
        md.append("")

        data_access = self._all_data_access()
        deps = self._all_dependencies()

        # 1. Database / datastore dependency.
        md.append("## Database")
        md.append("")
        if self.database:
            md.append("Primary datastore: **{0}**.".format(self.database))
        else:
            md.append("Primary datastore: _not configured_.")
        md.append("")

        # 2. Data stores / files touched (logical assets from data_access).
        md.append("## Data stores and files")
        md.append("")
        if data_access:
            md.append("Logical data assets the application reads or writes "
                      "(each maps to a table, file, or external store):")
            md.append("")
            md.append("| Asset | Accessed by (requirements) |")
            md.append("|---|---|")
            # Build asset -> requirement list mapping.
            asset_map = {}
            for _, rid, req in self._iter_requirements():
                for a in req.get("data_access", []) or []:
                    asset_map.setdefault(str(a), set()).add(str(rid))
            for asset in sorted(asset_map):
                reqs = ", ".join("`{0}`".format(r) for r in sorted(asset_map[asset]))
                md.append("| `{0}` | {1} |".format(asset, reqs))
            md.append("")
        else:
            md.append("_No data-access assets declared in the requirements graph._")
            md.append("")

        # 3. External / inter-service dependencies (capability dependencies).
        md.append("## Service dependencies")
        md.append("")
        if deps:
            md.append("Capabilities this application depends on (internal "
                      "inter-service / inter-requirement edges):")
            md.append("")
            for d in sorted(deps):
                md.append("- `{0}`".format(d))
            md.append("")
        else:
            md.append("_No inter-requirement service dependencies declared._")
            md.append("")

        # 4. Source-app provenance (where the legacy estate came from).
        source_apps = self.config.get("source_apps", [])
        if isinstance(source_apps, list) and source_apps:
            md.append("## Source-system provenance")
            md.append("")
            md.append("This application was modernized from:")
            md.append("")
            md.append("| Source app | Language |")
            md.append("|---|---|")
            for sa in source_apps:
                if isinstance(sa, dict):
                    md.append("| `{0}` | {1} |".format(
                        sa.get("name", "?"), sa.get("language", "?")))
            md.append("")

        return "\n".join(md)

    # -- ENVIRONMENTS.md ---------------------------------------------------- #

    def render_environments(self):
        md = []
        md.append("# Environments — {0}".format(self.project))
        md.append("")
        md.append("> Deployment targets and per-environment configuration "
                  "derived from `config.json`.")
        md.append("")

        md.append("## Deployment target")
        md.append("")
        md.append("Primary deployment platform: **{0}**.".format(self.deployment_target))
        md.append("")
        md.append("Backing datastore: **{0}**.".format(self.database))
        md.append("")

        md.append("## Environment ladder")
        md.append("")
        md.append("| Environment | Deployment target | Datastore | Notes |")
        md.append("|---|---|---|---|")
        for env in _DEFAULT_ENVIRONMENTS:
            if env == "local":
                target = "developer workstation"
                store = "local {0}".format(self.database)
                note = "Run the app directly; see README.md."
            elif env == "production":
                target = self.deployment_target
                store = "managed {0}".format(self.database)
                note = "Promote only after GATE_4_UAT sign-off."
            else:
                target = self.deployment_target
                store = "managed {0}".format(self.database)
                note = "Mirror of production; validate before promotion."
            md.append("| `{0}` | {1} | {2} | {3} |".format(env, target, store, note))
        md.append("")

        md.append("## Per-environment configuration")
        md.append("")
        md.append("Each environment supplies its own values for the following "
                  "configuration keys (do not hard-code them):")
        md.append("")
        for key in self._config_keys():
            md.append("- **{0}**".format(key))
        md.append("")

        md.append("## Setup per environment")
        md.append("")
        md.append("1. Provision a **{0}** instance and load the schema.".format(self.database))
        md.append("2. Set the configuration keys above for the environment.")
        md.append("3. Deploy the application artifact to **{0}**.".format(self.deployment_target))
        md.append("4. Run a smoke check confirming datastore connectivity.")
        md.append("")
        return "\n".join(md)

    def _config_keys(self):
        """Configuration keys every environment must supply.

        Database connection keys are always present; others are added when the
        config implies them (e.g. embeddings → embedding service endpoint).
        """
        keys = [
            "Database connection URL / host",
            "Database credentials",
            "Application listen port",
            "Log level",
        ]
        if self.config.get("embeddings"):
            keys.append("Embedding service endpoint")
        return keys


# --------------------------------------------------------------------------- #
# Manifest registration (reuses antilegacy_core.manifest helpers — single source of
# truth for the artifact-row shape, checksum, and audit append).
# --------------------------------------------------------------------------- #
def _register_artifact(manifest_path, artifact_id, doc_abs_path):
    """Register one produced doc as a manifest artifact (status=final).

    The registered `path` is stored relative to the manifest's `.anti-legacy/`
    anchor (using the SAME anchoring rule manifest.py applies), so the manifest's
    own integrity check (`manifest check`) resolves the file correctly even
    though the docs live OUTSIDE `.anti-legacy/`. Returns the stored path.
    """
    from antilegacy_core import manifest as mf  # noqa: E402

    m = mf.load_manifest(manifest_path)

    anti_legacy_dir = os.path.dirname(os.path.abspath(manifest_path))
    # manifest._artifact_full_path anchors a stored path P under .anti-legacy as
    # P (if P starts with '.anti-legacy') else join('.anti-legacy', P). To make
    # that resolve to doc_abs_path we store the path of doc relative to the
    # .anti-legacy dir, so join('.anti-legacy', rel) == doc_abs_path.
    rel = os.path.relpath(os.path.abspath(doc_abs_path), anti_legacy_dir)
    # Use forward slashes for cross-platform manifest stability.
    stored_path = rel.replace(os.sep, "/")

    checksum = mf.file_checksum(doc_abs_path)
    artifact = {
        "path": stored_path,
        "format": "markdown",
        "produced_by": "scripts/document.py",
        "status": "final",
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "depends_on": ["blueprint-json", "requirements-graph", "target_graph"],
    }
    if checksum:
        artifact["checksum"] = checksum

    m.setdefault("artifacts", {})[artifact_id] = artifact
    mf.save_manifest(m, manifest_path)

    # Append the audit event next to THIS manifest (manifest._append_audit is
    # cwd-relative and would target the wrong audit log when the manifest lives
    # outside the cwd). Append-only, one JSON object per line.
    audit_path = os.path.join(anti_legacy_dir, "audit.jsonl")
    if os.path.isdir(anti_legacy_dir):
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "anti-legacy:artifact-registered",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": {"artifact_id": artifact_id, "path": stored_path, "status": "final"},
            }) + "\n")
    return stored_path


def synthesize(config_path, blueprint_path, requirements_path, target_graph_path,
               target_dir_override=None, manifest_path=None, register=True):
    """Synthesize the four docs and (optionally) register them.

    Returns (target_dir, {artifact_id: abs_doc_path}). Raises ValueError when no
    target directory can be resolved (nowhere to write the docs).
    """
    config = _load_json(config_path)
    blueprint = _load_json(blueprint_path)
    requirements = _load_json(requirements_path)
    target_graph = _load_json(target_graph_path)

    target_dir = _resolve_target_dir(config, blueprint, target_graph, target_dir_override)
    if not target_dir:
        raise ValueError(
            "No target directory could be resolved (config.target_path, "
            "blueprint.target_path, target_graph.target_path all absent, and no "
            "--target-dir given). Cannot write target-app docs."
        )

    os.makedirs(target_dir, exist_ok=True)
    synth = DocSynthesizer(config, blueprint, requirements, target_graph)

    renderers = {
        "README.md": synth.render_readme,
        "ARCHITECTURE.md": synth.render_architecture,
        "DEPENDENCIES.md": synth.render_dependencies,
        "ENVIRONMENTS.md": synth.render_environments,
    }

    written = {}
    for filename, render in renderers.items():
        content = render()
        if not content.endswith("\n"):
            content += "\n"
        doc_path = os.path.join(target_dir, filename)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(content)
        artifact_id = DOC_ARTIFACT_IDS[filename]
        written[artifact_id] = doc_path
        if register and manifest_path and os.path.exists(manifest_path):
            _register_artifact(manifest_path, artifact_id, doc_path)

    return target_dir, written


def main():
    parser = argparse.ArgumentParser(
        prog="document",
        description="Synthesize target-app docs (README/ARCHITECTURE/DEPENDENCIES/"
                    "ENVIRONMENTS) from committed pipeline artifacts and register "
                    "them as manifest artifacts.",
    )
    parser.add_argument("--config", default=".anti-legacy/config.json",
                        help="Path to config.json")
    parser.add_argument("--blueprint", default=".anti-legacy/requirements/blueprint.json",
                        help="Path to blueprint.json")
    parser.add_argument("--requirements", default=".anti-legacy/requirements/requirements_graph.json",
                        help="Path to requirements_graph.json")
    parser.add_argument("--target-graph", default=".anti-legacy/target_graph.json",
                        help="Path to target_graph.json")
    parser.add_argument("--target-dir", default=None,
                        help="Override directory to write docs into (else config.target_path)")
    parser.add_argument("--manifest", default=".anti-legacy/manifest.json",
                        help="Path to manifest.json (artifacts are registered here)")
    parser.add_argument("--no-register", action="store_true",
                        help="Write docs but do not register them in the manifest")
    args = parser.parse_args()

    try:
        target_dir, written = synthesize(
            config_path=args.config,
            blueprint_path=args.blueprint,
            requirements_path=args.requirements,
            target_graph_path=args.target_graph,
            target_dir_override=args.target_dir,
            manifest_path=args.manifest,
            register=not args.no_register,
        )
    except ValueError as e:
        print("Error: {0}".format(e), file=sys.stderr)
        sys.exit(1)

    print("Target-app docs written to: {0}".format(target_dir))
    for artifact_id, path in sorted(written.items()):
        print("  {0:>18}  {1}".format(artifact_id, path))
    if not args.no_register and os.path.exists(args.manifest):
        print("Registered {0} doc artifact(s) in {1}".format(len(written), args.manifest))


if __name__ == "__main__":
    main()
