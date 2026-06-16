# Capability extraction on modern code — anti-legacy plan

**Problem.** Capabilities are derived from call-graph community detection (`cluster(weight="calls")`),
which is mainframe-shaped. On dense modern code it produces mega-blobs (full Kafka → one 64,529-node
community) or singletons (213 interface methods → 150 capabilities), names fall back to noun-frequency
(`AlreadyAlwaysCapability`), and nothing coalesces across apps (Kafka "record" vs Pulsar "message").

**Strategy.** Stop treating call topology as the *sole* capability signal. Blend in the three signals
modern code actually carries — **package/module structure, richer edges, semantic similarity** — most of
which are already in the engine's graph. This doc is the anti-legacy half; engine-owned items are flagged
`[ESTATE]` (the wicked-estate conversation).

**Status (2026-06-15):** Phases 1 + 2 ✅ implemented on `feat/modern-capability-extraction`
(`scripts/vocabulary.py` + `scripts/domain_graph.py`, +25 tests). Phase 1 also revealed — and routed
around — an engine gap: wicked-estate 0.1.7 has no native `annotate` store, so the projected-`domain_*`
round-trip writes nothing; naming now derives the term index **directly from the confirmed glossary +
node names** (`_derive_term_index_from_glossary`), engine-independent. Measured on the real Kafka+Pulsar
demo: cross-app coalescing **0 → 7**, names clean (`ProducerCapability` vs `AlreadyAlwaysCapability`),
schema-valid; `kafka-clients` 2703-node blob → 9 package capabilities; mainframe (carddemo) unchanged.
Phases 3–4 remain (partly `[ESTATE]`-gated).

**Known limitations (from a 6-dimension adversarial verification; the critical mainframe-tokenizer
regression it caught is fixed):**
- **Naming quality is glossary-curation-gated.** On a glossary that confirms *every* mined term (the demo's
  auto-confirm shortcut), code-mechanics tokens (`MAYBE`, `HANDLE`, `RESULT`, `REQUEST`) can outrank real
  domain nouns and produce run-on/noisy names — ~65% noise on raw `kafka-clients`. The real pipeline's
  human glossary curation (confirm only true domain terms) is what makes naming clean; auto-confirm is a
  demo convenience, not the contract. A future guard could warn when low-signal tokens dominate.
- **Flat (non-package-nested) modern modules** fall back to call-affinity (no package signal), so their
  names lean on the statement-noun namer — fine for nested real modules, weaker for a flattened scope.
- **Action-order under-coalescing:** `sendAndCommit` vs `commitAndSend` pick different head verbs
  (first-confirmed-in-name-order), so a compound-verb capability can under-coalesce. Deterministic; minor.
- **Adjacent acronyms with no lowercase boundary** stay glued (`getHTTPSURL` → `[GET, HTTPSURL]`) — inherent
  camelCase ambiguity, accepted.

---

## Phase 1 — Vocabulary-driven naming  ·  no engine dep · highest ROI · low risk

The naming chain (`domain_name_for`) already prefers canonical vocabulary terms; my demo just never ran
the vocabulary phase, so it fell to noun-frequency. Wire it in.

- Sequence `vocabulary mine → confirm → project_terms_to_graph` **before** graph-translate (orchestrate).
- `project_terms_to_graph` writes `domain_entity`/`domain_action` tags → `domain_graph._term_aware_name`
  emits `{Action}{Entity}Capability`.
- **Coalescing for free:** same canonical across apps → same domain key → they merge (`domain_name_for`
  docstring). Wherever Kafka and Pulsar already share a term, capabilities coalesce.
- Files: `skills/orchestrate` (phase order), `scripts/vocabulary.py` (exists), `scripts/domain_graph.py`
  (already consumes via `build_term_index`).
- Done when: demo capabilities are named from canonical terms (no `AlreadyAlwaysCapability`) and ≥1
  cross-app capability coalesces.
- Effort: **S**.

## Phase 2 — Package/module structure as a primary capability signal  ·  no engine dep · fixes blobs + singletons

Java/modern packages are designed as cohesive capabilities. `_sub_partition_by_package` already exists
(added by the overflow fix) but only as a fallback.

- For non-mainframe `source_apps`, derive capabilities from **package/directory** structure first, then
  refine within a package using call/implements edges — instead of call-clustering the whole estate.
- Add granularity control: merge singleton capabilities that share a package; cap capability size.
- Files: `scripts/domain_graph.py` (clustering/partition entry — `gather_app` / the cluster consumption).
- Done when: the demo yields O(packages) coherent capabilities, not 1 blob or 150 singletons.
- Effort: **M**.

## Phase 3 — Finer / multi-edge clustering  ·  ✅ ADOPTED (engine `--hierarchical`)

The engine now ships **hierarchical Louvain** (`clusters --hierarchical`) over its richer edge set
(calls + imports + the new Java/Spring DI / pub-sub / route edges). anti-legacy consumes it as the opt-in
`capability_partition: "hierarchical"` mode (`wicked_estate.cluster_modes(db, hierarchical=True)`), with
graceful fallback to the language-driven default when the engine predates it.

- Measured on the `kafka-clients` mega-blob: hierarchical → top community **62** (vs the package-partition's
  1880, vs plain Louvain's 1025). Members conserved, no dups.
- Files: `scripts/wicked_estate.py` (`cluster_modes`), `scripts/domain_graph.py` (`_capability_partition`).
- Residual: the engine's `--package-bias` / `--resolution` flags parse but had **no measurable effect** in
  the build tested — file an engine bug; `--hierarchical` is the one that works.

## Phase 4 — Semantic clustering for cross-vocabulary coalescing  ·  ✅ ADOPTED (engine `--weight semantic`)

The engine now ships **embedding clustering** (`clusters --weight semantic`, DBSCAN/KMeans over node
embeddings from `index --embeddings`). anti-legacy consumes it as `capability_partition: "semantic"`
(`cluster_modes(db, semantic=True)`), graceful-fallback as above. This groups code by what it DOES, which
can coalesce the same capability across source apps with different vocabularies (Kafka "record" ≈ Pulsar
"message") — the bridge exact-canonical naming can't make.

- Also LIVE now: the native `annotate` store round-trips, so the projected-`domain_*` path in
  `build_term_index` works (the glossary-direct derivation remains the engine-independent fallback).
- Residual: the default embedder's semantic quality is modest (top search hits were imports); build the
  engine with the `fastembed` feature for sharper embeddings. A vocabulary-side synonym-resolution step
  (embed mined terms → shared canonical before `project`) is still open as a complementary path.
- Files: `scripts/wicked_estate.py` (`cluster_modes`), `scripts/domain_graph.py`.

> **Engine-version note:** these modes require a wicked-estate build with hierarchical/semantic clustering.
> They are feature-detected and fall back cleanly on the released `0.1.7`, so adopting them is safe before
> the engine release lands on crates.io.

---

## Sequencing

- **Phases 1 + 2 are independent of estate** and deliver most of the visible win — canonical names, sane
  granularity, and same-word cross-app coalescing. Do these first.
- **Phases 3 + 4 deepen it** and partly depend on the engine features under discussion with estate.
- Highest single leverage overall is semantic clustering (Phase 4 / `[ESTATE]`); cheapest high-value is
  package-as-primary-signal (Phase 2, anti-legacy-only).

## What to ask estate for

1. `clusters --weight semantic` (or a per-symbol embedding export) — unblocks Phase 4, the biggest win.
2. A multi-edge `clusters --weight <blend>` — simplifies Phase 3.
3. Richer modern-language edges (DI wiring, event pub/sub, annotation relationships like `@EventListener`
   / REST route maps) — the real capability connectors call/import edges miss.
4. Tunable / hierarchical community detection (resolution param, Leiden) — avoids the 64K mega-community.
