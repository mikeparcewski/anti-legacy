# Panel Review — Adversarial Review of `RESTRUCTURE_SPEC.md`

**Method**: four parallel review agents with deliberately opposing charters (steelman / YAGNI-skeptic / cross-CLI-distribution-adversary / Python-correctness-auditor). Each was given the prior Claude findings and told to *challenge* them, not echo.
**Date**: 2026-06-16
**Feeds**: `RESTRUCTURE_SPEC.md` (updates) + Antigravity's "Run.py Resolution & Skills.sh Verification" implementation plan (gaps).

---

## 1. Decision (settled)

**Scope = PROCEED with the restructure.** Cross-CLI portability is the project's stated goal (turn-1) and the panel verified it is *achievable* (skills.sh is real; the bundle-in-skill delivery has precedent — §4). Proceeding is conditioned on:

1. Closing the **defect register D1–D7** below — two are CRITICAL and **three are absent from Antigravity's current implementation plan**.
2. Passing an **install smoke test** (§8) *before* the migration batches — the migration's "full suite must pass" gates run *through* the dispatcher, so a broken dispatcher makes the sequence unexecutable.

`RESTRUCTURE_SPEC.md` status should move from **"Approved"** → **"Approved pending D1–D5 closure + install smoke test."** "Approved" is premature: the panel found a critical orphaned-stem regression and a dropped security guard that the spec ships.

---

## 2. Panel verdicts (the spread is the signal)

| Agent | Charter | Verdict |
|---|---|---|
| Steelman | strongest case *for* | **SHIP after trivial fixes** |
| Skeptic (architecture-critic) | attack the premise | **DESCOPE** — ~40× effort for a collision that isn't occurring |
| Distribution adversary | falsify "any CLI" | **Install story HOLDS** — one untested hinge |
| Correctness auditor | hunt code bugs | **DESIGN-LEVEL FLAW** — 7 defects, empirically tested |

Consensus defects (below) are high-confidence (multiple independent agents). The scope tension (skeptic vs the rest) was resolved in favour of *proceed*, because portability is a stated requirement, not speculative — but the skeptic's factual findings (§5) stand and must reshape the spec's *justification*.

---

## 3. Defect register — must-fix before build

| ID | Defect | Sev | Evidence | In agy plan? | Fix |
|---|---|---|---|---|---|
| **D1** | `importlib.util.find_spec("antilegacy_core.<stem>")` **raises** `ModuleNotFoundError` (not `None`) when the parent isn't importable. Tested (3.14.6): parent absent → raises; parent present, child missing → `None`; parent-is-module → raises. So during the *entire* migration window (Steps 1–4a) and any partial install, `_resolve()`'s first probe crashes → glob + legacy fallback **unreachable** (dead code). §10's per-batch "full suite must pass" gates run through this dispatcher → **migration unexecutable as written**. | **CRIT** | spec §5 L313; auditor test | ✅ try/except | Wrap probe-1 in `try/except (ModuleNotFoundError, ValueError): spec=None`. **Must land before Step 7.** |
| **D2** | `wicked_estate.py`→`estate.py` (Decision 2) renames the **module** but the dispatch **stem stays `wicked_estate`** — and ~37–43 SKILL.md call sites still invoke `run.py wicked_estate …` (survey, analyze, gatekeeper, orchestrate, extraction). Post-cleanup all three probe steps miss → `unknown script: wicked_estate`, exit 2. **Directly falsifies "219 dispatch calls unchanged"** (§2 L28 / §5 L385). | **CRIT** | spec §3 L53; auditor + skeptic counted the stems | ❌ **MISSED** | Either keep the dispatch stem `wicked_estate` (alias `wicked_estate`→`antilegacy_core.estate` in `run.py`), or rewrite all call sites and drop the "unchanged" claim. Pick one explicitly. |
| **D3** | The current `templates/run.py` (L19–32) has a path-traversal / stem-confinement guard (rejects separators, `..`; `commonpath` check). The §5 rewrite **drops it**. Demonstrated live: stem `../../outside/evil` → step-3 `os.path.join`+`isfile` → **dispatches `/tmp/.../evil.py`**. | **HIGH** | `templates/run.py:19`; auditor live demo | ❌ **MISSED** | Port the sep/`..`/`commonpath` guard; confine each probe root (esp. step-3 legacy + the new `skills/*/scripts/` glob). |
| **D4** | `__file__`/`REPO_ROOT` path-walking breaks under the **deeper nesting**, *not* the `-m` switch (tested: `-m` and direct give identical `__file__`). Concrete sites: `manifest.py:180` (template dir → can't find `templates/manifest.json`), `coverage.py:50` (`REPO_ROOT`), `domain_graph.py:64`→`SCHEMA_PATH:83` (the only runtime schema open, L1427), `validator_discovery.py:17,210`, and `learn_coordinator.py:17` (hard-codes `join(dirname(__file__),"git_brain.py")` — breaks when it moves and git_brain doesn't follow → see D5). | **HIGH** | auditor + steelman | ⚠️ partial (generic test, wrong root cause) | Fix each site to resolve relative to the resolved package/asset locations; add a test asserting each migrated module resolves its paths under `python -m`. |
| **D5** | `git_brain.py` (1,037 LOC) is **absent** from §3 layout and §13 counts (which total 21 — the spec's list already silently dropped the 22nd file). It has a CLI and is imported-by-path by `learn_coordinator.py`. | **MED** | scripts/ listing; SCRIPT_REORG placed it at `setup/scripts/git_brain.py` | ❌ **MISSED** | Assign an owner (leaf under `develop-plugin/scripts/` or `setup/scripts/`), fix the count to **12** leaf scripts, fix the `learn_coordinator` reference. |
| **D6** | §4.1's rule ("cross-imports ⇒ library") is misapplied: real cross-imports are `domain_graph`→{coverage, estate, vocabulary} (`domain_graph.py:63–65`), `extract`→{estate, coverage}, `vocabulary`→{estate}, `coverage`→{estate, lazy L249}. **`manifest`, `validator`, `planner` import *no* sibling** yet sit in Batch 4a. The "Consumers" counts can't be reproduced from imports. | **MED** | auditor read of scripts/ | ❌ missed | Restate the rule as "the cross-importing core **+ its consumed dependencies + dispatch-target roots**"; fix or drop the consumer counts. |
| **D7** | §5 L384 claims "preflight runs on every dispatch — cannot be skipped," but `main()` wraps it in `except ImportError: pass` (L346) → a failed core import (broken install) silently skips preflight. | **LOW** | spec §5 | ✅ fail-loud + migration flag | Fail loud post-migration; auto-detect the migration window by presence of legacy `scripts/` (not an env var). Also: §4.3 over-claims schema consumers — only `domain_graph.py` opens a schema at runtime (L1427); `manifest`/`vocabulary` reference them in comments only. |

---

## 4. Corrections to the prior Claude review (for the record)

The panel was adversarial toward the earlier findings too, and overturned three:

- **B2 (nested `scripts/<pkg>` subtree survival) — REFUTED.** The astronomer `data-engineering` plugin ships `skills/analyzing-data/scripts/cli.py` and its README documents the installed path under `.cursor/skills/analyzing-data/scripts/`. Nested subtrees **do** ride along a skills.sh install. The earlier "no scripts/ subtree on this host" was a sampling artefact — every host skill examined was npm-installed (wicked-*), which carry no `scripts/`.
- **B1 (run.py `_CORE_PATH` assumes whole-repo) — REFINED, downgraded to MED.** `_CORE_PATH` derives from `__PLUGIN_ROOT__`, which `setup` bakes from its actual install location — *correct-by-construction*, not broken. It fails only if `setup` keys off a flat path with no `skills/` child. Load-bearing + untested, not broken. (Fix in §7.)
- **`-m` changes `__file__` — FALSE as stated.** Tested identical. The real breakage is nesting depth (now D4).

Intellectual honesty: two of the original three "blockers" were overstated. The auditor's D1/D2/D3 are the genuine blockers.

---

## 5. Premise correction (skeptic — must reshape the spec's justification)

- **The "coverage.py collides with pytest-cov" justification is false in this repo.** There is **no `pytest-cov`, no `--cov`, no `conftest`** — no coverage tooling at all. The collision is *latent*, not occurring. (`SCRIPT_REORG_SPEC.md` L86 should not be cited as live motivation.)
- **Only 2 modules truly collide** by name with stdlib/popular packages on `sys.path`: `coverage`, `wicked_estate`. The other 8 library modules are dragged in by cross-import coupling, not collision.
- **Implication (not a scope reversal):** since we are proceeding *for portability*, justify the library on **portability + latent-collision hygiene**, and **drop the false pytest-cov claim**. Don't oversell the collision urgency.

---

## 6. Assessment of Antigravity's implementation plan

**Fixes correctly:** D1 (try/except), D7 (fail-loud + migration flag), the install smoke test, and it asks the right bake-vs-dynamic question.

**Gaps that must be added:** **D2** (orphaned `wicked_estate` stems), **D3** (dropped security guard), **D5** (unaccounted `git_brain.py`), and D4 is mis-rooted (it blames `-m`; the cause is nesting). As written, the plan would fix the crash and still ship an orphaned-stem regression + a path-traversal hole.

**Recommended answers to its four open questions:**
1. **Bake vs dynamic** → *Bake at install*, discovered robustly: `setup` walks up from its own resolved path until it finds `…/anti-legacy-expert/scripts/antilegacy_core/__init__.py`; fail loud if absent. **Do not use an `AGENT_SKILLS_ROOT` env var** — hosts won't reliably set it.
2. **a / b / both** → Both; run **(b) the install smoke test first** as the gate, then **(a)** — and expand (a) to cover D2/D3/D4/D5.
3. **Consent for `npx skills` write** → user's call; low-risk if contained to a temp project + `--copy` + cleanup; the astronomer precedent makes it confirmatory.
4. **Env var name** → prefer **auto-detect** the migration window via presence of legacy `scripts/`; if one is required, `ANTILEGACY_MIGRATION` is acceptable.

---

## 7. `run.py` resolution — recommended shape

- `setup` resolves the core path **once, at init**, by self-locating: from `setup`'s own absolute path, walk up to the dir containing `skills/anti-legacy-expert/scripts/antilegacy_core/__init__.py`; bake that absolute `_CORE_PATH`. Fail loud (with remediation) if not found — covers whole-repo *and* flat skills.sh layouts because it's resolved from reality.
- **Keep the path-traversal guard** (D3) on every probe root.
- Probe order: (1) `antilegacy_core.<stem>` via the baked path, guarded by `try/except` (D1); (2) skill-local `scripts/`; (3) legacy `scripts/` **only when the migration window is auto-detected**.
- Library stems dispatch `python -m antilegacy_core.<stem>` with `_CORE_PATH` on the subprocess `PYTHONPATH`.
- Decision 6 wording: PYTHONPATH's win is "no separate `pip install -e` step," **not** inherent superiority over `--copy` (both ride the same delivered files).

---

## 8. Install smoke test — the Step 0 gate

Before any file moves: scaffold a throwaway project, `npx skills add <repo> --all --copy` into a temp agent dir, then assert:
1. `…/anti-legacy-expert/scripts/antilegacy_core/__init__.py` landed and is importable;
2. `python3 .anti-legacy/run.py manifest status` runs end-to-end (resolves the core + preflight) on a **flat** layout.

This converts B1/B2 from assumptions to verified facts. The astronomer precedent makes success likely; it has simply never been run for *this* repo on a flat host.

---

## 9. Open item carried forward

§16 (engine version floor `≥ 0.5.1`): still open — verify sufficiency for all engine features called (clustering, bulk source, typed annotations, community partition) before pinning.
