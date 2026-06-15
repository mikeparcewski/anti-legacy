---
name: "anti-legacy:graph-translator"
description: >
  The §I5 domain-graph re-think. Takes the ANNOTATED wicked-estate code graph (the
  resolved-or-flagged evidence spine produced by anti-legacy:extraction) plus the
  cluster(weight="calls") capability communities and re-thinks them into the
  TARGET-state DOMAIN graph — a capability-oriented requirements_graph.json, not a
  1:1 code skeleton. Merges one-or-more source apps into ONE target system,
  honoring disposition (keep | modify | drop | new) so a reimagined-away rule is an
  EXPLICIT drop with a reason, never a silent omission. Emits the gate-validated
  requirements graph + a drop manifest + disposition-aware round-trip coverage.
  Use when: you reached here to "translate the call graph" / "build the
  requirements graph" / "re-think the annotated graph into capability domains" /
  orchestrate Phase 4 "Graph Translate" (after extraction, before blueprint).
---

# anti-legacy:graph-translator — the §I5 domain-graph re-think

This skill drives `scripts/domain_graph.py` (via the dispatcher). It is the §I5
re-think CLAUDE.md names: the second of the two graphs.

- **CODE graph** (the spine) — the `wicked-estate` index, annotated by
  `anti-legacy:extraction`: every behavior-bearing node ends RESOLVED (a rule
  at/above `resolve_threshold`) or RISK-flagged. This is the evidence.
- **DOMAIN graph** (what THIS skill builds) — the TARGET requirements, re-thought
  into capability-oriented domains. A capability PLAN, not a code skeleton.

The product is a behavior-preserving **targeted rewrite**: data shapes, interfaces,
and jobs are INVARIANT; only the implementation is reimagined. The domain graph
must COVER every code-graph requirement edge — honoring disposition.

> Business-rule EXTRACTION is NOT this skill. That is `anti-legacy:extraction`
> (it annotates the graph). This skill consumes those annotations and re-thinks
> them into the target domain graph. If you were sent here to *extract rules from
> source*, run `anti-legacy:extraction` first.

---

## Precondition: front-half coverage == 1.0

§I5 refuses to translate an incompletely-annotated graph. Before building, the
latest `.anti-legacy/coverage-report.json` must show `coverage == 1.0` (every
behavior-bearing node RESOLVED or RISK). If it is below 1.0 the builder exits
non-zero listing the unaccounted SymbolIds — go back to `anti-legacy:extraction`
and annotate them. Regenerate the report with:

```bash
python3 .anti-legacy/run.py coverage
```

---

## Run it

The builder is dispatched through the workspace shim (never call `scripts/`
directly):

```bash
python3 .anti-legacy/run.py domain_graph \
  --output .anti-legacy/requirements/requirements_graph.json
```

It reads `config.source_apps` + `migration_mode` from `.anti-legacy/config.json`,
resolves each app's per-app DB under `.anti-legacy/graphs/<app>.db`, and the
`.anti-legacy/annotations.jsonl` overlay.

`migration_mode` selects the target shape and is written verbatim into the graph's
`metadata.migration_mode`. The intended default is **`functional`** — the domain
graph is a capability PLAN (capability-oriented domains from call-affinity
clusters), NOT a 1:1 code skeleton. `structural` produces code-equivalent nodes
for like-for-like rehost only. The builder honors `migration_mode` from config
(it does NOT silently default to structural); a `functional` config yields a
`functional` graph.

Useful flags:

- `--config <path>` — alternate config (default `.anti-legacy/config.json`).
- `--annotations <path>` — alternate overlay (default honors the
  `ANTI_LEGACY_ANNOTATIONS` env, else `.anti-legacy/annotations.jsonl`).
- `--db <path>` — score a single DB instead of every `source_apps` DB.
- `--coverage-report <path>` — alternate front-half precondition report.
- `--schema <path>` — alternate enriched schema (default
  `schemas/requirements-graph.enriched.schema.json`).
- `--net-new <path>` — a JSON list (or `{"net_new": [...]}`) of net-new TARGET
  requirement specs (`{domain, title, business_rules, data_access}`) — the
  add-capability half of the merge (`provenance="net-new"`, `legacy_components=[]`,
  exempt from the round-trip denominator). Also read from `config.net_new`.
- `--skip-front-half-check` — DRY-RUN/TEST ONLY; the gate requires the check in
  production.

---

## What it produces (three artifacts, under `.anti-legacy/requirements/`)

1. **`requirements_graph.json`** — the primary, GATE_1_DESIGN-validated artifact.
   Validates against `schemas/requirements-graph.enriched.schema.json` (Draft7,
   object-form rules) with ZERO errors or the build fails.
2. **`dispositions.json`** — the explicit DROP manifest. A legacy SymbolId listed
   here with a `drop_reason` is intentionally reimagined away (not a coverage
   gap). v1 emits no automatic drops; the manifest always exists so a curator can
   record an explicit drop the round-trip check will honor.
3. **`roundtrip-coverage.json`** — the disposition-aware round-trip gate evidence
   (`{legacy_rule_total, represented, dropped, uncovered_symbol_ids,
   roundtrip_coverage}`).

---

## How the domain graph is built

### Capability DOMAINS come from clusters, NEVER from files

Domains are derived from `cluster(db, weight="calls")` communities — a call-affinity
community is a set of nodes that CALL each other, i.e. a behavioral unit (a
capability), independent of which file/copybook holds them.

- `weight="calls"` is **mandatory** — the only sound mode today. `confidence` and
  `data-affinity` collapse to group-by-file via the engine's `contains` hub
  (documented shim limitation in `wicked_estate.cluster`). The builder hardcodes
  `calls` for that reason.
- Domains are **file/copybook-derived NEVER** (forbidden). A capability domain
  spans merged sources; a file-derived domain cannot (files are per-repo).
- `cluster()` / `list_nodes()` are single-DB; the **cross-app merge happens after
  per-DB clustering** — communities across apps that resolve the same capability
  coalesce by domain name into one target domain.
- **Entities are co-located** into the capability's domain from the members'
  `data_access`, so `data_access ⊆ own-domain entities` (the T2 invariant). Data
  follows the capability that uses it — the inverse of the old "domain per data
  file" anti-pattern.

### Each target requirement carries

- `legacy_components` — MANDATORY, non-null: the sorted member SymbolIds the
  capability derives from (the trace back to the code graph). `[]` only for
  net-new.
- `business_rules` — OBJECT form (gate-1 `{id, statement, source_ref, confidence,
  provenance}`), re-numbered `RULE-NNN` per requirement, **one rule per
  behavior-bearing member** (`source_ref` = that member's SymbolId). A RESOLVED
  member emits its real statement; a RISK or UNACCOUNTED member emits a
  `REVIEW REQUIRED: <reason>` rule that PRESERVES its statement / risk_reason and
  its `source_ref` — so the risk member's behavior is NEVER erased from the graph,
  even when the requirement also has resolved rules. Any risk/unaccounted member
  forces `status="review"`.
- `provenance` — the source app id, or `"net-new"`.
- `disposition` + `disposition_reason` — `keep | modify | drop | new`. Reason is
  mandatory for modify/drop/new.
- `parity_hints` (additive optional) — one per detected numeric output
  (money/rate/percent/count). The downstream `anti-legacy:test-strategy` phase
  turns these into the contract's `parity_rules` (parity_rules live in the test
  contract, NOT the requirements schema — its rule objects are
  `additionalProperties:false`).
- `data_access`, `dependencies` (cross-cluster call edges → that capability's
  REQ_ID), `validations`, `error_paths`, `status`, `merged_programs`.

### Disposition model (keep | modify | drop | new)

- **keep** — ≥1 resolved member rule, single-source. `disposition_reason:
  "behavior preserved from <app>"`.
- **modify** — the same capability was contributed by >1 source app (cross-source
  reconciliation) or required restructuring. `status` forced to `review` so a
  human signs at GATE_1; reason names what changed.
- **drop** — a behavior-bearing legacy edge intentionally NOT made an active
  requirement. NEVER an omission: it is written to `dispositions.json` with
  `{symbol_id, app, legacy_rule_id, drop_reason, decided_by}` (curator-authored).
  `build()` READS an existing `dispositions.json` and honors it end-to-end — the
  round-trip treats a dropped-with-reason SymbolId as covered. A malformed manifest
  is a hard error (a drop that cannot be read must not silently become "no drops").
- **new** — a target capability with NO legacy origin (`provenance="net-new"`,
  `legacy_components=[]`). Authored via `--net-new` / `config.net_new`. Exempt from
  the round-trip denominator; still carries ≥1 business rule and is schema-valid.

---

## The core invariant (no silent maybe-correct)

The builder enforces **disposition-aware round-trip coverage** and exits non-zero
when it fails. Let:

- **L** = every behavior-bearing legacy requirement edge — `{(app, symbol_id)}`
  for every behavior-bearing node carrying a rule (RESOLVED **or** RISK/UNACCOUNTED;
  each is an edge that must be accounted for).
- **T** = the `(app, source_ref)` of every emitted `business_rule` in an
  `active`/`review` requirement **∪** every SymbolId in the drop manifest with a
  non-empty reason (DROP).

`COVERAGE_RULE: L ⊆ T`, i.e. `roundtrip_coverage = |{l in L : l in T}| / |L| == 1.0`.

Representation is graded at **rule granularity** (`business_rule.source_ref`), NOT
at symbol granularity (`legacy_components`). A member whose behavior rule is absent
from the graph is uncovered even if its symbol still rides in `legacy_components` —
this is the exact silent-erasure the cardinal invariant forbids. A behavior edge in
NEITHER set is a **silent drop**; the builder lists each uncovered
`(app, symbol_id, state)` and exits non-zero, mirroring `coverage.py`.

The front-half precondition is enforced TWICE: the `coverage-report.json` scalar
(`coverage == 1.0`) AND a re-derivation from the SAME overlay the builder reads (so
a stale report cannot let an unannotated node through). The round-trip evidence also
carries a `clustering` diagnostic (`degenerate=true` when every behavior community
is a singleton — a disconnected/batch estate where `weight="calls"` found no
call-affinity to group on; the reviewer confirms the domains are real distinct
capabilities, not a laundered file/program 1:1 partition).

This is the DESIGN-time (GATE_1) round-trip check; it is complementary to
`scripts/compare_graphs.py`, which re-checks per-requirement `rule_coverage` at
BUILD time (GATE_3_BUILD / 3B). The builder keeps REQ_IDs and business_rule object
ids stable + schema-valid so `compare_graphs.split_item()` joins them.

---

## Done looks like

```
domains=N  requirements=M  roundtrip_coverage=1.0000  legacy_rule_total=K  represented=K  dropped=0
wrote .anti-legacy/requirements/requirements_graph.json
```

- `roundtrip_coverage == 1.0` (no silent drops).
- Zero schema errors against the enriched profile.
- Front-half `coverage == 1.0` was a precondition.

If any fails, the builder exits non-zero with the offending SymbolIds — fix the
gap (annotate, represent, or explicitly drop with a reason); do NOT advance.

### Still not done (per §6)

- **Cross-app call dependencies** are wired within a single DB — and for the
  INDEPENDENT-SYSTEMS merge case that is correct, not a gap (ISS-08). Independent
  legacy systems coalesce at the capability level (domain+title), not by calling
  each other's code: the two real source apps share 0 call targets and emit 0
  cross-app/unresolved edges, so there is nothing to resolve across DBs. If a
  config DID have one repo calling into another (a shared library), that surfaces
  as an unresolved edge target and would be resolved via the engine's
  `cross-graph`. How two MERGED capabilities INTERACT in the target is a
  target-design decision made at **blueprint** time — a target dependency, not a
  legacy-call edge — so it is intentionally not synthesized here.
- **Automatic drops** are not emitted — every resolved edge is represented. The
  drop mechanism + manifest exist for a human curator; no rule is dropped without
  a person writing the reason.
- **Disconnected/batch estates** (independent programs chained by JCL, not by
  CALL) cluster into singleton-per-program communities. `weight="calls"` cannot
  merge nodes that do not call each other, so the partition degenerates to one
  capability per program. The builder does NOT silently pass this off as capability
  domains: the round-trip evidence carries `clustering.degenerate=true` for the
  reviewer to confirm the domains are real distinct capabilities. Coalescing
  same-capability singletons by rule-text similarity is deliberately NOT done
  (it would guess at capability identity — "silent maybe-correct"); the human
  confirms instead. A native call-graph that resolves JCL step ordering into call
  affinity would fix this at the source.
- `roundtrip-coverage.json` IS now a registered GATE_1_DESIGN evidence id (ISS-10):
  orchestrate Phase 4b registers it (`manifest register roundtrip-coverage`) and
  `anti-legacy:gatekeeper`'s GATE_1_DESIGN check verifies it's registered and that
  `roundtrip_coverage >= 1.0` — so the disposition-aware round-trip is a checksummed
  audit seam the design gate cites, not signalling-only. This skill produces the
  evidence; the gate consumes it.

---

## Migration note

- `anti-legacy:orchestrate` Phase 4 ("Graph Translate") dispatches this skill
  (after `anti-legacy:extraction`, before `anti-legacy:blueprint`).
- `scripts/graph_normalizer.py` is the OLD code-graph-JSON scaffold builder. It is
  NOT extended and NOT deleted — it stays green for its existing tests. §I5
  SUPERSEDES its role with `scripts/domain_graph.py`, which reads the LIVE engine +
  overlay (there is no code-graph JSON in the wicked-estate world) and emits
  POPULATED object-form rules (graph_normalizer emits empty rule slots).
