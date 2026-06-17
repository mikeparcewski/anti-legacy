# How to use anti-legacy

---

## Prerequisites

- **Antigravity** (Gemini IDE) installed
- Python 3.8+ on PATH (`python3`)
- Git (for tracking pipeline state and gates)
- The legacy source code accessible as a local directory

Optional but recommended:
- Docker (for the deploy phase)

---

## Installation

```bash
# Portable — any CLI (Claude Code, Cursor, Codex, Gemini …) via the skills standard.
# Installs all skills + the bundled antilegacy_core library. Use --all (NOT --skill '*').
npx skills add mikeparcewski/anti-legacy --all
```

Or as a native plugin: Claude Code `/plugin install anti-legacy`, or Gemini
`gemini extensions install https://github.com/mikeparcewski/anti-legacy`.

Verify by asking Antigravity: *"What anti-legacy skills do I have?"*

---

## Project setup

Run setup from the root of your working directory — where you want the modernized codebase to live, not inside the legacy source.

```
"anti-legacy:setup billing-modernization targeting java at ./target from
 billing:./legacy/cobol-billing:cobol and api:./legacy/csharp-api:csharp
 deploying to gcp-cloud-run"
```

This creates `.anti-legacy/` with:
- `config.json` — source apps, target stack, deployment target, team roles
- `manifest.json` — pipeline state (which phase you're in, which gates are cleared)
- `audit.jsonl` — append-only event log, git-tracked

You can also write `config.json` directly:

```json
{
  "project_name": "billing-modernization",
  "source_apps": [
    { "name": "cobol-billing", "path": "./legacy/cobol-billing", "language": "cobol" },
    { "name": "csharp-api",    "path": "./legacy/csharp-api",    "language": "csharp" }
  ],
  "target_stack": "java",
  "target_path": "./target/billing-service",
  "deployment_target": "gcp-cloud-run",
  "roles": {
    "architect": "lead-architect",
    "uat_reviewer": "qa-lead"
  }
}
```

Commit the `.anti-legacy/` directory. It's the shared pipeline state for the team.

### Capability partition mode (optional)

Extraction groups behavior-bearing nodes into capabilities before naming them. `config.coverage.capability_partition` selects how:

```json
{
  "coverage": { "capability_partition": "auto" }
}
```

- `auto` (default) — language-driven: mainframe estates use **call-affinity** (who calls whom), modern source uses **source-package** structure. Mainframe behaviour is unchanged from before — leave it on `auto` unless you have a reason not to.
- `calls` — force call-affinity grouping regardless of language.
- `package` — force source-package grouping.
- `hierarchical` — engine Louvain community detection that splits dense mega-communities. Opt-in; use it when `auto` produces a few giant capabilities you want broken up.
- `semantic` — engine embedding clustering; groups by meaning rather than structure. Opt-in; needs the index built with `--embeddings` (see survey). Use it for cross-app consolidation where structurally distant code is the same capability.
- `community` — reuse the survey-time partition the engine persisted as `type:community` annotations (`clusters --annotate`, wicked-estate >= 0.5.0). Use it to consume one fixed partition across the run instead of re-clustering at translate time. Falls back to `auto` when no community labels are present.

`hierarchical` and `semantic` require **wicked-estate >= 0.4.0** — they are feature-detected and fall back gracefully (no-op, with a notice) on older engines, so the base pipeline still runs on 0.1.x. EXPERIMENTAL: the opt-in modes are newer than the `auto` path.

---

## Running the pipeline

Each phase is a skill invocation. You can run them from any Antigravity session as long as `.anti-legacy/config.json` exists in the working directory.

### Phase 1 — Survey

```
"anti-legacy:survey"
```

Runs `wicked-estate index` over each source repo, producing a per-repo code graph under `.anti-legacy/graphs/<app>.db` and registering a deterministic stats digest (`.anti-legacy/legacy-graph.digest.txt`) as the checksummed `legacy-graph` evidence. One engine indexes the mainframe estate and modern languages in the same pass — no language routing. The skill tells you what it found: node/edge counts, the programs/tables discovered, any app that indexed to an empty graph.

If an app shows zero nodes, check that the source path in config.json is correct and that the files are a language wicked-estate indexes — modern stacks (Java, C#, Go, TypeScript, Python, …) are indexed by this same `survey` pass, there is no separate modern track to fall back to. (`anti-legacy:survey-modern` is retired — a do-nothing redirect stub; do not run it.) The graph DBs are gitignored and rebuilt on demand; the committed evidence is the digest.

### Phase 2 — Analyze

```
"anti-legacy:analyze"
```

Produces `.anti-legacy/analysis-report.md`. Read it. It tells you: what the entry points are, which domains emerged from the data asset clustering, what's tightly coupled across apps (your highest-risk areas), and what looks like dead code. This shapes how you think about the requirements graph before it's built.

### Phase 3 — Extraction (crawl → annotate → coverage)

```
"anti-legacy:extraction"
```

This is the most token-intensive phase. The agent **crawls the wicked-estate code graph** with adaptive ring expansion (node + 1 dependency down / 1 dependent up via `blast-radius`), gathering source slices one ring at a time until it can state each node's business rule. Every behavior-bearing node ends **RESOLVED** (rule + confidence + provenance, written into the wicked-estate `requirement` field and the `.anti-legacy/annotations.jsonl` overlay) or **RISK-flagged** (placed on the human research queue). High-leverage programs resolve first (worklist ordered by `wicked-estate rank` PageRank). The crawl is idempotent and resumable — already-resolved nodes are skipped — so large codebases can run across multiple sessions:

```
"anti-legacy:extraction limit=50"     # cap the session; re-run to continue
```

On wicked-estate >= 0.4.0 the crawl uses the bulk `source_bundle` helper: it prefetches every node's source body for a file in one budget-bounded call (default full bodies; `max_total_chars` caps the bundle, and a truncated node still keeps its `byte_range`/`blob_sha` so no node is ever dropped) instead of one source call per node. It falls back to per-node source on older engines.

Output: `.anti-legacy/annotations.jsonl` (the rule overlay) + `.anti-legacy/coverage-report.json` + `.md`.

The phase has a **blocking done-gate**: it does not advance until coverage reaches `1.0` (every behavior-bearing node RESOLVED or RISK-flagged — zero unaccounted). Check it:

```bash
python3 .anti-legacy/run.py coverage --db .anti-legacy/graphs/<app>.db --out .anti-legacy/coverage-report.json
```

It exits non-zero and prints the unaccounted SymbolIds (your remaining worklist) while coverage `< 1.0`. The risk-flagged nodes are the HITL research queue the team works at Gate 1.

### Phase 4 — Blueprint

```
"anti-legacy:blueprint"
```

Maps the requirements graph to the target stack. Produces a package structure, API surface design, database schema with type translations, and component boundaries.

Output: `.anti-legacy/requirements/blueprint.json` + `blueprint.md`

### Phase 5 — Test Strategy

```
"anti-legacy:test-strategy"
```

Generates one test contract per requirement node. Each contract has: happy path scenarios, boundary conditions, error cases, and parity rules (the target must match legacy output on the same inputs).

Output: `.anti-legacy/contracts/{domain}/{req_id}.contract.json`

### Phase 6 — Review Packet

```
"anti-legacy:review-packet"
```

Assembles everything into a single offline Markdown document: architecture diagrams, entity tables, all requirement nodes, test contracts, and the Gate 1 sign-off checklist.

Output: `.anti-legacy/review_packet.md`

Share this via git push or a fileshare. No external tools needed.

---

## Gate 1 — Design review

The pipeline pauses here. Open `review_packet.md` and verify these five things before signing off:

1. Every domain has ≥1 requirement with non-empty `business_rules`
2. Numeric outputs (money, rates, counts) have `parity_rules` in their test contracts
3. No active requirement has empty `legacy_components` — every node traces to source
4. Entity field types are translated (COMP-3 → DECIMAL, packed → BIGINT, etc.)
5. Every active requirement maps to a named target component in the blueprint

If all five are true:

```bash
python3 .anti-legacy/run.py manifest gate GATE_1_DESIGN \
  --opinion passed \
  --evaluator "your-name" \
  --rationale "one sentence confirming what you verified" \
  --evidence "requirements-graph,blueprint-json,test-strategy"

git add .anti-legacy/ && git commit -m "gate: GATE_1_DESIGN cleared by your-name"
```

A `passed` opinion requires at least one `--evidence` id, and every id must already be a **registered** artifact in the manifest (the prior phases register `requirements-graph`, `blueprint-json`, `test-strategy`, etc.). If you cite an unregistered id, or omit `--evidence` entirely, the command exits non-zero and the gate is not recorded. Check what's registered with `python3 .anti-legacy/run.py manifest check`.

If something is wrong:

```bash
python3 .anti-legacy/run.py manifest gate GATE_1_DESIGN \
  --opinion failed \
  --evaluator "your-name" \
  --rationale "REQ_ACC_TRANS_01 has empty business_rules — BILLING.cbl was not read"
```

(The opinion enum is `passed`, `failed`, or `waived` — there is no `rejected`. Use `waived` only as an explicit, audited human override.) A `failed` opinion names the failing nodes. The agent re-runs `anti-legacy:extraction` for those nodes (idempotent — only the named nodes are re-crawled and re-annotated), regenerates the review packet, and presents the gate again. No full pipeline restart.

---

### Phase 7 — Planner

```
"anti-legacy:planner"
```

Topologically sorts the requirements graph into a build task list. Outputs `.anti-legacy/task.md` — one checkbox per requirement, in dependency order (data models → repositories → services → API entry points). The planner registers this file in the manifest under the artifact id `task-plan` (that id is the evidence you cite at Gate 2).

---

## Gate 2 — Plan review

PM and tech lead review `task.md`. Verify these five before approving:

1. Layer 0 tasks have no dependencies — they can start immediately
2. No task has a scope estimate > 8h (split any that do before approving)
3. Total task count equals active requirement count in `requirements_graph.json`
4. Team has capacity — total scope hours fit the target timeline
5. Traversal ordering complies with strategy checklist in [TRAVERSAL_STRATEGIES.md](TRAVERSAL_STRATEGIES.md#4-verification-checklists-for-gate-2-review) (run `python3 .anti-legacy/run.py planner_utils verify-order` to check programmatically)

Both reviewers sign separately:

```bash
python3 .anti-legacy/run.py manifest gate GATE_2_PLAN \
  --opinion passed \
  --evaluator "your-name" \
  --rationale "task ordering confirmed, scope accepted" \
  --evidence "task-plan"

git add .anti-legacy/ && git commit -m "gate: GATE_2_PLAN cleared by your-name"
```

The evidence id is `task-plan` (the artifact the planner registered) — not `task-md`. With the PASSED-gate evidence guard, citing an unregistered id here would hard-fail the sign-off. Gate 2 requires both reviewers listed in `config.json roles` to have recorded a `passed` opinion before swarm starts.

---

### Phase 8 — Swarm

```
"anti-legacy:swarm"
```

Picks the next uncompleted task from `task.md` and dispatches a `@developer` subagent with the micro-context for that requirement (business rules, blueprint spec, test contract, patterns). Repeat until all tasks are complete.

You can run swarm multiple times — it always resumes from the next incomplete task. The agent marks tasks complete as it goes. Translation patterns learned on each task are stored in the brain and reused on subsequent ones.

---

### Phase 9 — Target review

```
"anti-legacy:target-review"
```

Runs the target stack's build tool against the generated code via the runtime verifier (`validator_discovery`). Supported toolchains: `go build`, `mvn compile`/`gradle build`, `dotnet build`, `python -m compileall`, `tsc`. For Java the real toolchain (`mvn`/`gradle`/`javac`) must be present — there is no mock fallback, so a missing JRE/JDK makes the build **FAIL** rather than silently pass. A missing *optional* tool reports WARNING and does not auto-clear the gate on its own.

Compilation passing is necessary but **not sufficient**. As a blocking precondition the phase also generates a target-state graph and runs the rule-coverage round-trip (`compare_graphs`):

```bash
python3 .anti-legacy/run.py generate_target_graph \
  --workspace {target_path} \
  --output .anti-legacy/target_graph.json

python3 .anti-legacy/run.py compare_graphs \
  --requirements-graph .anti-legacy/requirements/requirements_graph.json \
  --blueprint .anti-legacy/requirements/blueprint.json \
  --target-graph .anti-legacy/target_graph.json \
  --report .anti-legacy/evidence/functional_comparison_report.md
```

**Gate 3 (GATE_3_BUILD) clears automatically** — no human sign-off required — only when the build status is `PASS` (or WARNING for an optional missing tool) **and** the rule-coverage round-trip reports full coverage. If any legacy rule is uncovered, the gate is not recorded; the uncovered rules surface and also feed GATE_3B below.

---

### Phase 10 — Semantic Validation

```
"anti-legacy:semantic-validation"
```

Requires GATE_3_BUILD to be cleared first. Groups requirements by dependency chains and dispatches validator subagents that compare the new code side-by-side with the original legacy source, recording any functional gaps directly back onto `requirements_graph.json`. This is the apples-to-apples proof that the legacy rules, endpoints, data models, and batch jobs are satisfied by the target — built on the same `target_graph.json` / `compare_graphs` round-trip that target-review produced.

Output:
- `.anti-legacy/evidence/semantic-validation-report.json` (registered as `semantic-validation-report`)
- `.anti-legacy/evidence/semantic_validation_report.md`

---

## Gate 3B — Semantic review

The pipeline pauses here for the architect and tech lead. GATE_3B_SEMANTIC reads the per-requirement semantic gaps recorded on the graph and **blocks** while any HIGH/CRITICAL gap is unresolved. Each connected dependency chain must have been reviewed, and every HIGH/MEDIUM gap resolved or explicitly approved.

```bash
python3 .anti-legacy/run.py manifest gate GATE_3B_SEMANTIC \
  --opinion passed \
  --evaluator "your-name" \
  --rationale "all dependency chains reviewed, no unresolved HIGH/CRITICAL gaps" \
  --evidence "semantic-validation-report"

git add .anti-legacy/ && git commit -m "gate: GATE_3B_SEMANTIC cleared by your-name"
```

The cited `semantic-validation-report` must be registered (Phase 10 registers it) or the PASSED gate is rejected. If unresolved gaps remain, the skill does not prompt for sign-off — it surfaces the gap and stops so you can fix and retry.

---

### Phase 11 — UAT

```
"anti-legacy:uat-crew"
```

Dispatches independent `@uat_reviewer` subagents — read-only, no developer context — to validate the built code against the test contracts. Each reviewer verifies one domain.

Output: `.anti-legacy/evidence/uat/*.json` + `uat-summary.md`

---

## Gate 4 — UAT sign-off

The UAT lead reviews `uat-summary.md` and the per-domain `evidence/uat/*.json` files. Verify these four before signing:

1. All domains have a verdict — no skipped domains
2. No open CRITICAL or MAJOR findings
3. All MINOR findings are triaged: accepted (with rationale) or assigned a tracking reference
4. Every test contract with `parity_rules` has a scenario marked pass or fail (not untested)

**Must be a different person from the Gate 1 reviewer.** This independence is machine-enforced — the verifier checks `audit.jsonl` and refuses the sign-off if the names match.

```bash
python3 .anti-legacy/run.py manifest gate GATE_4_UAT \
  --opinion passed \
  --evaluator "your-name" \
  --rationale "all domains reviewed, no open CRITICAL/MAJOR findings" \
  --evidence "uat-summary,uat-verdicts"

git add .anti-legacy/ && git commit -m "gate: GATE_4_UAT cleared by your-name"
```

If you are accepting MINOR findings rather than fixing them, name them in the rationale:

```
--rationale "Accepted UAT-003 (logging format, MINOR) tracked in issue #12. All CRITICAL/MAJOR clear."
```

---

### Phase 12 — Deploy

```
"anti-legacy:deploy"
```

Generates Dockerfile, CI/CD pipeline (GitHub Actions or GitLab CI), and deployment manifests for the configured platform.

Supported targets: `gcp-cloud-run`, `aws-ecs`, `azure-aks`, `kubernetes`, `docker-compose`

Output: `{target_path}/Dockerfile`, `deploy/`, `DEPLOY.md` runbook

---

## Common scenarios

### Mixed codebase (mainframe + modern)

Set multiple source apps in config.json with different languages. Survey processes them in parallel tracks and merges into a single graph. The requirements graph will have cross-app dependencies (e.g., a COBOL batch program calling a C# service) captured as requirement dependencies.

### Pure COBOL

Set `language: cobol` on the source app. Survey runs `wicked-estate index` — fast, no token cost on file enumeration. The extraction phase (the graph crawl + annotation) will be the token-intensive step.

### Java or C# monolith

Set the appropriate language. Survey uses `wicked-estate index` (which natively supports Java, C#, and 90+ other languages). Pay attention to the analysis report — monoliths often have surprisingly clear domain boundaries once you look at which classes share data access patterns.

### Partial modernization (one domain at a time)

The requirements graph is domain-scoped. You can run the swarm for a single domain by specifying it in the task list. Gate 3 (compilation) applies to whatever has been built so far.

---

## Troubleshooting

**Survey shows zero nodes for an app**  
The source path may be wrong, or the files are a language wicked-estate doesn't index. Check the `path` in config.json, and confirm the engine resolves — survey uses `config.json` key `wicked_estate_path`, then `WICKED_ESTATE_PATH`, then `wicked-estate` on PATH; if none resolve it errors with instructions to set `wicked_estate_path` (it never silently degrades). Modern languages are covered by this same pass — there is no separate modern survey track (`anti-legacy:survey-modern` is retired, a do-nothing redirect stub).

**Extraction RISK-flags more nodes than expected**  
A node is RISK-flagged when the crawl hits the ring/context budget (`crawl.max_rings`, `crawl.context_budget_chars` in config.json) or the rule is genuinely ambiguous. If a node looks thin, it may be a coordinator whose real logic is in called subprograms — extraction follows those edges via ring expansion, so widening `max_rings` can resolve it. Otherwise the flag is correct: it goes on the Gate 1 research queue rather than being guessed at. Inspect `.anti-legacy/annotations.jsonl` for the `risk_reason` and `ring_depth`.

**Swarm produces code that doesn't compile**  
Check `.anti-legacy/task.md` — if a Layer 2 service was built before its Layer 1 repository, the dependency ordering failed. Re-run `anti-legacy:planner` to regenerate the task order, then re-run swarm from the failing task.

**Gate 1 reveals missing requirements**  
This is the gate working as designed. Re-run `anti-legacy:extraction` for the named nodes (the crawl is idempotent and only re-touches those) — or research the RISK-flagged ones and record the rule — so the annotation lands on the graph and in `annotations.jsonl`, then re-run `anti-legacy:review-packet` and clear the gate. The downstream build picks up the corrected requirements.

**UAT fails with CRITICAL precision finding**  
Almost always a COMP-3 / fixed-point arithmetic issue. The requirement node should have a parity rule specifying the decimal precision. If it's missing, add it to the requirement node, regenerate the test contract, and re-run swarm for that task.
