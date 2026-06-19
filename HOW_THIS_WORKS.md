# How anti-legacy works

The fundamental problem with legacy modernization is that the code is the only documentation. You can't rewrite what you don't understand, and you can't understand 800 COBOL programs by reading them manually. anti-legacy solves this by treating modernization as a two-phase problem: **understand first, build second** — with a structured handoff between them.

---

## The core idea: extract requirements from code, then build against requirements

Legacy code contains business rules. They're buried in COMPUTE statements, nested IF/EVALUATE blocks, COMP-3 arithmetic, and forty-year-old file layouts — but they're there. The pipeline's job is to surface them as rule **annotations on a code graph**, get humans to verify the risk-flagged ones, and then build the target system against those rules rather than against the legacy source directly.

```
legacy code  →  wicked-estate code graph  →  rule annotations on the graph  →  target code
(what exists)   (structure: how it's wired)   (semantics: what each node does)   (modern impl)
```

The **annotated code graph** is the pivot. Everything before it is indexing + analysis; everything after it is construction. The graph is owned by **wicked-estate** (an MIT code-graph engine — see §H below); the annotations write into the engine's native `requirement` fields and an anti-legacy-owned sidecar. There is no hand-rolled graph and no separate `legacy_graph.json` intermediate.

The pipeline merges **one or more** source repositories into a **single** combined target spec and a single target app — for example two Java streaming platforms (Apache Kafka + Apache Pulsar, the bundled demo) consolidated into a single service, or a COBOL estate modernized to Java (the AWS CardDemo mainframe example). The rewrite is **behavior-preserving and targeted**: the data contracts (shapes), the interfaces, and the jobs (full functionality) are treated as invariant; only the code/implementation is reimagined in the target stack. The Kafka + Pulsar demo surfaces 213 source-grounded business rules and coalesces capabilities **across** both source apps — e.g. a single Producer capability spanning Kafka and Pulsar (7 cross-app capabilities coalesce on the default `auto` partition, 9 on `semantic`).

---

## Migration Paradigms: Match Code vs Match Functionality

When creating the requirements graph and the target state architecture, the pipeline supports two distinct migration paradigms:

1.  **Match Code (Structural / Code-Equivalent)**:
    *   **Goal**: Translate legacy files/modules 1-to-1 into the target language.
    *   **Best for**: Fast VM/container lift-and-shift migrations where mechanical equivalence is paramount and domain context is limited.
    *   **Verification**: Module-level side-by-side or golden-file parity tests.
2.  **Match Functionality (Functional / Intent-Equivalent)**:
    *   **Goal**: Extract core business logic and rules, merging legacy files into unified functional capabilities and clean target architectures.
    *   **Best for**: Long-term modernization strategies targeting Domain-Driven Design (DDD) or modular monoliths, eradicating legacy technical debt and dead code.
    *   **Verification**: Broad end-to-end integration and API-level business scenario verification.

For a detailed trade-off analysis and a decision matrix, see [CODE_VS_FUNCTIONAL.md](CODE_VS_FUNCTIONAL.md).

---

## Stage 1: Survey — building the structural skeleton

The survey phase runs `wicked-estate index` over each source repo and produces a per-repo SQLite code graph under `.anti-legacy/graphs/<app>.db`. This is purely structural: what programs exist, what they call, what tables they access, what files they read and write. No business logic extraction yet. Each node carries `{confidence, provenance}`, and cross-language edges resolve automatically (a JCL `EXEC PGM` step binds to its COBOL program; a `CALL` binds to the called program).

Every node carries native `file` and `line` provenance — the relative path and line of the source it came from. This is the thread that runs through the entire pipeline, and it's a property of the engine, not something a skill asserts per node. When the extraction phase reads a business rule, it knows which source slice to read. When the swarm builds a target component, it knows where to go if a rule is ambiguous. That provenance is what makes the graph a live connection to the source rather than a static snapshot.

One engine indexes the whole estate — the mainframe stack (COBOL/JCL/CICS/IMS/DB2) and modern languages (Java, C#, Python, TypeScript, Go, Rust, …) in the same pass, token-free even across thousands of files. There is no language-routing split and no batch Python extractor, and no separate modern survey track: `survey-modern` is retired — a do-nothing redirect stub kept only so stale references resolve to a clear pointer back at `anti-legacy:survey`.

**The thin seam.** The graph DBs themselves are gitignored (rebuilt by survey on demand). What survey *registers* as the checksummed `legacy-graph` evidence is a deterministic **stats digest** — `wicked-estate stats` with the volatile lines (staleness, git provenance, byte size) stripped, so re-indexing the same source yields a byte-identical canonical block and a stable SHA-256. That digest, written to `.anti-legacy/legacy-graph.digest.txt`, is the committed artifact the gate/audit contract checksums. (This replaces the old `legacy_graph.json` JSON blob and its `code-graph.schema.json` — both deleted.)

---

## Stage 2: Analyze — finding the domain boundaries

The analysis phase applies four structural lenses to the code graph. It reads the graph through the `wicked_estate` helper (`query` / `blast-radius` / `rank` / `stats` / `source` / `cross-graph`) — never raw SQLite, never the deleted `legacy_graph.json`.

- **Architecture**: entry points (in-degree-0 nodes that are JCL/CICS/MQ targets, via `blast-radius` in-degree detection — `detect_dead_ends.py` reads the graph through the helper), leaf programs, depth of call chains. Importance ordering comes from `wicked-estate rank` (PageRank), which replaces the old hand-rolled in/out-degree pass.
- **Domain**: which programs share data assets — this is how capability-oriented bounded contexts emerge (the default `functional` migration mode groups modules into business capabilities). Cross-domain coupling surfaces through estate `uses`/`accesses` edges in `blast-radius`. Every program that touches the ACCOUNT table belongs to the account domain, regardless of what it's called. How nodes are *partitioned* into capabilities is set by `config.coverage.capability_partition`, default `auto` — **language-driven**: a mainframe estate partitions by **call-affinity** (which programs call each other), a modern codebase by **source-package** (the directory/namespace the engine already records). The other modes (`calls`, `package`, `hierarchical`, `semantic`) are explicit overrides; `hierarchical` (engine Louvain community detection, which splits dense mega-communities) and `semantic` (engine embedding clustering) are **opt-in**, feature-detected, and require **wicked-estate >= 0.4.0** — on older engines they fall back gracefully and mainframe behaviour on `auto` is unchanged.
- **Technical**: batch vs. online classification via the estate node kinds (JCL steps, CICS programs), synchronous vs. fire-and-forget calls.
- **Ops**: cross-app coupling (the highest-risk programs to touch — federated via `cross-graph` over the per-app DBs), dead code candidates.

This phase doesn't read source files for rules — it reasons from graph topology. The output is a structural analysis report and domain groupings that shape the extraction worklist.

---

## Stage 3: Extraction — crawl, annotate, coverage

This is where the pipeline goes from structural to semantic. Extraction (`anti-legacy:extraction`) **crawls the wicked-estate code graph** and writes a business rule onto every behavior-bearing node. It replaces the old graph-translator enrich flow entirely.

**Adaptive ring expansion.** The agent doesn't read one file in isolation — it gathers context one ring at a time, blowing the radius out until it has enough to state the rule:

- **Ring 0** is the target node itself: its source slice (de-quoted body), its `description`, its kind/file/language. The crawl prefetches every node's body for a file in one `source_bundle` call (budget-bounded — full bodies by default, capped by `max_total_chars`, with a never-drop-a-node escape hatch that keeps a truncated node's `byte_range`/`blob_sha`) instead of one `wicked-estate source` call per node — the bulk-source helper needs **wicked-estate >= 0.4.0** and degrades to per-node source calls on older engines.
- **Ring N→N+1** expands one hop in both directions: 1 *down* = what the node calls/uses (follow `calls`/`uses`/`references` edges out of the node), 1 *up* = its dependents (`wicked-estate blast-radius <name>`, which follows all edge kinds including estate `uses`/`accesses`/`protects`). For each new ring the agent pulls the source slices of the new nodes and the edge kinds connecting them — so it sees "this paragraph PERFORMs that one, which does the EXEC SQL." A ring can cross domains, because cross-language edges resolve automatically (JCL → COBOL, `CALL` → COBOL).

Each expansion is a deliberate, bounded step — one more ring of seed names, never an unbounded whole-graph walk.

**Per-node terminal state.** After each ring the agent evaluates a stop condition, and every behavior-bearing node ends in exactly one of two terminal states:

1. **RESOLVED** — the rule can be stated with `confidence >= resolve_threshold` (default `0.75`), grounded in the gathered context. The helper writes the rule into wicked-estate's native `requirement` field (`requirement_validated=1`) and mirrors the full object to the `.anti-legacy/annotations.jsonl` overlay with `provenance` = the ring nodes/edges that grounded it and `resolved_by="extraction-skill@ring{N}"`. **Stop.**
2. **RISK** — the ring or context budget is exhausted, or there's genuine ambiguity (conflicting rules, missing source, an unresolved cross-ref, or two source repos that disagree). The node is flagged (`requirement_validated=0`, a `risk_reason`, `provenance`) and lands on the human research queue. **Stop.**
3. **EXPAND** — not enough context yet, but `ring_depth < max_rings` (default `3`) and context chars are under budget (default ~18,000, under wicked-estate's ~25K cap): blow the radius out one more ring and continue.

A node hit at the ring/confidence budget without reaching threshold is **never left bare** — it is RISK-flagged. Because EXPAND strictly decreases the remaining budget, the crawl cannot loop, and every node terminates. High-leverage nodes resolve first (worklist ordered by `wicked-estate rank` PageRank; entry points always in scope), and a node already resolved/risk in the overlay is skipped on re-run — the crawl is idempotent and resumable.

The resolved annotation captures the same rich rule structure the downstream schema expects — `business_rules`, `validations`, and `error_paths` as **objects** `{id, statement}` (plus `confidence` and `provenance`), with ids matching the `RULE-NNN` / `VAL-NNN` / `ERR-NNN` patterns (three digits). A resolved node's rule object looks like this (the object form the enriched schema `schemas/requirements-graph.enriched.schema.json` requires):

```json
{
  "domains": {
    "Account_Domain": {
      "entities": {
        "ACCOUNT": {
          "fields": [
            { "name": "ACC-ID", "type": "VARCHAR", "description": "Account identifier" },
            { "name": "BAL-AMT", "type": "DECIMAL(11,2)", "description": "Balance (COMP-3 PIC 9(9)V99 → DECIMAL(11,2))" }
          ]
        }
      },
      "requirements": {
        "REQ_ACC_TRANS_01": {
          "title": "Process account transaction",
          "description": "Apply a debit/credit to an account balance.",
          "legacy_components": ["src/ACC-TRANS-01.cbl"],
          "business_rules": [
            { "id": "RULE-001", "statement": "Debit amount must not exceed available balance" },
            { "id": "RULE-002", "statement": "Transaction date must be a business day" },
            { "id": "RULE-003", "statement": "Amounts are stored with 2 decimal places — COMP-3 precision must be preserved" }
          ],
          "validations": [
            { "id": "VAL-001", "statement": "ACC-ID must exist in ACCOUNT" },
            { "id": "VAL-002", "statement": "AMOUNT > 0" }
          ],
          "error_paths": [
            { "id": "ERR-001", "statement": "insufficient funds" },
            { "id": "ERR-002", "statement": "non-business day" }
          ],
          "data_access": ["ACCOUNT"],
          "dependencies": ["REQ_CALCRATE"]
        }
      }
    }
  }
}
```

The link back to source is never dropped. The annotation is **SymbolId-keyed** (the full interned wicked-estate symbol id, not the bare name — names aren't unique; carddemo has `MAIN-PARA`×21), so each rule binds to the exact scoped node and traces back through the node's native `file`/`line` provenance. If the swarm encounters an ambiguous rule, it goes back to the COBOL. If UAT finds a precision bug, it traces back to the original COMP-3 field. The annotated graph isn't a replacement for the source — it's an index into it.

The native `requirement` field is the in-graph evidence projection (it makes `wicked-estate by-requirement` and `drift` work, keeping the graph self-describing); the `.anti-legacy/annotations.jsonl` overlay is the lossless, IP-rich sidecar — the requirement-rule object stays anti-legacy's own. The helper writes both atomically.

---

## Coverage: the provable terminal

Because every behavior-bearing node ends RESOLVED or RISK-flagged, extraction has a **provable definition of done**: coverage. `run.py coverage` (the `antilegacy_core.coverage` module) computes

```
coverage = (resolved + risk_flagged) / behavior_bearing_total
```

over the behavior-bearing nodes of the code graph. The **denominator** is config-driven (`coverage.behavior_kinds`, default `module`/`function`/`method`/`class`/`struct`/`interface` plus estate behavior nodes like CICS programs, JCL steps, and DB2-accessing nodes). Structural leaves that carry no standalone business rule are excluded — `file`, `import`, `field`, `constant`, `variable`, `parameter`, `type_alias`, `enum`, `macro`, and pure data-only copybook modules with zero outgoing edges. Annotating a COBOL field or a Java import would inflate the denominator with un-rule-bearing leaves and make coverage un-provable; a paragraph or program *carries* behavior, a field does not.

Each node's state is read from the `annotations.jsonl` overlay (the source of truth) and cross-checked against the in-graph `requirement` field so the two can't silently diverge. **DoD: `coverage == 1.0`** — zero unaccounted nodes. The report (`coverage-report.json` + `.md`) also breaks out `resolved/total` (the slice that can be built now) from `risk_flagged/total` (the HITL queue depth), plus `mean_confidence` and `resolved_rate`. `coverage.py` exits non-zero and lists the unaccounted SymbolIds while coverage `< 1.0`, so it doubles as a gate predicate and a future CI drift check (§I6). The risk flags *are* the human research queue — the system never blocks work that can be done; it builds the resolved slice and gates only the risk-touching slices.

> **Where the front half ends.** Indexing (Stage 1), analysis (Stage 2), extraction + coverage (Stage 3) are the **extraction core** built in this work (BACKLOG §H/§I1/§I2). Rationalizing the annotated code graph into the target-state **domain** `requirements_graph.json` that Stage 4 onward consume — the *re-think* by business capability, with cross-source conflict detection — is the §I5 step, a later work item; until it lands, the downstream stages read a requirements graph that the re-think will produce from these annotations. The stages below describe the agreed end-to-end pipeline, not all of which is shipped (see BACKLOG.md).

**How a capability gets named.** Once nodes are partitioned (see the Domain lens above), each capability needs a business name, not a code name. The naming step **mines a glossary** of candidate domain terms, has a **human confirm** which are real domain terms, then **projects** the confirmed tags back onto the capability. The vocabulary miner now works across modern languages as well as mainframe: a tokenizer splits camelCase/PascalCase/snake_case (`getProducerName` → `GET`, `PRODUCER`, `NAME`), while hyphen/underscore mainframe names and digit-bearing tokens (`DB2`, `STAT1`) tokenize byte-identically to before; domain **entities** are mined from `class`/`interface`/`struct`/`trait`/`enum`/`record` declarations (so Java, C#, TypeScript, Kotlin, Swift, Python, Ruby, Go, Rust, C, C++ all yield domain types), with accessor boilerplate (`get`/`set`/`is`/`has`/`new`) excluded from naming. The final name **merges** a dense glossary-direct base with the authoritative engine-projected `domain_*` tags (the engine `annotate` store round-trips under **wicked-estate >= 0.4.0**); an engine-independent fallback keeps naming working without the native store. **Honest caveat:** naming *quality* is gated on glossary **curation** — against an un-curated glossary that confirms every mined term, code-mechanics tokens can produce noisy names. The human glossary-confirmation step (confirm only real domain terms) is what makes the names clean.

**How capabilities coalesce across source apps.** When more than one repo is merged, the re-think groups by capability *across* sources, not per-repo — so two apps that both implement the same business capability collapse into one. In the Kafka + Pulsar demo a single Producer capability spans both platforms; the count is partition-mode-dependent (7 coalesced capabilities on `auto`, 9 on `semantic`). This is the same cross-source conflict-detection pass that flags two repos disagreeing on a rule (the RISK terminal in Stage 3).

---

## Stage 3b: Negative extraction — what the system must reject

The first extraction pass reads each node's own body (ring[0]) and states the *positive* rule — what the program does. It systematically under-reads the **negative** behavior: the error paths, the validation guards, the rollback/ABEND logic, the SQL error clauses. `anti-legacy:negative-extraction` is a second pass that fixes that. It runs **after** extraction and **before** graph-translator, on the same overlay.

For each already-resolved behavior node it crawls **one or more rings deeper** — but aimed specifically at the error surface. It re-reads the node body for guards and handlers (`wicked-estate source`), follows ring+1 candidates (`wicked-estate blast-radius`), and reads the error/validation callees. From that it re-annotates the *same* SymbolId in `.anti-legacy/annotations.jsonl` with source-grounded `error_paths[]` and `validations[]` (objects `{id, statement, confidence, source_kinds}`). It is idempotent — it enriches existing overlay rows, it does not create a new file, and like extraction it owns no manifest phase enum value (it operates inside the `graph-translate` slot and does not `manifest advance`).

The honest line it preserves: a negative it can read straight from a source guard is grounded (`source_kinds: ["code-body"]`); a negative it can only **derive** — implied by a boundary or the inverse of a positive rule, with no explicit guard in the source — is written **flagged** at lower confidence and never labelled `code-body`. That keeps the read-from-source slice independently reviewable from the inferred slice. Done means the overlay gained at least one `error_path` or `validation`; if the pass finds zero, it reports that explicitly (crawl deeper, or state that no error surface exists) rather than passing silently. graph-translator then projects these negatives into `requirements_graph.json` alongside the positive rules.

---

## Stage 4: Blueprint — mapping requirements to target architecture

The blueprint takes the requirements graph and maps it to the target stack. Each domain becomes a package. Each requirement node becomes a class, service, or function. Each entity becomes a table with field types translated (COMP-3 PIC 9(9)V99 → DECIMAL(11,2), etc.).

The blueprint is still language-agnostic at the requirement level — it specifies *what* each component must implement, not *how*. The `how` is up to the developer subagent.

---

## Stages 5–6: Test strategy + review packet

Before any code is written, every requirement node gets a test contract: specific input/output scenarios, boundary conditions, error cases, and parity rules (the target must produce the same numeric results as the legacy system for the same inputs).

These contracts are assembled into a review packet — a single offline document the team can read in a browser, a PDF, or a shared drive. No external systems required.

**The stakeholder deliverables package.** Once the requirements graph is ready, `anti-legacy:deliverables` renders a fuller stakeholder package alongside the review packet — a product-requirements doc (agent-enriched via `anti-legacy:prd`), Mermaid architecture diagrams, a detailed functional test strategy and scripts (agent-enriched via `anti-legacy:test-scripts`), an end-to-end migration plan, and the *living* risk / decisions / evidence logs. They render into `.anti-legacy/deliverables/` (with a `README.md` index), each registered as a manifest artifact, and **none of them advances the pipeline** — they reuse the pipeline's existing structured artifacts rather than coining new prose. The Tier-A "snapshot" deliverables gate on the producer readiness check (see *The readiness gate* below); the three living logs deliberately do not, so they can run on an incomplete pipeline to surface its gaps.

---

## Gate 1: the human check

The pipeline pauses here. The team reviews the requirements graph, not the code. The question they're answering is: **did we understand the legacy system correctly?** This is where business analysts confirm that REQ_ACC_TRANS_01 actually describes how account transactions work. If the extraction missed something, it gets fixed here. If it's right, Gate 1 clears and construction begins.

---

## Stage 7: Planner — dependency ordering

The planner reads the requirements graph and orders the nodes into build tasks. By default, it topologically sorts them from dependencies to dependent systems (Bottom-Up), but supports multiple traversal strategies configuration-driven via `config.json`. See [TRAVERSAL_STRATEGIES.md](TRAVERSAL_STRATEGIES.md) for a deep dive into options, risks, and rationales.

- **Bottom-Up (Default)**: Builds leaf nodes (entities → domain classes → repositories) before services, and services before entry points. Ensures compilation safety.
- **Top-Down**: Builds entry points first using mock contracts for downstream dependencies, permitting early integration verification.
- **Vertical Slice**: Identifies end-to-end features or domains (e.g., controller → service → repo) and builds them in self-contained threads.

Regardless of the strategy, dependency tracking is deterministic and mapped directly from the edges in the requirements graph.

---

## Stage 7b: Functional tests — shift-left acceptance tests

After Gate 2 clears but **before** the swarm writes a line of target code, `anti-legacy:functional-tests` authors the executable acceptance tests from the per-requirement test contracts. This is deliberate sequencing: writing the scenario tests first (shift-left) means the build has a target to satisfy, and it forces a hard validation that every contract is actually *runnable and unambiguous* — a contract with no scenarios, no `target_component`, or an assertion that can't be evaluated fails the pass rather than slipping through to be discovered at UAT.

It emits JUnit 5 for a Java target stack and pytest for Python (one test class/module per contract, one test per scenario), written into the target tree so the tests travel with the delivered repo; any other stack returns an explicit "stack not yet supported" error rather than a silent pass. The authored tests are *executed* post-build in `anti-legacy:target-review`, where their results feed GATE_3_BUILD. functional-tests itself is a blocking pre-build validation phase with no gate of its own.

---

## Stage 8: Swarm — micro-context builds

The swarm dispatches one developer subagent per task. Each subagent receives a micro-context:

- The requirement node (business rules, validations, error paths)
- The blueprint spec (target class name, method signatures, package)
- The test contract (what scenarios must pass)
- Relevant translation patterns from the brain (e.g., COMP-3 → BigDecimal)

The subagent does not see the full codebase. It doesn't need to. The coordinator has already read the original source and distilled what's needed. This is what keeps the token cost manageable on large codebases — each subagent works in a focused context, not against 50,000 lines of COBOL.

Translation patterns compound. When the first COMP-3 precision issue is handled correctly in Layer 1, that pattern is stored in the brain and automatically included in the context for every subsequent subagent that touches financial arithmetic.

---

## Stages 9–13: Review, Semantic Validation, UAT, deploy

- **Target review** (Phase 10) runs the target stack's build tool plus the code-quality and security validators (`validator_discovery.py`), then a BLOCKING round-trip rule-coverage proof (`generate_target_graph.py` + `compare_graphs.py`). Gate 3 auto-clears only when the build passes AND `rule_coverage >= 1.0` (zero FAIL requirements); a missing required toolchain FAILs the gate rather than auto-passing it.
- **Semantic Validation** (Phase 11) groups requirements by dependency chains, deploys validator subagents to compare new code side-by-side with original legacy source, and records any functional gaps as nested per-requirement `semantic_gaps` directly back to the requirements graph (`requirements_graph.json`). Pauses at Gate 3B (Semantic Review), a human gate (Architect + Tech Lead).
- **UAT Crew** (Phase 12) dispatches independent read-only reviewer subagents who validate the built code against test contracts. Gate 4 requires an independent UAT lead to sign off.
- **Document** runs after Gate 4: `anti-legacy:document` synthesizes the target app's human-facing docs (README, ARCHITECTURE, DEPENDENCIES, ENVIRONMENTS) *from* the committed artifacts (config / blueprint / requirements graph / target graph), not by coining LLM prose, and writes them **inside the target app directory** so the delivered repo is self-describing. Each is registered as a manifest artifact and re-verified by checksum (no hand-editing after registration).
- **Final review** is the last step before deploy: `anti-legacy:final-review` is a completeness-review swarm that scans the **built** target app for mocked / half-done / incomplete work across four dimensions — CODE (TODO/stub/`NotImplementedError`/trivial returns), DOCS (empty or TODO sections, README with no setup/run steps), CONFIG (hardcoded test values, placeholder secrets), BUILD (skipped/disabled tests, skip-tests flags) — with one parallel reviewer subagent per dimension over a deterministic `completeness_scanner`. It runs last, so it reviews the docs and functional tests too. It emits `evidence/completeness-report.json` (`status: PASS|FAIL`); **GATE_5_COMPLETENESS auto-clears only on PASS** (zero HIGH findings). On any HIGH it does not clear the gate — it kicks back to the phase that owns the gap (a CODE/BUILD stub re-dispatches `swarm`; a DOCS gap re-runs doc-authoring; a CONFIG gap fixes config or kicks back to `blueprint`), regenerates, and re-runs — not a full pipeline restart.
- **Deploy** (the final phase) generates Dockerfiles, CI/CD pipelines, and deployment descriptors.

---

## The readiness gate: a producer that can refuse

Most of the pipeline's safety comes from gates that fire on *transition* — `manifest advance` refuses to leave a gate phase until the bound gate is signed. There is a second, complementary check that fires at *execution* time, inside the producers themselves: `antilegacy_core.precheck`.

The problem it solves: a producer that merely checks "did I write a non-empty file" can happily emit a deliverable against a half-finished or stale pipeline. And manifest state can silently drift from disk reality — a derived `requirements_graph.json` can outlive the gitignored `legacy-graph` evidence it was built from. So before a Tier-A producer runs, it calls `require_ready(phase)`, which evaluates per-phase probes:

- **Gates** — the required upstream gates are `passed`/`waived`.
- **Artifacts** — each required artifact is registered in the manifest, present on disk, and (if a checksum was recorded) checksum-verified.
- **Disk-reality reconcile** — a present artifact whose declared `depends_on` source is missing or checksum-drifted is flagged orphaned/stale. This catches the "derived artifact outlived its source" case.
- **Completeness** — phase-specific: every active requirement's `business_rules` carry a numeric `confidence`, and `coverage` is ≥ 1.0.

On any block-severity probe, precheck prints the blockers (each with a one-line fix) to stderr and exits non-zero — it **refuses** rather than producing against an incomplete, orphaned, or state-desynced pipeline. You can run it directly to ask "is phase X ready?": `python3 .anti-legacy/run.py precheck <phase>` (add `--advisory` to always exit 0 and just report, or `--json` for a machine-readable probe list). It is read-only — it never writes `audit.jsonl`, advances a phase, or clears a gate.

Which producers gate on it is a deliberate split. The **Tier-A "snapshot" deliverables** — `prd`, `diagrams`, `test-plan`, `test-scripts`, `migration-plan` — call `require_ready` and refuse on an incomplete pipeline, because a PRD or migration plan built from a half-extracted graph is misleading. The **living logs** — `risk-log`, `decisions-log`, `evidence-log` — deliberately do *not*, because their whole job is to run on an incomplete pipeline and **surface** its gaps (§6: every "done" needs "still not done").

---

## Detailed Phase & Deliverable Breakdown

The anti-legacy pipeline is structured into distinct phases (Setup → Deploy) separated by human and automated sign-off gates. Each phase maps to a legal value in the manifest phase enum (`survey`, `analyze`, `graph-translate`, `blueprint`, `test-strategy`, `review-packet`, `gate-design-review`, `planning`, `gate-plan-review`, `functional-tests`, `build`, `target-review`, `semantic-validation`, `gate-build-integrity`, `uat`, `gate-uat-signoff`, `document`, `final-review`, `complete`, plus the `semantic-join` step for multi-source merges); `manifest advance <phase>` rejects any value outside that enum. Note that `extraction` and `negative-extraction` both run inside the `graph-translate` enum slot and do not advance the phase themselves. Below is the detailed specification of inputs, templates, outputs, registered artifacts, and gate constraints for each phase.

All pipeline skills call their scripts through a single dispatcher — `python3 .anti-legacy/run.py <script-stem> <args>` (bare stem, no `scripts/` prefix, no `.py`) — which `anti-legacy:setup` writes into the workspace with the absolute plugin root baked in. State transitions and gate decisions are recorded against the manifest: `manifest advance <phase>` moves the pipeline forward, and `manifest gate <GATE_ID> --opinion <passed|failed|waived> --evaluator <x> [--evidence id1,id2]` records a verdict. There is no `rejected` or `approve` form. A gate may only be recorded `passed` when every cited `--evidence` id is a registered artifact (no-evidence and unknown-evidence PASSes are rejected); `waived` is an explicit human override and `failed` needs no evidence. `manifest status` is the authoritative pipeline state — file presence on disk is not.

---

### Phase 1: Setup
*   **Purpose**: Initializes the modernization workspace and records project configurations.
*   **Inputs & References**:
    *   the manifest template (package data in `antilegacy_core`) (scaffolds the initial project manifest)
    *   `skills/setup/references/anti_patterns.md` (reference: anti-patterns to prevent line-by-line or microservice rewrites)
*   **Outputs & Deliverables**:
    *   `.anti-legacy/manifest.json` (project status state)
    *   `.anti-legacy/config.json` (source application paths and target stack selection)
    *   `.anti-legacy/audit.jsonl` (raw tamper-evident chronological action log)
*   **Registered Artifacts**: None (initializes the workspace).
*   **Gate Constraints**: None.

---

### Phase 2: Survey
*   **Purpose**: Indexes the legacy source with `wicked-estate` to build a structural code graph (the engine, §H). Mainframe estate + modern languages in one pass.
*   **Inputs & References**:
    *   `.anti-legacy/config.json` (source application directories)
    *   Legacy codebase directories.
*   **Outputs & Deliverables**:
    *   `.anti-legacy/graphs/<app>.db` (per-repo wicked-estate code graph; gitignored, rebuilt on demand)
    *   `.anti-legacy/legacy-graph.digest.txt` (the deterministic, checksummable stats digest — the committed thin-seam evidence)
*   **Registered Artifacts**:
    *   `legacy-graph` (path: `legacy-graph.digest.txt`, format: `text`, produced-by: `anti-legacy:survey`, depends on: none). The SHA-256 of the digest is the checksummed evidence the gate/audit contract consumes. (Replaces the deleted `legacy_graph.json` JSON blob; no `--schema`.)
*   **Gate Constraints**: None at phase end; `GATE_0_DISCOVERY` (automated) runs post-survey.

---

### Phase 3: Analyze
*   **Purpose**: Analyzes call-graph topology, data-asset coupling, complexity risk, and logical domain boundaries — reading the wicked-estate code graph via the `wicked_estate` helper (`query`/`blast-radius`/`rank`/`stats`/`source`/`cross-graph`), never raw SQLite, never `legacy_graph.json`.
*   **Inputs & References**:
    *   `legacy-graph` (the registered digest + the per-app graph DBs under `.anti-legacy/graphs/`)
*   **Outputs & Deliverables**:
    *   `.anti-legacy/analysis-report.md` (topological and complexity report)
*   **Registered Artifacts**:
    *   `analysis-report` (path: `analysis-report.md`, format: `markdown`, depends on: `legacy-graph`)
*   **Gate Constraints**: None.

---

### Phase 4: Extraction — crawl, annotate, coverage
*   **Purpose**: Crawls the wicked-estate code graph with adaptive ring expansion (`anti-legacy:extraction`) and writes a business rule onto every behavior-bearing node — each ends RESOLVED (rule + confidence + provenance) or RISK-flagged. Replaces the old graph-translator enrich flow. (Occupies the `graph-translate` phase-enum slot.)
*   **Inputs & References**:
    *   `legacy-graph` & `analysis-report` & the per-app graph DBs
    *   Git-brain query: `business rule extraction {source_language} COBOL patterns gotchas`
    *   Git-brain query: `anti-pattern architecture modular monolith microservices line-by-line`
*   **Outputs & Deliverables**:
    *   Written wicked-estate `requirement`/`description`/`requirement_validated` fields (in-graph annotation)
    *   `.anti-legacy/annotations.jsonl` (the lossless IP overlay — the rule object + provenance + ring depth)
    *   `.anti-legacy/coverage-report.json` + `.anti-legacy/coverage-report.md` (resolved-or-flagged coverage)
*   **Registered Artifacts**:
    *   `coverage-report` (path: `coverage-report.json`, format: `json`, depends on: `legacy-graph`)
    *   `legacy-graph` is re-registered after the crawl so the digest reflects the freshly written `requirement` fields (the §I6 drift seam — wired, not gated in WF1).
*   **Gate Constraints**: None at phase end, but a **BLOCKING done-gate**: do not advance until `coverage.py` exits 0 (`coverage == 1.0`, zero unaccounted behavior-bearing nodes — the provable terminal). Resolved rules carry object-form `{id, statement}` items (RULE-/VAL-/ERR- + three-digit ids) matching the enriched profile `requirements-graph.enriched.schema.json` that GATE_1_DESIGN re-validates downstream.

---

### Phase 5: Review Packet
*   **Purpose**: Compiles all architectural and functional requirement details into a single human-readable review document.
*   **Inputs & References**:
    *   `requirements-graph`
*   **Outputs & Deliverables**:
    *   `.anti-legacy/review_packet.md` (unified review document)
*   **Registered Artifacts**:
    *   `review-packet` (path: `review_packet.md`, format: `markdown`, depends on: `requirements-graph`)
*   **Gate Constraints**:
    *   **GATE_1_DESIGN**: Pauses pipeline. Human gate (Lead Architect). Checklist verifies traceability, logic completeness, translated entities, and precision rules, with the requirements graph re-validated against the enriched profile (`requirements-graph.enriched.schema.json` — object-form `{id, statement}` rule items). Recording the gate PASSED requires citing at least one registered evidence artifact (`--evidence requirements-graph,review-packet`); a no-evidence or unknown-evidence PASS is rejected by `manifest gate`.

---

### Phase 6: Blueprint
*   **Purpose**: Designs the modern target stack architecture, file structures, and database schemas.
*   **Inputs & References**:
    *   `requirements-graph`
    *   `skills/blueprint/references/nfrs.md` (copied and customized into target Non-Functional Requirements)
    *   Git-brain query: `{target_stack} architecture patterns blueprint structure`
*   **Outputs & Deliverables**:
    *   `.anti-legacy/requirements/blueprint.json` (target architecture model)
    *   `.anti-legacy/requirements/blueprint.md` (human-readable system design)
    *   `.anti-legacy/requirements/nfrs.md` (custom Non-Functional Requirements document)
*   **Registered Artifacts**:
    *   `blueprint-json` (path: `requirements/blueprint.json`, format: `json`, depends on: `requirements-graph`)
    *   `blueprint-md` (path: `requirements/blueprint.md`, format: `markdown`, depends on: `requirements-graph`)
    *   `nfrs` (path: `requirements/nfrs.md`, format: `markdown`, depends on: `requirements-graph`)
*   **Gate Constraints**: Blocked until **GATE_1_DESIGN** is passed.

---

### Phase 7: Test Strategy
*   **Purpose**: Generates executable functional test contracts and parity verification scripts.
*   **Inputs & References**:
    *   `blueprint-json` & `requirements-graph`
    *   Git-brain query: `modernization test strategy parity testing {source_language} {target_stack} contracts`
*   **Outputs & Deliverables**:
    *   `.anti-legacy/contracts/test-strategy.md` (master strategy plan)
    *   `.anti-legacy/contracts/{domain}/*.contract.json` (per-requirement test contracts)
    *   `.anti-legacy/contracts/{domain}/*.integration.json` (integration scenario test contracts)
    *   `.anti-legacy/evidence/functional-test-report.json` (programmatic contract-run results from `test_runner.py`)
*   **Registered Artifacts**:
    *   `test-strategy` (path: `contracts/test-strategy.md`, format: `markdown`, depends on: `blueprint-json`)
*   **Gate Constraints**: None.

---

### Phase 8: Planner
*   **Purpose**: Decomposes target state components into estimated task checklists sorted by the configured traversal strategy.
*   **Inputs & References**:
    *   `blueprint-json` & `requirements-graph` & `test-strategy`
    *   [TRAVERSAL_STRATEGIES.md](TRAVERSAL_STRATEGIES.md) (architectural guidance on traversal options, risks, and rationales)
    *   `.anti-legacy/config.json` (defines the `traversal_strategy` configuration)
*   **Outputs & Deliverables**:
    *   `.anti-legacy/task.md` (build plan checklist with owner, status, and audit fields, sorted according to the selected strategy)
*   **Registered Artifacts**:
    *   `task-plan` (path: `task.md`, format: `markdown`, depends on: `blueprint-json`)
*   **Gate Constraints**:
    *   **GATE_2_PLAN**: Pauses pipeline. Human gate — PM + Tech Lead, both must sign. Checklist verifies task scope (≤8h), dependency sorting (verified programmatically via `planner_utils verify-order` according to the chosen strategy), completeness against the requirements graph, and adherence to the traversal strategy review checklist. Recording PASSED requires citing the registered `task-plan` evidence id; a no-evidence or unknown-evidence PASS is rejected.

---

### Phase 8b: Functional Tests (shift-left)
*   **Purpose**: Authors executable functional / scenario acceptance tests from the per-requirement contracts BEFORE the build (`anti-legacy:functional-tests`), and HARD-GATES that those contracts are runnable and unambiguous. A blocking pre-build validation pass — runs after GATE_2_PLAN, before `build`.
*   **Inputs & References**:
    *   `.anti-legacy/contracts/{domain}/*.contract.json` (each contract: `req_id`, non-empty `target_component`, ≥1 scenario with unique `id` + `inputs` + an `expected_output`/`expected_error`)
    *   `config.target_stack`
*   **Outputs & Deliverables**:
    *   Per-stack executable tests under the target tree — Java: JUnit 5 classes at `{target_path}/src/test/java/acceptance/`; Python: pytest modules at `{target_path}/tests/acceptance/` (one per contract, one test per scenario). Any other stack → explicit error, never a silent pass.
    *   `.anti-legacy/evidence/functional-authoring-report.json` (authoring report: status, per-contract authored list, validation errors)
*   **Registered Artifacts**: `functional-authoring-report`.
*   **Gate Constraints**: No gate of its own; it is a blocking pre-build pass (validation must exit 0 before authoring). The authored tests are *executed* post-build in `anti-legacy:target-review`, where their results feed **GATE_3_BUILD**. Invocation: `python3 .anti-legacy/run.py functional_tests validate --contracts .anti-legacy/contracts` then `… author --contracts … --stack {target_stack} --output … --report …`.

---

### Phase 9: Swarm Build
*   **Purpose**: Runs developer subagents in parallel to build modern target classes, models, and repositories.
*   **Inputs & References**:
    *   `task-plan`
    *   Per-task inputs: specific requirements node, target class blueprint, test contract, and legacy source file
    *   Git-brain query: `{source_lang}-to-{target_stack}/` translation recipes
*   **Outputs & Deliverables**:
    *   Modern target state source code files
*   **Registered Artifacts**: None.
*   **Gate Constraints**: Blocked until **GATE_2_PLAN** is passed.

---

### Phase 10: Target Review
*   **Purpose**: Compiles target code and executes the code-quality linter and security scanners (via `validator_discovery.py`, the runtime gate verifier). It then runs a BLOCKING round-trip rule-coverage proof: `generate_target_graph.py` scans the target tree to emit `target_graph.json`, and `compare_graphs.py` compares it against the requirements graph + blueprint. Clearing the gate requires both a clean build AND `rule_coverage >= 1.0` with zero FAIL requirements; if the round-trip fails, the swarm is re-dispatched for the uncovered rules rather than clearing the gate.
*   **Inputs & References**:
		*   Target state source files
		*   `requirements-graph` & `blueprint-json` (compared by `compare_graphs.py`)
*   **Outputs & Deliverables**:
		*   `.anti-legacy/evidence/build-integrity.json` (build verification evidence)
		*   `.anti-legacy/evidence/code-quality.json` (code quality linting evidence)
		*   `.anti-legacy/evidence/security-scan.json` (security scan vulnerabilities evidence)
		*   `.anti-legacy/target_graph.json` (target tree graph with implemented-rule anchors)
		*   `.anti-legacy/evidence/functional_comparison_report.json` / `.md` (round-trip rule-coverage proof)
*   **Registered Artifacts**:
		*   `build-integrity` (path: `evidence/build-integrity.json`, format: `json`, depends on: `task-plan`)
		*   `code-quality` (path: `evidence/code-quality.json`, format: `json`, depends on: `task-plan`)
		*   `security-scan` (path: `evidence/security-scan.json`, format: `json`, depends on: `task-plan`)
		*   `target-graph` (path: `target_graph.json`, format: `json`, produced by target-review)
		*   `functional-comparison-report` (path: `evidence/functional_comparison_report.json`, format: `json`)
*   **Gate Constraints**:
		*   **GATE_3_BUILD**: Automated. Clears only when `build-integrity.json` status is `PASS` (or `WARNING` for an optional missing tool — a missing *required* toolchain such as a JRE now FAILs; there is no mock compiler) AND the `compare_graphs.py` round-trip passes (`rule_coverage >= 1.0`, zero FAIL requirements). The gate is recorded citing `--evidence build-integrity,code-quality,security-scan,functional-comparison-report`; the PASS is rejected if any cited evidence id is unregistered. The round-trip is a hard precondition — compilation alone is insufficient.

---

### Phase 11: Semantic Validation
*   **Purpose**: Compares target implementation side-by-side with legacy source code using dependency chains to uncover logical and behavioral gaps.
*   **Inputs & References**:
    *   Target state source files & Legacy source code files
    *   `blueprint-json` & `requirements-graph`
*   **Outputs & Deliverables**:
    *   `.anti-legacy/requirements/requirements_graph.json` (updated with nested per-requirement `semantic_gaps` details)
    *   `.anti-legacy/evidence/semantic-validation-report.json` (JSON validation evidence)
    *   `.anti-legacy/evidence/semantic_validation_report.md` (validation markdown report)
*   **Registered Artifacts**:
    *   `semantic-validation-report` (path: `evidence/semantic-validation-report.json`, format: `json`, depends on: `build-integrity`)
*   **Gate Constraints**:
    *   **GATE_3B_SEMANTIC**: Pauses pipeline. Human gate (Architect + Tech Lead) — a rule-coverage round-trip review. The gate reads the nested per-requirement `semantic_gaps`; the checklist verifies all connected dependency chains were reviewed and all HIGH/MEDIUM severity gaps are resolved or explicitly approved. Recording PASSED requires citing the registered `semantic-validation-report` evidence.

---

### Phase 12: UAT Crew
*   **Purpose**: Executes independent quality assurance reviews on target files against business rules.
*   **Inputs & References**:
    *   Target state source files & test contracts
    *   Legacy source code files
*   **Outputs & Deliverables**:
    *   `.anti-legacy/evidence/uat/{domain}.json` (per-domain QA findings)
    *   `.anti-legacy/uat-summary.md` (summary of QA verdicts)
    *   `.anti-legacy/audit_report.md` (re-compiled compliance log table)
*   **Registered Artifacts**:
    *   `uat-verdicts` (path: `evidence/uat/`, format: `json`, depends on: `semantic-validation-report`)
    *   `uat-summary` (path: `uat-summary.md`, format: `markdown`, depends on: `semantic-validation-report`)
*   **Gate Constraints**:
    *   **GATE_4_UAT**: Pauses pipeline. Requires independent UAT Lead sign-off. Independence is **machine-enforced**: the `--evaluator` passed to `gate GATE_4_UAT` MUST differ from the manifest `roles.architect` (the GATE_1 reviewer); gatekeeper hard-fails the gate if they match. Checklist verifies all domains passed, no open critical/major findings, and precision parity verification. Compiles final `audit_report.md`.

---

### Phase 12b: Document
*   **Purpose**: Synthesizes the target app's human-facing documentation (`anti-legacy:document`) FROM committed pipeline artifacts — not by coining LLM prose — and writes it INSIDE the target app directory so the delivered repo is self-describing. Runs in the `document` phase, after GATE_4_UAT, before `final-review`.
*   **Inputs & References**:
    *   `.anti-legacy/config.json` (stack, db, deploy target, target_path)
    *   `blueprint.json` (domains, services, package layout)
    *   `requirements_graph.json` (`data_access`, `dependencies`)
    *   `target_graph.json` (ARCHITECTURE fallback if blueprint empty)
*   **Outputs & Deliverables**: four files under `config.target_path` — `README.md` (what the app does + setup/run), `ARCHITECTURE.md` (domains/services/package layout), `DEPENDENCIES.md` (service/db/file infra deps), `ENVIRONMENTS.md` (deploy targets + per-environment config).
*   **Registered Artifacts**: `doc-readme`, `doc-architecture`, `doc-dependencies`, `doc-environments` (each `status: final`, checksum re-verified by `manifest check` — no hand-editing after registration).
*   **Gate Constraints**: No gate of its own; it precedes GATE_5_COMPLETENESS (whose DOCS dimension scans these four docs). Invocation: `python3 .anti-legacy/run.py document` (reads standard artifact paths, writes to `config.target_path`, registers all four); `--no-register` for a dry run.

---

### Phase 12c: Final Review (completeness gate)
*   **Purpose**: Completeness-review swarm (`anti-legacy:final-review`) — scans the BUILT target app for mocked / half-done / incomplete work across four dimensions with one parallel reviewer subagent per dimension over the deterministic `completeness_scanner`. Runs LAST (after docs + functional tests exist, so it reviews those too), in the `final-review` phase, before `complete`.
*   **Inputs & References**:
    *   The target tree at `config.target_path`
    *   The four docs (README/ARCHITECTURE/DEPENDENCIES/ENVIRONMENTS) + the functional/UAT tests produced upstream
    *   Upstream prerequisite: GATE_4_UAT `passed`/`waived`
*   **Review dimensions**: **CODE** (TODO/FIXME/HACK, stub/mock/placeholder comments, trivial `return null/0/""`, `NotImplementedError`/`UnsupportedOperationException`/`panic()`/`todo!()`), **DOCS** (empty or TODO/TBD sections, README with no setup/run steps), **CONFIG** (hardcoded test values like `localhost`/`h2:mem`, placeholder env vars like `changeme`/`<…>`, empty sensitive keys), **BUILD** (skipped/disabled tests `@Disabled`/`@Ignore`/`it.skip`/`@pytest.mark.skip`, skip-tests flags like `-DskipTests`, commented-out steps). Severity: HIGH (would ship broken behavior → FAIL on any HIGH), MEDIUM (review-worthy), LOW (placeholders in files meant to carry them, e.g. `.env.example`).
*   **Outputs & Deliverables**:
    *   `.anti-legacy/evidence/completeness-report.json` (`status: PASS|FAIL`, `counts: {HIGH,MEDIUM,LOW}`, `dimension_counts`, `findings: [{dimension, path, line, severity, what}]`)
    *   `.anti-legacy/evidence/completeness-{CODE,DOCS,CONFIG,BUILD}.json` (per-reviewer slices)
*   **Registered Artifacts**: `completeness-report` (registered only on PASS).
*   **Gate Constraints**:
    *   **GATE_5_COMPLETENESS**: Automated — auto-clears ONLY when `status: PASS` (zero HIGH findings). On any HIGH it does NOT clear: each HIGH names the owning phase, and final-review kicks back targeted (CODE/BUILD → re-dispatch `anti-legacy:swarm` + re-run `anti-legacy:target-review`; DOCS → re-run doc-authoring; CONFIG → fix config or kick back to `anti-legacy:blueprint`), regenerates, and re-runs — not a full pipeline restart. Invocation: per-dimension `python3 .anti-legacy/run.py completeness_scanner --workspace {target_path} --dimension {CODE|DOCS|CONFIG|BUILD} --output …`, then a consolidating `… completeness_scanner --workspace {target_path} --output .anti-legacy/evidence/completeness-report.json` (exits 0 on PASS, non-zero on FAIL).

---

### Phase 13: Deploy
*   **Purpose**: Generates container files, deployment descriptors, and pipeline configs.
*   **Inputs & References**:
    *   `.anti-legacy/config.json` (deployment target settings)
*   **Outputs & Deliverables**:
    *   Target deployment manifests (Dockerfiles, Kubernetes YAML, etc.)
*   **Registered Artifacts**:
    *   `deployment-manifests` (path: target deployment path, format: `text`, depends on: `uat-summary`)
*   **Gate Constraints**: Blocked until **GATE_4_UAT** is passed.

---

## What the pipeline can't do

- It can't recover business logic that isn't in the code. If the legacy program has a `CALL "MAGIC-UTIL"` and MAGIC-UTIL.cbl doesn't exist in the source tree, that gap will show up in the requirements node as a missing dependency — visible before Gate 1, not after.
- It can't make Gate 1 unnecessary. The requirements extraction is good, not perfect. Human review of the requirements graph is the only way to catch semantic errors before they propagate into 200 generated files.
- It can't parallelize across gates. Gates exist because some decisions require human judgment. The pipeline is autonomous between gates, not across them.
