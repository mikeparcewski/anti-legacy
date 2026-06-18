---
name: "anti-legacy:negative-extraction"
description: >
  Second extraction pass that captures the NEGATIVE behavior the first pass (ring[0]) misses:
  error paths, validations, and negative requirements. For each already-resolved behavior node it
  crawls one+ ring DEEPER into the node's error-handling source (exception handlers, rollback/ABEND,
  validation guards, SQL error clauses) and re-annotates the overlay with source-grounded
  error_paths[] + validations[]; negatives it can only DERIVE (from boundaries/validations) are
  written flagged (lower confidence). Runs after anti-legacy:extraction, before
  anti-legacy:graph-translator. Use when: "no error paths", "no negative requirements",
  "extract error handling", "what should the system reject", "deepen the extraction".
---

# anti-legacy:negative-extraction

The positive extraction pass (`anti-legacy:extraction`) resolves each behavior node's primary
rule at the shallowest ring that yields confidence — which is usually **ring[0]**, the node's own
body. That captures *what the program does* but routinely misses *what it rejects, validates, and
fails on*: the exception handlers, `INVALID KEY` / `ON ERROR` / `SQLCODE` branches, `IF`-guards,
rollback/ABEND paths, and the negative requirements implied by them. This skill is the pass that
fills `error_paths[]` and `validations[]`.

Why a separate pass (not a deeper first pass): the two have different **provenance**. An error
path read from a `catch`/`ON ERROR` block is `code-body`-grounded (trusted). A *negative
requirement* ("the system must reject a non-positive APR") is partly **derived** from a validation
+ boundary reasoning — it must be flagged as derived, never asserted as if read from source. Keeping
this pass distinct keeps that read-vs-derived line clean and independently reviewable.

## Cross-Platform Notes

Every command runs through the dispatcher (`python3 .anti-legacy/run.py <stem>`), pure Python —
identical on macOS, Linux, WSL, Windows.

## When it runs / prerequisites

- **After** `anti-legacy:extraction` — the overlay (`.anti-legacy/annotations.jsonl`) already holds
  `status="resolved"` / `"risk"` nodes. **Before** `anti-legacy:graph-translator` — so the projected
  error_paths/validations land in `requirements_graph.json`.
- Like extraction, it owns **no** manifest phase enum value (it operates inside the `graph-translate`
  phase slot); it does **not** `manifest advance`.
- Requires the per-app wicked-estate DB under `.anti-legacy/graphs/<app>.db` (the engine reads the
  source slices) and the resolved engine (config `wicked_estate_path` → `WICKED_ESTATE_PATH` → PATH).

## The data flow (read this before crawling)

The overlay is lossless — `wicked_estate annotate --rule-object` preserves arbitrary keys. So this
pass **re-annotates the SAME SymbolId** with the node's existing rule-object PLUS two new keys,
`validations` and `error_paths`. Downstream, `antilegacy_core.domain_graph` projects those overlay
keys into the requirement's `validations[]` / `error_paths[]` (schema-legal VAL-/ERR- items with
provenance). Coverage is unaffected — adding these keys does not change a node's resolved/risk
classification.

## Step 1: Pick the worklist

Crawl the behavior nodes the positive pass already RESOLVED (a risk-flagged node has no settled rule
to attach error paths to — leave it on the HITL queue). Read them from the overlay:

```bash
python3 .anti-legacy/run.py wicked_estate --db .anti-legacy/graphs/<app>.db by-requirement --resolved
```

(or read `.anti-legacy/annotations.jsonl` and select rows with `status == "resolved"`.)

## Step 2: Crawl ring[1..N] DEEPER, for error behavior

For each resolved node, expand past ring[0] specifically toward error handling — do NOT re-derive
the primary rule. Per node:

```bash
python3 .anti-legacy/run.py wicked_estate --db <db> source <node_name>        # re-read the body for guards/handlers
python3 .anti-legacy/run.py wicked_estate --db <db> query <node_name>         # ring+1 callees (error-handling deps, 1 DOWN)
python3 .anti-legacy/run.py wicked_estate --db <db> source <error_helper>     # read the error/validation callee
```

Hunt for **error-bearing edges/nodes**: exception/`catch` blocks, COBOL `INVALID KEY` / `AT END` /
`ON SIZE ERROR` / `ON EXCEPTION`, file-status checks, `EVALUATE`/`IF` guards that set an error code
or branch to an abort, `EXEC SQL` error clauses (`SQLCODE` checks, `WHENEVER ... GO TO`),
rollback/ABEND/`STOP RUN` on failure, and field validations (range/format/required checks) before a
write. A node/edge is **error-relevant** when it matches one of these; ordinary happy-path callees
are not, and following them is the wrong phase (that was extraction's ring[0]).

### Stop condition (deterministic — two runs MUST crawl identically)

The crawl is a bounded breadth-first expansion along error-bearing edges only. Run it the SAME way
every time:

1. **ring[0]** = the resolved node's own body (already read by extraction). Seed the frontier with
   its error-bearing edges only.
2. **ring[k] → ring[k+1]**: from every node added at ring[k], follow ONLY error-bearing edges
   (the hunt list above) via `query` (1-DOWN dependencies — the error-handling callees a node
   invokes; `query` lists callees, `blast-radius` lists callers — see `anti-legacy:extraction`),
   read each new callee's `source`, and collect the
   **NEW error-relevant nodes** (not seen at any shallower ring — dedupe by SymbolId).
3. **STOP when EITHER terminal is hit — whichever comes first:**
   - **(a) Fixpoint** — ring[k+1] discovers **zero new error-bearing nodes** (the error surface is
     closed: every error edge reachable from the node has been read). This is the normal terminal.
   - **(b) Max ring depth** — the crawl reaches `crawl.max_negative_rings` rings past ring[0]
     (**default 3**, i.e. ring[1], ring[2], ring[3]). This bounds pathological fan-out so the pass
     always terminates. Record that the node hit the depth cap (it may have an unexplored error
     surface — a §6 finding, not a silent pass), do NOT crawl deeper.

The depth cap is **configurable**: set `crawl.max_negative_rings` in `config.json` (falls back to
the shared `crawl.max_rings` if unset, then to the default of 3). The char budget
`crawl.context_budget_chars` is a secondary guard, not the primary terminal — the stop condition is
defined by the fixpoint (a) and the ring-depth cap (b) above so the depth is reproducible
regardless of how large any one source slice is. Following a non-error (happy-path) edge does NOT
advance the ring or count toward the cap — only error-bearing edges expand the crawl.

## Step 3: Build the items (source-grounded vs derived)

For each construct found, build an item. **Every item carries `source_kinds`** (the grounding kind
you actually read — `code-body` for a guard/handler you read; `comment`/`doc` only if that is all
you had → `untrusted_verified` trust tier):

- **error_paths[]** — `{statement, code?, confidence, source_kinds}`. The condition, what happens,
  and (if present) the error code returned. `code-body`-grounded ⇒ high confidence.
- **validations[]** — `{statement, field?, error_ref?, confidence, source_kinds}`. The input/output
  constraint; `field` names the data item; `error_ref` points to the `ERR-id` of the error_path it
  triggers (cross-link — see §2 traceability). 
- **Negative requirements that are DERIVED** (no explicit source guard, only implied by a boundary
  or a positive rule's inverse): still write them, but with **lower confidence** and `source_kinds`
  reflecting the weaker grounding (e.g. `["data-def"]` or omitted). Never label a derived negative
  as `code-body`.

Assign ids: `ERR-001…`, `VAL-001…` per requirement; a `validation.error_ref` MUST match an
`error_path.id` in the same node. (The projection preserves the ids you author, so cross-links
survive.)

## Step 4: Re-annotate the overlay (idempotent, SymbolId-keyed)

Resolve the SymbolId first (names are not unique), then re-annotate with the existing rule-object
plus the new keys. **Do not drop the node's existing `status`/`confidence`/`statement`/`source_kinds`.**

```bash
python3 .anti-legacy/run.py wicked_estate --db <db> resolve-symbol-id <node_name> [--file <rel>] [--kind <kind>]

python3 .anti-legacy/run.py wicked_estate --db <db> annotate '<full_symbol_id>' \
  --rule-object '{"status":"resolved","confidence":<existing>,"statement":"<existing>","source_kinds":[...existing...],
                  "validations":[{"id":"VAL-001","statement":"APR must be > 0","field":"apr","error_ref":"ERR-001","confidence":0.9,"source_kinds":["code-body"]}],
                  "error_paths":[{"id":"ERR-001","statement":"non-positive APR is rejected before posting","code":"ERR-APR","confidence":0.9,"source_kinds":["code-body"]}]}'
```

If `resolve-symbol-id` returns empty → do NOT write (a name-keyed write silently no-ops). If it
returns multiple ambiguous matches → skip and note it (don't guess which node owns the error path).

## Step 5: Done-gate, then report

Assert the overlay actually gained error/validation behavior (so graph-translate has something to
project), and report honestly per AGENTS.md §6:

```bash
python3 -c "import json,sys; \
rows=[json.loads(l) for l in open('.anti-legacy/annotations.jsonl') if l.strip()]; \
n=sum(len(r.get('error_paths') or [])+len(r.get('validations') or []) for r in rows); \
sys.stderr.write('' if n>0 else 'negative-extraction produced 0 error_paths/validations — crawl deeper or report no error surface exists\n'); \
sys.exit(0 if n>0 else 1)"
```

Report to the user:
- error_paths / validations discovered, per requirement (and which nodes had **no** error surface — that is a finding, not a pass).
- Which negatives are **source-grounded** vs **derived** (the derived ones are the human-review queue).
- Next: `anti-legacy:graph-translator` projects these into `requirements_graph.json`; `precheck`'s
  `extraction-depth` warn clears for the nodes now carrying validations/error_paths.

## Output

- `.anti-legacy/annotations.jsonl` — overlay records enriched with `error_paths[]` / `validations[]`
  (idempotent re-annotate; same SymbolIds, same coverage).
- A status report (counts + the source-grounded-vs-derived split).

## Don'ts (AGENTS.md)

- DON'T invent an error path with `code-body` grounding you didn't read — derived negatives are
  flagged, lower-confidence, never dressed as source-read.
- DON'T re-annotate by name — resolve the SymbolId first (name writes silently no-op).
- DON'T change a node's resolved/risk `status` or its primary rule — only ADD error_paths/validations.
- DON'T skip `source_kinds` on any item (it is the trust-tier discriminator).
