# anti-legacy — Architecture & Backlog

This is the agreed architecture and the roadmap of work **not yet built**. The pipeline on disk
today is the repaired, gate-enforced modernization pipeline; the model below is where it's going.
Nothing under "Planned" should be presented as shipped.

Status legend: ✅ done · 🟡 partial/spiked · 🔵 designed · ⚪ idea

---

## Capability naming + cross-app coalescing on modern code 🔵

On dense modern codebases (the Kafka+Pulsar demo), capability **naming** falls back to noun-frequency
(`AlreadyAlwaysCapability`) and capabilities do **not coalesce** across source apps. Both have one root
cause: the vocabulary glossary was not projected onto the graph, so `domain_name_for`'s priority-1
term-aware path (`domain_entity`/`domain_action` → `{Action}{Entity}Capability`) has nothing to read and
drops to priority-2 (statement nouns).

- **Fix A (wiring, exists):** run `vocabulary mine → confirm → project_terms_to_graph` before
  graph-translate so capabilities are named from canonical terms. Same-canonical capabilities across apps
  then share a domain key and coalesce automatically (see `domain_name_for` docstring).
- **Fix B (the new bit):** exact-canonical only coalesces when both systems use the *same word*
  (Kafka "record" vs Pulsar "message" won't merge). Add a **semantic** synonym-resolution step — embed the
  mined terms / rule statements via wicked-estate `semantic` + `--embeddings` and cluster cross-vocabulary
  synonyms onto a shared canonical — so record≈message → one canonical → coalesce.

---

## The model (where we landed)

A code-graph spine, annotated and **re-thought** through an iterative, coverage-driven, HITL loop,
kept honest by a CI drift gate. **Two graphs**, not one:

- **Code graph** = `wicked-estate` — legacy *structure* (COBOL/JCL/CICS/IMS/DB2 + modern) plus the
  business-rule *annotations* written into its native `requirement` fields. This is the **evidence**
  ("what the legacy does"), with `{confidence, provenance, resolved_by}` on every edge.
- **Domain graph** = a **new, re-thought artifact** — the **target-state requirements**, rationalized
  by business capability, merged across source repos, traced back to code-graph nodes for provenance.
  This is the **plan** (the *reimagine*). Not an overlay or a view — the requirements are rethought —
  **but it must cover *every* requirement edge from the annotated code graph.** The re-think reorganizes
  and re-implements; it never silently drops a requirement (any drop is an explicit, audited decision).
  That total-coverage-of-originals is exactly what reconciles "reimagine" with "behavior-preserving."

**The pipeline:**
1. **Index** the source repo(s) with `wicked-estate` → the code graph (§H).
2. **Annotate** — agents crawl the graph with **adaptive ring expansion** (node + 1 up/1 down, blow
   the radius out one ring at a time until there's enough context) and write rules into the
   `requirement` fields. Each behavior-bearing node ends **resolved** (rule + confidence/provenance)
   or **risk-flagged** (human must research). Auto-resolve the confident; flag the rest.
3. **Coverage = resolved-OR-flagged** — a provable terminal (zero unaccounted nodes). The risk flags
   *are* the HITL research queue.
4. **HITL** — two-layer gate (mechanical + clarify) + risk research. **Never block work that can be
   done**: build the resolved slice; gate only the risk-touching slices.
5. **Re-think → domain graph** — rationalize the annotated rules into the new target-state domain
   graph (cross-source conflict detection happens here).
6. **Build / test incrementally**, producing SDLC docs throughout.
7. **CI drift gate (keystone)** — `wicked-estate drift`: a code change without a matching graph
   update **fails CI**, so the graph can never drift from the code → it is always the trustworthy
   plan + definition-of-done. The CI face of the round-trip.

**Execution order:** `wicked-estate (§H) → extraction model (§I) → A1 diagnostic → A1′/G2 → build-half`.

---

## ✅ Done (this branch, verified)

- **Gate enforcement** — `cmd_advance` phase→gate preconditions, `cmd_gate` content-verified evidence,
  validator runner gates (no javac mock, round-trip coverage, GATE_4 independence). *The trustworthy core.*
- **Contract fixes T1–T4** — functional-mode default, capability domains + entity co-location, object-form
  rules `{id, statement}` + `confidence`/`provenance`, rule-level round-trip done-check.
- **Decoupled from the test repos** — `enrich_requirements` 105KB→25KB (no `BUSINESS_MAPPINGS`),
  config-driven multi-language target scanner, schema accepts modern types, `test_runner` fails (not
  silently passes) unsupported stacks. **Repo-agnostic, proven on an unseen repo.**
- **Cruft removed** — `ui_server`/`chat_monitor`/`event_hub`/`start_ui.sh` gone; `audit.jsonl` is the interface.
- **LOW landmines** — F4 GATE_4 evidence ids, F5 mode-via-config, F7 subprocess timeouts, F11 dispatcher
  in develop-plugin, F12 `target_verifier`→`demo/`, F-pkt portable links.
- **AGENTS.md < 12K** via skill references (B1).
- **wicked-estate spike** — carddemo COBOL: 10,307 nodes / 1.8s, cross-domain `blast-radius` COBOL→JCL; Java clean.
- Suite green (232 passed).

---

## Planned

### H — Code-graph engine: adopt `wicked-estate` (foundational) 🟡 *spiked*
- Replace `graph_builder.py`. **Direct consumption, no adapter:** `survey` = `wicked-estate index`;
  `analyze`/`graph-translator` query it via its **CLI/MCP** (not raw SQLite — pre-1.0 stability boundary).
- **Delete** `graph_builder.py`, `schemas/code-graph.schema.json`, the `legacy_graph.json` intermediate.
- Thin seam: register a `wicked-estate stats` digest (or DB path) as the checksummed `legacy-graph`
  evidence artifact for the gate/audit contract.
- Effort **MED**; high leverage (graph quality is the foundation), low risk (own MIT engine, 617 tests).

### I — Extraction model: annotate → coverage → re-think 🔵 *(supersedes the old C1/C2/D1/D2)*
- **I1 — Crawl + annotate** — adaptive ring expansion; write rules into wicked-estate `requirement`
  fields; per-node **resolve** (with confidence/provenance) or **risk** (human research). **First spike:
  the crawl/expand recipe.**
- **I2 — Coverage = resolved-or-flagged** — provable terminal; the risk queue is the HITL input.
  **First spike: the precise coverage definition** (this is also the goal/DoD).
- **I3 — HITL + risk research** — two-layer gate; risk research closes flags; **never block resolvable
  work** (incremental build of resolved slices, gate only risk-touching slices).
- **I4 — Auto-resolve calibration** — confidence/provenance make every auto-resolve auditable; agreement
  rate ramps the threshold → autonomy (the flywheel).
- **I5 — Re-think → new domain graph** — rationalize into the target-state domain graph by capability;
  **cross-source conflict detection** (two repos, divergent rules → risk); provenance to code-graph nodes.
  **Requirement-coverage invariant:** the domain graph must cover **every** requirement edge from the
  annotated code graph — a *second* coverage check beyond I2's node coverage. 0 dropped; each intentional
  drop is an explicit, audited decision. This is the **requirements-level round-trip** and a CI/gate check.
- **I6 — CI drift gate (keystone)** — `wicked-estate drift`: code change without a graph/annotation update
  fails CI. The graph stays the source of truth.

### A — Validate the repair
- **A1 — CardDemo dry run, as a DIAGNOSTIC** 🔵 — validate gate enforcement / evidence / sequencing.
  **A pass ≠ generality.** Run after §H/§I are far enough to exercise the real path.
- **A2 — Path simulation** 🟡 — install-dir ≠ workspace harness (Layer A); `_diagnose_paths` probe per
  surface (Layer B). In a worktree.

### B — Real-install packaging (three Antigravity surfaces)
- B1 — AGENTS.md split ✅ (10,184 bytes via skill references).
- **B2 — Target-workspace scaffolding** 🔵 — `setup` writes `AGENTS.md` + symlinks into each target
  workspace; Windows → generated copies.
- **B3 — Surface reconciliation** ⚪ — concrete checklist: does `agv plugin import gemini` import this
  plugin today (test it)? `.agents/skills/` ↔ `skills/` canonical mapping; `mcp_config.json` when.

### E — Hardening & cleanup
- E1 — Independent gate evaluators ✅ (GATE_4) / 🔵 extend GATE_0/GATE_1B logic.
- **E2 — `target_verifier` retire** ⚪ — repoint its tests to `validator_discovery`, then remove the
  `demo/` copy. Migrate `jsonschema.RefResolver` → `referencing`.
- **E3 — Remaining F backlog** ⚪ — F9 phase-order enforcement, F10 target-graph schema, F13 `source_type`
  required, F14 multi-strategy rule detection + synthetic-id fix, F16 `survey-modern` chunking (largely
  moot once wicked-estate owns indexing), F17 concurrency-safe JSON writes, F18 tests for active-path scripts.

### D — teamwork-preview (optional accelerator) ⚪
- Use Antigravity's native `/teamwork-preview` (Worker/Reviewer/Critic/Auditor) for the swarm + independent
  gate review; graceful degradation to sequential (Ultra-plan/quota-gated).

---

## G — Acceptance criteria ("general-purpose")

- **G1 — Apache Kafka + Apache Pulsar → one streaming service.** Validates gate enforcement,
  evidence integrity, round-trip, sequencing. **NOT a generality proof.**
- **G2 — a NON-CardDemo, ideally NON-COBOL migration** (e.g. Java→Go, C#→Java). The real generality test —
  exercises wicked-estate's modern path + the extraction loop end-to-end. **"General-purpose" ⇔ G2 passes.**

---

## Superseded / retired

- `graph_builder.py`, `schemas/code-graph.schema.json`, `legacy_graph.json` → **replaced by §H** (wicked-estate).
- The single "requirements graph" artifact → **two graphs** (annotated code graph + new domain graph).
- Old **C1 (conflict linter) / C2 (clarify gate) / D1 (flywheel) / D2 (loop)** → **folded into §I**.
- F1/F2/F3/F6/F8/F-pkt + B1 → **done**.

---

_Designs recorded in the project brain memories: true-purpose, canonical-sdlc-flow, two-layer-gate-clarify-open-items,
extraction-model-two-graphs, codegraph-engine-wicked-estate, loop-engineering-antithrash, hitl-clarify-gate-autonomy,
runtime-wiring, three-surface-support, teamwork-preview, pipeline-broken-phantom-green._
