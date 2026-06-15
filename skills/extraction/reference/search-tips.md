# reference/search-tips — deeper reach when one ring isn't enough

Load this when `context(node)` came back thin, the node's behavior reaches past
one ring, or you can't yet state a rule with confidence. These are **tips, not a
mandate** — reach for what the node needs, stay bounded, never crawl the whole
graph. All run through the `wicked_estate` helper.

## When the rule needs more than the immediate neighborhood

- **Follow the real edges, not a guess.** `blast-radius <node>` widens to the
  transitive *dependents* (who calls/uses this); chase a specific dependency by
  reading the node's own `source` and following the `PERFORM`/`CALL`/`EXEC`
  targets it names. The graph's edges are the truth — don't infer call structure
  from names.
- **Pull only the NEW nodes' bodies.** Each ring, read `source <new_node>` (now
  the real body) for nodes you haven't seen; you already hold the prior rings.
  Stop when the rule is stateable — budget is `crawl.context_budget_chars`.

## When you don't know where a concept lives

- **Semantic free-text search** (needs an `--embeddings` index): `semantic
  "<plain-language concept>" --db <db>` finds nodes by meaning, not name — useful
  when the legacy name is opaque (`TCATBAL`, `CBTRN02C`) but you can describe the
  behavior ("transaction category balance update").
- **Reverse-lookup by an already-written rule:** `by-requirement "<rule_id>"`
  returns the nodes already carrying that requirement — to see whether a behavior
  is already covered before you write a duplicate (decomposition: merge vs. split).

## When the behavior spans repos (the merge)

- **`correspond --db-a <A> --db-b <B> --kind Function`** surfaces likely
  cross-repo counterparts (e.g. a COBOL paragraph ↔ a Java method). Use the
  `--kind` filter — the unfiltered default is buried by README/markdown nodes.
  Scores are low across languages (COBOL↔Java share no lexical surface); treat a
  match as an **untrusted_verified** candidate to confirm by reading both bodies,
  not a fact.
- **`cross-graph <name> --dbs a,b`** for a single-name federated lookup across
  the per-repo DBs.

## Discipline

- Every fact you assert about a node must trace to **code you read** (guardrail
  b: code is truth; docs/comments are claims to confirm). A semantic hit or a
  `correspond` pair is a *lead*, not a rule.
- Stay bounded — if you've widened twice and still can't state the rule with
  confidence, that's a **RISK** flag with the gap named, not more crawling.
