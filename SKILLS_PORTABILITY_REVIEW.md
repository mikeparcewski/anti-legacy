# Review & Recommendation — Collapsing the Antigravity Plugin to a Portable Skills Bundle

**Status**: Recommendation (v2 — converged). Supersedes the initial critique-only cut.
**Author**: Claude — review of Antigravity's `SCRIPT_REORG_SPEC.md` + the wider repo, refined through design discussion with Mike P.
**Date**: 2026-06-16
**Reviewing**: `SCRIPT_REORG_SPEC.md` (Antigravity, 2026-06-16) and the current repo layout.

---

## 0. Scope, terms, and one caveat

**What this is.** A counter-proposal to Antigravity's `SCRIPT_REORG_SPEC.md`. The spec reorganizes `scripts/`. This document recommends a different target — a **portable, auditable, file-first skills bundle with a proper Python library underneath** — and, per request, **states the rationale for every place it diverges from the spec** (§2 is the spine; each major section carries a `Why not the spec's way` callout).

**The meta-rationale, up front.** The spec optimizes for one thing: *clean Python imports inside a Python-package layout.* This recommendation optimizes for the goal that was actually stated: *a bundle any CLI/IDE can run, that keeps the project's auditability promise, with the shared Python as a real library used by name.* The spec's frame is Python-internal; this one is product-shape. Almost every divergence below flows from that single difference.

**What "skills.sh standard" means here.** There is **no literal `skills.sh` file in the repo** (grepped — the only hits are the substring inside `SKILL.md`). I read it as **the portable Agent Skills format**: a skill is a self-contained directory whose `SKILL.md` (name + description + instructions) is the entry point, resources referenced by relative path, discoverable/invocable by any host (Claude Code, Antigravity/Gemini, Codex, Cursor, Kiro) with no host-specific glue. If you meant a specific external spec, point me at it and I'll re-anchor §5–§6.

**Caveat.** `wicked-brain` was down all session (auto-start failed). This rests entirely on the filesystem + the project's own stated principles, not on any captured prior rationale. Where the flat top-level layout might have been a deliberate constraint I can't see, I've flagged it (§13).

---

## 1. Recommendation in one screen

Adopt a **shared-core + portable-leaf-skills** shape, delivered as one installable bundle:

1. **Package the shared Python as a real in-repo library** — `antilegacy_core` (editable-installed by `setup`), *not* three "library skills" under `skills/`.
2. **Schemas ride inside that library as package data** — single source of truth, versioned with the code, importable by name.
3. **Pivot the two agents to skills** — `developer`, `uat-reviewer`; drop the `gemini-2.0-flash` pins; make the existing inline path the default.
4. **Fold templates into their owning skills** — the easy, zero-cross-share portability win.
5. **Readiness is a preflight, not a server** — a deterministic check in the dispatch seam (the invariant) + a description-triggered `doctor` skill and a `wicked-estate` skill (discovery + diagnosis). **Descriptions route; the seam enforces.**
6. **Normalize skill `name:` frontmatter** — consistent namespacing (fixes the `orchestrate` latent bug).
7. **`run.py` stays the thin stable seam** — resolves stems by *probing*, delegates to the library or a skill-local script. The 219 dispatch call-sites don't change.

**Honest granularity claim:** portability is delivered at the **bundle** level ("install this one directory + the library"), not the single-skill level. The shared core (~16.7k LOC + schemas) cannot be made copy-portable per-skill without untenable duplication. Any plan implying otherwise — including a naive reading of "skills.sh standard" — will mislead.

**Do not run the spec as written.** Keep its *good kernel* (kill the stdlib import-collision risk, `run.py` as stable interface) as a **sub-step** of step 1, not the headline.

---

## 2. How this differs from the Antigravity spec — and why

| # | Concern | Antigravity spec | This recommendation | Why not the spec's way |
|---|---|---|---|---|
| 1 | Shared Python core | 3 "library skills" (`wicked-estate`, `domain-graph`, `pipeline`) under `skills/`, wired by `PYTHONPATH` injection | One real library `antilegacy_core/`, editable-installed | `AGENTS.md` §4: *"a 20-line skill is a stub… a shell alias."* A package whose `SKILL.md` exists only to host it **is** that stub. A package is not a skill — give it a package's home. |
| 2 | Import-collision fix | Namespaced packages + `run.py` injects 3 dirs onto `PYTHONPATH` (spec L186–208) | Named package on `site-packages` (`pip install -e`) | Same collision fix, but a `PYTHONPATH`-resolved import only works if `run.py` ran first — that skill is no longer copy-portable. An installed package imports anywhere. Tests `import by name`; the spec's `conftest.py` path-hacks and 25 `sys.path.insert` lines vanish. |
| 3 | Resolution table | `run.py` grows to ~80 lines with a hardcoded `_STEM_MAP`/`_PKG_MODULES` (spec L140–183) | `run.py` resolves by **probing** (`importlib.util.find_spec` for the library, glob for skill-local) | A hardcoded stem→module map is a **second source of truth** that drifts from the filesystem — the exact thing today's 38-line `run.py` (`stem + '.py'`) avoids. Probe, don't maintain a table. |
| 4 | Schemas (6) | **Not addressed** | Package data inside `antilegacy_core` (`importlib.resources`) | Consumed by 3 scripts (`manifest.py`, `domain_graph.py`, `vocabulary.py`) **and** 4 skills + the `GATE_1_DESIGN` validation. Multi-consumer → can't fold into one skill; scattering breaks single-source-of-truth. Package data keeps one copy, versioned with the code. |
| 5 | Agents (2) | **Not addressed** | Pivot `developer` + `uat_reviewer` → skills; drop `model: gemini-2.0-flash` | They're already dispatched via Antigravity-native `@agent` (`swarm/SKILL.md:103`) with a hand-rolled "run inline" fallback in every consumer (`swarm:166`, `uat-crew:160`). That's the literal definition of "piecemeal." Portable skills don't pin a vendor model. |
| 6 | Templates (7) | **Not addressed** | Fold into owning skills' `assets/` / `references/` | Single-owner, zero cross-share — the safest portability win, and the spec skipped it entirely. |
| 7 | Readiness / health | **Not addressed** | Deterministic preflight in the seam + `doctor` + `wicked-estate` skills | README promises graph phases *"fail fast with the install command"* and *"No external servers."* There's no shared preflight today; each skill guards ad hoc. Centralize it — as a check, not a daemon (§6). |
| 8 | `wicked_estate` naming | Keeps `wicked_estate` package name | Rename seam → `antilegacy_core.estate`; add `anti-legacy:wicked-estate` skill for the **external** engine | README/§H: `wicked-estate` is an external Rust engine you `cargo install`; `scripts/wicked_estate.py` is the *shim* to it. Naming a package + a "skill" `wicked_estate` implies the engine is vendored here. Name the shim for what it is. |
| 9 | Skill `name:` frontmatter | **Not addressed** | Normalize to one convention | Today it's inconsistent: `"anti-legacy:setup"` (quoted), `anti-legacy:convert` (unquoted), `orchestrate` (**no prefix**), `"anti-legacy:swarm"`. `orchestrate` missing the prefix is a latent dispatch bug. |
| 10 | Scope | `scripts/` only | The whole asset surface + the install story | The stated goal is a portable bundle every CLI runs, not just clean Python imports. The spec answers a real but narrower question. |

The rest of the document expands the non-obvious rows.

---

## 3. What the spec gets right (kept, not discarded)

- **The collision risk is real.** `coverage.py`, `manifest.py`, `extract.py` on `sys.path` shadow stdlib / `pytest-cov`. Correct diagnosis (spec L9–14).
- **`run.py` as the stable interface is the right instinct.** Skills talk to *stems*, not paths; the reorg stays invisible to 219 call-sites. Kept.
- **The migration is genuinely safe** — legacy dual-path fallback, batch-by-batch with the full suite after each, `git revert` rollback. Kept.
- **Dependency-ordered batching** (pipeline → estate → domain_graph) is correct. Kept.
- **`conftest.py` to kill per-test `sys.path.insert`** — superseded by "install the package, import by name," which is strictly cleaner, but the instinct (stop hand-hacking paths) is right.

The spec is good engineering for the problem it scoped. This recommendation re-scopes the problem, then keeps the spec's mechanics as a sub-step.

---

## 4. The current shape — why it reads as "piecemeal gemini"

| Asset | Location | Count | Portable today? |
|---|---|---|---|
| Skills | `skills/*/SKILL.md` | 24 (`survey-modern` is a retired stub) | Mostly — naming + bundling gaps |
| Subagents | `agents/*.md` | 2 (`developer`, `uat_reviewer`) | **No** — `model: gemini-2.0-flash`, `@agent` dispatch |
| Scripts | `scripts/*.py` | 21 (~16,735 LOC) | Via `run.py` seam only |
| Templates | `templates/*` | 7 (`run.py`, `manifest.json`, 2× `.java`, 3× `.md`) | Top-level, not skill-owned |
| Schemas | `schemas/*.json` | 6 | Top-level, shared by skills **and** scripts |
| Manifests | `plugin.json` + `gemini-extension.json` | 2 | Dual-publish; install path Antigravity-only |

Tell-tale Antigravity-first signs:
- `plugin.json:6` author = **Antigravity**; `gemini-extension.json:5` `contextFileName: AGENTS.md`; `GEMINI.md` + `CLAUDE.md` symlink to `AGENTS.md` (this part is *good* — one contract, many names).
- `README.md` install = `cp -r anti-legacy/ ~/.gemini/antigravity-ide/plugins/` — Antigravity-pathed.
- `agents/developer.md:10`, `agents/uat_reviewer.md:9` pin `model: gemini-2.0-flash` + `tools:` in the Gemini agent schema.
- Agents dispatched with `@developer` / `@uat_reviewer` (`swarm/SKILL.md:103`, `orchestrate/SKILL.md:350`, `convert/SKILL.md:511`), each with an "if your CLI can't do `@agent`, run inline" fallback (`swarm:166`, `uat-crew:160`). **The non-portability is already known and patched per-skill** — piecemeal by definition.

The agent *content* is valuable (COMP-3 precision rules, `@ImplementsRule` hooks, the read-only PASS/FAIL verdict). Only the delivery is Gemini-shaped.

---

## 5. The shared core as a standalone, in-repo library

**Recommendation:** package the genuinely-shared Python as `antilegacy_core` — a real library that lives in this repo, is **editable-installed** by `setup`, and is imported by name. This is the matured form of the "`shared/` directory" idea: a real package beats a loose folder.

### 5.1 Why a library, and what it actually buys

These are *real* libraries, not shims — the estate seam alone is ~5,980 LOC (engine 3,288 + coverage 807 + extract 893 + vocabulary 992); `domain_graph` is 1,707. There's enough substance for a package boundary to earn its keep. It buys:

- **The collision fix, done cleanly** — `import antilegacy_core.coverage`, no `PYTHONPATH` games, no `_STEM_MAP` table (spec rows 2–3).
- **Tests import by name** — `pip install -e .` and `from antilegacy_core import coverage`; the `conftest.py` path-hacking disappears.
- **Schemas as package data** (§5.3) — solves the shared-schema problem the spec ignored.
- **The naming collision fixed** — `antilegacy_core.estate` is unambiguously the shim, distinct from the external `wicked-estate` binary (spec row 8).
- **No §4 violation** — a package under `antilegacy_core/` isn't pretending to be a skill; `skills/` stays all-real-skills.
- **Optional external reuse** — the estate-binary wrapper is plausibly reusable in other projects; a package makes that possible (not a goal, a free option).

> **Why not the spec's way:** the spec gets the collision fix but pays for it with `PYTHONPATH` fragility (non-portable skills), a hardcoded resolution table (second source of truth), and three §4-violating "library skills." A real package gets the same fix with none of those costs.

### 5.2 The boundary — keep it tight

**Library = code with ≥2 consumers OR plausible external reuse.** Everything else stays skill-local.

- **In:** estate seam, `domain_graph` / `normalizer` / `comparator`, `coverage` / `extract` / `vocabulary`, `manifest`, `validator`.
- **Out (stay skill-local in each skill's `scripts/`):** the single-owner leaves — `completeness_scanner`, `document`, `functional_tests`, `semantic_validator`, `semantic_join`, `detect_dead_ends`, `generate_target_graph`, `test_runner`, `packet_generator`, `git_brain`, `learn_coordinator`. (This matches the spec's Phase 2, but they land in skills, not the library — they have one consumer each; packaging them bloats the library and blurs the skill/library line. Generic names like `document.py` are still namespaced under their skill, so collision is still avoided.)

State the rule in-repo so it doesn't drift.

### 5.3 Schemas as package data (the spec's silent gap, resolved)

Ship the 6 JSON schemas **inside** `antilegacy_core` and read them via `importlib.resources`. One copy, versioned with the code, accessed by name from both scripts and skills. This is strictly better than a loose top-level `schemas/` and far better than scattering them into 6 skills (which breaks the single source of truth the `GATE_1_DESIGN` validation depends on).

### 5.4 Costs and when this is the *wrong* call

- **Adds `pip install -e <plugin>/core` to `setup`.** Copy-and-go → copy-and-install. Acceptable — you already require `cargo install wicked-estate` and `pip install -r requirements.txt`; this is consistent, not a new class of friction. But it *is* a bootstrap change.
- **Version-skew footgun** — *if* published to PyPI, a global `antilegacy_core` could shadow the in-repo one. **Mitigation: editable-install-from-repo only, never publish** (Open Decision §12.4). Then "lives in this repo" stays literally true and skew risk is near zero.
- **Over-abstraction** if the boundary is drawn wide. Tight boundary (§5.2) keeps it honest.

### 5.5 `run.py` stays the seam

`run.py` (written by `setup`, `__PLUGIN_ROOT__` baked at `templates/run.py:4`) stays thin and stable. It resolves a stem by **probing** — library module first (`find_spec`), then skill-local `scripts/`, then legacy fallback during migration — and delegates (`python -m antilegacy_core.<x>` or the skill-local script). All 219 dispatch call-sites are unchanged (the spec's best idea, kept).

---

## 6. Readiness: a preflight, not a server

There were two ideas in the "server" proposal. Only one fits this project.

### 6.1 The good idea: a uniform readiness check
A single routine that verifies **wicked-estate resolvable (+ version), `jsonschema` present, workspace initialized (`run.py` + manifest exist), core importable**, and fails fast with the *exact* remediation string. Real gap — today each skill guards ad hoc.

### 6.2 Why **not** a server

> **Why a server is the wrong mechanism here** (none of these appear in the spec — it's silent on readiness — but the project's own principles decide it):

- **It contradicts a load-bearing promise.** README, verbatim: *"No external servers. No cloud services. Git and fileshares only."* Auditability (`audit.jsonl`, git-committed `manifest.json`, "the graph **is** the evidence") depends on durable files, not server memory.
- **It adds a failure mode to a crash-safe pipeline.** Today any process can die and you resume from the manifest. A required server is a single point of failure — server down → whole pipeline blocked. **We're living that this session**: the wicked-brain server is down and its skills are degraded. That's the failure class you'd import.
- **There's no hot path to amortize.** This is a batch pipeline gated on humans and on slow ops (estate indexing, the LLM swarm); Python startup is noise. The one hot loop — extraction's ring-crawl reading each node's source slice — is an **engine** concern (§3: don't reimplement parsing) and the project's own answer is *"bulk source"* (commit `4b2cc7b`): batch the calls, the right layer. Not an anti-legacy daemon.
- **SQLite already is the shared store.** The graph DB gives concurrent-reader persistence without a daemon.

A server earns its place only later, as an **optional accelerator with a CLI fallback** (a live dashboard over `site/`, a watch-mode) — *never* a hard gate every skill blocks on. Rule: a file-first pipeline may have an optional daemon on top; it must never *require* one underneath.

### 6.3 The design: descriptions route, the seam enforces

Skill `description` triggers ("use me when running X") are a **soft** guarantee — they fire only if the model has the description in context, recognizes the trigger, and invokes it before the gated work. Great for *discovery*; unreliable as the *sole* enforcer of a must-always-hold precondition (the one skipped run gives you the cryptic mid-pipeline failure fail-fast exists to prevent). So layer it:

- **Deterministic preflight in the seam** — `run.py` calls `antilegacy_core.preflight()` on every dispatch; cheap, fail-fast, **cannot be skipped**. This is the invariant.
- **`anti-legacy:doctor` skill** — description-triggered, renders the *rich* diagnosis + remediation (more than the seam's one-liner). Same `preflight()` underneath; two presentations.

Belt and suspenders. The seam guarantees the floor; the skill gives the experience + cross-CLI discoverability. The backstop also **de-risks description tuning**: a broad "use me before everything" trigger over/under-fires unpredictably, but a missed trigger is still caught by the seam.

### 6.4 The `wicked-estate` skill (the stronger application)

`doctor` is a cross-cutting guard (awkward "before everything" trigger). A `wicked-estate` skill is a **concrete capability** ("when you index / query / source / annotate the graph") — a clean trigger, far less prone to over/under-fire, and the natural home for the engine availability check + seam documentation. It's the engine sibling of `doctor`, and it resolves the §8 naming collision: `anti-legacy:wicked-estate` is the discovery + check + docs home for the **external** engine, distinct from the internal `antilegacy_core.estate` shim.

> **Guardrail:** this skill must *teach*, not *forward*, or it becomes the thin-wrapper stub §4 forbids (the same trap as the spec's "library skills"). It earns skill status by carrying the real subtlety — binary resolution order (`config → env → PATH`), the "never silently degrades — fails fast with the install command" contract, the `file`/`line` provenance guarantee, COBOL + modern indexed in one pass, the annotate semantics. There's enough there for a real skill; hold it to that bar.

---

## 7. Pivot the agents to skills

Keep the content verbatim; change the delivery.

- **`developer` → `skills/developer/SKILL.md`** (`name: anti-legacy:developer`). Persona + 7 rules + completion criteria + output format become the body. Bundle `ImplementsRule.java` / `ImplementsRules.java` as `assets/`. **Drop `model: gemini-2.0-flash`** (advisory line in the body at most). `swarm` invokes it as a skill/sub-task; the existing inline fallback (`swarm/SKILL.md:166`) **becomes the primary, host-agnostic path**, and `@developer` becomes one optional Antigravity accelerator.
- **`uat_reviewer` → `skills/uat-reviewer/SKILL.md`** (`name: anti-legacy:uat-reviewer`). Preserve READ-ONLY + the JSON verdict contract. Isolation is preserved by *what context you pass*, not by the Antigravity runtime.

> **Why not the spec's way:** the spec doesn't touch agents at all, leaving the most obviously non-portable assets (vendor-model-pinned, `@agent`-dispatched) in place. Pivoting them deletes the `agents/` dir, removes the model pins, and turns two "if your CLI can't…" caveats into the normal path.

*Open question (§12.2): on hosts with a first-class isolated-subagent concept, optionally also ship a host-native read-only agent that delegates to the same skill body — the portable skill is the floor, the native agent the enforced-isolation ceiling.*

---

## 8. Templates — fold into owning skills

Zero cross-share; the easy win the spec skipped.

| Template | Owner (grep of `skills/`) | Target |
|---|---|---|
| `run.py` | `setup` (writes it) | `skills/setup/assets/run.py.tmpl` |
| `manifest.json` | `setup` (seeds manifest) | `skills/setup/assets/manifest.json` |
| `ImplementsRule.java`, `ImplementsRules.java` | `swarm`/`developer` | `skills/developer/assets/` (§7) |
| `anti_patterns.md`, `nfrs.md` | `blueprint` | `skills/blueprint/references/` |
| `traversal_strategies.md` | analyze/extraction (also a root `TRAVERSAL_STRATEGIES.md` — dedupe) | owning skill's `references/` |

(Confirm each owner before moving — §13.)

---

## 9. Normalize skill `name:` frontmatter

Pick one convention and apply it everywhere. For portability the cleanest is `name: <dir-slug>` with the host applying the namespace — but the pipeline's dispatch strings assume the literal `anti-legacy:` prefix, so the pragmatic call is **enforce `anti-legacy:<slug>` consistently**. Either way, `orchestrate` missing the prefix is a latent bug to fix first (it's cheap and mechanical — a good Migration step 1).

---

## 10. Proposed target layout

```
anti-legacy/
  plugin.json                       # + a portable "skills" manifest section
  gemini-extension.json             # keep (dual-publish)
  AGENTS.md  +  GEMINI.md→  +  CLAUDE.md→     # keep the symlink trick

  antilegacy_core/                  # the standalone in-repo library (NOT a skill)
    pyproject.toml                  # editable install; never published (Open Decision §12.4)
    antilegacy_core/
      __init__.py                   # exposes preflight(), __version__
      estate.py                     # was scripts/wicked_estate.py (the seam, renamed)
      coverage.py  extract.py  vocabulary.py
      domain_graph.py  normalizer.py  comparator.py
      manifest.py  validator.py  planner.py
      schemas/*.json                # the 6 schemas as PACKAGE DATA (§5.3)
    tests/                          # import by name; no sys.path hacks

  skills/
    setup/         SKILL.md  assets/{run.py.tmpl, manifest.json}
    doctor/        SKILL.md                      # readiness diagnosis (description-triggered)
    wicked-estate/ SKILL.md                      # engine capability + availability + seam docs
    developer/     SKILL.md  assets/{ImplementsRule.java, ImplementsRules.java}   # was agents/developer.md
    uat-reviewer/  SKILL.md                       # was agents/uat_reviewer.md
    swarm/  uat-crew/  blueprint/(references/)  …phase skills, each owning leaf scripts in scripts/…

  # run.py (written by setup): thin seam — probes stem → `python -m antilegacy_core.<x>`
  #   or skills/<skill>/scripts/<stem>.py; runs antilegacy_core.preflight() first.
  # deleted: top-level  agents/   templates/   scripts/
```

The defining difference from the spec: shared code lives in an honest **library**, not three fake "library skills"; readiness is a **preflight + skills**, not a server; and the *whole* asset surface moves, not just `scripts/`.

---

## 11. Migration sequencing (supersedes the spec's phasing)

Portability wins first (low-risk); the spec's package move last (its hygiene contribution, correctly placed).

1. **Normalize `name:` frontmatter** (§9). Fixes the `orchestrate` bug. Mechanical. Full suite.
2. **Fold templates into owning skills** (§8). Zero cross-imports — the safe early win.
3. **Pivot `agents/` → skills** (§7). Drop model pins; inline becomes default; update `swarm`/`uat-crew`/`orchestrate`/`convert` references. Full suite + a swarm dry-run.
4. **Stand up `antilegacy_core` + move schemas to package data** (§5). `pip install -e` in `setup`; repoint the 3 scripts + 4 skills; `run.py` probes. Keep the spec's dependency-ordered batches + legacy fallback. Rename the seam (`wicked_estate.py` → `estate.py`). Full suite after each batch.
5. **Add the preflight + `doctor` + `wicked-estate` skills** (§6). `antilegacy_core.preflight()` wired into `run.py`.
6. **Cleanup**: delete `scripts/`, `templates/`, `agents/`; drop the legacy fallback.
7. **Portable install**: add a CLI-agnostic install path to the README beside the Antigravity `cp`.

Steps 1–3 deliver most of the *portability* value at low risk. Step 4 is the spec's contribution, repositioned as a hygiene sub-step rather than the headline.

---

## 12. Open decisions (genuinely yours)

1. **`name:` convention** — strip the `anti-legacy:` prefix (host applies it) or enforce it everywhere? *(Lean: enforce — dispatch strings already assume it.)*
2. **uat-reviewer isolation** — portable skill only, or *also* a host-native read-only agent on hosts that support it (§7)? Trade-off: two surfaces vs. enforced isolation.
3. **Granularity** — OK to tell users "portable at the *bundle* level" (the recommendation), or is single-skill portability a hard requirement? If the latter, the shared core must be duplicated — a worse plan.
4. **Publish the library?** — editable-install-from-repo only (my strong lean — kills version skew, keeps it in-repo), or a real PyPI distribution? Only choose PyPI if external reuse is a genuine goal.
5. **Did I read "skills.sh standard" right?** If it's a specific external spec, point me at it and I'll re-anchor §5–§6.

---

## 13. What this does *not* establish (still-not-done)

- **Template ownership not exhaustively verified.** I used a grep of `skills/` (`templates/` hits: `setup`, `develop-plugin`, `blueprint`); `traversal_strategies.md`'s consumer is inferred and `develop-plugin` may co-own. Confirm before moving each file.
- **Schema consumers not exhaustively traced.** Found 3 scripts + 4 skills; indirect loads may exist. `git grep` each schema filename before §5.3.
- **The extraction hot-loop claim is doc-grounded, not measured.** It rests on `AGENTS.md` §1 + commit `4b2cc7b`, not a profiler run. If perf ever becomes the driver, measure before building anything.
- **The suite was not run.** Counts in §4 are from `ls`/`find` (21 scripts, 38 test files), not a green run. The spec's "653 tests" is unverified here.
- **No prior-decision context.** Brain down (§0). If the flat layout was a deliberate Antigravity constraint, §5/§10 may need to bend.
- **Next falsifiable step:** settle Open Decision §12.1, execute Migration step 1, and confirm the full suite stays green — proving the normalization is safe before any file moves.
