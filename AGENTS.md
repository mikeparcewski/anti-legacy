# anti-legacy — Agent Contract

Semi-autonomous legacy modernization pipeline for Antigravity. Indexes legacy source into a `wicked-estate` code graph (the structural spine), annotates each behavior-bearing node with its business rule (resolved-or-flagged, to a provable coverage terminal), runs gated human review, then builds the target system against those requirements — not against the legacy code directly.

The code graph is owned by **wicked-estate** (the MIT code-graph engine, §H) — no hand-rolled graph: `graph_builder.py`, `legacy_graph.json`, `code-graph.schema.json` are deleted. The graph **is** the evidence; rule annotations write into wicked-estate's native `requirement` fields, mirrored to the anti-legacy `.anti-legacy/annotations.jsonl` IP sidecar.

`migration_mode` (config.json) selects functional vs structural; the requirements graph is a capability plan, not a code skeleton. `functional` (the intended default) groups legacy modules into business capabilities; `structural` produces 1:1 code-equivalent nodes for like-for-like rehost only.

Read HOW_THIS_WORKS.md before touching any phase. The mental model matters more than the commands.

This file is the lean contract: WHAT the pipeline is, the dispatcher + manifest CLI, the gate principle, the phase sequence. The per-phase field schemas and per-gate review checklists live in the named skills (loaded on demand) — follow the references below.

---

## Voice

Factual. Surface findings, name the file and the line. Do not soften gaps.
"Graph-translator produced 47 nodes; 3 have no business_rules — CALCRATE, LOG-UTIL, AUDIT-TRAIL are called programs with no source in the tree. Marked unresolvable." That is a correct status report. "Graph-translator complete" is not.

---

## Deliverables

Every phase produces exactly one primary artifact. Each producing skill documents that artifact's field schema and done-gate assertions — produce them or flag why you cannot. Full JSON Schemas ship as package data under `antilegacy_core/schemas/` (in the `anti-legacy-expert` skill); the enriched requirements profile is `requirements-graph.enriched.schema.json`, gate-validated.

| Artifact | Schema + assertions live in |
|---|---|
| `graphs/<app>.db` (wicked-estate code graph) + `legacy-graph.digest.txt` (checksummed seam) | `anti-legacy:survey` |
| `annotations.jsonl` (rule overlay) + `coverage-report.json` | `anti-legacy:extraction` |
| `requirements_graph.json` | `anti-legacy:graph-translator` (§I5 — the domain-graph builder) |
| `blueprint.json` | `anti-legacy:blueprint` |
| `contracts/{domain}/{req_id}.contract.json` | `anti-legacy:test-strategy` |
| `task.md` | `anti-legacy:planner` |
| `evidence/build-integrity.json` | `anti-legacy:target-review` |
| `evidence/uat/{domain}.json` | `anti-legacy:uat-crew` |

Per-app graph DBs under `.anti-legacy/graphs/` are gitignored (rebuilt by survey); the committed seam is the deterministic `legacy-graph.digest.txt` whose SHA-256 is the checksummed `legacy-graph` artifact the gate/audit contract consumes.

Cross-artifact invariants no skill may relax (see Universal Don'ts): every wicked-estate node carries native `file`/`line` provenance (the engine asserts it); `legacy_components` and `business_rules` on every active requirement, `parity_rules` on every numeric output; every behavior-bearing graph node ends RESOLVED or RISK-flagged — never bare.

---

## Gate Approval Cycle

The full gate set is **nine** ids. The six mainline gates run in order, no skipping, no reordering: GATE_1_DESIGN, GATE_2_PLAN, GATE_3_BUILD, GATE_3B_SEMANTIC, GATE_4_UAT, GATE_5_COMPLETENESS. GATE_1_DESIGN, GATE_2_PLAN, GATE_4_UAT require a human. GATE_3B_SEMANTIC is a human semantic review. GATE_3_BUILD auto-clears on evidence (build-integrity `status: PASS` + round-trip rule_coverage ≥ 1.0); GATE_5_COMPLETENESS auto-clears on evidence (`completeness-report` `status: PASS`) at the `final-review` phase and kicks back on FAIL; no skill, script, or agent may synthesize a human gate. Three further gates sit outside the mainline advance-precondition sequence: **GATE_0_DISCOVERY** (automated, runs post-survey), **GATE_1B_SEMANTIC_JOIN** (multi-repo only — the semantic-join side phase), and **GATE_3C_DIFFERENTIAL** (automated executed-parity gate, ISS-7 — runs a golden legacy-I/O corpus against the built target and asserts COMP-3-safe output parity within each contract's `parity_rules`; vacuous-safe when no corpus is supplied). It is **provenance-graded, not a hard gate by default**: the corpus (assembled by `anti-legacy:capture-corpus` from contracts' `expected_output`, a source oracle, or captured legacy I/O) carries a confidence, and a parity FAIL **WARNS** ("the data could be incorrect, and why") rather than blocking — *only* a FAIL against a **captured-legacy** (high-confidence) golden BLOCKS and kicks back to `build`. `anti-legacy:gatekeeper` is the authoritative list of all nine gates, their evaluators, and their required evidence ids.

Three phases sit between the gate phases to host the content agents' work: **`functional-tests`** (after GATE_2_PLAN, before `build` — a blocking pre-build validation pass), **`document`** (after GATE_4_UAT, before `final-review` — the documentation pass), and **`final-review`** (after `document`, before `complete` — the automated GATE_5_COMPLETENESS completeness gate). Each phase's field schema and done-gate assertions live in its producing skill (named below); the manifest wires the phase enum + sequence + advance preconditions.

| Phase | Skill | Purpose | Inputs | Outputs | Gate |
|---|---|---|---|---|---|
| `functional-tests` | `anti-legacy:functional-tests` | Author executable acceptance tests from the per-requirement contracts *before* the build (shift-left), and HARD-GATE that the contracts are runnable/unambiguous. JUnit 5 (Java) / pytest (Python); any other stack errors explicitly — never a silent pass. | `contracts/{domain}/*.contract.json`, `config.target_stack` | Per-stack tests under the target tree (`{target_path}/src/test/java/acceptance/` or `{target_path}/tests/acceptance/`) + `evidence/functional-authoring-report.json` | No gate of its own; blocking pre-build pass. Post-build execution runs in `anti-legacy:target-review` and feeds GATE_3_BUILD. |
| `document` | `anti-legacy:document` | Synthesize the target app's human-facing docs FROM committed artifacts (config/blueprint/requirements/target graphs) — not by coining LLM prose. Writes them INSIDE the target app dir so the delivered repo is self-describing. | `config.json`, `blueprint.json`, `requirements_graph.json`, `target_graph.json` | `README.md` / `ARCHITECTURE.md` / `DEPENDENCIES.md` / `ENVIRONMENTS.md` under `config.target_path`, each registered (`doc-readme`/`doc-architecture`/`doc-dependencies`/`doc-environments`) | None of its own; precedes GATE_5 (whose DOCS dimension scans these). |
| `final-review` | `anti-legacy:final-review` | Completeness-review swarm: one parallel reviewer per dimension — CODE / DOCS / CONFIG / BUILD — runs the deterministic `completeness_scanner`, reasons over findings, FAILs on any HIGH. Reviews the docs + functional tests too (runs last). On FAIL, kicks back to the phase that owns the gap. | The built target tree at `config.target_path`; the docs + tests produced upstream | `evidence/completeness-report.json` (`status: PASS\|FAIL`, dimension + severity counts) + per-dimension slices | **GATE_5_COMPLETENESS** — auto-clears on `status: PASS` (zero HIGH); kicks back on FAIL. |

### Generalized gate kick-back

Recording any gate `failed` (`manifest gate <ID> --opinion failed`) triggers a **guided kick-back**: `manifest.py` resets `phase.current` back to that gate's producing phase (`GATE_PRODUCING_PHASE`, the inverse of the precondition map), drops that phase from `completed` so the pipeline genuinely re-enters it, writes a `blocked_reason`, appends an `anti-legacy:gate-kicked-back` audit event, names the skill to re-run, and exits non-zero (code 3) so callers (orchestrate, CI) can branch. It does NOT auto-dispatch the skill — the human/orchestrator decides when to re-run. This applies to ALL gates, not just GATE_1 (a failed GATE_4_UAT rewinds to `uat`/`anti-legacy:uat-crew`; a failed GATE_5_COMPLETENESS rewinds to `document`/`anti-legacy:document`). `passed`/`waived` never reset the phase.

### Advance preconditions (phase → required gate)

`manifest advance` is gate-precondition-aware. The check fires on **exit** — when the pipeline tries to LEAVE a gate phase it requires the bound gate(s) to be `passed` or `waived` first; advancing INTO a gate phase is always allowed so the human can sign while parked there. The data-driven map (mirrored in `manifest.py`'s `GATE_PHASE_PRECONDITIONS`):

| Leaving phase | Requires gate(s) `passed`/`waived` |
|---|---|
| `gate-design-review` | GATE_1_DESIGN |
| `gate-plan-review` | GATE_2_PLAN |
| `gate-build-integrity` | GATE_3_BUILD **and** GATE_3B_SEMANTIC |
| `gate-uat-signoff` | GATE_4_UAT |
| `document` | GATE_4_UAT |
| `final-review` | GATE_5_COMPLETENESS |

If a required gate is not `passed`/`waived`, `advance` exits non-zero and the phase is unchanged — sign or waive the gate first. GATE_0_DISCOVERY and GATE_1B_SEMANTIC_JOIN are intentionally NOT in this map: neither has a dedicated `gate-*` phase enum value (GATE_0 is post-survey automated; GATE_1B is the optional semantic-join side phase), so they are enforced by their own skills and runners, not by the advance precondition. Two **non-`gate-*`-named** phases are still in the map: `document` (ISS-9 — leaving it requires GATE_4_UAT, so the documentation pass can't feed the completeness review ahead of UAT sign-off; this also closes the phase-jump bypass of skipping `gate-uat-signoff`) and `final-review` (it is itself the completeness-gate phase, so leaving it requires GATE_5_COMPLETENESS). Entering either is always allowed — only the exit gates.

### Producer readiness gate (precheck)

Separate from the gate-precondition check above (which guards `manifest advance`), `antilegacy_core.precheck` is an **execution-time** readiness gate the *producers* consult before they run. A producer calls `require_ready(phase, force=False)` on startup; precheck evaluates per-phase probes — required upstream gates `passed`/`waived`, registered artifacts present-on-disk + checksum-verified, a **disk-reality reconcile** (a derived artifact whose `depends_on` source is missing or checksum-drifted is flagged orphaned/stale, e.g. a `requirements_graph.json` that outlived its gitignored `legacy-graph`), and phase completeness (every active requirement's `business_rules` carry a numeric `confidence`; coverage ≥ 1.0). On any block-severity probe it prints the blockers + fixes to stderr and `sys.exit(1)` — it REFUSES rather than producing against an incomplete/orphaned/desynced pipeline (override with `force=True`, NOT recommended). Run it standalone with `python3 .anti-legacy/run.py precheck <phase> [--advisory] [--json]` (`--advisory` always exits 0 and just reports). The **Tier-A deliverables** (`prd`, `diagrams`, `test-plan`, `test-scripts`, `migration-plan`) gate on it; the **living logs** (`risk-log`, `decisions-log`, `evidence-log`) intentionally do NOT — they must run on an incomplete pipeline to SURFACE its gaps (§6). precheck is read-only: it never writes `audit.jsonl`, advances a phase, or clears a gate.

**Recording a gate** (one form for all of them):
```bash
python3 .anti-legacy/run.py manifest gate <GATE_ID> \
  --opinion <passed|failed|waived> \
  --evaluator "{your name}" \
  --rationale "{one sentence}" \
  --evidence "{id1,id2}"   # passed requires ≥1 evidence id, each a registered artifact

git add .anti-legacy/ && git commit -m "gate: <GATE_ID> cleared by {name}"
```

A gate is recorded `passed` only with registered `--evidence` (no-evidence and unknown-evidence both hard-fail); `failed` needs no evidence; `waived` is an explicit human override. There is no `rejected` and no `approve` form.

The per-gate human-review checklists (what each reviewer verifies before `passed`), the reviewer roles, the required evidence ids, and the verification commands live in `anti-legacy:gatekeeper` (its Gate Definitions table + each gate's `check` section). GATE_3_BUILD's automated build tiers live in `anti-legacy:gatekeeper` / `anti-legacy:target-review`. A `failed` opinion on ANY gate triggers the generalized kick-back above — the pipeline rewinds to that gate's producing phase, the producing skill is named for re-run (graph-translator for GATE_1, planner for GATE_2, swarm for GATE_3, semantic-validation for GATE_3B, uat-crew for GATE_4, document for GATE_5), the evidence regenerates, and the gate is re-presented — not a full pipeline restart.

---

## Working Style

### §1 — Survey is structural, not semantic
Survey → topology (`wicked-estate index`), not reading source for rules. Extraction → semantics: crawls the graph with adaptive ring expansion and reads each node's source slice (`wicked-estate source <name>`). Survey that reads source for business rules, or extraction that skips the source slice, is doing the wrong phase.

### §2 — The traceability thread never breaks
wicked-estate node (native `file`/`line` provenance) → its `requirement` annotation + `annotations.jsonl` overlay row (keyed `{db_id, symbol_id}`, carrying `provenance` = the ring nodes/edges that grounded the rule) → `legacy_components` → `task.md` task → `req_id` → `uat verdict`. The annotation is SymbolId-keyed because names are not unique (carddemo `MAIN-PARA`×21) — the helper resolves name→SymbolId before every write, so the link binds the exact scoped node. A broken link means a swarm agent cannot trace back to source; fix it before advancing.

### §3 — One engine indexes the whole estate
`wicked-estate index` captures the mainframe estate (COBOL/JCL/CICS/IMS/DB2 — module/function/field, JCL step/dataset, cics_program/cics_map, ims_database/segment, db2_table) AND modern languages (Java, C#, Go, Python, TypeScript, Rust, …) in one pass, resolving cross-language edges automatically (JCL `EXEC PGM` → COBOL, `CALL` → COBOL). No language-routing split, no batch Python extractor. Do not add Python parsers — parsing is a wicked-estate concern. There is no separate modern survey track: `survey-modern` is **retired** — a do-nothing redirect stub kept only so stale references resolve; modern source is indexed by `anti-legacy:survey` like everything else.

### §4 — Skills are instructions, not thin wrappers
A skill that calls one script and prints output is a shell alias. Skills must explain context, give executable steps, handle failure cases, and state what done looks like. Most phase skills are 100–300 lines; a 20-line skill is a stub.

### §5 — Micro-context means micro
Each swarm subagent receives: its requirement node + blueprint spec + test contract + patterns. Not the full requirements graph, the full codebase, other tasks, or build history. The coordinator reads legacy source before dispatching; the developer subagent does not.

### §6 — Every "done" needs "still not done"
Status reports state: (1) what is verifiably true with evidence, (2) what is NOT yet true that callers may assume, (3) the next falsifiable claim.

### §7 — Three failures, then recon
After 3 failed attempts at the same problem: stop. Send a read-only recon agent before attempt 4. The third failure is evidence the model of the problem is wrong, not the fix.

### §8 — Every producer self-reviews its output (advisory)
Every producer runs an **advisory adversarial review** on its primary artifact at its done-gate — *before* declaring done — because every artifact in this pipeline is rendered/derived from upstream data and the render is trusting. Use `anti-legacy:adversarial-review` in **single-artifact mode**: `refine_loop descriptor --artifact <id>` resolves the rendered file + the source data the critic cross-checks (the requirements-graph §2 spine + the artifact's manifest `depends_on`); dispatch the read-only critic against it. On `REVISE`/`BLOCK`, run the **bounded loop** (`refine_loop decide --verdict … --attempt …`): re-run the producer to fix at source and re-review, capped at §7's three attempts (then recon), or proceed under a **stated** `--forced` override. This is advisory — it clears no gate and advances no phase (a critic verdict is never a gate approval, per Universal Don'ts). It is the pre-build analog of `anti-legacy:uat-reviewer` for built code. *"Adversarial review for all outputs, even individually."*

### §9 — Model-tier routing (recommendation, not enforcement)
Model selection is a **host-runtime decision** — this plugin is portable and cannot force a model on any CLI. So this is a **recommendation/policy layer**: the plugin *recommends* a tier per task type by its cognitive load, and the **host/orchestrator decides** whether to honor it when dispatching a subagent. Where a dispatching skill names a tier (its dispatch-point "Model tier:" line), that is the recommended tier for the *subagent it spawns*, keyed to this table. The plugin recommends; the runtime decides — a CLI with a single model, a fixed model, or a cost ceiling simply runs everything on what it has, and nothing in the pipeline breaks. Tiers are **relative** (cheap/fast · mid · strongest), not vendor model ids, so they map onto whatever model family the host exposes.

The routing principle: **match the model to the cognitive load of the task, not to the phase**. Mechanical work that is fully constrained by upstream data wastes a strong model; semantic judgment that decides what the rule *means* (or whether code is *wrong*) under-runs on a cheap one — and a wrong call there is silent and expensive (a missed business rule, a rubber-stamped critique). When unsure, round **up** for judgment work and **down** for deterministic renders.

| Task type (what the subagent actually does) | Recommended tier | Why |
|---|---|---|
| Mechanical / deterministic renders — docs synthesized from committed artifacts, diagram emission, offline packet/Markdown rendering, deploy-manifest templating | cheap / fast | Output is fully determined by upstream data; no open judgment. A stronger model adds cost, not correctness. |
| Bookkeeping — manifest/state transitions, registration, coverage scoring, digest refresh, checksum reconcile | cheap / fast | Deterministic state machine / arithmetic; there is one correct answer the code already computes. |
| Coordination / orchestration / planning — driving the pipeline phase-to-phase, assembling micro-context, building the task list, sequencing the swarm | mid | Routing and decomposition over known inputs; benefits from competence but not from peak reasoning. |
| Code translation / build — translating one requirement into idiomatic target code + tests, faithfully preserving every business rule and numeric precision | strongest | Precision-critical, rule-faithful synthesis; a silent drop or COMP-3 precision slip is catastrophic and hard to catch downstream. |
| Rule extraction — stating the business rule a legacy node encodes, with confidence + provenance | strongest | The meaning is the IP of the pipeline; a confidently-wrong rule poisons everything built on it. |
| Adversarial critique — the read-only critic in `anti-legacy:adversarial-review` (§8) and the independent UAT reviewer | strongest | Catching subtle divergence and resisting rubber-stamping is exactly where weak models fail quietly. |
| Semantic judgment — round-trip rule-coverage / semantic validation, completeness reasoning over scanner findings | strongest | Judging whether code *means* what a rule says (not whether an id is referenced) is the hardest reasoning in the pipeline. |
| Cross-source conflict detection — reconciling rules when two source repos disagree (the §I5 re-think; cross-repo RISK arbitration) | strongest | Conflict resolution requires holding and contrasting multiple groundings — peak reasoning, or escalate to a human. |

This recommendation never overrides a gate or a done-gate: a cheap-tier render and a strongest-tier extraction are both still subject to the same §6 honesty report, §8 self-review, and the gate evidence checks. Routing changes *which model* does the work, never *whether the work is verified*.

---

## Universal Don'ts

**Don't advance a phase with broken assertions.** If a producing skill's done-gate assertions are not met, do not call `manifest advance`. Surface the gap. Fix it.

**Don't drop file_path or legacy_components.** Any code that builds a node without `file_path`, or a requirement without `legacy_components`, is broken. Both are mandatory and non-null.

**Don't auto-clear human gates.** Of the nine gates, GATE_1_DESIGN, GATE_2_PLAN, GATE_3B_SEMANTIC, and GATE_4_UAT require a human. GATE_3_BUILD, GATE_5_COMPLETENESS, GATE_0_DISCOVERY, and GATE_3C_DIFFERENTIAL are the automated, evidence-cleared gates (GATE_1B_SEMANTIC_JOIN is multi-repo only — see `anti-legacy:gatekeeper` for the authoritative list). No skill, script, or agent may synthesize a human gate approval.

**Don't build Python parsers for modern languages.** Java, Go, TypeScript, C#, Python — the engine (`wicked-estate index`) indexes these natively, in the same pass as the mainframe estate. A regex parser is strictly less accurate and less complete than the engine's tree-sitter-backed graph. (`survey-modern` is retired — see §3; do not resurrect a grep track.)

**Don't write requirement nodes without business_rules.** A requirement with no business rules is a placeholder. Mark it `unresolvable` with a reason, or read the source file again.

**Don't skip parity rules on numeric outputs.** COMP-3 precision loss is silent and catastrophic. Money, rates, percentages, and counts all need `parity_rules` in their test contracts.

**Don't modify audit.jsonl.** It is append-only and tamper-evident. Gate approvals live here. Never edit, truncate, or rewrite it.

**Don't run swarm before Gate 2.** The task list is the approved build contract. Running against an unreviewed task list means building against an unreviewed plan.

---

## Skill Dispatch

| Situation | Skill |
|---|---|
| One-command full conversion (formulate DoD → approve → drive the whole pipeline) | `anti-legacy:convert` |
| Starting a new project | `anti-legacy:setup` |
| Indexing legacy source into the code graph (any language, mainframe or modern) | `anti-legacy:survey` |
| Structural analysis of the code graph | `anti-legacy:analyze` |
| Extracting business rules (crawl + annotate + coverage) | `anti-legacy:extraction` |
| Extracting error paths + negative requirements (deepen the crawl, after extraction) | `anti-legacy:negative-extraction` |
| Re-thinking annotated rules into the domain graph (§I5) | `anti-legacy:graph-translator` |
| Designing target architecture | `anti-legacy:blueprint` |
| Generating target-state build skills from the blueprint (a skill that writes build skills) | `anti-legacy:skill-forge` |
| Writing test contracts per requirement | `anti-legacy:test-strategy` |
| Compiling team review document | `anti-legacy:review-packet` |
| Producing the stakeholder deliverables package (graph ready → PRD, diagrams, test strategy + scripts, migration plan, risk/decisions/evidence logs) | `anti-legacy:deliverables` |
| Adversarial review of ANY generated output — individually or batch (read-only critic vs its source data → PASS/REVISE/BLOCK; advisory, not a gate; the pre-build analog of `anti-legacy:uat-reviewer`) | `anti-legacy:adversarial-review` |
| Detailed product requirements (PRD) | `anti-legacy:prd` |
| Architecture diagrams (Mermaid) | `anti-legacy:diagrams` |
| Detailed functional test strategy (data-parity / UAT / E2E / API) | `anti-legacy:test-plan` |
| Functional test scripts (data-parity / UAT / E2E / API) | `anti-legacy:test-scripts` |
| End-to-end migration plan (epics→stories→tasks→subtasks + Jira CSV) | `anti-legacy:migration-plan` |
| Risk log / register | `anti-legacy:risk-log` |
| Design decisions log (ADRs) | `anti-legacy:decisions-log` |
| Phase evidence log with receipts | `anti-legacy:evidence-log` |
| Checking / recording a gate | `anti-legacy:gatekeeper` |
| Creating build task list | `anti-legacy:planner` |
| Building target code | `anti-legacy:swarm` |
| Verifying compilation | `anti-legacy:target-review` |
| Verifying rule coverage (round-trip semantic check) | `anti-legacy:semantic-validation` |
| Executed output-parity gate — golden legacy corpus vs the built target's outputs (GATE_3C_DIFFERENTIAL) | `anti-legacy:differential-equivalence` |
| Assemble a golden corpus for GATE_3C from what is available (contracts' `expected_output` / a source oracle / captured legacy I/O), graded by provenance confidence | `anti-legacy:capture-corpus` |
| Driving the pipeline phase-to-phase | `anti-legacy:orchestrate` |
| Running UAT | `anti-legacy:uat-crew` |
| Generating deployment artifacts | `anti-legacy:deploy` |
| Translating one requirement → target code + tests (dispatched by swarm) | `anti-legacy:developer` |
| Independent read-only UAT review of a domain (dispatched by uat-crew) | `anti-legacy:uat-reviewer` |
| Code-graph engine capability + availability (index / query / annotate) | `anti-legacy:wicked-estate` |
| Pipeline internals SME + home of the shared `antilegacy_core` library | `anti-legacy:expert` |

---

## Key Scripts

All scripts are invoked through the workspace dispatcher: `python3 .anti-legacy/run.py <script-stem> <args...>`. The shared core lives in the namespaced `antilegacy_core` library (hosted by the `anti-legacy-expert` skill); single-owner leaf scripts live in their owning skill's `scripts/`. `run.py` discovers the installed library and dispatches each stem as `python -m antilegacy_core.<stem>` (shared core) or `skills/*/scripts/<stem>.py` (leaf) — never call a script by file path.

**Dispatcher carve-out (two exemptions).** Two callers are exempt from the dispatcher rule because `run.py` does not apply to them: (1) `anti-legacy:setup` runs at bootstrap, *before* `run.py` exists — it runs `python -m antilegacy_core.manifest init` (with `PYTHONPATH` at the library's parent, `skills/anti-legacy-expert/scripts`) to seed the manifest, then writes `run.py` from its bundled `assets/run.py.tmpl`. (2) the `develop-plugin` skill operates on the plugin **source tree itself**, not a workspace, so there is no `.anti-legacy/run.py` to dispatch through; it invokes scripts directly under the plugin root. Every other caller, in every workspace, must go through `.anti-legacy/run.py`.

| Script (stem) | Purpose | Never use for |
|---|---|---|
| `manifest` | Pipeline state — init, advance, register, gate, learn, check, status | Inferring phase from file presence |
| `git_brain` | Git-backed memory — init, store, search, ingest, sync, status | External brain services or npm packages |
| `wicked_estate` | The code-graph engine seam — resolve binary, index/stats/query/blast-radius/source/rank/cross-graph, resolve-symbol-id, annotate, by-requirement | Raw-SQLite graph reads (except the one documented read-only id-resolution lookup) |
| `coverage` | Resolved-or-flagged coverage over graph + `annotations.jsonl` → `coverage-report.{json,md}`; exits non-zero (lists unaccounted SymbolIds) when `coverage < 1.0` | A substitute for annotating the nodes |
| `precheck` | Producer-side readiness gate (`precheck <phase> [--advisory] [--json]`): per-phase completeness predicates + disk-reality reconcile; producers call `require_ready(phase, force=)` and REFUSE (exit 1) on an incomplete/orphaned/state-desynced pipeline. Read-only — never writes audit.jsonl, advances a phase, or clears a gate | Clearing or recording a gate (that is `manifest gate`) |
| `graph_normalizer` | Code graph → draft requirements scaffold (pinned reference; `domain_graph` is the production §I5 builder) | Front-half rule extraction (that is `extraction`) |
| `validator_discovery` | The build/semantic/UAT verifier — runs build tooling, writes evidence (`run --gate <id>`) | Clearing a gate manually |
| `packet_generator` | Requirements graph → offline Markdown packet | Replacing the human review |
| `refine_loop` | The bounded make→review→refine primitive (§8): `descriptor --artifact <id>` resolves the generic single-artifact critic target (rendered file + source data from the §2 spine + manifest `depends_on`); `decide --verdict … --attempt …` returns the next move (refine / stop-converged / stop-at-§7-cap-recommend-recon / forced) with a branchable exit code | Acting on the decision (it computes only — the agent runs the producer + critic) or clearing a gate |
| `differential_equivalence` | The executed output-parity harness (GATE_3C_DIFFERENTIAL, ISS-7): `run --corpus <golden> --actuals <target> [--contracts …]` diffs the built target's outputs against a golden legacy corpus field-by-field within each contract's `parity_rules` (precision-aware Decimal — COMP-3 safe), writing `differential-equivalence-report.json` with a `golden_confidence` + `gate_posture` (PASS/WARN/BLOCK/NOT_APPLICABLE). Vacuous-safe (no corpus → NOT_APPLICABLE) | Relaxing a `parity_rule` to force a money mismatch to pass, or treating a low/medium-confidence WARN as a hard block |
| `capture_corpus` | Assembles the golden corpus for GATE_3C from what is available — `assemble --contracts <dir> [--oracle <json>] [--captured <json>]` overlays sources by `scenario_id` (captured-legacy > source-oracle > contract-expected), tags each entry's `provenance`, and emits a provenance report grading overall confidence + warnings | Mislabeling a contract-expected/oracle golden as `captured-legacy` to force a hard gate (the provenance must be honest) |

`python3 .anti-legacy/run.py manifest status` is the authoritative pipeline state. File presence is not.

### Dispatcher
`.anti-legacy/run.py` is a thin exec shim written by `anti-legacy:setup` at init time. It **discovers** the bundled `antilegacy_core` library across install shapes and CLIs (a `.*/skills/*/scripts/antilegacy_core` glob — plugin install, `npx skills` flat install, or source tree), then dispatches a bare stem as `python -m antilegacy_core.<stem>` (shared core) or `skills/*/scripts/<stem>.py` (leaf), with the library parent on `PYTHONPATH`. The CWD is always the workspace, so `.anti-legacy/run.py` resolves relative to it on macOS/Linux/WSL/Windows alike — pure Python, no shell features. The `<script-stem>` is the bare name: no `scripts/` prefix, no `.py` suffix (`manifest`, `validator_discovery`, `git_brain`). Do not hand-edit `run.py`; re-run setup if the plugin root moves.
