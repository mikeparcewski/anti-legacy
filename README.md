# anti-legacy

> ⚠️ **Experimental.** This is an early-stage, experimental project under active development. Interfaces, schemas, and outputs may change without notice. Capability extraction now works across modern languages (a camelCase/PascalCase/snake_case tokenizer plus domain-entity mining from class/interface/struct/trait/enum/record) as well as the mainframe estate, but naming **quality is gated on glossary curation** — an un-curated glossary that confirms every mined term can yield noisy names from code-mechanics tokens, so the human glossary-confirmation step (confirm only real domain terms) is what makes naming clean (see [BACKLOG.md](BACKLOG.md)). Not production-ready — use it for exploration and evaluation.

A semi-autonomous legacy modernization pipeline plugin for **Antigravity**. Point it at one or more legacy/source codebases — COBOL, Java, SAP ABAP, RPG/400, C#, whatever — and it indexes them into a **wicked-estate** code graph (the structural spine), annotates each behavior-bearing node with its business rule to a provable coverage terminal, runs a structured team review, then orchestrates a swarm of subagents to rebuild them as **one** combined target spec / **one** app in your target stack (e.g. a COBOL carddemo + a Java credit-card service merged into a single Java service). Gates require human sign-off; everything between them runs autonomously.

This is a **behavior-preserving targeted rewrite**: the data contracts (shapes), interfaces, and jobs (full functionality) are **invariant** — only the code/implementation is reimagined in the new stack.

No external servers. No cloud services. Git and fileshares only.

→ **[How it works](HOW_THIS_WORKS.md)** — the mental model  
→ **[How to use it](HOW_TO_USE.md)** — setup, config, running the pipeline  
→ **[GEMINI.md](GEMINI.md)** — agent contract (deliverables, gate approval cycle, working style)

---

## Install

### Prerequisite: the wicked-estate engine (required)

anti-legacy is built on the [**wicked-estate**](https://github.com/mikeparcewski/wicked-estate) code-graph engine — a single Rust binary (tree-sitter + SQLite, 91 languages + the mainframe/IaC estate). It is a **hard requirement**: `survey`, `extraction`, `analyze`, and the build phases all index and read the estate through it. The pipeline cannot run without it.

Install it with one command:

```bash
cargo install wicked-estate
```

No Rust toolchain yet? Install it first (one line, all platforms), then re-run the command above:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # https://rustup.rs
```

Verify it is on your `PATH`:

```bash
wicked-estate --version
```

> The plugin resolves the engine in priority order: `wicked_estate_path` in `.anti-legacy/config.json` → `WICKED_ESTATE_PATH` env var → `wicked-estate` on `PATH`. If none resolve, every graph phase fails fast with the install command above — it never silently degrades.

### Install the plugin

Portable install — any CLI (Claude Code, Cursor, Codex, Gemini/Antigravity …) via the [skills standard](https://skills.sh). Installs all skills **and** the bundled `antilegacy_core` library in one step:

```bash
npx skills add mikeparcewski/anti-legacy --all
```

> Use `--all` (it expands to every skill + every detected agent). A bare `--skill '*'` installs **nothing** — use `--all`.

Or install as a native plugin (ships the whole repo):

```bash
# Claude Code
/plugin install anti-legacy                # from a marketplace, or point at a local clone

# Gemini / Antigravity
gemini extensions install https://github.com/mikeparcewski/anti-legacy
```

All methods deliver the same bundle. `anti-legacy:setup` then writes the workspace dispatcher `.anti-legacy/run.py`, which **discovers the installed `antilegacy_core` library automatically** (no path baking, no `pip install` of the core) and dispatches each script as `python -m antilegacy_core.<stem>` (shared core) or a skill-local `scripts/<stem>.py` (leaf).

**Python dependencies**: `GATE_1_DESIGN` and the T3 schema evals validate the requirements graph against the enriched profile, which needs `jsonschema`. Install it with:

```bash
pip install -r requirements.txt
```

Without it, the design gate reports an error (rather than silently skipping the enriched-schema check).

**Runtime config (agent contract)**: `AGENTS.md` is the single source of truth for the agent contract — deliverables, gates, working style. `CLAUDE.md` and `GEMINI.md` are **symlinks** to it (never edit them separately). `gemini-extension.json` declares `contextFileName: "AGENTS.md"` so the plugin loads the same file across all three Antigravity surfaces: the **antigravity CLI** (`agv`), the **antigravity app**, and **antigravity-IDE**.

---

## Quick start

```
"Run the anti-legacy pipeline on ./legacy/cobol targeting java"
```

The `anti-legacy:orchestrate` skill sequences everything automatically:

```
setup → survey → analyze → extraction → negative-extraction → blueprint
      → test-strategy → review-packet → [GATE 1] → planner → [GATE 2]
      → functional-tests → swarm → target-review → [GATE 3 auto]
      → semantic-validation → [GATE 3B] → uat-crew → [GATE 4]
      → document → final-review → [GATE 5 auto] → deploy
```

`survey` runs `wicked-estate index` over each source repo (one graph DB per repo, federated via cross-graph for multi-repo); `extraction` crawls that code graph with adaptive ring expansion and writes a business rule onto every behavior-bearing node — each ends **resolved** (rule + confidence + provenance) or **risk-flagged** (human research queue) — to a provable coverage terminal (`coverage-report.json`). `negative-extraction` is a second pass that deepens the crawl into each resolved node's error-handling source for `error_paths` + `validations`. `functional-tests` authors executable acceptance tests from the test contracts *before* the build (shift-left), `document` writes the target app's README/ARCHITECTURE/DEPENDENCIES/ENVIRONMENTS, and `final-review` scans the built app for stubs/mocks/TODOs before deploy.

Eight gates: **GATE 0** (discovery, auto — survey integrity), **GATE 1** (design, human/architect), **GATE 1B** (semantic-join, human — multi-repo only), **GATE 2** (plan, human — PM + tech lead both sign), **GATE 3** (build integrity, auto — clears on PASS evidence), **GATE 3B** (semantic, human — rule-coverage round-trip review), **GATE 4** (UAT, human — must differ from the GATE 1 evaluator), **GATE 5** (completeness, auto — `final-review` clears `GATE_5_COMPLETENESS` on a zero-HIGH-finding completeness report).

A producer-side **readiness gate** (`python3 .anti-legacy/run.py precheck <phase>`) backs the Tier-A deliverables: a producer calls `require_ready(phase)` on startup and **refuses** (exits non-zero) when the pipeline is incomplete, orphaned, or state-desynced from disk — see [How to use it](HOW_TO_USE.md).

Or run individual phases: `anti-legacy:setup`, `anti-legacy:survey`, etc.

---

## Deliverables

Once the requirements graph is ready, **`anti-legacy:deliverables`** renders the full
stakeholder package into `.anti-legacy/deliverables/` — each registered in the manifest, nothing
advancing the pipeline:

- **Product requirements** — `product-requirements.md` (`anti-legacy:prd`)
- **Architecture diagrams** — Mermaid C4 / ERD / sequence / deployment (`anti-legacy:diagrams`)
- **Test strategy** — data-parity / UAT / E2E / API, with a traceability matrix (`anti-legacy:test-plan`)
- **Functional test scripts** — the same four types, in the target stack (`anti-legacy:test-scripts`)
- **Migration plan** — epics→stories→tasks→subtasks (prep→build→deploy→test), Markdown + Jira CSV (`anti-legacy:migration-plan`)
- **Risk log**, **decisions log (ADRs)**, **evidence log with receipts** — *living* deliverables, re-run at each gate (`anti-legacy:risk-log`, `anti-legacy:decisions-log`, `anti-legacy:evidence-log`)

`anti-legacy:deliverables` runs them all and writes a `deliverables/README.md` index. They
complement the `review-packet` (the single GATE_1 review doc) and reuse the pipeline's existing
structured artifacts rather than duplicating them.

## Language support

The survey phase runs **wicked-estate index** once over each source repo — one engine captures the mainframe estate and modern languages in the same pass (token-free, handles thousands of files), resolving cross-language edges automatically (JCL `EXEC PGM` → COBOL, `CALL` → COBOL). No language routing, no batch Python extractor.

**Mainframe estate**: COBOL/JCL/CICS/IMS/DB2 — modules, paragraphs, fields, JCL steps/datasets, CICS programs/maps, IMS databases/segments, DB2 tables  
**Modern**: Java, C#, Python, TypeScript, Go, Kotlin, Rust, and other languages the engine indexes. Domain entities are mined from `class`/`interface`/`struct`/`trait`/`enum`/`record` declarations, so Java, C#, TypeScript, Python, Go, Rust, C, and C++ all extract domain types (accessor boilerplate — `get`/`set`/`is`/`has`/`new` — is excluded from naming).

There is no separate "modern" survey track: modern languages are indexed by the same `anti-legacy:survey` pass as the mainframe estate. (`anti-legacy:survey-modern` is retired — a do-nothing redirect stub kept only so stale references resolve.)

**Capability partition** (`config.coverage.capability_partition`): `auto` (default — language-driven: mainframe → call-affinity, modern → source-package), `calls`, `package`, `hierarchical`, `semantic`, or `community`. The `hierarchical` (Louvain community splitting) and `semantic` (embedding clustering) modes are opt-in, feature-detected, and require **wicked-estate ≥ 0.4.0**; `community` reuses the survey-time partition the engine persisted as `type:community` annotations (≥ 0.5.0). All fall back gracefully on older engines, and mainframe behaviour is unchanged on `auto`. The base pipeline runs on older engines; the clustering/bulk-source/typed-annotation capabilities need 0.4.0+ (typed annotations 0.5.0+).

**Typed annotations** (wicked-estate ≥ 0.5.0): agents record their reasoning *around* a rule as `observation` (a noticed fact), `assumption` (a belief acted on — verify), or `question` (an unresolved unknown — needs a human). The rule itself always stays in the `requirement` field; advisory annotations (`assumption`/`question`) become the gate-review work-list (`advisory-nodes`). Read-only consumption is additive and falls back cleanly on older engines.

---

## Memory system (git-brain)

Translation patterns and learnings are stored on **orphan branches** in the project's git repo — no external services needed. Run `python3 .anti-legacy/run.py git_brain init` to create the brain branches. (All workspace script calls go through the `.anti-legacy/run.py` dispatcher written by `anti-legacy:setup`, never a bare `scripts/…` path.)

| Branch | Stores |
|---|---|
| `brain/anti-legacy/learnings` | Episodic notes from translation tasks |
| `brain/anti-legacy/decisions` | Gate decisions and architectural choices |
| `brain/anti-legacy/patterns` | Reusable code translation recipes |

Learnings compound across sessions. `git push` shares them with the team.

---

## Demo

A working demo with 3 COBOL programs (customer management, billing, payment gateway) is in `demo/legacy-src/`. The end-to-end test proves the pipeline:

```bash
python3 -m unittest tests.test_demo_pipeline -v
```

This runs: setup → `wicked-estate index` (survey → deterministic stats digest as the checksummed `legacy-graph` evidence) → analyze (reads the graph via the `wicked_estate` helper, then `graph_normalizer`) → packet_generator → target_verifier — producing real output at each step. The graph phases gate on the wicked-estate binary being resolvable and **skip cleanly** when it is not installed, so the suite stays green in CI without the engine. Note: `target_verifier.py` is the demo's lightweight verifier; the pipeline's **runtime** build/semantic/UAT verifier is `validator_discovery.py`, which supersedes it for gate verification. The two are distinct (the demo verifier lives at `demo/target_verifier.py`, used only by this demo test).

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```

---

## Structure

```
anti-legacy/
├── plugin.json, gemini-extension.json   plugin + extension manifests
├── AGENTS.md  (← CLAUDE.md, GEMINI.md are symlinks)   the agent contract
├── skills/                              ~27 skills — the whole bundle
│   ├── anti-legacy-expert/              internals SME + the shared library:
│   │   └── scripts/antilegacy_core/       manifest, wicked_estate (engine seam),
│   │       coverage, extract, domain_graph, validator, … + schemas/ (package data)
│   ├── developer/, uat-reviewer/        build + independent-review skills (were agents/)
│   ├── setup/  (assets/run.py.tmpl, references/…)   bootstrap + folded templates
│   ├── survey/ analyze/ extraction/ blueprint/ … orchestrate/   phase skills,
│   │                                    each owning its single-consumer leaf scripts/
│   └── wicked-estate/                   the engine capability + availability skill
├── demo/legacy-src/   COBOL programs for end-to-end testing
└── tests/             unit + integration + demo pipeline tests
```

> The migration moved all Python into skills: shared code lives in the namespaced
> `antilegacy_core` library (hosted by `anti-legacy-expert`, shipped as package data),
> and single-owner leaf scripts live in their owning skill's `scripts/`. There is no
> top-level `scripts/`, `agents/`, `templates/`, or `schemas/` directory.
