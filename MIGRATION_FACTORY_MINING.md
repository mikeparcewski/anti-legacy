# MIGRATION_FACTORY_MINING — transferable value for anti-legacy

> Synthesis of 6 read-only mining agents over `~/Projects/migration-factory` (MF), each a distinct
> lens (gating, testing, docs/KB, skills/portability, engines/methodology, orchestration), each
> briefed on anti-legacy's pipeline + the ROOT A/B gating gap (`GATING_REVIEW.md`). Findings are
> deduplicated and ranked by **leverage × liftability**, weighted toward the gating gap. Scratch
> doc; nothing here is implemented — it's the "what's worth borrowing" report.

## Bottom line

- **MF's *front half* is behind anti-legacy** (no equivalent to wicked-estate's token-free
  cross-language one-pass graph, the `coverage==1.0` provable terminal, or git-brain). Don't adopt
  MF's parsers/graph/KB-vector infra — they're weaker substitutes.
- **MF's *back half* and *control plane* are ahead**, and that's where the value is:
  1. an **execution-time gate primitive** that refuses by construction (the ROOT A remedy, already written);
  2. a **deterministic transform tier + differential-equivalence gate** anti-legacy's all-LLM swarm lacks;
  3. **model-tier routing** — a lane anti-legacy is completely empty in.
- **Strongest signal:** the gating and orchestration agents *independently* converged on the same
  top finding — `loop.mjs` + `print` dry-run + `stack-registry` refuse-dispatch. Two lenses, one answer.

---

## TIER 1 — Close the gating gap (ROOT A/B, C1/C2). The live problem.

These compose into a single coherent remedy — see "Assembled remedy" below.

| # | Finding (tag) | MF source | What it gives anti-legacy | Gap | Effort | Found by |
|---|---|---|---|---|---|---|
| **A1** | **`loopStage`: make → hard-gate → review → refine → escalate** (Liftable code) | `tools/orchestrate/lib/loop.mjs` (56 lines) | An **execution-time gate a producer cannot skip**: red `continue`s and *never reaches output*; round-exhaustion returns `needs-human`. Port to Python in `antilegacy_core`; bind each phase's gate to deterministic checks first (coverage==1.0, digest match), AI review second. | **ROOT A** | Med | gating, orchestration |
| **A2** | **`print`/dry-run mode** (Liftable + Idea) | `tools/orchestrate/lib/agent.mjs:140-165`; `orchestrate-smoke.yml` | No-AI run of the *whole* control flow → makes gate/precheck logic **CI-testable** without a model. The home for `manifest precheck --dry-run`. **Must be loud** — never mistakable for a real run in audit/evidence (the exact failure we hit). | ROOT A testability | Low-Med | gating, orchestration |
| **A3** | **Declarative dispatch registry + "refuse, never fabricate"** (Pattern) | `60-transform/stack-registry.json`; refuse-branch `migrate-pipeline.workflow.mjs:291-300` | A table keyed **phase → {producing skill, required gates, required artifacts, completeness predicate}**; on a missing input it **emits a capability-gap instead of producing confident output**. Directly counters **C3** (the 9 register-only deliverables). `functional-tests` already does a primitive version ("stack not supported → explicit error"). | ROOT A + C3 | Med | gating, orchestration, engines |
| **A4** | **`detect` probe model / `engine-scan`** (Liftable) | `tools/orchestrate/lib/engines.mjs:148-169`; `engine-scan.mjs` | Declarative readiness probes (`{cmd}|{file}|{env}`) → ran/ready/skipped with the exact fix step. The shape for **`manifest precheck`**: probe *disk reality* (`graphs/*.db` present? `legacy-graph.digest` matches? coverage==1.0?) not just `manifest.phase`. Exit-code contract flips by caller: 0 advisory report, non-zero producer gate. | **ROOT B** + C1 | Low-Med | gating, orchestration |
| **A5** | **"Code Is Law" reliability layer** (Liftable code, ~3 stdlib files) | `engines/test_iq/backend/agents/{step_tracker,result_handler,validators}.py` | `determine_status()` **never defaults to passed**; `ResultQuality{EMPTY,PARSE_FAILED,…}` + `is_usable` makes an empty-but-well-formed LLM result a **tracked failure, not a silent pass**; Pydantic validate-before-write. Wrap `extraction`/`graph-translator` annotation writes so EMPTY → RISK, and reject a rule missing `business_rules`/`legacy_components`. | **C2** | Low | testing |
| **A6** | **Graded confidence bands + machine-checked traceability** (Liftable schema) | `engines/re_natural/prompts/stage1_re_generation.txt`, `schemas/review_feedback.schema.json`; `20-knowledge-base/_schema/evidence-model.md` | Confidence **bands** (0.75/0.85/0.95) + sub-threshold → explicit Open-Questions queue (richer than one risk flag); `traces_to_rules`/`x-traces-to-stories` **encoded and validated** in schema (every story→≥1 rule). Make **every rule carry a confidence** (closes the `risk_log:139` silent-skip) and validate the §2 thread mechanically. Adopt the *schema*, not re_natural's parser. | C1 + C2 | Low-Med | engines, docs |
| **A7** | **Deterministic control program (spine) vs an agent reading `manifest status`** (Architectural) | `tools/orchestrate/migrate-pipeline.workflow.mjs` + `lib/runtime.mjs` | The structural root: MF puts **sequencing in code, AI only in per-unit workers**; anti-legacy fuses both into the `orchestrate` *agent*, which is *why* gating is only enforced at `advance`. A thin driver (`run.py orchestrate-step`) reads manifest+registry(A3), runs precheck(A4), dispatches one skill **or refuses**. `orchestrate` becomes a thin caller. | ROOT A (root) | High | orchestration |

**Caveat that reframes the remedy (both gating + orchestration agents flagged it):** MF's *human governance gates* are **weaker** than anti-legacy's — doc-header sign-offs + AGENTS.md prose, no executable enforcement. anti-legacy's `manifest gate` + tamper-evident `audit.jsonl` + the kick-back state machine is **already ahead**. So lift MF's **automated** execution-time gate (A1) and the deterministic scaffolding (A2/A3/A4/A7); **do not** regress the human gates toward doc-convention.

**ROOT B insight (sharp):** *Neither repo* has a true state-vs-reality reconcile — but MF **doesn't need one because its evidence (`context.json`, `engagement.json`) is committed, not gitignored.** anti-legacy's ROOT B is partly self-inflicted by gitignoring `.anti-legacy/graphs/*.db`. Two fix paths: (a) the probe+fingerprint precheck (A4 extended with input-checksums MF lacks), and/or (b) **snapshot/commit the evidence spine** so the derived graph can't outlive its source.

---

## TIER 2 — Genuinely-new capabilities (quality & outcomes)

| # | Finding (tag) | MF source | What it gives anti-legacy | Effort | Found by |
|---|---|---|---|---|---|
| **B1** | **Deterministic transform tier + differential-equivalence gate** (Pattern + skill) — *the biggest back-half gap* | `engines/rde-migrate/skills/migrate/SKILL.md`, `references/map-format.md`, `skills/migrate-sql/`; `60-transform/` | MAP (AST, never regex) → TRANSFORM (codemod/OpenRewrite/expand-contract) → **GATE: `old(input)==new(input)` on seeded data**; LLM only for self-flagged residue. anti-legacy's `swarm` is all-LLM with **no transform tier** and `GATE_3_BUILD` proves a rule is *referenced*, not that outputs are *identical*. The map schema `{id,source,current,target,classification,technique,risk,dependsOn,status,evidence}` maps onto anti-legacy's task model. **Best first slice: the golden-master/differential gate for *numeric* requirements** (high value, contained — directly the COMP-3 "silent and catastrophic" risk). | Med-High | engines |
| **B2** | **Provider-neutral model-tier routing** (Liftable code) — *anti-legacy is empty here* | `tooling/skilldrop/route.py` (279 lines), `model-routing.json` | Each skill declares an abstract **tier (light/standard/heavy)**; a provider map resolves it per CLI; `route.py` is zero-dep/offline with transparent escalation signals. anti-legacy dispatches many subagents (swarm→developer, uat-crew→uat-reviewer) with **no model discipline** — all inherit the session model. Tiers map cleanly: heavy = extraction/graph-translator/target-review/semantic-validation; light = survey/coverage/diagrams; standard = blueprint/planner/developer. Ship only the provider you can verify; leave others as templates (model ids churn). | Med | portability |
| **B3** | **Multi-agent consensus: debate → anonymized peer review → chairman** (Idea + prompts) | `engines/discovery_council/backend/council/{orchestrator,discussion,debate}.py` | Structured consensus with **bias-breaking anonymized review** + contradiction-pair detection. Fits **RISK-flag triage in `extraction`** (2-3 agents debate a conflicting rule before it hits the human queue → raises resolved-rate) and **GATE_3B_SEMANTIC pre-review**. Ground on wicked-estate source slices, **not** their pgvector RAG. Keep to 2-3 agents on highest-ambiguity nodes (over-engineering risk). | Med | engines |
| **B4** | **Dual-emit + coverage-delta + residue + verdict** (Liftable pattern) | `tools/frontend/angularjs-to-angular-audit.mjs` (the *only shipped* deterministic gate) | Legacy↔target **coverage delta** + **migration-residue detection** (`CODEMOD-REVIEW`/scaffold markers) + one JSON **and** stakeholder-Markdown emit + `SME_READY`/`--strict` exit. `final-review`/`completeness_scanner` today scans the target **in isolation, not against the legacy inventory** — port the shape, source the delta from the wicked-estate graph (strictly better than MF's regex). Also the render-half for `evidence-log`. | Med | testing, docs |

---

## TIER 3 — Lower-effort enrichments

| # | Finding | MF source | anti-legacy fit | Effort |
|---|---|---|---|---|
| C1 | **Parity doctrine** (COMP-3 rounding-mode+scale, batch control-total reconciliation, edge-date/locale/null input classes, dual-run) | `00-process/06-validate.md`, `40-blueprints/cobol-to-java.md` | Enrich the `parity_rules` schema + `test-strategy`/`semantic-validation` (doctrine, not code — MF's golden-master *harness* is unbuilt) | Low |
| C2 | **Per-skill `manifest.json`** (deps/env/tier; "folder=SKILL.md=manifest name" invariant) | `tooling/skilldrop/AGENTS.md`, `skills/*/manifest.json` | 1 manifest across 36 skills today → declarative install/preflight target (which skills need `jsonschema`, `WICKED_ESTATE_PATH`; the model tier for B2). Only worth it if `setup`/`run.py` **consume** it | Low-Med |
| C3 | **10-category edge-case checklist + EARS notation + epic-decomposition strategies** | `engines/ai-native-sdlc/commands/review-spec.md`; `engines/spec_driven_dev/.../_templates/`, `steering/` | `test-strategy`/`functional-tests` boundary/error scenarios beyond parity; EARS makes `business_rules` statements testable; "OPEN item flows downstream" discipline for `extraction` | Low |
| C4 | **Honest status ledger + capability badges** ("lead with machine verdict, then dated gaps"; `demo`/`not-invoked` tier) | `20-knowledge-base/engagements/ea-admin-web/MIGRATION-STATUS.md`; `tools/site/build-site.mjs` | The narrative layer for `evidence-log` + an honesty tier in the deliverables index (generate it from `audit.jsonl`+manifest to stay tamper-evident) | Low |
| C5 | **One data source → 3 renders incl. self-contained interactive HTML** | `20-knowledge-base/engagements/disney-fastpass/dependency-graph/` | A shareable, offline, emailable graph deliverable from `requirements_graph.json`/`target_graph.json` — generate from the engine graph, don't add a second extractor | Med |
| C6 | **Stable ID-prefix taxonomy + contradicts/supersedes** (`BR-`/`RSK-`/`DEC-`) | `20-knowledge-base/_schema/` | Extend the §2 traceability thread **into** the deliverables (PRD cites `BR-012`, risk-log cites `RSK-004`); give `decisions-log` a provenance chain | Low |
| C7 | **mainframe_iq complexity/risk/disposition scorers** (heuristics ONLY) | `engines/mainframe_iq/.../scorers/{cobol_complexity,migration_risk}.py` | An `analyze` lens for complexity-to-effort + disposition (retire/rehost/rearchitect) — anti-legacy has structural importance (PageRank/blast-radius) but no effort/disposition score. **Lift only the formulas; its parser/Neo4j is redundant with wicked-estate (AGENTS.md §3 forbids hand-rolled mainframe parsers).** | Med |
| C8 | **app-mod-framework Java pattern corpus** (100+ legacy-Java→Spring-Boot-3.4 pairs) | `engines/app-mod-framework/java-delivery-framework/` | Seed git-brain translation patterns as a **target-stack pack** (keep out of the language-agnostic blueprint core) | Low |
| C9 | **`feature-implement-loop` discipline** (hard 3-round cap→escalate; "skills don't invoke skills") | `tooling/skilldrop/skills/feature-implement-loop/SKILL.md` | Operationalizes AGENTS.md §7 (three failures→recon) and §5 (micro-context) — borrow the verbatim phrasing into `developer`/`swarm` | Low |

---

## Do NOT adopt (anti-legacy already wins, or it's a weaker substitute)

- **`mainframe_iq` parser + Neo4j graph; `cognitive_iq` embedding KB** — weaker, single-language substitutes for wicked-estate's cross-language one-pass graph + git-brain. (`cognitive_iq`'s RRF+IDF rerank is a nugget, but solves a 500K-chunk search-relevance problem anti-legacy doesn't have.)
- **MF's human governance gates** — doc-header/prose, not executable. anti-legacy's `manifest gate` + audit + kick-back is ahead.
- **MF's markdown per-engagement status files; RAG/pgvector infra; the `skilldrop` CLI** — anti-legacy's structured `manifest.json` + skills.sh install are better/sufficient.
- **Portability basics** (AGENTS.md symlinks, `gemini-extension.json`, `npx --all`, `run.py` dispatcher) — anti-legacy already matches or beats MF here.

---

## The gating remedy, assembled from MF parts

`GATING_REVIEW.md` sketched the remedy abstractly; MF supplies concrete, mostly-liftable parts:

1. **Execution-time gate** = port `loop.mjs` (A1) into producers; the gate predicate = deterministic
   checks first (A6's "every rule has confidence", coverage==1.0, A4's disk probes), AI review second (A5 keeps it honest).
2. **It refuses, doesn't fabricate** = the registry + capability-gap branch (A3) — the structural cure for C3 (the register-only deliverables).
3. **It's verifiable** = `print`/dry-run (A2) so the gate/precheck logic is CI-tested, plus `manifest precheck` built on the `detect` probe shape (A4).
4. **State can't lie** = A4's probes extended with input-fingerprinting **and/or** committing the evidence spine (the ROOT B insight) — MF shows the probe pattern; the freshness/checksum binding is the part *neither* repo has and anti-legacy must add.
5. **(Optional, structural) the deterministic driver** (A7) moves sequencing out of the agent so enforcement isn't bypassable by direct invocation.

**This also resolves the open question from `GATING_REVIEW.md`** (hard-block vs advisory): `loop.mjs` **hard-blocks** by construction and escalates on exhaustion — i.e. hard-block the automated gate, with the dry-run/advisory mode (A2) as the explicit, loud escape, never absence-of-evidence.

## Suggested adoption order (recommendation only — not implemented)

1. **A5 + A6** (reliability layer + mandatory-confidence/traceability) — Low effort, immediately closes C2 and the `risk_log` silent-skip; no architecture change.
2. **A4 → `manifest precheck`** (probe disk reality + completeness) — the single most direct ROOT A/B mitigation; producers + the deliverables suite call it and refuse.
3. **A1 `loop.mjs`** ported into one producer (extraction) as the pilot; generalize.
4. **B1 differential-equivalence gate for numeric requirements** — highest-value new capability, contained slice.
5. Then A2/A3/A7 (the deterministic-driver direction) and B2 (model routing) as larger structural bets; Tier 3 as opportunistic.

---

*All MF paths are under `/Users/michael.parcewski/Projects/migration-factory/`. Six agents, read-only — nothing was modified in either repo.*
