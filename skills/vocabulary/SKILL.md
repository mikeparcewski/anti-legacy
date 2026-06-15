---
name: "anti-legacy:vocabulary"
description: >
  Own the project's domain VOCABULARY — the shared, evidence-grounded glossary of
  entity nouns, action verbs, and abbreviations that rule statements are written in.
  A config-driven bootstrap MINES the live vocabulary from the wicked-estate code
  graph (db2_table/cics_program/step entities, paragraph/program verbs, field-token
  abbreviations); the extraction agent then PROPOSES new terms and PROMOTES meanings
  against code-logic ground truth. Two orthogonal axes per term: `status`
  (proposed -> confirmed = is the TERM real) and `verification` (unverified ->
  untrusted_verified -> trusted_verified = is the MEANING proven). Writes
  .anti-legacy/vocabulary.json, validated by schemas/vocabulary.schema.json.
  Use when: "bootstrap the vocabulary", "mine the glossary", "what does this
  abbreviation mean", "propose a term", "promote this term", "confirm a definition",
  "add an alias", "is this term trusted", "verify the vocabulary".
---

# anti-legacy:vocabulary

The vocabulary is the **canonical name set** for rule statements. When the extraction
agent writes "EDIT the CARD-NUM before POST", `EDIT`, `CARD`, and `POST` must be
*confirmed* vocabulary terms — not spellings the agent coined on the spot. This skill
owns `.anti-legacy/vocabulary.json`: how it is bootstrapped from the graph, how a new
term is proposed, and how a term's MEANING earns trust.

This is guardrail (c) made operable: **use CONFIRMED terms, PROPOSE new ones, never
silently coin.**

---

## The two axes (never conflate them)

Every term carries **two independent** lifecycle fields:

| Axis | Values | Question it answers |
|---|---|---|
| `status` | `proposed` → `confirmed` | Is this a REAL term in the domain? |
| `verification` | `unverified` → `untrusted_verified` → `trusted_verified` | Is the term's DEFINITION proven? |

A term can be `confirmed` (it is genuinely a domain word) while its definition is still
`unverified` (nobody has proven what it means yet). They move independently.

**The trust spectrum mirrors guardrail (b): code is ground truth, docs are claims.**

- `unverified` — mined token, or a definition taken only from a comment / README /
  copybook remark. That is an unproven **CLAIM**. Bootstrap emits this.
- `untrusted_verified` — definition corroborated by a SECONDARY source (a doc plus a
  naming convention, or one program's incidental usage) but **not** yet confirmed
  against authoritative CODE LOGIC.
- `trusted_verified` — definition **proven against CODE LOGIC** (you read the real
  paragraph body / the db2_table's actual usage) OR ratified by a human SME. **This is
  the only level a rule statement may assert as fact.**

When you write a rule and need a term's meaning, only a `trusted_verified` definition
may be stated as fact. A lower level means "name it, but flag the meaning as unproven."

---

## Bootstrap (mine the graph)

```bash
python3 .anti-legacy/run.py vocabulary --db .anti-legacy/graphs/<app>.db
# options: --config <path> --out .anti-legacy/vocabulary.json --min-freq N (default 3)
```

It reads `config.coverage.*` to decide which kinds each miner looks at — **no
name-prefix heuristic** (`startswith('CB')` is banned; it lost all data). Three miners:

- **entities** — `config.coverage.estate_behavior_kinds` (db2_table / cics_program /
  step: the whole node name is a verbatim noun, e.g. `CARDDEMO.TRANSACTION_TYPE`,
  `COADM01C`) + leading field-name token clusters (ACCT, CARD, CUST, TRAN, ...).
- **actions** — `config.coverage.behavior_kinds` (function / module): the leading verb
  of each paragraph/program name after dropping pure-numeric COBOL prefixes
  (`2000-POST-TRANSACTION` → `POST`). Yields READ / POST / UPDATE / EDIT / SEND / ...
- **abbreviations** — short (≤6 char) recurring field/variable tokens (WS, FLG, CUST,
  ACCT, NUM, ...). The expansion is left **BLANK** — propose, don't coin.

Every mined record lands `status: proposed`, `verification: unverified`, blank
`definition`, with `freq` (the TRUE recurrence count) and `mined_from` (the miner that
proposed it). `--min-freq` keeps one-off filler out (FILLER/X are also stopword-dropped).

**Helper gotcha the miner already honors:** `list_nodes(--kinds=...)` filters SIMPLE
kinds only; the estate object-kinds (`{"other":"db2_table"}`) come back ONLY when
`kinds=None`. The bootstrap enumerates with `kinds=None` and buckets by normalized kind
itself. (If you write your own graph read, do the same.)

The glossary stores **no** inline `sources[]` provenance by default
(`config.vocabulary.max_sources_per_term` defaults to **0**) — where-used is the
engine's job (`run.py wicked_estate query <token>` / `rank`), so the file stays a
compact meaning dictionary. Set the cap to a positive N to inline up to N representative
SymbolIds per term (a self-contained glossary at the cost of size).

If `vocabulary.json` is missing when extraction starts, run this first.

---

## Read the glossary (it's small — Read it directly)

The glossary is a MEANING dictionary, kept small on purpose (default-0 provenance), so
**Read `.anti-legacy/vocabulary.json` directly** to check a token's `status` /
`verification` / `definition` before naming it in a rule. There is no sidecar query
seam — an earlier substring-`lookup` over the file was retired (querying a parallel copy
of the file is the parallel-engine smell this codebase avoids). If your estate's glossary
ever grows large enough to matter, that is the signal to push where-used + categorization
into the engine (below), not to grep a bigger file.

---

## Project confirmed terms into the engine (domain resolution)

Once terms are `confirmed`, BIND them onto the code graph so wicked-estate's **own**
domain resolution is term-aware — this is "put the terms in wicked-estate":

```bash
python3 .anti-legacy/run.py vocabulary project --db .anti-legacy/graphs/<app>.db
# options: --config <path> --vocab <path>
```

For each `confirmed` term it re-derives the grounding nodes (same miners as bootstrap)
and writes a native k/v annotation onto each UNAMBIGUOUS node via the engine's
precision-guarded `annotate`: `domain_entity` / `domain_action` / `domain_abbrev` =
`<canonical>` (a `trusted_verified` term carries confidence 1.0; an ambiguous name is
SKIPPED, never smeared). Only `confirmed` terms are bound — proposed terms are not yet
authoritative. With the tags written, the engine resolves domains through the graph:

- **categorization / capability communities** → `run.py wicked_estate cluster ...`
- **which nodes carry a domain term** → `run.py wicked_estate read-kv <name>` /
  `by-requirement`

### Two axes — and which is the durable record

These are two different "single sources", do not conflate them:

- **Term MEANING** (canonical / definition / aliases / lifecycle) → the committed
  `.anti-legacy/vocabulary.json` is the single durable source of record (force-included
  in git). This is what a human reviews and signs.
- **Term → node RESOLUTION** (which nodes carry a domain, what cluster a node is in) →
  the engine's `domain_*` annotations are the single query source. They are **NOT**
  truth — they are a **disposable, re-derivable PROJECTION** of the confirmed glossary
  onto the current graph (the exact term-level analog of the native `requirement` field
  vs the committed `annotations.jsonl` for rules).

### Reprojection is MANDATORY — the cache is wiped on every rebuild

`.anti-legacy/graphs/*.db` is gitignored and **fresh-deleted on every survey**, and
`annotate_kv` never mirrors to a sidecar — so the `domain_*` tags are **destroyed on the
next re-index** with no durable trace. Re-run `vocabulary project`:

- **after every fresh survey** (the graph was rebuilt → all tags gone), and
- **after any term-confirming extraction step** (newly confirmed terms must bind), and
- **after any change to the miners / `config.coverage.*` / canonicalization** — these
  change which nodes a confirmed term binds to, so re-derive AND treat it as a re-review
  trigger (the glossary text is unchanged, so a git diff will not catch the shift).

Never carry bindings across a rebuild from a stored copy — SymbolIds re-intern on each
index, so a persisted `SymbolId → term` file goes stale instantly. Always re-derive
against the **current** graph's SymbolIds (that is exactly what `project` does). There is
therefore **no committed binding index** — and there should not be one.

### Determinism-drift seam — `vocabulary-bindings.json` (per-run, gitignored)

`project` also writes `.anti-legacy/vocabulary-bindings.json` — a **per-run, gitignored**
record of which node **names** each confirmed term bound to, plus a `content_hash`. It is
NOT a system of record (it's regenerated every run); it exists solely to make
**determinism drift** detectable. It keys on node *names* (stable across reindex), so the
`content_hash` changes **iff the node SET a confirmed term binds to actually changes** —
not merely because the graph was rebuilt. If a re-run finds the prior artifact with the
**same `glossary_hash` but a different `content_hash`**, `project` prints a `DRIFT:` line:
the confirmed terms are unchanged yet a miner / `config.coverage.*` / canonicalization
change re-bound them — a shift a glossary git-diff would miss. Treat a `DRIFT:` line as a
**re-review trigger**.

### Read the projection summary — silent 0-binds are gaps

Because terms have no committed per-binding overlay (rules do, via `annotations.jsonl`),
a bind that silently fails leaves no trace unless you read the summary. `project` reports
`{projected, terms, skipped, confirmed_available, unbound, all_skipped}` and prints a
`GAP:` line for each:

- `unbound` — a confirmed term that mined **no grounding on this graph** (not present
  here): a real coverage gap to investigate.
- `all_skipped` — a confirmed term that **did** ground but every node was ambiguous and
  refused (e.g. carddemo `MAIN-PARA`×21): grounded yet 0 bound — a name-collision gap.

`confirmed_available == 0` is a clean no-op (no confirmed terms yet), not a failure.

---

## The record schema

`.anti-legacy/vocabulary.json` is `{ "terms": [ <record> ], "meta": {...} }`. One record:

```json
{
  "canonical": "ACCT",
  "term_type": "entity",
  "definition": "",
  "aliases": ["ACCOUNT", "ACCT-RECORD"],
  "pseudonyms_slang": ["the master"],
  "status": "proposed",
  "verification": "unverified",
  "freq": 180,
  "mined_from": "field-token",
  "sources": []
}
```

`freq` (true recurrence count) and `mined_from` are always present; `sources` is empty
by default (where-used belongs to the engine) and only populated when
`config.vocabulary.max_sources_per_term` opts in or a human attaches a doc/SME citation.
Run-level metadata (`db`/`ts`/`min_freq`) lives once in the doc `meta.bootstrap_run`, not
per record.

- `canonical` — the interned key, unique in `terms[]`.
- `term_type` — `entity` | `action` | `abbreviation` | `domain_concept`.
- `aliases` — other SPELLINGS that mean the SAME canonical (mined variants + your adds).
- `pseudonyms_slang` — informal / tribal / business-side names; **human-attached only,
  never bootstrap-coined.**
- `sources[]` — the EVIDENCE, ≥1 required. `kind`: `graph_node` (CODE LOGIC ground
  truth) | `doc` (a README/comment CLAIM — unproven) | `human` (SME confirmation).

`schemas/vocabulary.schema.json` validates the shape (`additionalProperties:false` on
the record; status/verification enums; `sources` minItems 1).

---

## Propose a new term (in-flight, during extraction)

When you hit a token that is NOT in `vocabulary.json` while forming a rule, do NOT coin
a meaning inline. Add a record:

1. `canonical` = the token; `term_type` = entity/action/abbreviation as appropriate.
2. `status: proposed`, `verification: unverified`, `definition: ""` (or a draft you
   mark unverified).
3. `sources` ≥ 1 — at minimum the graph node you saw it on
   (`{"kind":"graph_node","ref":"<SymbolId>", ...}`). A README mention is a `doc`
   source, noted as a CLAIM.

A proposed term names the thing; it does not yet assert what it means.

## Promote a term (earn status, earn trust)

- **proposed → confirmed** (the term is real): you have seen it used as a genuine domain
  word across the estate, or an SME confirms it. Set `status: confirmed`.
- **definition → trusted_verified** (the meaning is proven): **read the CODE LOGIC** —
  the actual paragraph body, the db2_table's read/write usage, the COMP-3 field's
  computation. When the body proves the meaning, write the `definition`, set
  `verification: trusted_verified`, and add a `graph_node` (or `human`) source for the
  evidence. A doc-only corroboration is **at most** `untrusted_verified` — never let a
  README alone push a definition to trusted (guardrail b).

Add `aliases` whenever you find another spelling for the same canonical; add
`pseudonyms_slang` when a human gives you a tribal name.

---

## Idempotency / merge contract

Re-running the bootstrap is safe. It merges by `canonical` and **never downgrades a
human-touched record**: any record that is `confirmed`, has `verification` above
`unverified`, or carries a non-blank `definition` / `aliases` / `pseudonyms_slang`
keeps every authored field — only its mined `sources` and `provenance.bootstrap_run`
are refreshed. Pure-bootstrap (untouched) records are replaced by fresh mining. So you
can re-mine after re-indexing the graph without losing curation.

---

## Done looks like

- `.anti-legacy/vocabulary.json` exists and validates against the schema.
- Every term has ≥1 `graph_node` source; bootstrap terms are `proposed` + `unverified`.
- Terms you cite as FACT in a rule statement are `confirmed` + `trusted_verified`, with
  a `graph_node`/`human` source proving the definition.
- No invented definitions: an unproven meaning is `unverified`, not asserted.

A vocabulary where rule-bearing terms are still `unverified` is not done — read the code
and promote them, or flag the rule's meaning as unproven.
