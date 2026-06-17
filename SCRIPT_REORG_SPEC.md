# Script Reorganization Spec — Namespaced Python Packages

**Status**: Proposed  
**Author**: Antigravity (specced from conversation with Mike P.)  
**Date**: 2026-06-16  

---

## Problem

All 21 scripts live in a flat `scripts/` directory. Generic names (`coverage.py`,
`manifest.py`, `extract.py`, `document.py`) risk collision with Python stdlib and
popular packages when added to `sys.path`. The flat layout also makes ownership
unclear — which skill owns which script?

## Solution

Reorganize scripts into **namespaced Python packages** grouped by domain. Each
package lives inside a skill directory, making ownership explicit and imports
collision-proof.

---

## Package Design

### Three library packages

```
skills/
  wicked-estate/                      # Code graph engine
    wicked_estate/                    # Python package
      __init__.py
      engine.py                       # was wicked_estate.py  (3,288 LOC)
      coverage.py                     # was coverage.py       (807 LOC)
      extract.py                      # was extract.py        (893 LOC)
      vocabulary.py                   # was vocabulary.py     (992 LOC)
    SKILL.md                          # documents the package API

  domain-graph/                       # Requirements graph layer
    domain_graph/                     # Python package
      __init__.py
      builder.py                      # was domain_graph.py   (1,707 LOC)
      normalizer.py                   # was graph_normalizer.py (540 LOC)
      comparator.py                   # was compare_graphs.py (713 LOC)
    SKILL.md

  pipeline/                           # Pipeline state machine
    pipeline/                         # Python package
      __init__.py
      manifest.py                     # was manifest.py       (636 LOC)
      validator.py                    # was validator_discovery.py (988 LOC)
      planner.py                      # was planner_utils.py  (298 LOC)
    SKILL.md
```

### Single-owner scripts → owning skill

These scripts have zero cross-imports and are used by exactly one skill.
They move into their owning skill's `scripts/` directory (no package needed).

```
skills/
  final-review/scripts/completeness_scanner.py       (660 LOC)
  document/scripts/document.py                       (687 LOC)
  functional-tests/scripts/functional_tests.py       (440 LOC)
  semantic-validation/scripts/semantic_validator.py   (422 LOC)
  semantic-join/scripts/semantic_join.py              (556 LOC)
  analyze/scripts/detect_dead_ends.py                 (492 LOC)
  target-review/scripts/generate_target_graph.py      (487 LOC)
  target-review/scripts/test_runner.py                (621 LOC)
  review-packet/scripts/packet_generator.py           (154 LOC)
  setup/scripts/git_brain.py                          (1,037 LOC)
  develop-plugin/scripts/learn_coordinator.py         (317 LOC)
```

---

## Import Changes

### Before (collision-prone)

```python
# scripts/domain_graph.py
sys.path.insert(0, os.path.dirname(__file__))
import wicked_estate   # could collide
import coverage        # DOES collide with pytest-cov
import vocabulary
```

### After (namespaced)

```python
# skills/domain-graph/domain_graph/builder.py
from wicked_estate import engine
from wicked_estate import coverage
from wicked_estate import vocabulary
```

### Cross-package import resolution

Each library package's `__init__.py` is empty (namespace marker only).
The packages resolve because `run.py` adds the **package parent directories**
(not the package directories themselves) to `PYTHONPATH`:

```python
# run.py adds these to PYTHONPATH:
#   skills/wicked-estate/    → so `import wicked_estate` finds the package
#   skills/domain-graph/     → so `import domain_graph` finds the package
#   skills/pipeline/         → so `import pipeline` finds the package
```

This is **safe** because:
- `wicked_estate` is not a real Python package (no collision)
- `domain_graph` is not a real Python package (no collision)
- `pipeline` is not a real Python package (no collision)
- We add exactly 3 specific directories, not a wildcard glob

---

## run.py Changes

The dispatcher needs two updates:

### 1. Script discovery (find the file)

```python
#!/usr/bin/env python3
import os, sys, subprocess, glob

PLUGIN_ROOT = r"__PLUGIN_ROOT__"

# --- Package parent dirs (for cross-package imports) ---
_LIB_SKILLS = ['wicked-estate', 'domain-graph', 'pipeline']
_LIB_ROOTS = [os.path.join(PLUGIN_ROOT, 'skills', s) for s in _LIB_SKILLS]

# --- Stem → file resolution ---
# Priority: library packages first, then skill-local scripts, then legacy scripts/
_STEM_MAP = None

def _build_stem_map():
    """Build a stem → absolute-path mapping. Called once, cached."""
    global _STEM_MAP
    _STEM_MAP = {}

    # 1. Library packages: stem maps to package.module
    _PKG_MODULES = {
        # wicked-estate package
        'wicked_estate': ('wicked_estate', 'engine'),
        'coverage':      ('wicked_estate', 'coverage'),
        'extract':       ('wicked_estate', 'extract'),
        'vocabulary':    ('wicked_estate', 'vocabulary'),
        # domain-graph package
        'domain_graph':     ('domain_graph', 'builder'),
        'graph_normalizer': ('domain_graph', 'normalizer'),
        'compare_graphs':   ('domain_graph', 'comparator'),
        # pipeline package
        'manifest':            ('pipeline', 'manifest'),
        'validator_discovery': ('pipeline', 'validator'),
        'planner_utils':       ('pipeline', 'planner'),
    }

    for stem, (pkg, mod) in _PKG_MODULES.items():
        for lib_root in _LIB_ROOTS:
            candidate = os.path.join(lib_root, pkg, mod + '.py')
            if os.path.isfile(candidate):
                _STEM_MAP[stem] = candidate
                break

    # 2. Skill-local scripts
    for skill_scripts in glob.glob(os.path.join(PLUGIN_ROOT, 'skills', '*', 'scripts')):
        for f in glob.glob(os.path.join(skill_scripts, '*.py')):
            stem_name = os.path.basename(f)[:-3]
            if stem_name not in _STEM_MAP:
                _STEM_MAP[stem_name] = f

    # 3. Legacy fallback: scripts/ (during migration)
    legacy_dir = os.path.join(PLUGIN_ROOT, 'scripts')
    if os.path.isdir(legacy_dir):
        for f in glob.glob(os.path.join(legacy_dir, '*.py')):
            stem_name = os.path.basename(f)[:-3]
            if stem_name not in _STEM_MAP:
                _STEM_MAP[stem_name] = f
```

### 2. PYTHONPATH injection (for cross-imports)

```python
def _run(stem, args):
    if _STEM_MAP is None:
        _build_stem_map()

    if stem not in _STEM_MAP:
        sys.stderr.write(f'unknown script: {stem}\n')
        sys.exit(2)

    script = _STEM_MAP[stem]

    # Build PYTHONPATH: library package roots + script's own dir
    extra_paths = _LIB_ROOTS + [os.path.dirname(script)]
    env = os.environ.copy()
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = os.pathsep.join(extra_paths + ([existing] if existing else []))

    result = subprocess.run(
        [sys.executable, script] + args,
        env=env
    )
    sys.exit(result.returncode)
```

---

## Test Migration

### Current pattern (per-test sys.path injection)

```python
# tests/test_coverage.py
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)
import coverage  # ← collision risk
```

### New pattern (conftest.py + namespaced imports)

Create `tests/conftest.py`:

```python
import os, sys, glob

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Add library package parent dirs
for skill in ['wicked-estate', 'domain-graph', 'pipeline']:
    path = os.path.join(_ROOT, 'skills', skill)
    if path not in sys.path:
        sys.path.insert(0, path)

# Add all skill script dirs (for single-owner scripts)
for d in glob.glob(os.path.join(_ROOT, 'skills', '*', 'scripts')):
    if d not in sys.path:
        sys.path.insert(0, d)

# Legacy fallback during migration
legacy = os.path.join(_ROOT, 'scripts')
if os.path.isdir(legacy) and legacy not in sys.path:
    sys.path.insert(0, legacy)
```

Then update each test file:

```python
# Before:
import coverage

# After:
from wicked_estate import coverage
```

### Two import styles in tests

| Script type | Import style |
|-------------|-------------|
| Library package module | `from wicked_estate import coverage` |
| Skill-local script | `import completeness_scanner` (unchanged — conftest adds path) |

---

## SKILL.md Dispatch Changes

### Current (219 dispatch calls across all skills)

```bash
python3 .anti-legacy/run.py manifest status
python3 .anti-legacy/run.py coverage --db ...
python3 .anti-legacy/run.py wicked_estate stats --db ...
```

### After — NO CHANGE

`run.py` still resolves stems. Skills don't need updating. The stem `coverage`
resolves to `skills/wicked-estate/wicked_estate/coverage.py` instead of
`scripts/coverage.py`. Same CLI, same args, same output.

This is the key design decision: **`run.py` is the stable interface**. Skills
talk to stems, not file paths. The reorganization is invisible to skills.

---

## Migration Plan

### Phase 0: Prep (no code moves)
1. Create `tests/conftest.py` with the path setup above
2. Add legacy `scripts/` fallback to both conftest and run.py
3. Run all 653 tests — must pass with zero changes
4. Commit: `chore: add conftest.py with path discovery`

### Phase 1: Library packages (the hard part)

Move in dependency order — leaves first, roots last:

**Batch 1a: `pipeline` package** (no internal cross-imports)
1. Create `skills/pipeline/pipeline/__init__.py`
2. Move `manifest.py` → `skills/pipeline/pipeline/manifest.py`
3. Move `validator_discovery.py` → `skills/pipeline/pipeline/validator.py`
4. Move `planner_utils.py` → `skills/pipeline/pipeline/planner.py`
5. Update internal imports (validator imports manifest: `from pipeline import manifest`)
6. Update affected tests (3 files)
7. Run full suite — 653 pass

**Batch 1b: `wicked_estate` package** (the biggest — 5 importers)
1. Create `skills/wicked-estate/wicked_estate/__init__.py`
2. Move `wicked_estate.py` → `skills/wicked-estate/wicked_estate/engine.py`
3. Move `coverage.py` → `skills/wicked-estate/wicked_estate/coverage.py`
4. Move `extract.py` → `skills/wicked-estate/wicked_estate/extract.py`
5. Move `vocabulary.py` → `skills/wicked-estate/wicked_estate/vocabulary.py`
6. Update all `import wicked_estate` → `from wicked_estate import engine`
7. Update all `import coverage` → `from wicked_estate import coverage`
8. Update affected tests (16 files)
9. Run full suite — 653 pass

**Batch 1c: `domain_graph` package** (depends on wicked_estate)
1. Create `skills/domain-graph/domain_graph/__init__.py`
2. Move `domain_graph.py` → `skills/domain-graph/domain_graph/builder.py`
3. Move `graph_normalizer.py` → `skills/domain-graph/domain_graph/normalizer.py`
4. Move `compare_graphs.py` → `skills/domain-graph/domain_graph/comparator.py`
5. Update internal imports
6. Update affected tests (8 files)
7. Run full suite — 653 pass

### Phase 2: Skill-local scripts (easy — just move files)
1. Create `scripts/` subdirectory in each owning skill
2. Move each leaf script to its owner
3. No import changes needed (these have zero cross-imports)
4. Run full suite after each move

### Phase 3: Cleanup
1. Delete empty `scripts/` directory
2. Remove legacy fallback from `run.py` and `conftest.py`
3. Update `run.py` — remove the `_PKG_MODULES` hardcoded map, replace with
   auto-discovery if desired
4. Final test run — 653 pass
5. Commit: `chore: remove legacy scripts/ directory`

---

## Backward Compatibility

During migration, the legacy `scripts/` directory coexists with the new
package layout. `run.py` checks packages first, then `scripts/` as fallback.
This means:

- Skills never break (stems resolve throughout)
- Tests never break (conftest adds both paths)
- You can migrate one batch at a time across multiple PRs
- Rollback = `git revert` the batch commit

---

## File Count Summary

| Category | Files | LOC |
|----------|-------|-----|
| Library packages (3) | 10 scripts | 9,862 |
| Skill-local scripts | 11 scripts | 6,873 |
| **Total** | **21 scripts** | **16,735** |
| Tests to update | ~25 files | import lines only |
| Skills to update | 0 (run.py is stable interface) | — |
| New files | 4 (`__init__.py` × 3 + `conftest.py`) | ~30 |

---

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Import collision during migration | LOW | HIGH | Legacy fallback ensures both paths work |
| Test breakage | MEDIUM | LOW | conftest.py + batch migration with full suite after each |
| SKILL.md breakage | NONE | — | run.py stems unchanged |
| Merge conflicts with parallel work | MEDIUM | MEDIUM | Do in a feature branch, merge when clean |

---

## Definition of Done

- [ ] All 21 scripts moved out of `scripts/`
- [ ] 3 library packages with `__init__.py`
- [ ] 11 skill-local scripts in owning skill's `scripts/`
- [ ] `run.py` updated with package-aware dispatch
- [ ] `tests/conftest.py` handles all path setup
- [ ] All 653 tests pass
- [ ] `scripts/` directory deleted
- [ ] No `sys.path.insert` in any test file (all handled by conftest)
- [ ] No `sys.path.insert` pointing at `scripts/` in any script (all via PYTHONPATH)
