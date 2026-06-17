# Consolidated Restructure Spec — Portable Skills Bundle + `antilegacy_core` Library

**Status**: Approved  
**Date**: 2026-06-16 (consolidated with amendment)  
**Supersedes**: `SCRIPT_REORG_SPEC.md`, `SKILLS_PORTABILITY_REVIEW.md`, `RESTRUCTURE_SPEC.AMENDMENT.md`  
**Contributors**: Antigravity (original spec), Claude (portability review + amendment), Mike P. (design decisions)

---

## 1. Goal

Restructure the anti-legacy plugin from an Antigravity-first layout with a flat
`scripts/` directory into a **portable skills bundle** backed by a **namespaced
Python library**. The result installs with `npx skills add --all` on any CLI,
resolves the Python import-collision risk, and makes ownership of every file
explicit.

**Portability is at the bundle level.** The shared core (~10.8k LOC + schemas)
cannot be made copy-portable per-skill without untenable duplication. The bundle
is the unit of install. The only supported install is `--all`.

---

## 2. Design Principles

1. **A skill is a skill.** `SKILL.md` + instructions + assets. Not a package host — unless the skill earns its place on content alone.
2. **A library is a library.** Shared Python code lives in `antilegacy_core/`, namespaced and collision-proof.
3. **`run.py` is the stable seam.** 219 dispatch calls in SKILL.md files don't change.
4. **Descriptions route, the seam enforces.** Skill descriptions trigger routing; `run.py` runs a deterministic preflight.
5. **`npx skills add --all` is the install story.** skills.sh symlinks skill directories (entire subtrees). The library ships inside a skill, delivered automatically. No `pip install -e` at runtime. Two prereqs total: `npx skills` + `cargo install wicked-estate`.

---

## 3. Target Layout

```
anti-legacy/
  plugin.json                         # + portable skills manifest
  gemini-extension.json               # keep (dual-publish)
  AGENTS.md  +  GEMINI.md→  +  CLAUDE.md→     # symlink trick (keep)

  skills/
    # ── Core library host (the internals SME) ──
    anti-legacy-expert/
      SKILL.md                        # "use when: understanding/operating the pipeline core,
                                      #  diagnosing a core module, extending antilegacy_core"
                                      # teaches: estate seam contract, domain_graph build,
                                      # resolved-or-flagged coverage model, manifest state machine,
                                      # validator gate logic
      scripts/
        antilegacy_core/              # ── the shared Python library ──
          __init__.py                 # exposes preflight(), __version__
          estate.py                   # was scripts/wicked_estate.py  (3,288 LOC)
          coverage.py                 # was scripts/coverage.py       (807 LOC)
          extract.py                  # was scripts/extract.py        (893 LOC)
          vocabulary.py               # was scripts/vocabulary.py     (992 LOC)
          domain_graph.py             # was scripts/domain_graph.py   (1,707 LOC)
          normalizer.py               # was scripts/graph_normalizer.py (540 LOC)
          comparator.py               # was scripts/compare_graphs.py (713 LOC)
          manifest.py                 # was scripts/manifest.py       (636 LOC)
          validator.py                # was scripts/validator_discovery.py (988 LOC)
          planner.py                  # was scripts/planner_utils.py  (298 LOC)
          schemas/                    # package data (importlib.resources)
            audit-event.schema.json
            evidence-envelope.schema.json
            manifest.schema.json
            requirements-graph.schema.json
            requirements-graph.enriched.schema.json
            vocabulary.schema.json
        pyproject.toml                # local dev/test convenience ONLY (not runtime)

    # ── Engine skill (the external wicked-estate seam) ──
    wicked-estate/
      SKILL.md                        # "use when indexing, querying, annotating the code graph"
                                      # teaches: binary resolution, provenance guarantees,
                                      # COBOL+modern in one pass, annotate semantics
                                      # checks: engine availability, version ≥ 0.5.1

    # ── Agent-to-skill pivots ──
    developer/
      SKILL.md                        # was agents/developer.md (no model pin)
      assets/
        ImplementsRule.java           # was templates/
        ImplementsRules.java
    uat-reviewer/
      SKILL.md                        # was agents/uat_reviewer.md (no model pin)

    # ── Phase skills (own their leaf scripts) ──
    setup/
      SKILL.md
      assets/
        run.py.tmpl                   # was templates/run.py
        manifest.json                 # was templates/manifest.json
      references/
        traversal_strategies.md       # was templates/ (deduped — root copy removed)
    analyze/
      SKILL.md
      scripts/
        detect_dead_ends.py           # was scripts/ (492 LOC)
    blueprint/
      SKILL.md
      references/
        anti_patterns.md              # was templates/
        nfrs.md                       # was templates/
    convert/            SKILL.md
    deploy/             SKILL.md
    develop-plugin/
      SKILL.md
      scripts/
        learn_coordinator.py          # was scripts/ (317 LOC)
    document/
      SKILL.md
      scripts/
        document.py                   # was scripts/ (687 LOC)
    extraction/
      SKILL.md
      references/                     # existing (decomposition.md, writing-standard.md, etc.)
    final-review/
      SKILL.md
      scripts/
        completeness_scanner.py       # was scripts/ (660 LOC)
    functional-tests/
      SKILL.md
      scripts/
        functional_tests.py           # was scripts/ (440 LOC)
    gatekeeper/         SKILL.md
    graph-translator/   SKILL.md
    orchestrate/        SKILL.md
    planner/            SKILL.md
    review-packet/
      SKILL.md
      scripts/
        packet_generator.py           # was scripts/ (154 LOC)
    semantic-join/
      SKILL.md
      scripts/
        semantic_join.py              # was scripts/ (556 LOC)
    semantic-validation/
      SKILL.md
      scripts/
        semantic_validator.py         # was scripts/ (422 LOC)
    survey/             SKILL.md
    survey-modern/      SKILL.md      # retired stub (keep for redirect)
    swarm/              SKILL.md
    target-review/
      SKILL.md
      scripts/
        generate_target_graph.py      # was scripts/ (487 LOC)
        test_runner.py                # was scripts/ (621 LOC)
    test-strategy/      SKILL.md
    uat-crew/           SKILL.md
    vocabulary/         SKILL.md

  # ── DELETED after migration ──
  # scripts/            → split into antilegacy_core + skill-local scripts
  # templates/          → folded into owning skills
  # agents/             → pivoted to skills
  # schemas/            → package data inside antilegacy_core
```

---

## 4. The `antilegacy_core` Library

### 4.1 What goes in

**Rule: the cross-importing core + its shared dependencies.** If a module
imports other library modules (estate, coverage, vocabulary), it belongs in
the library regardless of its consumer count — it can't be skill-local without
breaking imports. Everything with zero cross-imports stays skill-local.

| Module | Was | LOC | Consumers |
|--------|-----|-----|-----------|
| `estate.py` | `wicked_estate.py` | 3,288 | 5 scripts, 9 tests, `wicked-estate` skill |
| `coverage.py` | `coverage.py` | 807 | 4 scripts, 5 tests |
| `extract.py` | `extract.py` | 893 | 1 script, 1 test |
| `vocabulary.py` | `vocabulary.py` | 992 | 2 scripts, 1 test |
| `domain_graph.py` | `domain_graph.py` | 1,707 | 0 scripts, 1 test |
| `normalizer.py` | `graph_normalizer.py` | 540 | 0 scripts, 5 tests |
| `comparator.py` | `compare_graphs.py` | 713 | 0 scripts, 2 tests |
| `manifest.py` | `manifest.py` | 636 | 3 scripts, 1 test |
| `validator.py` | `validator_discovery.py` | 988 | 0 scripts, 3 tests |
| `planner.py` | `planner_utils.py` | 298 | 1 script, 2 tests |
| **Total** | | **10,862** | |

### 4.2 Where it lives

Inside `skills/anti-legacy-expert/scripts/antilegacy_core/`. skills.sh
symlinks skill directories as entire subtrees, so the library ships with
the skill automatically. No `pip install -e` at runtime.

**Why `anti-legacy-expert` is a real skill, not a stub:** Its `SKILL.md` is
the user manual for the core — how the requirements graph is built, how
coverage terminates resolved-or-flagged, the manifest/gate state machine, the
estate seam contract. Documentation co-located with the code it documents.
It *hosts* the package; it isn't *defined by* hosting it.

**Relationship to `wicked-estate` skill:** Separate skills with different
routing intents. `wicked-estate` = external engine capability (binary
resolution, version floor, polyglot indexing). `anti-legacy-expert` = internal
core SME (operating/extending the pipeline). May merge later if triggers prove
redundant; default is sibling.

### 4.3 Schemas as package data

The 6 JSON schemas ship inside `antilegacy_core/schemas/` and are accessed via
`importlib.resources`:

```python
from importlib import resources
schema_text = resources.files("antilegacy_core.schemas").joinpath(
    "manifest.schema.json"
).read_text()
```

**Consumers**: `manifest.py`, `domain_graph.py`, `vocabulary.py` (scripts) +
`graph-translator`, `orchestrate`, `vocabulary` (skills via `run.py`).

Single source of truth. Versioned with the code. No loose top-level `schemas/`.

### 4.4 `pyproject.toml`

```toml
[project]
name = "antilegacy-core"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["jsonschema"]

[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]

[tool.setuptools.package-data]
"antilegacy_core" = ["schemas/*.json"]
```

**Local dev/test convenience only.** Contributors can `pip install -e` in their
venv for IDE support. This is NOT part of the runtime install — `run.py`
handles path resolution for dispatched scripts.

### 4.5 Import examples

```python
# Before (collision-prone):
sys.path.insert(0, scripts_dir)
import coverage        # collides with pytest-cov
import wicked_estate   # ambiguous — the engine or the shim?

# After (namespaced):
from antilegacy_core import coverage
from antilegacy_core import estate
from antilegacy_core.estate import index_repo
```

### 4.6 `preflight()`

`antilegacy_core.__init__` exposes a `preflight()` function:

```python
def preflight() -> list[str]:
    """Return a list of error strings. Empty = ready."""
    errors = []
    # 1. wicked-estate binary resolvable + version ≥ 0.5.1
    # 2. jsonschema importable
    # 3. workspace initialized (run.py + manifest exist)
    # 4. antilegacy_core importable (already true if you're here)
    return errors
```

Called by `run.py` on every dispatch. Fail-fast with exact remediation.

> **Conscious accept (no `doctor` skill):** Non-engine setup failures
> (missing `jsonschema`, uninitialized workspace) get only the terse seam
> error from `preflight()`, not a rich discoverable skill. This is acceptable
> because those failures are simple ("run `pip install`", "run `setup`").
> If richer non-engine diagnosis is ever needed, add a `doctor` skill then.

---

## 5. `run.py` — Probe-Based Resolution + PYTHONPATH

`run.py` stays the thin stable seam. It resolves stems by **probing** — no
hardcoded resolution tables. It adds **one** directory to `PYTHONPATH` for the
bundled `antilegacy_core` package.

```python
#!/usr/bin/env python3
"""Dispatch a script by stem name. Written by anti-legacy:setup."""
import os, sys, subprocess, glob, importlib.util

PLUGIN_ROOT = r"__PLUGIN_ROOT__"

# The one path for the bundled antilegacy_core package
_CORE_PATH = os.path.join(
    PLUGIN_ROOT, 'skills', 'anti-legacy-expert', 'scripts'
)

def _resolve(stem):
    """Resolve a stem to a script path. Probe order:
    1. antilegacy_core module  (import by name)
    2. skill-local scripts     (glob skills/*/scripts/)
    3. legacy fallback         (scripts/ during migration)
    """
    # Ensure the core is importable for probing
    if _CORE_PATH not in sys.path:
        sys.path.insert(0, _CORE_PATH)

    # 1. Library module
    spec = importlib.util.find_spec(f"antilegacy_core.{stem}")
    if spec and spec.origin:
        return ('module', f"antilegacy_core.{stem}")

    # 2. Skill-local scripts
    for candidate in glob.glob(
        os.path.join(PLUGIN_ROOT, 'skills', '*', 'scripts', stem + '.py')
    ):
        return ('script', candidate)

    # 3. Legacy fallback (remove after migration)
    legacy = os.path.join(PLUGIN_ROOT, 'scripts', stem + '.py')
    if os.path.isfile(legacy):
        return ('script', legacy)

    return None

def main():
    if len(sys.argv) < 2:
        sys.stderr.write('usage: run.py <script-stem> [args...]\n')
        sys.exit(2)

    # Preflight
    if _CORE_PATH not in sys.path:
        sys.path.insert(0, _CORE_PATH)
    try:
        from antilegacy_core import preflight
        errors = preflight()
        if errors:
            sys.stderr.write('preflight failed:\n')
            for e in errors:
                sys.stderr.write(f'  ✗ {e}\n')
            sys.exit(1)
    except ImportError:
        pass  # library not yet installed (migration in progress)

    stem = sys.argv[1]
    resolved = _resolve(stem)
    if not resolved:
        sys.stderr.write(f'unknown script: {stem}\n')
        sys.exit(2)

    kind, target = resolved

    # Build env with PYTHONPATH for the bundled core
    env = os.environ.copy()
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = os.pathsep.join(
        [_CORE_PATH] + ([existing] if existing else [])
    )

    if kind == 'module':
        result = subprocess.run(
            [sys.executable, '-m', target] + sys.argv[2:], env=env
        )
    else:
        result = subprocess.run(
            [sys.executable, target] + sys.argv[2:], env=env
        )
    sys.exit(result.returncode)

if __name__ == '__main__':
    main()
```

**Key properties:**
- **One** PYTHONPATH entry — `skills/anti-legacy-expert/scripts/` — namespaced under `antilegacy_core`, collision-proof
- No hardcoded stem map — probes the filesystem
- Library stems dispatch via `python -m antilegacy_core.<stem>` (intra-package imports resolve)
- Skill-local stems get the same PYTHONPATH, so they can `from antilegacy_core import ...`
- Legacy fallback during migration — remove in cleanup phase
- Preflight runs on every dispatch — cannot be skipped
- All 219 SKILL.md dispatch calls unchanged

---

## 6. The `wicked-estate` Skill

The engine capability skill. NOT a library wrapper — it **teaches**
the engine's contract and handles availability.

```yaml
name: "anti-legacy:wicked-estate"
description: >
  Discovery, diagnosis, and documentation for the wicked-estate code graph
  engine. Use when: indexing a codebase, querying the code graph, checking
  engine availability, understanding the annotate semantics, or diagnosing
  engine errors.
```

**Content:**
- Binary resolution order: `config.json wicked_estate_path` → `WICKED_ESTATE_PATH` env → `PATH`
- Version requirement: ≥ 0.5.1
- The "never silently degrades — fails fast with the install command" contract
- `file`/`line` provenance guarantee
- COBOL + modern indexed in one pass (wicked-estate is polyglot)
- Annotate semantics (requirement field, confidence, provenance)
- Troubleshooting: common errors, env setup, cargo install

**Why this earns skill status:** There's real subtlety — binary resolution,
provenance guarantees, the polyglot indexing contract, annotate semantics.
It's not a thin wrapper; it's the engine's user manual inside the pipeline.

---

## 7. Agent → Skill Pivots

### `developer` → `skills/developer/SKILL.md`

Keep the content verbatim (COMP-3 precision rules, 7 rules, completion
criteria, output format). Change the delivery:

- **Drop** `model: gemini-2.0-flash` (advisory line in the body at most)
- **Drop** `tools:` Gemini agent schema
- `swarm` invokes it as a skill/sub-task — the existing inline fallback
  (`swarm/SKILL.md:166`) **becomes the primary path**
- `@developer` becomes one optional Antigravity accelerator, not the default
- Bundle `ImplementsRule.java` / `ImplementsRules.java` as `assets/`

### `uat_reviewer` → `skills/uat-reviewer/SKILL.md`

Keep the content verbatim (READ-ONLY + JSON verdict contract). Change delivery:

- **Drop** `model: gemini-2.0-flash`
- Isolation preserved by *what context you pass*, not by the runtime
- On hosts with first-class isolated-subagent concepts, optionally also ship
  a host-native read-only agent — the portable skill is the floor

### Update consumers

These skills reference `@developer` / `@uat_reviewer`:
- `swarm/SKILL.md` → update to reference `skills/developer/SKILL.md`
- `uat-crew/SKILL.md` → update to reference `skills/uat-reviewer/SKILL.md`
- `orchestrate/SKILL.md` → update agent dispatch references
- `convert/SKILL.md` → update agent dispatch references

---

## 8. Template Ownership

All 7 templates are single-owner. Fold into owning skill:

| Template | Owner | Target location |
|----------|-------|----------------|
| `run.py` | setup | `skills/setup/assets/run.py.tmpl` |
| `manifest.json` | setup | `skills/setup/assets/manifest.json` |
| `ImplementsRule.java` | developer | `skills/developer/assets/` |
| `ImplementsRules.java` | developer | `skills/developer/assets/` |
| `anti_patterns.md` | blueprint | `skills/blueprint/references/` |
| `nfrs.md` | blueprint | `skills/blueprint/references/` |
| `traversal_strategies.md` | setup (copies it), gatekeeper (references it) | see below |

**`traversal_strategies.md` dedupe:** `TRAVERSAL_STRATEGIES.md` (root) and
`templates/traversal_strategies.md` are **identical** (same MD5). `setup`
copies the template into the workspace; `gatekeeper` references the root copy.
Resolution: keep one copy in `skills/setup/references/traversal_strategies.md`,
have `setup` copy it into the workspace, and update `gatekeeper` to reference
the workspace copy (which it should already be doing post-setup).

**Verify**: `develop-plugin` also references templates (2 hits). Confirm it's
consuming, not owning, before moving.

---

## 9. Normalize Skill `name:` Frontmatter

Current state (inconsistent):

| Pattern | Count | Examples |
|---------|-------|---------|
| `"anti-legacy:slug"` (quoted) | 20 | analyze, blueprint, deploy, ... |
| `anti-legacy:slug` (unquoted) | 2 | convert, ... |
| `slug` (NO PREFIX) | 1 | **orchestrate** ← latent bug |

**Fix**: enforce `"anti-legacy:<slug>"` (quoted, prefixed) everywhere.
The `orchestrate` missing prefix is a dispatch bug — fix first.

---

## 10. Migration Sequence

Portability wins first (low-risk), library move last (the hygiene contribution).

### Step 1: Normalize `name:` frontmatter
- Fix `orchestrate` → `"anti-legacy:orchestrate"`
- Normalize quoting on `convert`
- Full test suite
- **Commit**: `fix: normalize skill name frontmatter`

### Step 2: Fold templates into owning skills
- Create `assets/` and `references/` directories in owning skills
- Move 7 template files
- Dedupe `traversal_strategies.md` (delete root copy)
- Update SKILL.md references (relative paths)
- Full test suite
- **Commit**: `refactor: fold templates into owning skills`

### Step 3: Pivot agents to skills
- Create `skills/developer/SKILL.md` from `agents/developer.md` content
- Create `skills/uat-reviewer/SKILL.md` from `agents/uat_reviewer.md` content
- Move Java templates to `skills/developer/assets/`
- Drop `model: gemini-2.0-flash` pins
- Update `swarm`, `uat-crew`, `orchestrate`, `convert` references
- Full test suite + swarm dry-run
- **Commit**: `refactor: pivot agents to portable skills`

### Step 4: Stand up `antilegacy_core` inside `anti-legacy-expert`
Create the package at `skills/anti-legacy-expert/scripts/antilegacy_core/`.
Dependency order — leaves first, roots last. Legacy `scripts/` fallback
throughout.

**Batch 4a: Pipeline modules** (no cross-imports)
1. Create `skills/anti-legacy-expert/` with `SKILL.md`
2. Create `scripts/antilegacy_core/__init__.py`
3. Move `manifest.py` → `antilegacy_core/manifest.py`
4. Move `validator_discovery.py` → `antilegacy_core/validator.py`
5. Move `planner_utils.py` → `antilegacy_core/planner.py`
6. Update internal imports: `from antilegacy_core import manifest`
7. Update 3 test files
8. Full suite — must pass

**Batch 4b: Estate modules** (the biggest — 5 importers)
1. Move `wicked_estate.py` → `antilegacy_core/estate.py`
2. Move `coverage.py` → `antilegacy_core/coverage.py`
3. Move `extract.py` → `antilegacy_core/extract.py`
4. Move `vocabulary.py` → `antilegacy_core/vocabulary.py`
5. Update all `import wicked_estate` → `from antilegacy_core import estate`
6. Update all `import coverage` → `from antilegacy_core import coverage`
7. Update 16 test files
8. Full suite — must pass

**Batch 4c: Domain graph modules** (depends on estate)
1. Move `domain_graph.py` → `antilegacy_core/domain_graph.py`
2. Move `graph_normalizer.py` → `antilegacy_core/normalizer.py`
3. Move `compare_graphs.py` → `antilegacy_core/comparator.py`
4. Update internal imports
5. Update 8 test files
6. Full suite — must pass

**Batch 4d: Schemas as package data**
1. Move `schemas/*.json` → `antilegacy_core/schemas/`
2. Update 3 scripts to use `importlib.resources`
3. Update 3 skills that reference schema paths
4. Full suite — must pass

**Commit per batch**: `refactor(core): migrate <batch> to antilegacy_core`

### Step 5: Move skill-local scripts
1. Create `scripts/` in each owning skill (11 skills)
2. Move each leaf script
3. No import changes (zero cross-imports)
4. Full suite after each move
- **Commit**: `refactor: move leaf scripts to owning skills`

### Step 6: Create the `wicked-estate` skill
1. Write `skills/wicked-estate/SKILL.md`
2. Wire `preflight()` into `antilegacy_core.__init__`
3. Wire preflight call into `run.py`
4. Full suite
- **Commit**: `feat: add wicked-estate engine skill + preflight`

### Step 7: Update `run.py` template
1. Update `run.py` template with probe-based resolution + PYTHONPATH
2. `run.py` prepends `skills/anti-legacy-expert/scripts/` to PYTHONPATH
3. Library stems dispatch via `python -m antilegacy_core.<stem>`
4. No `pip install -e` in setup — path-based resolution only
5. Full suite
- **Commit**: `feat: run.py with probe-based resolution`

### Step 8: Cleanup
1. Delete `scripts/` directory
2. Delete `templates/` directory
3. Delete `agents/` directory
4. Delete `schemas/` directory
5. Delete root `TRAVERSAL_STRATEGIES.md` (deduped into setup)
6. Remove legacy fallback from `run.py`
7. Remove per-test `sys.path.insert` calls
8. Final test run — full suite green
9. Update README install instructions
- **Commit**: `chore: remove legacy directories`

---

## 11. Install Story

Two prerequisites, no pip step:

```bash
npx skills add mikeparcewski/anti-legacy --all   # skills + bundled antilegacy_core (any CLI)
cargo install wicked-estate                       # the Rust engine binary
```

skills.sh clones the repo, discovers `skills/*/SKILL.md`, and symlinks each
skill directory (entire subtree) into the agent's skills location. Because
`antilegacy_core/` lives inside `anti-legacy-expert/scripts/`, it ships
automatically.

On CLIs with native plugin install (Claude Code `/plugin install`, Gemini
extension), whole-repo install remains an equivalent path.

> **Guardrail: `--all` is mandatory.** skills.sh has no inter-skill dependency
> model. A partial `--skill <subset>` install that omits `anti-legacy-expert`
> breaks every `from antilegacy_core import ...`. The only supported install is
> `--all`. Document this prominently.

---

## 12. Backward Compatibility

During migration, legacy directories coexist with the new layout:

- `run.py` checks library → skill-local → `scripts/` (fallback)
- Tests use `conftest.py` for legacy path fallback **only during migration**
  (Steps 4-5). Once the package sits under `anti-legacy-expert/scripts/`,
  tests add that one path (via conftest or `pip install -e` locally for IDE
  support). Step 8 removes legacy path-adding. `conftest.py` is the bridge,
  not the target.
- Skills use stems via `run.py` — never break
- Rollback = `git revert` the batch commit

---

## 13. File Counts

| Category | Files | LOC |
|----------|-------|-----|
| `antilegacy_core` modules | 10 | 10,862 |
| `antilegacy_core` schemas | 6 | — |
| Skill-local scripts | 11 | 5,873 |
| Templates → skills | 7 | — |
| New skills | 2 (`wicked-estate`, `anti-legacy-expert`) | — |
| Agent → skill pivots | 2 (`developer`, `uat-reviewer`) | — |
| Tests to update | ~25 | import lines only |
| SKILL.md dispatch calls | 219 | **unchanged** |

---

## 14. Decisions Made

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | `antilegacy_core` library inside `anti-legacy-expert` skill | Skill earns its place as the internals SME. Library ships via skills.sh subtree symlink. No pip step. |
| 2 | Rename `wicked_estate.py` → `estate.py` | Disambiguates shim from external engine. `wicked-estate` skill handles the external seam. |
| 3 | No `doctor` skill | `wicked-estate` skill handles engine readiness. Preflight in `run.py` handles the rest. Gap consciously accepted (§4.6). |
| 4 | `npx skills add --all` + `cargo install wicked-estate` | Two prereqs, no pip. Verified live against this repo. `--all` mandatory (no inter-skill deps in skills.sh). |
| 5 | Enforce `"anti-legacy:<slug>"` naming | Consistent. Fixes `orchestrate` dispatch bug. |
| 6 | One PYTHONPATH entry, not pip install | `run.py` adds `skills/anti-legacy-expert/scripts/` — one dir, one namespaced package. Strictly better than `pip install -e` against a symlinked clone cache. |
| 7 | Probe-based resolution in `run.py` | No hardcoded stem map to maintain. |
| 8 | Bundle-level portability | Honest about the shared core. `--all` is the only supported install. |

---

## 15. Definition of Done

- [ ] All skill `name:` frontmatter normalized
- [ ] All 7 templates folded into owning skills
- [ ] `traversal_strategies.md` deduped (single copy in setup)
- [ ] Both agents pivoted to skills (no model pins)
- [ ] `anti-legacy-expert` skill with `SKILL.md` + `antilegacy_core/` package
- [ ] 10 library modules in `antilegacy_core/` with 6 schemas as package data
- [ ] 11 leaf scripts in owning skill `scripts/` directories
- [ ] `wicked-estate` skill written with engine docs + availability check
- [ ] `run.py` uses probe-based resolution + single PYTHONPATH entry + preflight
- [ ] All tests pass (import by name, no `sys.path` hacks)
- [ ] `scripts/`, `templates/`, `agents/`, `schemas/` directories deleted
- [ ] README install: `npx skills add --all` + `cargo install wicked-estate`

---

## 16. Open Decision

1. **Engine version floor.** Currently set to `≥ 0.5.1` based on the
   `annotate --replace` dependency (commit `54c3568`). Verify this is
   sufficient for all features called (clustering, bulk source, typed
   annotations, community partition). The plugin is currently at `0.6.0`;
   pinning too low lets an incompatible engine pass preflight.
