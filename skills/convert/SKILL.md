---
name: anti-legacy:convert
description: >-
  One command to kick off a full modernization conversion. Formulates acceptance
  criteria, presents for user review, then drives the pipeline to completion
  via /goal — dispatching subagents for all work while monitoring and reporting.
  Use when: "convert this codebase", "modernize this app", "run the full
  conversion", "kick off the migration".
---

# Convert — Autonomous Modernization Runner

Single command entry point. You are the **controller** — you formulate the
definition of done, get approval, then drive the pipeline via `/goal` until
every acceptance criterion is met. You never do phase work yourself — you
dispatch subagents, monitor results, enforce gates, and report progress.

## How to Invoke

> "Convert this codebase" · "Modernize this app" · "Kick off the migration"

## Prerequisites

- Legacy source code accessible as a local directory
- `python3` and `git` available in the workspace
- The user has told you what to convert and (ideally) the target stack

---

## Step 1 — Pre-Discovery: Understand the Scope

Before you can set acceptance criteria, you need to understand what you're
converting. Do this BEFORE survey.

### 1a. Initialize (if needed)

```bash
python3 .anti-legacy/run.py manifest status 2>/dev/null || echo "NO_MANIFEST"
```

If `NO_MANIFEST`, dispatch a subagent for `anti-legacy:setup` to create the
workspace and `config.json`. If manifest exists, read current state and resume
(see Resume section at the bottom).

### 1b. Scan the source

Read `.anti-legacy/config.json` to get `source_apps`, `target_stack`, `migration_mode`.

Then **scan each source directory yourself** (not a subagent — you need to see this):

```bash
# For each source app, get a quick picture
find {source_path} -type f -name '*.cbl' -o -name '*.cpy' -o -name '*.jcl' \
  -o -name '*.java' -o -name '*.cs' -o -name '*.go' -o -name '*.py' \
  -o -name '*.ts' -o -name '*.kt' -o -name '*.rs' | head -100

# Count files by type
find {source_path} -type f | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -20

# Estimate size
find {source_path} -type f \( -name '*.cbl' -o -name '*.java' -o -name '*.cs' \
  -o -name '*.go' -o -name '*.py' \) -exec wc -l {} + | tail -1
```

### 1c. Formulate DRAFT acceptance criteria

Based on what you see, build an initial scope estimate:

```markdown
## Pre-Discovery Scope Estimate

### What I'm looking at
- **Source app(s)**: {app_1} ({language}, ~{file_count} files, ~{loc} LOC) [+ app_2 ...]
- **Target stack**: {target_stack}
- **Migration mode**: {mode}
- **Multi-repo merge**: {yes/no} ({count} source apps)

### Initial complexity signals
- Languages detected: {list}
- Estimated programs/modules: {count}
- Database artifacts visible: {tables, copybooks, schemas}
- Inter-app dependencies: {likely/unlikely based on imports, API calls}

### DRAFT Acceptance Criteria (will be refined after survey)

#### Graph Integrity
- [ ] Code graph fully annotated (coverage == 1.0)
- [ ] Domain graph roundtrip coverage == 1.0 (zero silent drops)
- [ ] CI drift gate green

#### Working Output
- [ ] Target code compiles
- [ ] Round-trip rule coverage ≥ 1.0 (zero FAIL requirements)
- [ ] Semantic validation clean (zero unresolved CRITICAL/MAJOR gaps)
- [ ] UAT passed (all domains PASS)
- [ ] Completeness review passed (zero HIGH findings)

#### Pipeline Integrity
- [ ] All 8 gates cleared (GATE_0 through GATE_5)
- [ ] Deployment artifacts generated

> ⚠️ These criteria are DRAFT. After survey + analyze, I'll have real
> numbers (node counts, behavior-bearing programs, complexity metrics)
> and will refine these into concrete, measurable targets.
```

Present to the user:

> "Here's my initial read on the scope. I'll refine the acceptance criteria
>  after I survey and analyze the codebase. Any red flags or adjustments
>  before I start?"

**Wait for user acknowledgment** (not full approval — that comes after survey).

---

## Step 2 — Survey, Analyze, and Refine Acceptance Criteria

### 2a. Run Discovery (dispatch subagents)

Define the `phase-worker` subagent (see Step 4 below), then dispatch:

**Phase 2 — Survey**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/survey/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:survey. Index all source_apps. Register the digest and advance."
```

**Phase 3 — Analyze**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/analyze/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:analyze. Query the graphs, produce analysis.md, register and advance."
```

### 2b. Read the real numbers

After survey + analyze complete, gather the concrete scope data:

```bash
# Real node/edge counts from wicked-estate
python3 .anti-legacy/run.py wicked_estate stats --db .anti-legacy/graphs/{app}.db

# Behavior-bearing node count (the extraction denominator)
python3 .anti-legacy/run.py coverage --db .anti-legacy/graphs/{app}.db --dry-run 2>&1 | head -5

# Read the analysis report
cat .anti-legacy/analysis-report.md
```

### 2c. Formulate REFINED acceptance criteria

Now you have real data. Build concrete, measurable criteria:

```markdown
## Acceptance Criteria for {project_name} (Refined)

Converting: {source_app_1} ({language_1}, {node_count_1} nodes) [+ {source_app_2} ...] → {target_stack}
Mode: {migration_mode}

**Scope**: {total_nodes} total nodes, {behavior_bearing} behavior-bearing,
{edge_count} edges, {shared_assets} shared data assets

### Graph Integrity (the evidence spine)
- [ ] **Code graph fully annotated**: `coverage.py` exits 0 — all {behavior_bearing}
      behavior-bearing nodes RESOLVED or RISK-flagged (coverage == 1.0)
- [ ] **Domain graph covers every code-graph requirement**: `roundtrip-coverage.json`
      `roundtrip_coverage == 1.0` — zero uncovered symbol IDs, zero silent drops
- [ ] **CI drift gate green**: `wicked-estate drift` reports zero untracked changes

### Working Output (the target system)
- [ ] **Target code compiles**: `build-integrity.json` status == PASS
- [ ] **Round-trip rule coverage ≥ 1.0**: `functional_comparison_report.json` shows
      zero FAIL requirements across all {behavior_bearing} extracted rules
- [ ] **Semantic validation clean**: zero unresolved CRITICAL/MAJOR gaps
- [ ] **UAT passed**: all domain verdicts PASS, no CRITICAL/MAJOR findings
- [ ] **Completeness review passed**: `completeness-report.json` status == PASS
      (zero HIGH findings across CODE/DOCS/CONFIG/BUILD)

### Pipeline Integrity
- [ ] **All 8 gates cleared**: GATE_0 through GATE_5 status == passed
- [ ] **Deployment artifacts generated**: Dockerfile and/or CI config registered

### Scope-Specific Risks (from analysis)
{list any high-risk findings from analysis.md — shared tables, complex coupling,
 dead-end programs, batch/online split concerns}
```

### 2d. Present for approval

Show the refined criteria to the user:

> "Survey and analysis complete. Here's the refined picture:
>
>  **Scope**: {total_nodes} nodes across {app_count} apps, {behavior_bearing}
>  behavior-bearing programs/modules to extract rules from.
>
>  **Risks identified**: {top 2-3 from analysis}
>
>  {rendered refined acceptance criteria}
>
>  These are the concrete acceptance criteria — the pipeline won't stop until
>  every box is checked. Review and adjust, then I'll kick off `/goal`."

**Wait for the user to approve, modify, or approve with changes.**

---

## Step 3 — Kick Off with /goal

Once the user approves the refined acceptance criteria, recommend they invoke
`/goal` with the full conversion prompt. Tell the user:

> "Criteria approved. Please invoke `/goal` and I'll drive this to completion."

When `/goal` is active (or if the user says "just go"), proceed with the
conversion prompt:

```
Convert {source_apps} to {target_stack}.

Acceptance criteria (the definition of done — do not stop until ALL are met):

{the approved REFINED acceptance criteria from Step 2c}

Execution instructions:
- You are the CONTROLLER. Never do phase work yourself.
- Define a `phase-worker` subagent and dispatch one per phase.
- Monitor each subagent's results, verify artifacts, update the progress tracker.
- At human gates (GATE_1, GATE_2, GATE_3B, GATE_4): stop and present findings.
- On failure: report, offer retry/skip, do not silently continue.
- Track progress in .anti-legacy/conversion-progress.md.
- Read the anti-legacy:convert skill for the full phase dispatch protocol.
- Survey and Analyze are already done — resume from Phase 4 (Extraction).
```

---

## Step 4 — Create Progress Tracker

Create `.anti-legacy/conversion-progress.md`:

```markdown
# Conversion Progress

**Project**: {project_name}
**Source**: {source_apps} → **Target**: {target_stack}
**Mode**: {migration_mode}
**Started**: {timestamp}

## Acceptance Criteria Status

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Code graph fully annotated (coverage == 1.0) | ⬜ | — |
| Domain graph roundtrip coverage == 1.0 | ⬜ | — |
| CI drift gate green | ⬜ | — |
| Target code compiles | ⬜ | — |
| Round-trip rule coverage ≥ 1.0 | ⬜ | — |
| Semantic validation clean | ⬜ | — |
| UAT passed | ⬜ | — |
| All 8 gates cleared (GATE_0–GATE_5) | ⬜ | — |
| Deployment artifacts generated | ⬜ | — |

## Phase Progress

| # | Phase | Status | Subagent | Artifacts | Duration |
|---|-------|--------|----------|-----------|----------|
| 1 | Setup | ✅ | — | config.json | Done in pre-discovery |
| 2 | Survey | ✅ | — | graphs/*.db | Done in scope refinement |
| 3 | Analyze | ✅ | — | analysis.md | Done in scope refinement |
| 4 | Extraction | ⏳ | — | — | — |
| 4b| Graph Translate | ⬜ | — | — | — |
| 5 | Blueprint | ⬜ | — | — | — |
| 6 | Test Strategy | ⬜ | — | — | — |
| 7 | Review Packet | ⬜ | — | — | — |
| 8 | Planner | ⬜ | — | — | — |
| 8b| Functional Tests | ⬜ | — | — | — |
| 9 | Swarm Build | ⬜ | — | — | — |
| 10| Target Review | ⬜ | — | — | — |
| 11| Semantic Validation | ⬜ | — | — | — |
| 12| UAT Crew | ⬜ | — | — | — |
| 12b| Document | ⬜ | — | — | — |
| 12c| Final Review | ⬜ | — | — | — |
| 13| Deploy | ⬜ | — | — | — |

Last updated: {timestamp}
```

---

## Step 5 — Define the Phase Worker

Before dispatching any work, define a reusable subagent:

```
define_subagent:
  name: phase-worker
  description: "Executes a single modernization phase by reading and following its skill instructions."
  enable_write_tools: true
  system_prompt: |
    You are a modernization phase worker. Your job:
    1. Read the skill file specified in your prompt using view_file (set IsSkillFile: true)
    2. Execute every step in the skill completely
    3. When done, report back with:
       - What artifacts were produced (file paths)
       - What was registered in the manifest
       - Any issues or warnings encountered
       - The manifest status after your work
    
    You have full read/write/command access. Follow the skill instructions exactly.
    Do NOT skip steps. Do NOT advance the manifest unless the skill says to.
    If a step fails, report the failure — do not silently continue.
```

---

## Step 6 — Execute Phases

Dispatch phases one at a time. After each subagent reports back, update BOTH
the phase progress table AND the acceptance criteria status table.

### RULES

1. **One subagent per phase** — dispatch, wait for completion, verify, then next.
2. **Never do phase work yourself** — you are the controller.
3. **Update the tracker** after every phase completes — both tables.
4. **At human gates**: stop, report to the user, wait for explicit approval.
5. **On failure**: report the failure, ask the user whether to retry or skip.
6. **Verify acceptance criteria** after each phase — check if any criteria flipped.
7. **After the final phase**: verify ALL acceptance criteria are met. If any are
   not, report which ones failed and what needs to happen to fix them.

### Phase Dispatch Sequence

#### Block 1: Discovery — ALREADY DONE

Phases 1-3 (Setup, Survey, Analyze) were executed during Steps 1-2 as part
of scope discovery and acceptance criteria formulation. Mark them ✅ in the
tracker and proceed to Block 2.

If resuming a pipeline that skipped Steps 1-2 (e.g. setup/survey/analyze were
done manually), verify their artifacts exist before continuing.

---

#### Block 2: Extraction + Domain Graph

**Phase 4 — Extraction**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/extraction/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:extraction. Crawl the code graph with adaptive ring expansion. Every
   behavior-bearing node must end RESOLVED or RISK-flagged. Run coverage.py and do not
   advance until coverage == 1.0. Register and advance."
```

> ⚠️ This is typically the longest phase. Monitor and report partial coverage.

After extraction completes, **check acceptance criterion**: "Code graph fully
annotated (coverage == 1.0)" — update the criteria table.

**Phase 4b — Graph Translator**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/graph-translator/SKILL.md (set IsSkillFile: true) and
   execute anti-legacy:graph-translator. Consume the annotated code graph and produce
   the target-state domain graph (requirements_graph.json). Verify the roundtrip
   coverage invariant. Register and advance to blueprint."
```

After graph-translator completes, **check acceptance criterion**: "Domain graph
roundtrip coverage == 1.0" — update the criteria table.

---

#### Block 3: Design (leads to GATE 1)

**Phase 5 — Blueprint**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/blueprint/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:blueprint. Design the target architecture. Register and advance."
```

**Phase 6 — Test Strategy**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/test-strategy/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:test-strategy. Generate test contracts. Register and advance."
```

**Phase 7 — Review Packet**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/review-packet/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:review-packet. Compile the review packet. Register and advance."
```

### 🚧 GATE 1 — Design Review (HUMAN REQUIRED)

```
STOP. Report to the user:

"Design phase complete. The review packet is ready.

📋 Review packet: .anti-legacy/review_packet.md
📊 Requirements: {count} across {domains} domains
🏗️ Blueprint: {target_stack} with {components} components
🧪 Test contracts: {count} generated

Please review and say 'approve gate 1' when ready to proceed."

Do NOT proceed until the user explicitly approves.
```

---

#### Block 4: Build Planning (leads to GATE 2)

**Phase 8 — Planner**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/planner/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:planner. Produce task.md. Register and advance."
```

### 🚧 GATE 2 — Plan Review (HUMAN REQUIRED)

```
STOP. Report to the user:

"Build plan ready.

📝 Task plan: .anti-legacy/task.md
🔢 Tasks: {count} in {layers} layers
⏱️ Estimated: {hours} hours

Please review and say 'approve gate 2' when ready to start building."
```

**Phase 8b — Functional Tests** (pre-build validation):
```
invoke_subagent phase-worker:
  "Read the skill file at skills/functional-tests/SKILL.md (set IsSkillFile: true) and
   execute anti-legacy:functional-tests. Author acceptance tests from the test contracts.
   Validate contracts, generate test source files. Register and advance."
```

---

#### Block 5: Build + Verify (GATE 3 auto-clears)

**Phase 9 — Swarm Build**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/swarm/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:swarm. Build task by task in dependency order. Track in task.md."
```

> ⚠️ Longest phase. Monitor and report progress.

**Phase 10 — Target Review**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/target-review/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:target-review. Run validator_discovery for GATE_3_BUILD."
```

After target-review, **check acceptance criteria**:
- "Target code compiles" — from `build-integrity.json`
- "Round-trip rule coverage ≥ 1.0" — from `functional_comparison_report.json`

### 🚧 GATE 3 — Build Integrity (AUTOMATED)

Auto-clears when build passes + round-trip coverage ≥ 1.0 with 0 FAIL.
If it doesn't auto-clear, report failures to user and dispatch fix subagents.

---

#### Block 6: Validation (leads to GATE 3B, GATE 4)

**Phase 11 — Semantic Validation**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/semantic-validation/SKILL.md (set IsSkillFile: true) and
   execute anti-legacy:semantic-validation. Deploy validators, record gaps."
```

After semantic-validation, **check acceptance criterion**: "Semantic validation
clean" — update criteria table.

### 🚧 GATE 3B — Semantic Review (HUMAN REQUIRED)

```
STOP. Report to the user:

"Semantic validation complete.

🔍 Gaps found: {count} ({critical} critical, {major} major)
📄 Report: evidence/semantic_validation_report.md

Please review and say 'approve gate 3b' when ready for UAT."
```

**Phase 12 — UAT Crew**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/uat-crew/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:uat-crew. Dispatch @uat_reviewer subagents. Produce verdicts."
```

After UAT, **check acceptance criterion**: "UAT passed" — update criteria table.

### 🚧 GATE 4 — UAT Review (HUMAN REQUIRED)

```
STOP. Report to the user:

"UAT complete.

✅ Passed: {pass_count} domains
❌ Failed: {fail_count} domains
📄 Evidence: evidence/uat/

Please review and say 'approve gate 4' when ready to proceed."
```

---

#### Block 7: Documentation + Completeness (GATE 5 auto-clears)

**Phase 12b — Document**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/document/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:document. Synthesize README, ARCHITECTURE, DEPENDENCIES, ENVIRONMENTS
   docs inside the target app directory. Register and advance."
```

**Phase 12c — Final Review**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/final-review/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:final-review. Run completeness_scanner across CODE/DOCS/CONFIG/BUILD.
   Produce evidence/completeness-report.json. GATE_5_COMPLETENESS auto-clears on PASS.
   On FAIL, kick back to document phase."
```

After final-review, **check acceptance criterion**: "Completeness review passed"
— update criteria table.

### 🚧 GATE 5 — Completeness (AUTOMATED)

Auto-clears when `evidence/completeness-report.json` has `status: PASS`.
If FAIL, report findings and kick back to the phase that owns the gap.

---

#### Block 8: Deploy

**Phase 13 — Deploy**:
```
invoke_subagent phase-worker:
  "Read the skill file at skills/deploy/SKILL.md (set IsSkillFile: true) and execute
   anti-legacy:deploy. Generate deployment artifacts."
```

After deploy, **check acceptance criteria**:
- "All 8 gates cleared" — from `manifest status`
- "Deployment artifacts generated" — from registered evidence
- "CI drift gate green" — run `wicked-estate drift`

---

## Step 7 — Final Verification Against Acceptance Criteria

After all phases complete, verify **every** acceptance criterion:

```bash
# 1. Code graph coverage
python3 .anti-legacy/run.py coverage --db .anti-legacy/graphs/*.db

# 2. Domain graph roundtrip
cat .anti-legacy/requirements/roundtrip-coverage.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'roundtrip: {d[\"roundtrip_coverage\"]}'); sys.exit(0 if d['roundtrip_coverage']>=1.0 else 1)"

# 3. Build integrity
cat evidence/build-integrity.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'build: {d[\"status\"]}'); sys.exit(0 if d['status']=='PASS' else 1)"

# 4. Rule coverage
cat evidence/functional_comparison_report.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'rule_coverage: {d[\"rule_coverage\"]}, fails: {d[\"fail_count\"]}'); sys.exit(0 if d['fail_count']==0 and d['rule_coverage']>=1.0 else 1)"

# 5. All gates
python3 .anti-legacy/run.py manifest status
```

Update the acceptance criteria table. If ALL criteria are met:

```
"🎉 Conversion complete — all acceptance criteria met.

## Final Status

{rendered acceptance criteria table — all ✅}

📊 Source: {source_apps} → {target_stack}
📋 Requirements: {count} ({kept} kept, {modified} modified, {dropped} dropped, {new} new)
🏗️ Components: {count} built
🧪 Rule coverage: {coverage}
🔒 Gates: all 8 cleared
📦 Deployment: {artifacts}

The goal is achieved."
```

If any criteria are NOT met, report which ones failed and what needs to happen:

```
"⚠️ Conversion completed all phases but {N} acceptance criteria are not met:

{list of failed criteria with evidence}

Next steps: {what to do for each — retry phase, fix code, manual review}"
```

**Do not declare the goal achieved until every acceptance criterion is verified.**

---

## Error Recovery

If a subagent fails:

1. **Update the tracker** — mark the phase as ❌ with the error
2. **Report to the user**:
   > "Phase {name} failed: {error_summary}.
   >  Options: 'retry' to re-run, 'skip' to force-advance (not recommended),
   >  or tell me what to fix."
3. **On retry**: dispatch a new subagent for the same phase
4. **On skip**: advance the manifest with `--force` and log a WARNING

---

## Resume

If invoked on a pipeline that's already in progress:

1. Read `manifest status` to find current phase
2. Read the tracker artifact for history
3. Check which acceptance criteria are already met
4. Resume from the current phase — do NOT re-run completed phases
5. Report what's already done, what criteria are met, and what remains
