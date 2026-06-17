# Amendment to `RESTRUCTURE_SPEC.md` — Library Lives Inside a Skill (skills.sh delivery)

**Amends**: `RESTRUCTURE_SPEC.md` §3 (Target Layout), §4 (library home), §5 (`run.py`), §7/§10 (setup install), §15.1 (install mechanism)
**Date**: 2026-06-16
**Basis**: Live verification of `npx skills` against this repo (not inference).

---

## 1. The finding that drives this

`skills` (skills.sh) installs a skill by **symlinking the skill's directory** into each agent's skills location (default; `--copy` to copy). Symlinking a directory exposes its **entire subtree** — so any files nested under a skill, including a `scripts/<package>/` Python package, are delivered along with it.

Verified live:
```
$ npx skills add mikeparcewski/anti-legacy --list
◇ Cloning repository … ◇ Found 24 skills
  anti-legacy:analyze, anti-legacy:blueprint, anti-legacy:convert, …
```
It clones the repo, discovers the `skills/*/SKILL.md` dirs, and (on `add`) symlinks them per-agent, multi-CLI via `-a`.

**Consequence:** the earlier claim that `antilegacy_core` can't ship via skills.sh was wrong — it was true only because the spec placed the library as a **top-level sibling** of `skills/`. Nest the package *inside a skill* and skills.sh delivers it. This closes the §15.1 gap **without** a `pip install -e` step.

---

## 2. Amendment A — library home + the `anti-legacy-expert` skill

Move `antilegacy_core/` from a top-level sibling **into** a real skill:

```
skills/
  anti-legacy-expert/                 # real skill — the pipeline-internals SME
    SKILL.md                          # documents the core module contracts (real content):
                                      #   estate seam, domain_graph build, resolved-or-flagged
                                      #   coverage, the manifest state machine, validator.
                                      #   "Use when: understanding/operating the pipeline core,
                                      #    diagnosing a core module, extending antilegacy_core."
    scripts/
      antilegacy_core/                # ── the shared library, delivered by the dir symlink ──
        __init__.py                   # exposes preflight(), __version__
        estate.py  coverage.py  extract.py  vocabulary.py
        domain_graph.py  normalizer.py  comparator.py
        manifest.py  validator.py  planner.py
        schemas/*.json                # package data (importlib.resources)
      pyproject.toml                  # local dev/test convenience ONLY (see Amendment B)
```

**Why this is a real skill, not the §4 stub:** `anti-legacy-expert`'s `SKILL.md` is the user manual for the core — how the requirements graph is built, how coverage terminates resolved-or-flagged, the manifest/gate state machine, the estate seam contract. Documentation co-located with the code it documents. It *hosts* the package; it isn't *defined by* hosting it. (Contrast: a skill whose only reason to exist is the package — that's the stub we reject.)

**Relationship to the `wicked-estate` skill (spec §6):** keep them separate. `wicked-estate` is the *external engine* capability (binary resolution, version floor ≥ 0.5.1, polyglot indexing) with a concrete "indexing/querying the graph" trigger. `anti-legacy-expert` is the *internal core* SME with a "operating/extending the pipeline" trigger. Different routing intents. (They may be merged later if the triggers prove redundant; default is sibling.)

**Naming:** `anti-legacy-expert` (chosen). Rejected `anti-legacy-core` / `-internals` — "core" reads as *package*, reviving the skill-vs-package-host ambiguity §4 exists to kill; "expert" signals instruction-first, which is what keeps it a real skill.

---

## 3. Amendment B — drop `pip install -e`; `run.py` puts the bundled package on the path

Because the package is **delivered inside the bundle** at a known location, it does not need installing. `run.py` adds the one directory to the subprocess path and runs library stems as modules:

- Prepend `skills/anti-legacy-expert/scripts/` to `PYTHONPATH` for the dispatched subprocess.
- Library stem → `python -m antilegacy_core.<stem>` (intra-package imports resolve).
- Skill-local leaf stem → run the file directly (it gets the same `PYTHONPATH`, so it may `from antilegacy_core import …` too).
- `preflight()` import: `run.py` adds the path *first*, then `from antilegacy_core import preflight`.

Collision stays dead (everything is namespaced under `antilegacy_core`). Schemas via `importlib.resources.files("antilegacy_core.schemas")` resolve off the path with no install. `pyproject.toml` remains only for local dev/test (`pip install -e` in a contributor's venv); it is **not** part of the runtime install.

> **"Didn't we both reject PYTHONPATH?"** — We rejected the *original spec's* version: injecting **multiple** library-skill dirs + a hardcoded `_STEM_MAP`, for packages a copied skill couldn't import. This is different on every count: **one** directory, **no** map (probe), and the package is **shipped in the bundle** (not a missing dependency). With bundle-level portability already accepted, a single seam-managed path to a delivered, namespaced package is the clean choice — and it's strictly better than `pip install -e`, which against a *symlinked* skill dir resolves to skills.sh's transient clone cache.

---

## 4. Amendment C — §15.1 install mechanism, resolved

Install is now two prerequisites, no pip step:

```bash
npx skills add mikeparcewski/anti-legacy --all   # skills + the bundled antilegacy_core library (any CLI)
cargo install wicked-estate                       # the Rust engine binary (skills.sh can't ship a binary)
```

`wicked-estate` stays an external prereq (it's a separate Rust tool) — already documented in the README and enforced by `preflight()`. Everything else (24 skills + the full Python core + schemas) arrives in one `npx skills` command, multi-CLI. On CLIs with native plugin install (Claude Code / Antigravity), `/plugin install` or the Gemini extension remains an equivalent whole-repo path.

---

## 5. Guardrails (unchanged intent, restated for this layout)

1. **`anti-legacy-expert` is a mandatory bundle member.** `skills` has no inter-skill dependency model, so a partial `--skill <subset>` install could omit it and break every `from antilegacy_core import …`. The supported install is `--all` — consistent with the bundle-level-portability decision (Decision 8).
2. **Prefer the `run.py` path over editable-install** specifically because the default delivery is a *symlink* into skills.sh's clone cache; `pip install -e` against that is fragile. (Amendment B.)
3. **Host the package on a skill that earns its place.** `anti-legacy-expert` qualifies on its internals-SME content. Do not spawn a contentless skill to hold the package.

---

## 6. Migration-sequence deltas (vs `RESTRUCTURE_SPEC.md` §10)

- **Step 4** ("Stand up `antilegacy_core`"): create the package at `skills/anti-legacy-expert/scripts/antilegacy_core/`, **not** a top-level `antilegacy_core/`. Same dependency-ordered batches (4a–4d) otherwise.
- **Step 6**: writing `anti-legacy-expert/SKILL.md` joins writing `wicked-estate/SKILL.md`. `preflight()` still lands in `antilegacy_core.__init__`.
- **Step 7** ("setup installs `antilegacy_core` via `pip install -e`"): **removed.** Replaced by: `run.py` template prepends `skills/anti-legacy-expert/scripts/` to `PYTHONPATH` and dispatches library stems via `python -m`. `setup` no longer runs pip.
- **Step 8**: unchanged, except DoD line "setup installs antilegacy_core via pip install -e" → "run.py resolves the bundled antilegacy_core via PYTHONPATH; no pip step." Remove the "never publish to PyPI" worry entirely — nothing is installed.
- **§11 Backward-compat**: the conftest legacy-path bridge still applies during Steps 4–5; once the package sits under `anti-legacy-expert/scripts/`, tests add that one path (conftest) or `pip install -e` locally.

---

## 7. Net effect on the spec's decision table

| Spec decision | Change |
|---|---|
| §3 library at top-level `antilegacy_core/` | → inside `skills/anti-legacy-expert/scripts/antilegacy_core/` |
| Decision 6 "never publish to PyPI" | moot — runtime install is path-based, nothing is pip-installed |
| §7/§10 Step 7 `setup` runs `pip install -e` | removed — `run.py` puts the bundled package on `PYTHONPATH` |
| §15.1 install mechanism (TBD) | resolved — `npx skills add … --all` + `cargo install wicked-estate` |
| New skill: `wicked-estate` | now **two**: `wicked-estate` (engine) + `anti-legacy-expert` (core host + internals SME) |

§15.2 (engine version floor sufficiency) is unaffected and still open.
