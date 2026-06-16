---
name: "anti-legacy:extraction"
description: >
  Crawl the wicked-estate code graph with adaptive ring expansion and annotate
  each behavior-bearing node with its business rule. Per node, gather context one
  ring at a time (node + 1 dependency down / 1 dependent up via blast-radius) until
  the rule can be stated with confidence, then RESOLVE it (rule + confidence +
  provenance) or RISK-flag it for human research. Annotations are written into
  wicked-estate's native `requirement` fields and mirrored to the anti-legacy
  `.anti-legacy/annotations.jsonl` IP overlay. Emits a resolved-or-flagged coverage
  report. Replaces the old graph-translator enrich flow.
  Use when: "extract the business rules", "annotate the graph", "crawl and resolve",
  "run the extraction loop", "build the coverage report", "resolve or risk-flag the nodes".
---

# anti-legacy:extraction

Walks the `wicked-estate` code graph and annotates it. The graph is the *structure*
(programs / paragraphs / methods / JCL steps / CICS programs / DB2 tables, with
cross-language edges); this skill writes the *business rules* on top of it. Every
behavior-bearing node ends in one of exactly two terminal states — **RESOLVED**
(rule stated at/above the confidence threshold, with provenance) or **RISK**
(placed on the human research queue) — so coverage is provably 1.0.

This skill drives the `wicked_estate` helper for all graph reads/writes (never raw
SQLite, and never the deleted standalone JSON graph intermediate that the §H
deletion removed) and `scripts/coverage.py` for the worklist + the coverage metric.
The per-node *rule extraction* is the LLM step you perform; everything structural
comes from the helper.

The control flow — **cluster once → rank-ordered worklist → per node: context fan-out
→ frame with cluster → extract rule → cluster-as-confidence → RESOLVE or RISK** — is
wired in `scripts/extract.py` (the §I3 cluster-aware extraction loop), driven via
`run.py extract`. The loop owns the plumbing (worklist, resumability, the cluster
signal, the atomic annotate write); YOU supply the per-node rule extraction by
injecting an `extract_rule(node, framed_context)` callable into `extract.run(...)`.
The loop never calls a model itself, so the structural spine is deterministic and the
meaning is the part you author. The named helper functions it stands on:

- `wicked_estate.cluster(db, weight=...)` — partitions the graph into capability
  communities (label propagation; `calls` / `confidence` / `data-affinity` weights).
  Run ONCE per session; every node gets a community label that seeds the §I5 domain
  graph and powers the confidence signal below.
- `wicked_estate.context(db, node, budget=, max_hops=, file=, kind=)` — the bounded,
  rank-ordered, minimal-sufficient neighborhood around a node, honoring
  `crawl.context_budget_chars` / `crawl.max_rings`. This is the fan-out you read.
- `wicked_estate.annotate(db, symbol_id, requirement=, description=, validated=,
  rule_object=, overlay_path=)` — the single atomic write (native field + IP sidecar).
- `scripts/coverage.py` — the resolved-or-flagged terminal + worklist denominator.

### Cluster-as-confidence (the fan-out-shape prior)

The SHAPE of a node's `context()` fan-out is evidence about how well-bounded its rule
is. The loop computes `cluster_cohesion` = (context neighbors in the node's OWN
cluster) / (behavior neighbors with a known cluster), excluding the seed and
structural (File/field/import) noise. A node whose context stays inside its cluster is
a clean capability — its raw extractor confidence stands. A node whose context
**sprawls** across many clusters (a god-program / cross-cutting seam) is penalized:
`apply_cluster_signal` multiplies the raw confidence by `floor + (1-floor)*cohesion`,
so a sprawling node is dragged BELOW `resolve_threshold` and **RISK-flagged for a
human** rather than asserted. In-cluster context ⇒ higher confidence; cross-cluster
sprawl ⇒ RISK. The same confident extractor output resolves a cohesive node and flags
a sprawling one — the cluster signal alone splits the verdict.

## Cross-Platform Notes

All script invocations go through `python3 .anti-legacy/run.py <stem> ...` (with a
`python` fallback if `python3` is unavailable). The helper itself is pure-Python
subprocess + `shutil.which` — no shell builtins. Source slices are read via the
helper's `source` command, not `cat`/`sed`.

**Bulk source prefetch (wicked-estate ≥ 0.4.0).** The crawl loop now fetches each
file's full bodies ONCE via `wicked_estate.source_bundle(db, file=…)` instead of a
per-node `source` call, and surfaces each node's COMPLETE own-body as
`framed_context["own_source"]` — so the extractor reasons over the whole method,
not just what fit in the ring budget. Feature-detected: on an older engine
`source_bundle` returns None and `own_source` is None (the ring `slices` remain the
body source — no behavior change). `source_bundle` also takes `cluster=`/`symbols=`
selectors and a `max_total_chars` budget; on truncation a node keeps its metadata
(`source: null` + `byte_range`/`blob_sha`) so you fetch the remainder with
`fetch_source_range(file, byte_range)` — a node is never dropped, only its body.

**Typed annotations — record your reasoning (wicked-estate ≥ 0.5.0).** The business
rule always goes to the `requirement` field (RESOLVED / RISK). Use typed annotations
(`wicked-estate annotate <name|--symbol id> --type <t> --key K --value V`) for the
reasoning *around* it — what you noticed, believed, or couldn't resolve. The
convention:

- **`observation`** (informational) — a structural/factual thing you noticed that is
  not a rule and not uncertain. *"EXEC PGM=CALCRATE references a program with no
  source in the tree"; "unreferenced paragraph"; "COMP-3 field, precision-sensitive."*
- **`assumption`** (advisory) — a belief you **acted on** and that should be verified.
  *"assumed this branch is never run and excluded it"; "assumed these two source
  rules are the same capability."* Use when you proceeded on an inference (it lowers
  trust).
- **`question`** (advisory) — an unknown you **couldn't resolve** and that needs a
  human or another source. *"is this COMP-3 field a monetary amount? affects parity
  rules"; "the two merged sources disagree on the retry rule — which wins?"* Use when
  you're blocked/unsure (an open item).

Pair every RISK-flagged rule with a `question` or `assumption` saying *why* it is on
the HITL queue. The `advisory:true` flag is engine-computed (gate on it, not the type
string); the gate review reads it via `wicked_estate.advisory_nodes(db)` as the
human work-list. (Cache-class key/value tags like `domain_*` re-project idempotently
with `annotate --replace` on ≥ 0.5.1; advisory annotations stay append-only.)

## Config

```bash
python3 -c "import json; print(json.dumps(json.load(open('.anti-legacy/config.json'))))"
```

Relevant keys (all defaulted by `coverage.py`/the helper; present here so a
non-COBOL repo can tune them):

- `wicked_estate_path` — absolute path to the binary (else PATH / env / wicked-estate fallback).
- `coverage.behavior_kinds` — the denominator predicate (default
  `["module","function","method","class","struct","interface"]` + estate resource nodes).
- `coverage.resolve_threshold` — RESOLVE confidence floor (default `0.75`).
- `crawl.max_rings` — ring budget per node (default `3`).
- `crawl.context_budget_chars` — char budget per node (default `18000`, under wicked-estate's ~25K cap).

## Parameters

- **limit** (optional): max number of behavior-bearing nodes to crawl this session.
  Useful for large graphs — the crawl is resumable, so a capped session is safe.
- **app** (optional): restrict the crawl to a single source app / DB.

## The model (read first)

```
index (survey, §H, already done) → cluster + crawl + annotate (this skill) → coverage

  cluster(db) ONCE ──► every node gets a capability/community label (§I5 seed)
        │
        ▼  rank-ordered worklist of UNSETTLED behavior-bearing nodes (resumable)
  per node:
  ┌───────────────────────────────────────────────────────────────────────────┐
  │ context(db, node)  — bounded fan-out (context_budget_chars / max_rings)     │
  │ FRAME with the node's cluster (capability context, not line-by-line)        │
  │ extract_rule(node, framed)  — the LLM step: {statement, confidence, ...}    │
  │ cluster-as-confidence: adjusted = raw_conf * (floor + (1-floor)*cohesion)   │
  │   ├─ statement present AND adjusted ≥ resolve_threshold? ──► RESOLVE. STOP. │
  │   └─ else (no statement / below threshold / cluster sprawl) ─► RISK.  STOP. │
  └───────────────────────────────────────────────────────────────────────────┘
```

RESOLVE and RISK are the only terminal states — there is no silent "maybe-correct":
a low-confidence or sprawling node MUST flag, never assert. The worklist is finite and
already-settled nodes are skipped, so the loop terminates and is resumable. Every node
therefore ends RESOLVED or RISK → coverage reaches 1.0.

When you crawl by hand instead of driving `extract.run(...)`, the adaptive ring
expansion below (ring 0 → ring N, RESOLVE / EXPAND / RISK after each ring) is the
equivalent manual procedure — same terminal contract, same budget discipline.

## Step 1: Verify prerequisites

```bash
python3 .anti-legacy/run.py manifest status
```

Require that `legacy-graph` is registered and `final` (produced by `anti-legacy:survey`,
which is now `wicked-estate index` + the `legacy-graph.digest.txt` evidence). If it
is missing or not `final`, halt and instruct the user to run `anti-legacy:survey`.

Confirm the engine resolves and the per-app DBs exist:

```bash
python3 .anti-legacy/run.py wicked_estate --db .anti-legacy/graphs/<app>.db stats
```

**Helper CLI shape (used in every step below):** `--db` is a GLOBAL option that
goes **before** the subcommand — `run.py wicked_estate --db <path> <subcommand>
[args]`. Subcommand names are hyphenated (`blast-radius`, `resolve-symbol-id`,
`read-semantics`, `by-requirement`, `stats-digest`, `cross-graph`). The node name /
symbol id / requirement string is a **positional** argument (no `--name` flag).

There is one DB per source app under `.anti-legacy/graphs/`. The default
single-repo DB for consumers is the primary app's DB; multi-repo crawls iterate
the per-app DB list (cross-repo questions use the helper's `cross-graph`). If
`stats` errors, the engine did not resolve or the repo was not indexed — fix
`wicked_estate_path` / re-run survey; do NOT fall back to any other graph source.

## Step 2: Query git-brain for extraction patterns

```bash
python3 .anti-legacy/run.py git_brain search \
  --query "business rule extraction ring expansion crawl COBOL JCL CICS resolve risk patterns gotchas" \
  --limit 5

python3 .anti-legacy/run.py git_brain search \
  --query "anti-pattern line-by-line translation over-annotation copybook leaf nodes" \
  --category patterns \
  --limit 3
```

Surface prior learnings (e.g. "MAIN-PARA names repeat — always key by SymbolId",
"copybook-only modules carry no standalone rule"). Capture intent, not line-by-line
logic: each rule is a language-agnostic statement of *what* the node does.

## Step 3: Build the worklist (rank-ordered, resumable)

`coverage.py` computes the denominator (behavior-bearing nodes) from the helper's
node list and reports per-node state from the overlay. Run it once up front to get
the current coverage picture and the list of **unaccounted** nodes — that list is
the crawl worklist:

```bash
python3 .anti-legacy/run.py coverage --db .anti-legacy/graphs/<app>.db \
  --json .anti-legacy/coverage-report.json
```

`coverage.py` flags: `--db` (single DB; omit to score every per-app DB from
`config.source_apps`), `--json` (report path, default
`.anti-legacy/coverage-report.json`), `--md` (default beside `--json`),
`--config`, `--no-cross-check` (skip the in-graph `requirement`-field cross-check),
`--quiet`. It writes `.anti-legacy/coverage-report.json` and
`.anti-legacy/coverage-report.md` and exits non-zero while `coverage < 1.0` (it
doubles as the terminal gate). Read `unaccounted_nodes[]` from the JSON — those are
the SymbolIds still needing a decision. A node already `resolved` or `risk` in
`.anti-legacy/annotations.jsonl` is NOT in that list (idempotency: re-runs skip
settled nodes, so the crawl is resumable and pairs with `wicked-estate subscribe
--since` — an incremental re-index only re-crawls changed nodes).

Order the worklist **most-important-first** with PageRank, and always include the
estate entry points (in-degree-0 JCL/CICS/MQ targets):

```bash
python3 .anti-legacy/run.py wicked_estate --db .anti-legacy/graphs/<app>.db rank
```

Process unaccounted nodes in descending rank so high-leverage programs / entry
points resolve first (this is the slice §I3 builds now). Apply `limit`/`app` here
if set.

## Step 4: Run the cluster-aware loop (or crawl by hand)

There are two ways to execute the per-node spine; both end every behavior node
RESOLVED or RISK.

### 4a. Drive the loop (`extract.run`, the §I3 wiring)

The loop in `scripts/extract.py` owns the cluster pass, the rank-ordered resumable
worklist, the cluster-as-confidence signal, and the atomic `annotate` write. YOU
inject the per-node rule extractor (the LLM step) — the loop never calls a model
itself. From an orchestration step / a Python-driving skill:

```python
import sys; sys.path.insert(0, "scripts")
import extract

def extract_rule(node, framed):
    # node           = {symbol_id, name, kind, file, rank_score, cluster}
    # framed         = {"context": <context() result>, "cluster": <label>,
    #                   "cluster_members": [...], "cohesion": <float>}
    # READ framed["context"]["slices"] (the bounded source) + framed["cluster"]
    # (the capability frame) and state the BUSINESS rule. Return:
    return {"statement": "<language-agnostic rule>", "confidence": 0.0-1.0,
            "rule_id": "<optional>", "resolved_by": "<optional>"}

summary = extract.run(
    ".anti-legacy/graphs/<app>.db",
    extract_rule=extract_rule,        # the injected LLM step
    cluster_weight="calls",           # or "confidence" / "data-affinity"
    limit=None,                       # cap the session (resumable)
)
# summary -> {processed, resolved, risk_flagged, num_communities, results:[...]}
```

The loop applies the cluster-sprawl prior automatically: a node whose `context()`
fan-out stays in its own cluster keeps its confidence; a cross-cutting node is dragged
below `resolve_threshold` and RISK-flagged. It writes the cluster label onto every
overlay record so §I5 can consume the capability grouping. A `CLI --dry-run` exists for
wiring smoke tests only (a deterministic stub extractor — never real extraction); the
CLI refuses to run without `--dry-run` because it cannot call an LLM.

**Vocabulary (load before naming anything in a rule).** Before your `extract_rule`
writes a `statement`, load `.anti-legacy/vocabulary.json` and name entities/actions
with its **confirmed + trusted_verified** terms (carddemo: `ACCT`, `CARD`, `CUST`,
`TRAN`, `XREF`, `AUTH`, `DALYTRAN`, `TCATBAL` …). For an unknown token, **propose** a
record (`status: proposed`, `verification: unverified`) — never coin a definition
inline. If `vocabulary.json` is missing, run `run.py vocabulary --db
.anti-legacy/graphs/<app>.db` first (it mines the real graph, coins nothing). The
promotion procedure (how a definition reaches `trusted_verified` against CODE LOGIC),
the record schema, and the status-vs-verification distinction live in
`anti-legacy:vocabulary` — load it when you hit an unfamiliar abbreviation or need to
promote a term.

### 4b. Crawl by hand — adaptive ring expansion (per node)

If you are crawling manually rather than injecting an extractor, gather context **one
ring at a time** and evaluate the stop condition after each ring. Keep a running
`context_chars` total and a `ring_depth` counter; track the exact node ids and edge
kinds you pulled — that set IS the `provenance`. (`context()` does this fan-out for you
in 4a; the manual procedure below is the equivalent when reading rings yourself.)

### Ring 0 — the node itself

```bash
# plain-language "what it is" + kind/file/language (description, if already set)
python3 .anti-legacy/run.py wicked_estate --db <db> query <node_name>
# the REAL source body via source (engine now returns the real multi-line body)
python3 .anti-legacy/run.py wicked_estate --db <db> source <node_name>
```

**Always read the REAL body before writing a rule.** `source` returns the node's
real multi-line body — for COBOL paragraphs too (the engine returns the actual
OPEN/PERFORM/MOVE/… statements, not the label line). Never confabulate a rule from
the paragraph NAME or its label instead of its real logic: reading a name and
inventing a rule is a silent maybe-correct failure — refuse it, read the body.

Ring 0 context = the node's own real source body (`source`) + its
`description` + its kind / file / language. If the rule is already obvious from
the node alone (e.g. a small self-contained paragraph), you may RESOLVE at ring 0.

### Ring N → N+1 — expand by ONE hop, both directions

Expand the neighborhood by exactly one hop, in both directions, then pull the
**new** nodes' source slices and the edge kinds connecting them:

- **1 DOWN — dependencies** (what this node calls/uses): `wicked_estate query`
  and follow the outgoing `calls` / `uses` / `references` edges where `source == node`.
- **1 UP — dependents** (what calls/uses this node): `wicked_estate blast-radius`,
  which follows ALL edge kinds including estate `uses` / `accesses` / `protects`.

```bash
python3 .anti-legacy/run.py wicked_estate --db <db> blast-radius <node_name>
# then, for each NEW node introduced this ring (source — see Ring 0):
python3 .anti-legacy/run.py wicked_estate --db <db> source <new_node_name>
```

Pull the source slices of the NEW nodes only (ring 0 + prior rings are already in
hand) and note the edge kinds, so you can read "this paragraph PERFORMs that one,
which does the EXEC SQL". Cross-language edges resolve automatically (JCL EXEC PGM
→ COBOL, CALL → COBOL), so a ring can legitimately cross domains.

**Bounded traversal (do not violate):** blast-radius is depth-capped at 12 by the
binary. Expand one ring by **widening the set of seed names** by one hop, NOT by
re-querying deeper — each ring is a deliberate, bounded step, never an unbounded
whole-graph walk.

### Stop condition (evaluate after EACH ring)

1. **RESOLVE** — you can state the node's business rule with `confidence ≥
   resolve_threshold` (default 0.75), grounded in the gathered context → write the
   annotation (Step 5, `validated=true`). STOP.

2. **EXPAND** — not enough context to decide, AND `ring_depth < crawl.max_rings`,
   AND `context_chars < crawl.context_budget_chars` → blow the radius out one more
   ring (back to "Ring N → N+1"). CONTINUE. (Confidence below threshold but the
   case is non-ambiguous and within budget → keep expanding; only budget or genuine
   ambiguity forces a flag.)

3. **RISK-FLAG** — `ring_depth == crawl.max_rings`, OR the context budget is
   exhausted, OR genuine ambiguity (conflicting rules, missing source, an
   unresolved cross-ref the graph can't bind, or two source repos disagree) → write
   the risk flag (Step 5, `validated=false`). The node lands on the HITL queue. STOP.

A node hit at the ring/confidence budget without reaching threshold MUST be
RISK-flagged — never leave a behavior-bearing node bare.

## Search tips — how to hydrate a node (tips, not steps)

These are *tips*, not a mandated procedure. The loop (4a) and the manual rings (4b)
already gather context; lean on these when a node is thin and `context()`/one ring is
not enough to state the rule. They are language-agnostic — the engine's edges and
kinds carry the same meaning across COBOL/JCL/Java.

- **Find behavior by config-driven kind, never by name.** The denominator and the
  worklist come from `coverage.behavior_kinds` / `estate_behavior_kinds` — config, not
  a `name.startswith(...)` guess. When you go looking for *more* behavior to read, ask
  the engine for the kind (`module`/`function`/`method` and the estate resources), not
  a name pattern. A name prefix is not a kind.
- **Anchor on real invocation edges, not proximity.** `blast-radius <node>` gives the
  real **callers** (who invokes this — the dependents that constrain its contract);
  `context <node>` gives the ordered, budgeted **neighborhood** (what it calls/uses,
  rank-ordered). Read the edges the engine actually resolved (`calls` / `uses` /
  `accesses` / cross-language `EXEC PGM`→COBOL, `CALL`→COBOL) — that is ground-truth
  invocation, not textual nearness.
- **Rules cluster where data is validated, transformed, or branched.** The business
  meaning lives where a node *checks* an input, *moves/computes* a value, or *branches*
  on a condition — read those source slices first. In COBOL that is the
  `EVALUATE/WHEN`, the `MOVE`/`COMPUTE` runs, the `IF` guards, and the down-ring `EXEC
  SQL` (e.g. the real `2000-POST-TRANSACTION`: 12 `MOVE`s then `PERFORM
  2700/2800/2900`). Skip the structural plumbing (paragraph dispatch, `GOBACK`,
  copybook `COPY`); spend the budget on the validate/transform/branch lines.
- **Cluster is a HINT, confirmed by reading.** The capability community label and the
  `cluster_cohesion` signal *point* at how well-bounded a node is — they do not state
  its rule. A cohesive cluster says "this is probably one capability"; you still RESOLVE
  only after reading the real body. A sprawling node is a flag to read more (or RISK),
  not a verdict on its own.
- **Code is truth; docs and comments are claims to confirm.** A copybook remark, a
  README line, or a leading comment is an *unproven CLAIM* — read the paragraph body /
  the `db2_table` usage and confirm it against the code before you assert it in a rule
  statement. A definition taken only from a comment is `unverified`; only a definition
  proven against CODE LOGIC is `trusted_verified` (the level you may treat as fact).
- **When the node is language X, load `reference/X.md`.** Each node carries
  `node.language` from the graph; pull the matching language reference for its idioms
  (COBOL `EVALUATE`→one rule per branch, `COMP-3`→parity rule; etc.) — load it lazily,
  by the node under crawl, not up front.

### References (load on demand)

Lean entry doc; depth is pulled only when the trigger fires. Load via the normal
skill/file read — none are read up front (keeps agv context light).

| Reference | Load when… |
|---|---|
| `anti-legacy:vocabulary` | you hit an unfamiliar abbreviation, or need to add an alias / promote a term to `trusted_verified`. |
| `reference/writing-standard.md` | you are FORMING a rule statement — canonical-named, language-agnostic, parity-on-numeric, maps to RULE-/VAL-/ERR- schema items. |
| `reference/decomposition.md` | you are deciding node↔requirement cardinality (split one god-program into N requirements; merge a copybook + its readers into one). |
| `reference/search-tips.md` | `context()` is thin and one ring is insufficient — the engine's deeper reach (semantic free-text search, `by-requirement` reverse lookup, `cross-graph`, widening `blast-radius`), bounded, never whole-graph. |
| `reference/cobol.md` · `reference/java.md` · `reference/jcl.md` · `reference/cics.md` | the node's `language` / kind is COBOL / Java / JCL / a `cics_program` — its idioms and this estate's mined examples. |

The **verification spectrum** (`unverified` → `untrusted_verified` →
`trusted_verified`) governs which terms you may assert as fact: only
`trusted_verified` (proven against CODE LOGIC or ratified by a human SME) is fact in a
rule statement. The spectrum and the promotion procedure are owned by
`anti-legacy:vocabulary`.

## Step 5: Write the annotation (helper does both writes atomically)

The helper's `annotate` is the single write path. It (a) shells `wicked-estate
semantics <symbol_id> --requirement ... --description ... --validated ...` into the
native graph fields, and (b) appends the lossless rule object to
`.anti-legacy/annotations.jsonl`. Both happen in one call.

**The SymbolId gotcha (this is what makes or breaks the write).** The `semantics`
write key is the FULL interned SymbolId string, NOT the simple name. Passing a
simple name is a SILENT NO-OP (0 rows update, raw DB stays NULL, `by-requirement`
returns nothing). Names are not unique either (carddemo: `MAIN-PARA`×21,
`PROCESS-ENTER-KEY`×14). So you MUST resolve the name → SymbolId first, and
disambiguate by `file`/`kind` when a name maps to more than one row:

```bash
python3 .anti-legacy/run.py wicked_estate --db <db> resolve-symbol-id <node_name> \
  [--file <rel_path>] [--kind <kind>]
```

This returns one or more SymbolIds. If it returns **empty**, do NOT attempt the
write — there is nothing to key on, and a name-keyed write would silently vanish.
(The helper's `annotate` itself raises on an empty/unresolved id, guarding the
trap — a behavior pinned by the `scripts/wicked_estate.py` helper's own test, not
this skill's; the skill just relies on the guard.) If it returns **more than one**
and you can't narrow to the intended node by file/kind, treat that as ambiguity →
RISK-flag the node rather than guessing.

### Annotation value contract (anti-legacy IP packed into a TEXT field)

The native `requirement` field is free-text TEXT, exact-string-matched by
`by-requirement`, so we pack a compact tagged string the helper unpacks:

```
requirement = "<rule_id>|<confidence>|<provenance>|<statement>"
```

- `rule_id` — stable id (the `by-requirement` reverse-lookup keys on this prefix).
- `confidence` — 0.0–1.0 (0.0 for a RISK flag).
- `provenance` — compact ref to the grounding ring nodes/edges.
- `statement` — the language-agnostic rule (RESOLVE) or the literal token `RISK`
  (RISK flag).

`requirement_validated` = `1` when RESOLVED at/above threshold, `0` when
RISK-flagged. `description` = the node's plain-language "what it is".

The native `requirement` / `description` / `requirement_validated` fields are the
in-graph evidence projection (they keep `drift` / `by-requirement` working and the
graph self-describing). The lossless IP-rich object — `resolved_by`, `risk_reason`,
`ring_depth`, the structured `provenance` set — is mirrored to
`.anti-legacy/annotations.jsonl`, keyed by `{db_id, symbol_id}`. The overlay is
coverage's source of truth.

**Two write paths, same helper:**

- **CLI `annotate`** (`run.py wicked_estate annotate`) takes a POSITIONAL
  `symbol_id`, `--requirement` / `--description` / `--validated true|false`, and a
  `--rule-object '<JSON>'`. **Pass `--rule-object`** — it merges the structured
  fields (`status`, `confidence`, `verification`, `statement`, `provenance`,
  `source_kinds`, `parity`, `legacy_components`, `risk_reason`) as first-class
  overlay keys, which `coverage.py` and `domain_graph.py` (§I5) read directly. The
  packed `--requirement` string (`<rule_id>|<conf>|<prov>|<statement>`) is still
  written and coverage falls back to parsing it + `requirement_validated` if
  `--rule-object` is omitted — but omitting it loses the `verification` spectrum and
  the `source_kinds` trust grounding, so include it.
- **Importable `annotate(db, symbol_id, requirement=, description=, validated=,
  rule_object=<dict>)`** — when a skill drives the helper as a Python library it can
  pass a `rule_object` dict (`{rule_id, statement, confidence, provenance,
  source_kinds, resolved_by | risk_reason, ring_depth, status}`) that is merged
  losslessly into the same overlay record. Use this when you want the structured
  fields as first-class overlay keys rather than only inside the packed
  `requirement` string.

### RECORD `source_kinds` — the GOTCHA-3 trust-tier grounding (REQUIRED)

Every rule's annotation MUST carry a `source_kinds` array naming the grounding
kind(s) you ACTUALLY READ to state the rule — the evidence behind every
load-bearing fact in the `statement`, not where you wish it came from. The four
legal kinds (the enriched schema's enum, enforced):

| `source_kind` | You read… |
|---|---|
| `code-body` | executable logic read directly — the paragraph body / method: the `EVALUATE/WHEN`, `MOVE`/`COMPUTE`, `IF` guards, `EXEC SQL`. |
| `data-def` | a copybook PIC / level-88 `VALUE` (a verifiable DATA DEFINITION) — the field's declared type/width or a named condition value. |
| `comment` | inline prose — a leading comment, a copybook remark. A *claim*, not code. |
| `doc` | a README / external document — also a *claim*, not code. |

Record EVERY kind you leaned on (it is a list — a rule grounded in both the body
and a copybook is `["code-body","data-def"]`; a comment you then CONFIRMED against
the body is `["comment","code-body"]`).

**The trust rule (this is why the kind matters).** The trust tier follows the
grounding, not your confidence:

- grounded in `code-body` and/or `data-def` ⇒ **`trusted_verified`** (ground truth
  — set `verification: trusted_verified`).
- grounded ONLY in `comment`/`doc`, NOT confirmed against the code/data ⇒
  **`untrusted_verified`** (a prose claim) — it is **RISK-eligible**: prefer to read
  the body and confirm, and if you cannot, RISK-flag rather than assert
  `trusted_verified`.

A copybook-PIC fact (`data-def`) and a comment fact (`comment`) are NOT the same
evidence — never fold a comment-only claim into `trusted_verified`. `domain_graph.py`
(§I5) copies `source_kinds` straight onto the emitted rule's `provenance.source_kinds`
(validated against the enum, optional) so the downstream trust tier is computable;
omit it and that signal is lost.

In both paths the helper RAISES on an empty/unresolved `symbol_id` (the silent-no-op
guard) and writes the native field + the JSONL overlay atomically.

### RESOLVE write (CLI)

```bash
python3 .anti-legacy/run.py wicked_estate --db <db> annotate '<full_symbol_id>' \
  --requirement '<rule_id>|<confidence>|<provenance>|<statement>' \
  --description '<plain-language what-it-is>' \
  --validated true \
  --rule-object '{"status":"resolved","confidence":<0..1>,"verification":"trusted_verified","statement":"<rule>","provenance":"<rings/edges>","source_kinds":["code-body"],"parity":"<parity note or empty>","legacy_components":["<symbol_id>"]}'
```

`source_kinds` names the grounding you READ (see "RECORD `source_kinds`" above):
`["code-body"]` / `["data-def"]` / both for a `trusted_verified` resolve;
`["comment"]` or `["doc"]` ALONE is a claim — confirm it against the body (then add
`code-body`/`data-def`) or RISK-flag, do not assert `trusted_verified` on prose.

### RISK write (CLI)

```bash
python3 .anti-legacy/run.py wicked_estate --db <db> annotate '<full_symbol_id>' \
  --requirement '<rule_id>|<confidence>|<provenance:rings/edges examined>|<gap>' \
  --description '<plain-language what-it-is>' \
  --validated false \
  --rule-object '{"status":"risk","confidence":<0..1>,"verification":"unverified|untrusted_verified","risk_reason":"<the named gap>","provenance":"<rings/edges examined>","source_kinds":["comment"],"legacy_components":["<symbol_id>"]}'
```

For a RISK flag, encode the `risk_reason` (conflicting rules | missing source |
unbindable cross-ref | repo disagreement | budget exhausted) and the rings/edges
examined inside the `<provenance>` segment of the tagged string, and set the
statement token to `RISK`. Still record `source_kinds` for whatever grounding you
DID read (e.g. `["comment"]` when only a prose claim was available — exactly the
comment-only, `untrusted_verified`, RISK-eligible case the trust rule names; omit it
only when you read nothing). (Drive the importable `annotate(..., rule_object=...)`
if you need `risk_reason` / `ring_depth` / `resolved_by` / `source_kinds` as discrete
overlay keys.)

After each write, verify the round-trip (catches the silent-no-op — positional
`symbol_id` / `req`):

```bash
python3 .anti-legacy/run.py wicked_estate --db <db> read-semantics '<full_symbol_id>'
# reverse lookup by rule_id prefix:
python3 .anti-legacy/run.py wicked_estate --db <db> by-requirement '<rule_id>'
```

Store a learning per resolved cluster (optional but recommended):

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "Rule for <node> (<file>): <statement> [conf <c>, ring <N>, prov <prov>]" \
  --tags "discovery,business-rules,extraction" \
  --category learnings
```

## Step 6: Re-run coverage and check the terminal

After the worklist is processed (or the session `limit` is hit), recompute coverage:

```bash
python3 .anti-legacy/run.py coverage --db .anti-legacy/graphs/<app>.db \
  --json .anti-legacy/coverage-report.json
```

- **Exit 0** ⇒ `coverage == 1.0` (UNACCOUNTED == 0) — the provable terminal of §I2.
  Every behavior-bearing node is RESOLVED or RISK.
- **Exit non-zero** ⇒ `coverage < 1.0`; the printed unaccounted SymbolIds are the
  remaining worklist. Resume the crawl on those (idempotent — settled nodes are
  skipped). Repeat until exit 0.

The report surfaces `resolved/total` (the auto-resolved slice §I3 can build now),
`risk_flagged/total` (the HITL queue depth, gated by §I3), plus `resolved_rate` and
`mean_confidence` (the §I4 threshold-ramp signals — emitted, not acted on in WF1).

**Done-gate (BLOCKING).** Do not register or advance unless `coverage.py` exits 0
(`coverage == 1.0`). A node left UNACCOUNTED is a coverage hole — finish the crawl
or RISK-flag it; never weaken the denominator or the threshold to force the gate.

## Step 7: Register coverage evidence + refresh the digest, then advance

Only on a passing done-gate (coverage 1.0):

```bash
python3 .anti-legacy/run.py manifest register coverage-report \
  --path coverage-report.json \
  --format json \
  --produced-by anti-legacy:extraction \
  --status final \
  --depends-on legacy-graph
```

Re-register the `legacy-graph` digest so it reflects the freshly written
`requirement` fields — this makes drift between code and annotation checksum-
detectable (the §I6 keystone; wired in WF1, gated later). The digest is the helper's
`stats-digest` block (the deterministic node/edge-count block with the volatile
lines — `STALENESS:`, `repo:` git provenance, `db=NN.NMB` — already stripped, so it
is byte-stable across re-indexes of the same source). `stats-digest` prints one DB's
block to stdout; rebuild the canonical `legacy-graph.digest.txt` survey produced by
writing the primary app's block, then appending each additional app's block:

```bash
# primary app — overwrite
python3 .anti-legacy/run.py wicked_estate --db .anti-legacy/graphs/<app1>.db stats-digest \
  > .anti-legacy/legacy-graph.digest.txt
# each additional app — append (multi-repo)
python3 .anti-legacy/run.py wicked_estate --db .anti-legacy/graphs/<app2>.db stats-digest \
  >> .anti-legacy/legacy-graph.digest.txt

python3 .anti-legacy/run.py manifest register legacy-graph \
  --path legacy-graph.digest.txt \
  --format text \
  --produced-by anti-legacy:extraction \
  --status final
```

(Single-repo estates: just the first `>` line. Survey owns the canonical first
write of this file; extraction reproduces it identically so the only delta is the
now-populated `requirement` fields reflected in the counts.)

### Project confirmed domain terms onto the graph (before advancing)

Extraction is where vocabulary terms reach `confirmed`. Bind those confirmed terms onto
the graph as native `domain_*` annotations so the downstream domain graph / clustering
resolves through the engine, not by re-guessing names:

```bash
python3 .anti-legacy/run.py vocabulary project --db .anti-legacy/graphs/<app>.db
```

This is the term-level analog of the rule annotation you just wrote: the committed
`vocabulary.json` is the durable record of term **meaning**; the `domain_*` graph tags
are a **disposable, re-derived projection** for term→node **resolution** (the same record
vs projection split as `annotations.jsonl` vs the native `requirement` field). The tags
are wiped by any future `--fresh` re-survey, so **survey re-runs this too** (Step 3c) —
here it binds the terms this pass confirmed.

**Done-gate note (not blocking the advance, but surface it):** read the projection
summary. `confirmed_available=0` is a clean no-op (nothing confirmed yet). But a `GAP:`
line means a confirmed term failed to bind — distinguish the two: `unbound` = the term
mined **no grounding on this graph** (a real coverage gap to chase), `all_skipped` = it
grounded but **every node was ambiguous** and refused (a name-collision gap, e.g.
`MAIN-PARA`×21). Note any gap in the status report (§6) rather than silently advancing.
(Determinism caveat: `project` re-mines with the same miners as bootstrap — if those
miners or `config.coverage.*` change later, a confirmed term can re-bind to a different
node set with no git diff; the per-run `vocabulary-bindings.json` content-hash makes this
detectable and `project` prints a `DRIFT:` line — treat it as a re-review trigger.)

**Reprojection-enforcement gate (BLOCKING — ISS-03).** Before advancing, confirm the
projection actually landed (a fresh re-survey wipes domain_* tags — a skipped reproject
would silently degrade the domain graph to name-only):

```bash
python3 .anti-legacy/run.py vocabulary check-projection --db .anti-legacy/graphs/<app>.db
```

Exit 1 (BLOCKED) iff confirmed terms ground on the graph but it carries 0 domain_* tags —
re-run `vocabulary project`. Exit 0 when there is nothing to enforce (no confirmed terms,
or none present in this graph). Do NOT advance on a non-zero exit.

Then advance. The extraction skill occupies the `graph-translate` phase slot — it
replaces the old graph-translator enrich flow, and `extraction` is NOT a legal
manifest phase enum value (`manifest advance` rejects it with exit 2). Advance into
`graph-translate`:

```bash
python3 .anti-legacy/run.py manifest advance graph-translate
```

## Output

- `.anti-legacy/annotations.jsonl` — the lossless IP overlay (one object per
  resolved/risk node, keyed `{db_id, symbol_id}`): `{rule_id, statement,
  confidence, provenance, source_kinds, resolved_by | risk_reason, ring_depth,
  status}`. `source_kinds` (the GOTCHA-3 grounding kinds — see Step 5) rides
  through to the §I5 rule's `provenance.source_kinds` and fixes the trust tier.
- wicked-estate native `requirement` / `description` / `requirement_validated`
  fields — the in-graph evidence projection (round-trips via `by-requirement`).
- `.anti-legacy/coverage-report.json` + `.anti-legacy/coverage-report.md` —
  `{total, behavior_bearing, resolved, risk_flagged, unaccounted, coverage,
  resolved_rate, mean_confidence, per_app[], unaccounted_nodes[]}`.
- `.anti-legacy/legacy-graph.digest.txt` — refreshed checksummable evidence.
- Manifest: phase = `graph-translate` (the slot this skill now owns),
  `coverage-report` registered `final`, `legacy-graph` digest re-registered.

**Next step**: HITL + risk research (§I3) closes the RISK queue; the re-think into
the target-state domain graph (§I5) rationalizes the resolved rules.

## Common crawl gotchas

- **SymbolId, not name.** Every write keys on the full interned SymbolId. Resolve
  first; if resolution is empty, do NOT write (silent no-op). If it's ambiguous
  (>1 row, can't narrow), RISK-flag rather than guess.
- **Copybook / data-only modules carry no standalone rule.** They're excluded from
  the denominator by `coverage.behavior_kinds` (a `module` with 0 outgoing
  `calls`/`uses` edges is structural). Don't annotate every COBOL field — that
  inflates the denominator with un-rule-bearing leaves and makes coverage
  un-provable.
- **Budget discipline.** Track `context_chars` and `ring_depth` per node. EXPAND
  must strictly consume budget; when it's gone, RISK-flag. This is the termination
  guarantee — without it the crawl can thrash.
- **Cross-repo disagreement = RISK.** If two source repos' rings yield conflicting
  rules for the same capability, that's genuine ambiguity → RISK-flag (the §I5
  re-think resolves cross-source conflicts, not the per-node crawl).
- **Idempotency.** A node already RESOLVED/RISK in the overlay is skipped. Safe to
  re-run; safe to resume after a `limit`-capped session; pairs with `subscribe
  --since` for incremental re-crawl.
- **COBOL specifics** (intent, not line-by-line): `EVALUATE/WHEN` → one rule per
  branch; `EXEC SQL` in a down-ring → data-access rule; `COMP-3 PIC` precision is
  documented in the statement; `ABEND`/rollback in a ring → an error-path rule.
