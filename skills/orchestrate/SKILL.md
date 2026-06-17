---
name: anti-legacy:orchestrate
description: >-
  Master orchestrator for the anti-legacy pipeline.
  Sequences all 16 phases (plus an optional semantic-join phase for multi-repo
  surveys), enforces all eight gates (GATE_0_DISCOVERY, GATE_1_DESIGN,
  GATE_1B_SEMANTIC_JOIN, GATE_2_PLAN, GATE_3_BUILD, GATE_3B_SEMANTIC,
  GATE_4_UAT, GATE_5_COMPLETENESS), and resumes from wherever it left off.
  Start here — this skill dispatches everything else.
---

# Orchestrate — Pipeline Runner

Master entry point for the anti-legacy modernization pipeline. Sequences all
phases, enforces gates, and resumes from the current manifest state.

There are **eight gates** total, matching `anti-legacy:gatekeeper`'s enumeration:
`GATE_0_DISCOVERY` (automated, after survey), `GATE_1_DESIGN`, `GATE_1B_SEMANTIC_JOIN`
(only for multi-repo semantic-join surveys), `GATE_2_PLAN`, `GATE_3_BUILD` (automated),
`GATE_3B_SEMANTIC`, `GATE_4_UAT`, and `GATE_5_COMPLETENESS` (automated, final completeness
gate). Four of these (`GATE_1_DESIGN`, `GATE_2_PLAN`, `GATE_3B_SEMANTIC`, `GATE_4_UAT`)
require human sign-off; the rest are automated. Every `--opinion passed` sign-off MUST cite
`--evidence` with registered artifact ids — the manifest content-verifies each cited id
(registered AND status not failed/pending AND file present AND checksum matches) and
hard-fails the gate otherwise.

**Generalized gate kick-back.** Recording any gate `--opinion failed` rewinds the pipeline
to that gate's producing phase (a guided reset — `manifest.py` resets `phase.current`,
names the skill to re-run, and exits non-zero / code 3). It does NOT auto-dispatch the
skill. On a non-zero `manifest gate` exit, read the printed reset target and re-run the
named producing skill before re-presenting the gate. See `anti-legacy:gatekeeper`
(Generalized gate kick-back) for the full per-gate phase/skill map.

Three phases host the content agents' work between gates: `functional-tests` (after
GATE_2_PLAN, before swarm — blocking pre-build validation), `document` (after GATE_4_UAT,
before final-review — the documentation pass), and `final-review` (after document, before
deploy — the automated GATE_5_COMPLETENESS completeness gate). Their producing skills are
dispatched in the phase table below; this skill only sequences them.

## How to Invoke

> "Run the anti-legacy pipeline" or "Continue the modernization"

## Prerequisites

- Legacy source code accessible as a local directory
- `python3` available
- `git` initialized in the workspace

## Workflow

### Step 1 — Read Pipeline State

```bash
python3 .anti-legacy/run.py manifest status
```

If manifest doesn't exist, start from Phase 1 (setup). Otherwise, resume from
the current phase reported by `status`.

### Step 2 — Execute the Current Phase

Use the phase dispatch table below. After each phase completes, check if a gate
blocks the next phase. After each phase, run `python3 .anti-legacy/run.py manifest status` to confirm advancement.

---

## Phase Dispatch Table

Execute phases **in order**. Each phase produces one primary artifact.
After each phase, run `python3 .anti-legacy/run.py manifest status` to confirm advancement.

### Phase 1: Setup

**Skill**: `anti-legacy:setup`
**Produces**: `.anti-legacy/manifest.json`, `config.json`
**Blocks**: Nothing — always runs first
**Gate**: None

```
What to do:
1. Run anti-legacy:setup to initialize the workspace
2. Confirm manifest.json exists and shows phase: survey
3. If the user hasn't specified a target stack, ask now
```

---

### Phase 2: Survey

**Skill**: `anti-legacy:survey`
**Produces**: per-app `.anti-legacy/graphs/<app>.db` + `.anti-legacy/legacy-graph.digest.txt`
**Blocks**: Must have config.json with legacy source paths
**Gate**: None

```
What to do:
1. Run anti-legacy:survey, which shells `wicked-estate index <path> --db .anti-legacy/graphs/<app>.db`
   (via the `wicked_estate` helper) once per `source_apps` entry — one DB per source repo so
   cross-graph federation (the multi-repo merge case) works. Handles mainframe (COBOL, JCL, CICS,
   IMS, DB2) and modern (Java/.NET/Python) sources alike — wicked-estate owns indexing.
2. Validate: each per-app DB has ≥1 behavior-bearing node (`wicked-estate stats` reports node/edge counts).
3. Re-project confirmed domain terms onto each fresh DB: python3 .anti-legacy/run.py vocabulary project --db .anti-legacy/graphs/<app>.db
   — a `--fresh` index WIPES prior `domain_*` term tags (they live only in the gitignored DB), so re-apply them.
   No-op on the first survey (nothing confirmed yet); MANDATORY on any re-survey after extraction confirmed terms.
4. Write the deterministic stats digest: python3 .anti-legacy/run.py wicked_estate write_digest --out .anti-legacy/legacy-graph.digest.txt --db .anti-legacy/graphs/<app>.db [--db ... per app]
5. Register the digest (text, no --schema): python3 .anti-legacy/run.py manifest register legacy-graph --path legacy-graph.digest.txt --format text --produced-by anti-legacy:survey --status final
6. Advance: python3 .anti-legacy/run.py manifest advance survey
```

> **Cross-phase invariant — reproject after every graph rebuild.** Domain-term tags
> (`domain_*`) are a disposable projection of the committed glossary onto the gitignored,
> rebuilt-by-survey graph DB — never the system of record. Any phase that rebuilds a graph
> (a fresh survey) or confirms new terms (extraction) MUST re-run `vocabulary project`, or
> the engine's `cluster`/`by-requirement`/`read-kv` silently degrade to name-only
> resolution. See `anti-legacy:vocabulary` (Project confirmed terms) for the two-axes model.

---

### Phase 3: Analyze

**Skill**: `anti-legacy:analyze`
**Produces**: `analysis.md` (complexity, risk, shared assets)
**Blocks**: Must have `legacy-graph` registered (the survey digest + per-app DBs under `.anti-legacy/graphs/`)
**Gate**: None

```
What to do:
1. Run anti-legacy:analyze — it queries the per-app graphs via the `wicked_estate` helper
   (`python3 .anti-legacy/run.py wicked_estate <cmd> --db .anti-legacy/graphs/<app>.db`):
   entry points + importance via `rank` (PageRank), shared-asset coupling via cross-domain
   `blast_radius` (estate uses/accesses edges), batch-vs-online via JCL/CICS estate node kinds.
   Multi-repo coupling uses `cross_graph` over the per-app DB list.
2. Surface shared tables/files, complexity metrics, risk areas — every claim traced to a helper query.
3. Register and advance
```

---

### Phase 4: Extraction (crawl → annotate → coverage)

**Skill**: `anti-legacy:extraction`
**Produces**: `.anti-legacy/coverage-report.json` + `.anti-legacy/coverage-report.md`,
  the `.anti-legacy/annotations.jsonl` IP overlay, and written wicked-estate `requirement` fields
**Blocks**: Must have `legacy-graph` registered (survey digest + per-app DBs) and analysis.md
**Gate**: None — but feeds GATE_1
**Phase value**: advances `graph-translate`

```
What to do:
1. Run anti-legacy:extraction. It crawls the per-app graph with adaptive ring expansion
   (node + 1 up / 1 down via the `wicked_estate` helper's blast_radius/query/source), driving
   the worklist most-important-first off `wicked-estate rank`.
2. Per behavior-bearing node, the skill ends RESOLVED (rule + confidence/provenance written via
   `we.annotate` into the wicked-estate `requirement` field AND mirrored to
   `.anti-legacy/annotations.jsonl`) or RISK-flagged (placed on the HITL research queue). No
   bare nodes — every behavior-bearing node terminates RESOLVED or RISK.
3. Emit coverage: python3 .anti-legacy/run.py coverage --db .anti-legacy/graphs/<app>.db --out .anti-legacy/coverage-report.json
   (resolved-or-flagged metric over behavior-bearing nodes; exits non-zero while coverage < 1.0).
4. Done-gate (BLOCKING): do not register/advance until coverage.py exits 0 (coverage == 1.0,
   UNACCOUNTED == 0) — the provable terminal of §I2. The printed unaccounted SymbolIds are the
   remaining worklist.
5. Register coverage evidence: python3 .anti-legacy/run.py manifest register coverage-report --path coverage-report.json --format json --produced-by anti-legacy:extraction --status final --depends-on legacy-graph
6. Project confirmed domain terms onto the graph (extraction is where terms reach `confirmed`):
   python3 .anti-legacy/run.py vocabulary project --db .anti-legacy/graphs/<app>.db
   — binds confirmed terms as native `domain_*` tags so the domain graph / clustering resolves
   through the engine. Read the summary: a `GAP:` line (`unbound` / `all_skipped`) is a coverage
   signal to note in the status report, not a blocker. `confirmed_available=0` is a clean no-op.
   A `DRIFT:` line (same glossary, changed binding set) is a re-review trigger (ISS-04).
6b. Reprojection-enforcement gate (BLOCKING — ISS-03): do not advance until the projection is
   present. A fresh survey wipes domain_* tags, so this catches a skipped reproject:
   python3 .anti-legacy/run.py vocabulary check-projection --db .anti-legacy/graphs/<app>.db
   — exit 1 (BLOCKED) iff confirmed terms ground on the graph but it carries 0 domain_* tags;
   exit 0 when there is nothing to enforce (no confirmed terms, or none present in this graph).
7. Refresh + re-register the digest so it reflects the freshly written `requirement` fields:
   python3 .anti-legacy/run.py wicked_estate write_digest --out .anti-legacy/legacy-graph.digest.txt --db .anti-legacy/graphs/<app>.db [--db ...]
   then python3 .anti-legacy/run.py manifest register legacy-graph --path legacy-graph.digest.txt --format text --produced-by anti-legacy:extraction --status final
8. Advance: python3 .anti-legacy/run.py manifest advance graph-translate
```

---

### Phase 4b: Graph Translator (re-think → domain graph)

**Skill**: `anti-legacy:graph-translator`
**Produces**: `.anti-legacy/requirements/requirements_graph.json`, `.anti-legacy/requirements/dispositions.json`,
  `.anti-legacy/requirements/roundtrip-coverage.json`
**Blocks**: Must have completed extraction (coverage == 1.0)
**Gate**: None
**Phase value**: remains in `graph-translate` (same manifest phase slot as extraction)

```
What to do:
1. Run anti-legacy:graph-translator. It consumes the ANNOTATED code graph
   (wicked-estate DBs with requirement fields + annotations.jsonl overlay)
   plus the cluster communities (wicked-estate cluster weight=calls) and
   re-thinks them into the TARGET-STATE domain graph.
2. Disposition per legacy rule: keep | modify | drop | new.
   Every `drop` requires explicit rationale (written to dispositions.json).
3. Requirement-coverage invariant: the domain graph must cover EVERY
   requirement edge from the annotated code graph. 0 silent drops.
   python3 .anti-legacy/run.py domain_graph --db .anti-legacy/graphs/<app>.db --out .anti-legacy/requirements/requirements_graph.json
4. Register: python3 .anti-legacy/run.py manifest register requirements-graph --path requirements/requirements_graph.json --format json --schema schemas/requirements-graph.enriched.schema.json --produced-by anti-legacy:graph-translator --status final --depends-on legacy-graph,coverage-report
5. Register the disposition-aware round-trip evidence (the checksummed audit seam GATE_1_DESIGN cites — proves every legacy rule is represented OR dropped-with-reason): python3 .anti-legacy/run.py manifest register roundtrip-coverage --path requirements/roundtrip-coverage.json --format json --produced-by anti-legacy:graph-translator --status final --depends-on requirements-graph
6. Advance: python3 .anti-legacy/run.py manifest advance blueprint
```

---

### Phase 5: Blueprint

**Skill**: `anti-legacy:blueprint`
**Produces**: `blueprint.json`
**Blocks**: Must have requirements_graph.json
**Gate**: None

```
What to do:
1. Read the requirements graph
2. Design target architecture — packages, services, data layer
3. Map each requirement to target files
4. Register blueprint.json
5. Advance
```

---

### Phase 6: Test Strategy

**Skill**: `anti-legacy:test-strategy`
**Produces**: `contracts/{req_id}.json` for each requirement
**Blocks**: Must have blueprint.json
**Gate**: None

```
What to do:
1. For each requirement, generate a test contract
2. Each contract must have ≥1 happy_path, ≥1 error scenario
3. Numeric outputs must have parity_rules
4. Register contracts
5. Advance
```

---

### Phase 7: Review Packet

**Skill**: `anti-legacy:review-packet`
**Produces**: `review_packet.md`
**Blocks**: Must have requirements_graph.json + blueprint.json + contracts/
**Gate**: None — but feeds GATE_1

```
What to do:
1. Produce the stakeholder deliverables package first: run `anti-legacy:deliverables`
   (PRD, diagrams, test strategy + scripts, migration plan, risk/decisions/evidence logs →
   .anti-legacy/deliverables/). It registers each deliverable; it does NOT advance the phase.
2. Run packet_generator to compile the review packet
3. Print a summary of the packet AND the deliverables package (deliverables/README.md) for the user
4. Register review_packet.md
5. Advance
```

---

### 🚧 GATE 1 — Design Review

**Skill**: `anti-legacy:gatekeeper`
**Requires**: requirements_graph.json + blueprint.json + contracts/ + review_packet.md

```
STOP. This gate requires human review and sign-off.

Tell the user:
  "The review packet is ready at review_packet.md.
   Please review the requirements graph, blueprint, and contracts,
   and sign off when ready.
   Say 'approve gate 1' to continue."

Do NOT proceed until the user explicitly approves.
When approved: python3 .anti-legacy/run.py manifest gate GATE_1_DESIGN --opinion passed --evaluator <user> --evidence review-packet,requirements-graph,blueprint-json
```

---

### Phase 8: Planner

**Skill**: `anti-legacy:planner`
**Produces**: `task.md` (topologically sorted build plan)
**Blocks**: GATE_1 must be approved (needs blueprint.json + contracts/)
**Gate**: None — but feeds GATE_2

```
What to do:
1. Topologically sort requirements by dependencies
2. Group into layers: L0=entities, L1=repositories, L2=services, L3=API
3. Estimate hours per task
4. Register task.md
5. Advance
```

---

### 🚧 GATE 2 — Plan Review

**Skill**: `anti-legacy:gatekeeper`
**Requires**: `task-plan` (task.md) + blueprint.json

```
STOP. This gate requires human review and sign-off.

Tell the user:
  "The execution plan is ready at task.md.
   {N} tasks in {L} layers, estimated {H} total hours.
   Say 'approve gate 2' to continue."

Do NOT proceed until the user explicitly approves.
When approved: python3 .anti-legacy/run.py manifest gate GATE_2_PLAN --opinion passed --evaluator <user> --evidence task-plan
```

---

### Phase 8b: Functional Tests (pre-build validation)

**Skill**: built separately (content agent) — slotted at the `functional-tests` phase
**Produces**: the pre-build functional-test artifacts (owned by that skill)
**Blocks**: GATE_2 must be approved — this is a BLOCKING pre-build validation pass
**Gate**: None — but it MUST pass before swarm starts building
**Phase value**: advances `functional-tests`

```
What to do:
1. Advance into the functional-tests phase: python3 .anti-legacy/run.py manifest advance functional-tests
2. Run the functional-tests skill (built separately) to validate the approved plan/contracts
   against expected behavior BEFORE any target code is written. This is the blocking
   pre-build gate-of-discipline: a failure here means the plan is not yet buildable.
3. Do NOT advance to `build` until this phase's done-gate is satisfied.
4. Advance: python3 .anti-legacy/run.py manifest advance build
```

---

### Phase 9: Swarm Build

**Skill**: `anti-legacy:swarm`
**Produces**: Target source files per task.md
**Blocks**: GATE_2 must be approved AND `functional-tests` phase complete
**Gate**: None

```
What to do:
1. Read task.md — execute tasks layer by layer (L0 first)
2. For each task, dispatch a anti-legacy:developer subagent with:
   - The requirement from requirements_graph.json
   - The target file from blueprint.json
   - The test contract from contracts/
   - Access to the legacy source via file_path
3. Wait for each layer to complete before starting the next
4. Track completed tasks in task.md (mark [x])
5. Advance when all tasks are done
```

---

### Phase 10: Target Review

**Skill**: `anti-legacy:target-review`
**Produces**: `evidence/build-integrity.json`
**Blocks**: Must have built target files
**Gate**: GATE_3 (auto-clear)

```
What to do:
1. Run `python3 .anti-legacy/run.py validator_discovery run --gate GATE_3_BUILD --workspace {target_path} --config .anti-legacy/config.json` (target_verifier.py is removed; validator_discovery.py is the verifier)
2. If build fails, report errors back to the swarm for fixing
3. Loop until build passes
4. Register evidence (`build-integrity`, `code-quality`, `security-scan`, `functional-comparison-report`)
5. Gate auto-clears when `evidence/build-integrity.json` has top-level `status: PASS` AND `evidence/functional_comparison_report.json` reports 0 FAIL with `rule_coverage >= 1.0` (both enforced deterministically by `validator_discovery._run_gate_3_build`)
```

---

### 🚧 GATE 3 — Build Integrity (Automated)

**Auto-clears** when `evidence/build-integrity.json` has top-level `status: PASS`
AND `evidence/functional_comparison_report.json` reports 0 FAIL requirements with
`rule_coverage >= 1.0`. No human intervention needed.

The sign-off MUST cite the registered build evidence — the manifest content-verifies
each id, so a no-evidence clear hard-fails:

```
python3 .anti-legacy/run.py manifest gate GATE_3_BUILD --opinion passed --evaluator anti-legacy:target-review --evidence build-integrity,code-quality,security-scan,functional-comparison-report
```

---

### Phase 11: Semantic Validation

**Skill**: `anti-legacy:semantic-validation`
**Produces**: `evidence/semantic-validation-report.json`, `evidence/semantic_validation_report.md`
**Blocks**: GATE_3 must be approved
**Gate**: None — but feeds GATE_3B

```
What to do:
1. Run `python3 .anti-legacy/run.py semantic_validator` to extract connected dependency chains
2. Deploy @validator subagent to perform side-by-side legacy vs target code review
3. Identify semantic discrepancies and record gaps using `python3 .anti-legacy/run.py semantic_validator record-gap`
4. Update requirements_graph.json to back-propagate the gaps
5. Register evidence and advance
```

---

### 🚧 GATE 3B — Semantic Review

**Skill**: `anti-legacy:gatekeeper`
**Requires**: semantic-validation-report

```
STOP. This gate requires human review and sign-off.

Tell the user:
  "Semantic validation is complete. {G} gaps identified.
   Review evidence/semantic_validation_report.md for details.
   Say 'approve gate 3b' to continue."

Do NOT proceed until the user explicitly approves.
When approved: python3 .anti-legacy/run.py manifest gate GATE_3B_SEMANTIC --opinion passed --evaluator <user> --evidence semantic-validation-report
```

---

### Phase 12: UAT Crew

**Skill**: `anti-legacy:uat-crew`
**Produces**: `evidence/uat/{domain}.json` per domain
**Blocks**: GATE_3B must be approved
**Gate**: None — but feeds GATE_4

```
What to do:
1. Dispatch anti-legacy:uat-reviewer subagent (read-only, different from build subagent)
2. Review each requirement's target implementation against:
   - Business rules in requirements_graph.json
   - Test contracts in contracts/
   - Legacy behavior from legacy source
3. Produce verdict per domain: PASS or FAIL
4. FAIL if any CRITICAL or MAJOR finding
5. Register evidence
6. Advance
```

---

### 🚧 GATE 4 — UAT Review

**Skill**: `anti-legacy:gatekeeper`
**Requires**: All UAT evidence files

```
STOP. This gate requires human review and sign-off.

Tell the user:
  "UAT is complete. {P} domains passed, {F} domains need attention.
   Review evidence/uat/ for findings.
   Say 'approve gate 4' to continue."

Do NOT proceed until the user explicitly approves.
When approved: python3 .anti-legacy/run.py manifest gate GATE_4_UAT --opinion passed --evaluator <user> --evidence uat-summary,uat-verdicts
```

---

### Phase 12b: Document

**Skill**: built separately (content agent) — slotted at the `document` phase
**Produces**: the documentation artifacts (owned by that skill)
**Blocks**: GATE_4 must be approved
**Gate**: None — but feeds GATE_5_COMPLETENESS
**Phase value**: advances `document`

```
What to do:
1. Advance into the document phase: python3 .anti-legacy/run.py manifest advance document
2. Run the document skill (built separately) to produce the target-system documentation
   (architecture, runbook, requirement-to-code traceability, etc.).
3. Advance: python3 .anti-legacy/run.py manifest advance final-review
```

---

### Phase 12c: Final Review (GATE 5 — Completeness, Automated)

**Skill**: `anti-legacy:gatekeeper` (check) + the `final-review` completeness-report writer (built separately)
**Produces**: `evidence/completeness_report.json` (registered as `completeness-report`)
**Blocks**: Must be at the `final-review` phase with `document` complete
**Gate**: GATE_5_COMPLETENESS (auto-clear on `completeness-report` status: PASS; kicks back to `document` on FAIL)

```
What to do:
1. Run the final-review completeness check (built separately), which writes
   evidence/completeness_report.json and registers it as `completeness-report`.
2. Auto-clear when the report status is PASS:
   python3 .anti-legacy/run.py manifest gate GATE_5_COMPLETENESS --opinion passed --evaluator anti-legacy:final-review --evidence completeness-report
3. On FAIL, record failed — this kicks the pipeline BACK to the `document` phase
   (manifest resets phase.current and exits non-zero / code 3); re-run the document
   skill, then re-present GATE_5:
   python3 .anti-legacy/run.py manifest gate GATE_5_COMPLETENESS --opinion failed --evaluator anti-legacy:final-review --rationale "{reason}"
4. Once GATE_5 is passed/waived, advance to deploy.
```

---

### 🚧 GATE 5 — Completeness (Automated)

**Auto-clears** when `evidence/completeness_report.json` has top-level `status: PASS`.
A FAIL recorded via `--opinion failed` kicks the pipeline back to the `document` phase
(see Generalized gate kick-back, above) — no full restart. No human intervention needed.

```
python3 .anti-legacy/run.py manifest gate GATE_5_COMPLETENESS --opinion passed --evaluator anti-legacy:final-review --evidence completeness-report
```

---

### Phase 13: Deploy

**Skill**: `anti-legacy:deploy`
**Produces**: Deployment artifacts (Dockerfile, CI config, etc.)
**Blocks**: GATE_5_COMPLETENESS must be passed/waived (leaving `final-review` requires it)
**Gate**: None

```
What to do:
1. Generate deployment configuration for target stack
2. Register artifacts
3. Advance to "complete"
4. Print final summary
```

---

## Resume Logic

When resuming a pipeline that's already in progress:

1. Run `python3 .anti-legacy/run.py manifest status` — read current_phase
2. Check if a gate is blocking (PENDING gates before current phase)
3. If gate is blocking, tell the user what needs approval
4. If no gate blocking, execute the current phase from the dispatch table
5. After phase completes, check for the next gate or advance

## Error Handling

If any phase fails:
- Do NOT advance the manifest
- Report the specific failure to the user
- The user can say "retry" to re-run the current phase
- The user can say "skip" to manually advance (not recommended)

## Completion

When all phases are done and all gates are cleared:

```
Pipeline complete.

Summary:
- Legacy source: {path}
- Target stack: {stack}
- Requirements: {count}
- Domains: {count}
- Build: PASS
- UAT: {verdict}
- Artifacts: {list}
```
