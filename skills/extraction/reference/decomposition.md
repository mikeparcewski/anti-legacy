# Node ↔ Requirement Decomposition (M:N)

> **Load when:** you are deciding *cardinality* — how many requirements a node becomes, or whether several nodes collapse into one requirement. This is the M:N call (guardrail d: every requirement traces to `legacy_components`; the trace is many-to-many, so split and merge freely).

The relationship between a graph node and a requirement is **many-to-many**, not 1:1. The legacy code's paragraph boundaries reflect *COBOL structuring conventions* (sequence numbers, EXIT paragraphs, PERFORM granularity) — not business outcomes. Your job is to map **business outcomes**, then attach the legacy nodes that realize each outcome to its `legacy_components`. Whatever the cardinality, `legacy_components` is what keeps the trace back to source intact — never write a requirement with an empty one.

```
   nodes (legacy paragraphs/programs)        requirements (business outcomes)
   ───────────────────────────────          ───────────────────────────────
   2000-POST-TRANSACTION ──┬──────────────▶ RULE-POST-001  (post the transaction)   SPLIT
                           └──────────────▶ ERR-TRANFILE-001 (abort on write fail)
   2900-WRITE-TRANSACTION-FILE ──(merged)──▶ ERR-TRANFILE-001
   9910-DISPLAY-IO-STATUS ──────(merged)──▶ ERR-TRANFILE-001                         MERGE
```

A node can feed several requirements; a requirement can draw on several nodes. The two operations below are how you get there.

---

## SPLIT — one node → several requirements

**Trigger:** a single node produces **distinct business outcomes** that a target developer would implement separately and a contract would test separately.

**Signals you should split:**
- The node both **validates AND acts** (a gate that can reject, plus the behavior that runs when it passes).
- The node has a **happy path and an error/abort path** — the failure semantics are their own outcome (`ERR-`).
- An `EVALUATE`/`WHEN` (or Java `switch`/`if-else` ladder) where **each branch is a different business decision** → one requirement per branch.
- The node **fans out** to several independent downstream effects that each stand alone as a rule.

**How:**
1. List the distinct outcomes. Give each a `rule_id` (`RULE-`/`VAL-`/`ERR-` per its kind — see `writing-standard.md`).
2. Put the **same** node in each requirement's `legacy_components` (the M:N link — the node legitimately backs all of them).
3. If a *specific* sub-paragraph realizes one outcome, attach *that* node too, so the trace is as tight as the evidence allows.

**Emit every split rule ATOMICALLY in one pass — there is no downstream cleanup.** The extraction loop (`scripts/extract.py`) lets a node's extractor return **multiple rule objects at once** — a list `[primary, ERR-twin, ...]` or a `{"primary": {...}, "splits": [...]}` envelope — and it materializes **all of them in the same pass** (one overlay row + one native field per rule, distinct `rule_id` each; the primary written last so last-record-wins coverage settles on it). Do **not** emit only the primary and "name the sibling for a later register" — that was the cardinal silent failure (the dropped `ERR-` twin while coverage stayed `< 1.0` with no error). If a returned rule *declares* a sibling rule_id (in `decomposition` / `sibling_rule_ids` / `splits` / `siblings`) that you did **not** also return in the same batch, the loop **raises** rather than proceeding — so a declared split is always materialized here, never deferred.

**Real carddemo example — `2000-POST-TRANSACTION` splits into 2 (or 3):**
Its body copies fields + stamps a timestamp + fans out to `PERFORM 2700 / 2800 / 2900`. That is at minimum:
- `RULE-POST-001` — copy DALYTRAN→TRAN and stamp processing timestamp (the posting behavior).
- `ERR-TRANFILE-001` — abort if the transaction write fails (failure semantics, surfaced via the `2900` fan-out).

If `2700-UPDATE-TCATBAL` and `2800-UPDATE-ACCOUNT-REC` carry meaningfully different balance rules, those become their own `RULE-` requirements too — each pulling the relevant sub-paragraph into *its* `legacy_components`. Do **not** force all of `2000`'s behavior into one mega-requirement; "and also updates the balance and also writes the file and also abends on failure" is four testable outcomes wearing one statement.

**Real carddemo example — a validate-and-route node:**
`1500-VALIDATE-TRAN` performs `1500-A-LOOKUP-XREF`, and only on success performs `1500-B-LOOKUP-ACCT`. Two validation gates, each able to reject for a different reason → `VAL-XREF-001` (card has a cross-reference) and `VAL-ACCT-001` (the referenced account exists). One node, two `VAL-` requirements; both list `1500-VALIDATE-TRAN` (and their respective `1500-A`/`1500-B` sub-paragraph) in `legacy_components`.

---

## MERGE — several nodes → one requirement

**Trigger:** several nodes are **mechanical sub-steps of one business outcome**. They have no independent business meaning — they exist only because COBOL structures I/O and housekeeping into separate paragraphs.

**Signals you should merge:**
- **File/cursor housekeeping** — `OPEN`/`CLOSE`/`READ-NEXT`/status-check paragraphs (`0000-…OPEN`, `9000-READ-FORWARD`, `9910-DISPLAY-IO-STATUS`). These are plumbing, not behavior.
- **EXIT paragraphs** (`…-EXIT`) and pure `GO TO` targets — never their own requirement.
- A **copybook/record layout + the paragraphs that populate it** when together they express one outcome (e.g. one balance-update with its working fields).
- A `PERFORM`-chain that is one logical operation split across `-A`, `-B`, `-C` sub-paragraphs with no branch-level business decision.

**How:**
1. Pick the node that names the **business outcome** as the anchor (the verb-bearing one).
2. Add the mechanical sub-step nodes to that requirement's `legacy_components` — they belong on the trace because they implement it, but they get **no requirement of their own** (guardrail a is still satisfied: they are RESOLVED *as part of* the merged requirement, not left bare — record that in `provenance`).
3. The statement describes the outcome once, in business terms; the plumbing is invisible to the target.

**Real carddemo example — the abort path merges 3 nodes into 1:**
`2900-WRITE-TRANSACTION-FILE` on a bad file status does `DISPLAY 'ERROR…'` → `PERFORM 9910-DISPLAY-IO-STATUS` → `PERFORM 9999-ABEND-PROGRAM`. The display and the abend paragraphs have **no independent business meaning** — they are *how* the system aborts. One requirement `ERR-TRANFILE-001` ("abort on transaction-file write failure"); `legacy_components` lists **all three** (`2900`, `9910`, `9999`). The `9910`/`9999` nodes are resolved-by-merge, not separate requirements.

**Real carddemo example — file open/read plumbing:**
The `OPEN TCATBAL-FILE` / `READ TCATBAL-FILE … INVALID KEY` plumbing inside `2700-UPDATE-TCATBAL` is mechanical. The *business* outcome is "update the transaction-category balance, creating the record if it does not yet exist" — one `RULE-TCATBAL-001`. The READ/INVALID-KEY/status paragraphs merge into its `legacy_components`; only the create-if-missing decision is a business rule worth stating.

---

## Keeping the trace intact (guardrail d)

Whatever the cardinality, **`legacy_components` is the trace** — and it is never empty:

- **SPLIT** → the same node appears in several requirements' `legacy_components`. Expected and correct. The loop writes **all** of a split node's requirement rows in one atomic pass (one per `rule_id`); a declared-but-unemitted sibling raises rather than silently dropping.
- **MERGE** → several nodes appear in one requirement's `legacy_components`. Expected and correct.
- The annotation overlay (`annotations.jsonl`) is **SymbolId-keyed** (`{db_id, symbol_id}`), because names are not unique — carddemo has `MAIN-PARA` ×21. Resolve **name → SymbolId** before every write so the link binds the *exact scoped node*, not a name collision. (Helper: `wicked_estate resolve-symbol-id`.)
- After decomposition, the **coverage check** (`run.py coverage`) must account for **every behavior-bearing SymbolId** — via its own requirement (split) or as a `legacy_components` member of a merged requirement (merge). A node that is neither is *unaccounted* and fails coverage `< 1.0`. There is no third option: every behavior node ends RESOLVED (in/under a requirement) or RISK-flagged (guardrail a).

**Don't-list:**
- Don't leave a mechanical sub-step as its own bare requirement *and* also don't drop it off the trace — merge it into the outcome's `legacy_components`.
- Don't merge two genuine outcomes to reduce node count — if a contract would test them separately, they are separate requirements (split).
- Don't write any requirement — split or merged — with an empty `legacy_components`. That breaks the thread back to source and a swarm agent cannot trace it.
