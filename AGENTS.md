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

Every phase produces exactly one primary artifact. Each producing skill documents that artifact's field schema and done-gate assertions — produce them or flag why you cannot. Full JSON Schemas live under `schemas/`; the enriched requirements profile is `schemas/requirements-graph.enriched.schema.json`, gate-validated.

| Artifact | Schema + assertions live in |
|---|---|
| `graphs/<app>.db` (wicked-estate code graph) + `legacy-graph.digest.txt` (checksummed seam) | `anti-legacy:survey` |
| `annotations.jsonl` (rule overlay) + `coverage-report.json` | `anti-legacy:extraction` |
| `requirements_graph.json` | `anti-legacy:graph-translator` (§I5 re-think; later WF) |
| `blueprint.json` | `anti-legacy:blueprint` |
| `contracts/{domain}/{req_id}.contract.json` | `anti-legacy:test-strategy` |
| `task.md` | `anti-legacy:planner` |
| `evidence/build-integrity.json` | `anti-legacy:target-review` |
| `evidence/uat/{domain}.json` | `anti-legacy:uat-crew` |

Per-app graph DBs under `.anti-legacy/graphs/` are gitignored (rebuilt by survey); the committed seam is the deterministic `legacy-graph.digest.txt` whose SHA-256 is the checksummed `legacy-graph` artifact the gate/audit contract consumes.

Cross-artifact invariants no skill may relax (see Universal Don'ts): every wicked-estate node carries native `file`/`line` provenance (the engine asserts it); `legacy_components` and `business_rules` on every active requirement, `parity_rules` on every numeric output; every behavior-bearing graph node ends RESOLVED or RISK-flagged — never bare.

---

## Gate Approval Cycle

The full gate set is **seven** ids. The five mainline gates run in order, no skipping, no reordering: GATE_1_DESIGN, GATE_2_PLAN, GATE_3_BUILD, GATE_3B_SEMANTIC, GATE_4_UAT. GATE_1_DESIGN, GATE_2_PLAN, GATE_4_UAT require a human. GATE_3B_SEMANTIC is a human semantic review. GATE_3_BUILD auto-clears on evidence (build-integrity `status: PASS` + round-trip rule_coverage ≥ 1.0); no skill, script, or agent may synthesize a human gate. Two further gates sit outside the mainline sequence: **GATE_0_DISCOVERY** (automated, runs post-survey) and **GATE_1B_SEMANTIC_JOIN** (multi-repo only — the semantic-join side phase). `anti-legacy:gatekeeper` is the authoritative list of all seven gates, their evaluators, and their required evidence ids.

### Advance preconditions (phase → required gate)

`manifest advance` is gate-precondition-aware. The check fires on **exit** — when the pipeline tries to LEAVE a gate phase it requires the bound gate(s) to be `passed` or `waived` first; advancing INTO a gate phase is always allowed so the human can sign while parked there. The data-driven map (mirrored in `manifest.py`'s `GATE_PHASE_PRECONDITIONS`):

| Leaving phase | Requires gate(s) `passed`/`waived` |
|---|---|
| `gate-design-review` | GATE_1_DESIGN |
| `gate-plan-review` | GATE_2_PLAN |
| `gate-build-integrity` | GATE_3_BUILD **and** GATE_3B_SEMANTIC |
| `gate-uat-signoff` | GATE_4_UAT |

If a required gate is not `passed`/`waived`, `advance` exits non-zero and the phase is unchanged — sign or waive the gate first. GATE_0_DISCOVERY and GATE_1B_SEMANTIC_JOIN are intentionally NOT in this map: neither has a dedicated `gate-*` phase enum value (GATE_0 is post-survey automated; GATE_1B is the optional semantic-join side phase), so they are enforced by their own skills and runners, not by the advance precondition.

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

The per-gate human-review checklists (what each reviewer verifies before `passed`), the reviewer roles, the required evidence ids, and the verification commands live in `anti-legacy:gatekeeper` (its Gate Definitions table + each gate's `check` section). GATE_3_BUILD's automated build tiers live in `anti-legacy:gatekeeper` / `anti-legacy:target-review`. A `failed` GATE_1 opinion that names the wrong nodes triggers a targeted re-run (`extraction` re-crawls/re-annotates the named nodes — idempotent, only the named nodes are re-touched — the packet regenerates, the gate is re-presented) — not a full pipeline restart.

---

## Working Style

### §1 — Survey is structural, not semantic
Survey → topology (`wicked-estate index`), not reading source for rules. Extraction → semantics: crawls the graph with adaptive ring expansion and reads each node's source slice (`wicked-estate source <name>`). Survey that reads source for business rules, or extraction that skips the source slice, is doing the wrong phase.

### §2 — The traceability thread never breaks
wicked-estate node (native `file`/`line` provenance) → its `requirement` annotation + `annotations.jsonl` overlay row (keyed `{db_id, symbol_id}`, carrying `provenance` = the ring nodes/edges that grounded the rule) → `legacy_components` → `task.md` task → `req_id` → `uat verdict`. The annotation is SymbolId-keyed because names are not unique (carddemo `MAIN-PARA`×21) — the helper resolves name→SymbolId before every write, so the link binds the exact scoped node. A broken link means a swarm agent cannot trace back to source; fix it before advancing.

### §3 — One engine indexes the whole estate
`wicked-estate index` captures the mainframe estate (COBOL/JCL/CICS/IMS/DB2 — module/function/field, JCL step/dataset, cics_program/cics_map, ims_database/segment, db2_table) AND modern languages (Java, C#, Go, Python, TypeScript, Rust, …) in one pass, resolving cross-language edges automatically (JCL `EXEC PGM` → COBOL, `CALL` → COBOL). No language-routing split, no batch Python extractor. Do not add Python parsers — parsing is a wicked-estate concern. `survey-modern` survives only as a thin grep fallback for source the engine cannot index.

### §4 — Skills are instructions, not thin wrappers
A skill that calls one script and prints output is a shell alias. Skills must explain context, give executable steps, handle failure cases, and state what done looks like. Most phase skills are 100–300 lines; a 20-line skill is a stub.

### §5 — Micro-context means micro
Each swarm subagent receives: its requirement node + blueprint spec + test contract + patterns. Not the full requirements graph, the full codebase, other tasks, or build history. The coordinator reads legacy source before dispatching; the developer subagent does not.

### §6 — Every "done" needs "still not done"
Status reports state: (1) what is verifiably true with evidence, (2) what is NOT yet true that callers may assume, (3) the next falsifiable claim.

### §7 — Three failures, then recon
After 3 failed attempts at the same problem: stop. Send a read-only recon agent before attempt 4. The third failure is evidence the model of the problem is wrong, not the fix.

---

## Universal Don'ts

**Don't advance a phase with broken assertions.** If a producing skill's done-gate assertions are not met, do not call `manifest advance`. Surface the gap. Fix it.

**Don't drop file_path or legacy_components.** Any code that builds a node without `file_path`, or a requirement without `legacy_components`, is broken. Both are mandatory and non-null.

**Don't auto-clear human gates.** Of the seven gates, GATE_1_DESIGN, GATE_2_PLAN, GATE_3B_SEMANTIC, and GATE_4_UAT require a human. GATE_3_BUILD and GATE_0_DISCOVERY are the only automated gates (GATE_1B_SEMANTIC_JOIN is multi-repo only — see `anti-legacy:gatekeeper` for the authoritative list). No skill, script, or agent may synthesize a human gate approval.

**Don't build Python parsers for modern languages.** Java, Go, TypeScript, C#, Python — the LLM reads these natively. A grep pattern in survey-modern beats a regex parser every time.

**Don't write requirement nodes without business_rules.** A requirement with no business rules is a placeholder. Mark it `unresolvable` with a reason, or read the source file again.

**Don't skip parity rules on numeric outputs.** COMP-3 precision loss is silent and catastrophic. Money, rates, percentages, and counts all need `parity_rules` in their test contracts.

**Don't modify audit.jsonl.** It is append-only and tamper-evident. Gate approvals live here. Never edit, truncate, or rewrite it.

**Don't run swarm before Gate 2.** The task list is the approved build contract. Running against an unreviewed task list means building against an unreviewed plan.

---

## Skill Dispatch

| Situation | Skill |
|---|---|
| Starting a new project | `anti-legacy:setup` |
| Indexing legacy source into the code graph (any language) | `anti-legacy:survey` |
| Fallback grep track for source the engine can't index | `anti-legacy:survey-modern` |
| Structural analysis of the code graph | `anti-legacy:analyze` |
| Extracting business rules (crawl + annotate + coverage) | `anti-legacy:extraction` |
| Re-thinking annotated rules into the domain graph (§I5, later WF) | `anti-legacy:graph-translator` |
| Designing target architecture | `anti-legacy:blueprint` |
| Writing test contracts per requirement | `anti-legacy:test-strategy` |
| Compiling team review document | `anti-legacy:review-packet` |
| Checking / recording a gate | `anti-legacy:gatekeeper` |
| Creating build task list | `anti-legacy:planner` |
| Building target code | `anti-legacy:swarm` |
| Verifying compilation | `anti-legacy:target-review` |
| Verifying rule coverage (round-trip semantic check) | `anti-legacy:semantic-validation` |
| Driving the pipeline phase-to-phase | `anti-legacy:orchestrate` |
| Running UAT | `anti-legacy:uat-crew` |
| Generating deployment artifacts | `anti-legacy:deploy` |

---

## Key Scripts

All scripts are invoked through the workspace dispatcher: `python3 .anti-legacy/run.py <script-stem> <args...>`. Never call `python3 scripts/<x>.py` directly — `scripts/` is not on the workspace path; `run.py` resolves the script under the baked-in plugin root.

**Dispatcher carve-out (two exemptions).** Two callers are exempt from the dispatcher rule because `run.py` does not apply to them: (1) `anti-legacy:setup` runs at bootstrap, *before* `run.py` exists — it calls the plugin-root scripts by absolute path to write `run.py` and initialize the manifest. (2) the `develop-plugin` skill operates on the plugin **source tree itself**, not a workspace, so there is no `.anti-legacy/run.py` to dispatch through; it invokes scripts directly under the plugin root. Every other caller, in every workspace, must go through `.anti-legacy/run.py`.

| Script (stem) | Purpose | Never use for |
|---|---|---|
| `manifest` | Pipeline state — init, advance, register, gate, learn, check, status | Inferring phase from file presence |
| `git_brain` | Git-backed memory — init, store, search, ingest, sync, status | External brain services or npm packages |
| `wicked_estate` | The code-graph engine seam — resolve binary, index/stats/query/blast-radius/source/rank/cross-graph, resolve-symbol-id, annotate, by-requirement | Raw-SQLite graph reads (except the one documented read-only id-resolution lookup) |
| `coverage` | Resolved-or-flagged coverage over graph + `annotations.jsonl` → `coverage-report.{json,md}`; exits non-zero (lists unaccounted SymbolIds) when `coverage < 1.0` | A substitute for annotating the nodes |
| `graph_normalizer` | Code graph → draft requirements scaffold (§I5 re-think, later WF) | Front-half rule extraction (that is `extraction`) |
| `validator_discovery` | The build/semantic/UAT verifier — runs build tooling, writes evidence (`run --gate <id>`) | Clearing a gate manually |
| `packet_generator` | Requirements graph → offline Markdown packet | Replacing the human review |

`python3 .anti-legacy/run.py manifest status` is the authoritative pipeline state. File presence is not.

### Dispatcher
`.anti-legacy/run.py` is a thin exec shim written by `anti-legacy:setup` at init time, with the resolved **absolute** plugin root baked in, so it always finds the real script at `<plugin_root>/scripts/<stem>.py`. The CWD is always the workspace, so `.anti-legacy/run.py` resolves relative to it on macOS/Linux/WSL/Windows alike — pure Python, no shell features. The `<script-stem>` is the bare name: no `scripts/` prefix, no `.py` suffix (`manifest`, `validator_discovery`, `git_brain`). Do not hand-edit `run.py`; re-run setup if the plugin root moves.
