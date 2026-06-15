# Graph-Enrichment Procedure — The Proven Spec

> **Load when:** you need to know *exactly how a node gets enriched into a rule* — the real command spine, how far past the node body you actually have to crawl, how vocabulary and the reference docs feed the statement, and the RESOLVE-vs-RISK calculus. This is the post-mortem spec, reconstructed from **16 real extraction sessions** over the carddemo `CBTRN02C` daily-transaction posting program (`.anti-legacy/graphs/carddemo.db`). Every claim below is cited to the node that proves it.
>
> This is the operational complement to the three forming docs: `writing-standard.md` (the rule shape), `cobol.md` (idiom→shape), `decomposition.md` (M:N cardinality). It does not replace them — it tells you the *procedure* they are executed inside.

The headline finding, stated first because it governs everything: **enrichment is a RING-0-first procedure.** Of the 16 nodes, **9 resolved from the node body alone (RING-0)** and **7 required a targeted one-hop expansion (RING-1)** — and *not one* of the 16 used `wicked_estate blast-radius` or `context`. Expansion, when it happened, was always a hand-followed `wicked_estate source` of a *named* neighbor (a PERFORM target, a parent caller, or a same-compilation-unit DATA DIVISION / copybook), never a graph-wide fan-out. **Read the body first. Crawl only when a specific clause of the rule cannot be grounded without a specific named neighbor.**

---

## 1. The canonical step sequence — deterministic spine vs agent reasoning

There are two interleaved tracks. The **deterministic spine** is the fixed set of tool calls every node ran in the same order. The **agent reasoning** is the variable judgement layered on top (how far to crawl, split-or-merge, resolve-or-risk).

### The deterministic spine (ran on all 16, in this order)

```bash
# [1] GROUND TRUTH FIRST — always, before any reference is opened.
python3 .anti-legacy/run.py wicked_estate source <NODE> --db .anti-legacy/graphs/carddemo.db

# [2] BIND name → SymbolId (§2 traceability; names are not unique).
python3 .anti-legacy/run.py wicked_estate resolve-symbol-id <NODE> --db .anti-legacy/graphs/carddemo.db

# [3] LOAD the substrate (Tier 0/1 vocabulary + Tier 2/3 references) — see §3, §4.
#     vocabulary.json is small by default (default-0 provenance) — Read it directly;
#     the three forming docs via Read. (Where-used/categorization -> the engine.)

# [4] ANALYZE — the reasoning step (§2 ring decision, §5 resolve calculus, §6 split/parity).

# [5] EMIT the rule object (StructuredOutput in these sessions).
```

Step **[1] before [3] is invariant and load-bearing** — every session read the verbatim paragraph body *before* opening any reference doc. `cobol.md` mandates this ("ground truth is the code logic; comments and data-names are CLAIMS"), and the sessions honored it literally. Example: `2500-WRITE-REJECT-REC` read the body, *then* the references, and on that basis correctly ignored a commented-out `DISPLAY '***'` as non-executing. `1500-A-LOOKUP-XREF` did the same with two commented-out `DISPLAY` lines.

Step **[2] is not optional and not cosmetic** — it is the §2 traceability bind, and in this estate it is forced by real name collisions. `resolve-symbol-id` returned **two** SymbolIds for `0000-DALYTRAN-OPEN`, `0400-ACCTFILE-OPEN`, and `1000-DALYTRAN-GET-NEXT` — the `CBTRN01C` twin and the `CBTRN02C` twin. The agent pinned the `CBTRN02C` scope by cluster path before writing. For `0400-ACCTFILE-OPEN` the twin disambiguation was *semantic*, not just bookkeeping: `CBTRN01C` opens the file `OPEN INPUT` (read-only) and `CBTRN02C` opens `OPEN I-O` (read-write, because posting rewrites balances) — the access-mode difference is the entire behavioral delta between the name-twins, and binding the wrong SymbolId would mis-state the rule.

### The `--rule-object` annotate form

In these 16 sessions the rule was returned via **StructuredOutput**, not a direct `wicked_estate annotate` call — none of the 16 traces issued an `annotate`, `by-requirement`, or `coverage` call. The persisted-write form the spine is designed to terminate in is:

```bash
python3 .anti-legacy/run.py wicked_estate annotate <NODE> \
  --db .anti-legacy/graphs/carddemo.db \
  --rule-object '<the JSON rule object: rule_id, statement, terminal, confidence,
                 verification, business_rules[], parity_rules[], legacy_components[],
                 vocabulary_terms[], provenance{}>'
```

**ATOMIC MULTI-EMIT — the one-record-per-node gap is CLOSED (was the cardinal silent failure):** the original 16 sessions ran a **one-record-per-node** StructuredOutput shape, so every SPLIT node emitted its *primary* rule and merely *named the sibling* in the `decomposition` field for a downstream pass to materialize — and that pass silently never ran. The drop was real and silent: `1000-DALYTRAN-GET-NEXT` emitted `RULE-DALYTRAN-001` and flagged `ERR-DALYTRAN-001` for a "later register," `2000-POST-TRANSACTION` noted "coverage stays < 1.0 until 2700/2800/2900 are independently extracted," and `2700-UPDATE-TCATBAL` left "a downstream pass must MATERIALIZE [ERR-TCATBAL-001]." Each of those is the ERR- twin vanishing while coverage stayed `< 1.0` with no error.

That gap is now closed in the loop itself (`scripts/extract.py`), not by discipline. A node's extractor MAY return **multiple rule objects atomically** — a list `[primary, ERR-twin, ...]`, or a `{"primary": {...}, "splits": [...]}` envelope — and the loop materializes **every one of them in the same pass**: one overlay row + one native requirement field per rule, distinct `rule_id` each. A single-dict return is still accepted unchanged (full back-compat). The primary is written **last** so the `{db_id, symbol_id}`-keyed, last-record-wins overlay keeps the node's settled coverage state on the primary outcome, while each split sibling is persisted as its own requirement row for §I5 / `by_requirement` to consume. **There is no downstream cleanup pass to remember — split here, in one pass.**

**The silent case is now structurally impossible:** if any returned rule *declares* a decomposition sibling rule_id (`decomposition`, `sibling_rule_ids`, `splits`, or `siblings`) that is **not** itself emitted in the same batch, the loop **raises `ExtractionError`** naming the missing sibling and its declarer — it never silently proceeds. So the `1000-DALYTRAN-GET-NEXT`-shaped "emit RULE, flag ERR- for later" pattern can no longer leave a dropped twin: either you return the ERR- twin in the same call (it is materialized) or the loop refuses to continue.

---

## 2. Ring-depth analysis — how far past the node you actually go

**This is the central operational question and the evidence answers it sharply.**

| Ring depth | Count | Nodes |
|---|---|---|
| **RING-0** (node body alone) | **9** | `0000-DALYTRAN-OPEN`, `0500-TCATBALF-OPEN`, `1500-A-LOOKUP-XREF`, `1500-B-LOOKUP-ACCT`, `2500-WRITE-REJECT-REC`, `2800-UPDATE-ACCOUNT-REC`, `2900-WRITE-TRANSACTION-FILE`, `CBTRN02C`, `Z-GET-DB2-FORMAT-TIMESTAMP` |
| **RING-1** (one targeted hop) | **7** | `0400-ACCTFILE-OPEN`, `1000-DALYTRAN-GET-NEXT`, `1500-VALIDATE-TRAN`, `2000-POST-TRANSACTION`, `2700-A-CREATE-TCATBAL-REC`, `2700-B-UPDATE-TCATBAL-REC`, `2700-UPDATE-TCATBAL` |
| **blast-radius / context used** | **0** | — never. |

**Distribution: 9/16 RING-0, 7/16 RING-1, 0/16 blast-radius.** The crawl, when it happened, was always a hand-followed `wicked_estate source` of a *named* neighbor, never a radius expansion.

### Why RING-0 was sufficient (the 9)

A node resolves at RING-0 when its body is a **self-contained behavioral unit** — the whole decision (success gate, failure branch, literals) is local. The proven RING-0 shapes:

- **Self-documenting file-OPEN / status-check / abend units.** `0000-DALYTRAN-OPEN`: the entire success/failure decision (`APPL-RESULT` 8/0/12, the level-88 `APPL-AOK` gate, `CONTINUE` vs `DISPLAY`+log+`ABEND`) is local, and the embedded `DISPLAY 'ERROR OPENING DAILY TRANSACTION FILE'` literal plus self-naming abend paragraph names supply the failure semantics. The two PERFORM targets (`Z-DISPLAY-IO-STATUS`, `Z-ABEND-PROGRAM`) were handled **by name only** and merged into `legacy_components` without reading their bodies.
- **Leaf validation gates with inline literals.** `1500-A-LOOKUP-XREF` (keyed READ + `INVALID KEY` → fail code 100 + verbatim `'INVALID CARD NUMBER FOUND'`) and `1500-B-LOOKUP-ACCT` (READ + three rejection branches, reason codes **101/102/103**, every reason string, and the projected-balance COMPUTE — all inline). Nothing was delegated to a PERFORMed sub-paragraph or external CALL.
- **Self-contained persistence-plus-error units.** `2500-WRITE-REJECT-REC` (MOVE/MOVE/WRITE + `DALYREJS-STATUS='00'` gate + abend branch) and `2900-WRITE-TRANSACTION-FILE` (both `TRANFILE-STATUS='00'` branches literal). The PERFORM targets `9910-DISPLAY-IO-STATUS` / `9999-ABEND-PROGRAM` were classified as plumbing **from their names** and merged, not read.
- **Self-contained arithmetic units.** `2800-UPDATE-ACCOUNT-REC`: the `ADD`/`IF-ELSE`/`REWRITE`/`INVALID-KEY` logic plus its inline literals (reason code 109, `"ACCOUNT RECORD NOT FOUND"`) are all in the body; the sign-routing (`IF DALYTRAN-AMT >= 0`) is local.
- **The top-level driver node.** `CBTRN02C` resolved at RING-0 *because it is the PROGRAM-ID node* — its PROCEDURE DIVISION **inlines** the whole driver (the `PERFORM UNTIL END-OF-FILE` loop, the `IF WS-VALIDATION-FAIL-REASON = 0 ... ELSE ...` routing, the tail `IF WS-REJECT-COUNT > 0 MOVE 4 TO RETURN-CODE`). The only rules this node *owns* — the routing and the return-code — are entirely visible in its own body. Children (`1500`/`2000`/`2500`) were deliberately **not** crawled because their behavior is not this node's rule; they were split out to their own extractions.
- **Straight-line utility helpers.** `Z-GET-DB2-FORMAT-TIMESTAMP`: a branch-free MOVE chain (`FUNCTION CURRENT-DATE` → component MOVEs → `'0000'` pad → separators → EXIT). No branch, no validation, no error path = nothing to crawl for.

### Why RING-1 was needed (the 7) — the exact fan-out triggers

Each expansion was triggered by a **specific clause of the rule that the body could not ground**. There are exactly three crawl axes:

**Axis A — follow the PERFORM target, because the body shows the *call* but not the *consequence*.**
- `0400-ACCTFILE-OPEN`: the body shows `DISPLAY` + `PERFORM 9999-ABEND-PROGRAM`, but "abnormally terminates" is the load-bearing word in the rule. The agent read `9999-ABEND-PROGRAM` and confirmed `MOVE 999 TO ABCODE` + `CALL 'CEE3ABD'` — a **true OS abend, not a clean GOBACK**. That neighbor body supplied the semantic that makes the rule true; without it, "abend" is an assumption. (It also read `9910-DISPLAY-IO-STATUS` to confirm it is mere status-formatting plumbing.)
- `1500-VALIDATE-TRAN`: a pure orchestrator — its body is only `PERFORM 1500-A` / `IF WS-VALIDATION-FAIL-REASON = 0 PERFORM 1500-B ELSE CONTINUE` / `EXIT`. It names *no* concrete validation semantics. The agent followed **both** children: `1500-A` supplied "first gate = card XREF, fail 100," and `1500-B` supplied "second gate = account, fails 101/102/103" — which is what makes the `= 0` test meaningful and proves the fail-fast ordering.
- `2000-POST-TRANSACTION`: from the orchestrator body alone you cannot tell whether `TRAN-PROC-TS` is **generated at post time or carried from the DALYTRAN input**. The agent followed exactly **one** of the four PERFORM targets — `Z-GET-DB2-FORMAT-TIMESTAMP` — whose body (`MOVE FUNCTION CURRENT-DATE ...`) closed that gap. It deliberately did **not** read the other three (2700/2800/2900) because those are separate outcomes (SPLIT), named-only here.

**Axis B — follow the parent caller, because the body shows *what* it does but not *when it fires*.**
- `2700-A-CREATE-TCATBAL-REC` and `2700-B-UPDATE-TCATBAL-REC`: each body does an `ADD ... TO TRAN-CAT-BAL` but neither states its **precondition**. The agent read the parent `2700-UPDATE-TCATBAL`, which does `READ TCATBAL-FILE ... INVALID KEY → MOVE 'Y' TO WS-CREATE-TRANCAT-REC` then `IF ... = 'Y' PERFORM 2700-A ELSE PERFORM 2700-B`. That parent is what proves `-A` is the *create-when-missing* branch (opening balance seeded from zero) and `-B` is the *update-existing* branch (accumulate, not replace). The body alone cannot tell you which branch it is.
- `2700-UPDATE-TCATBAL` (the parent itself): the body shows the routing (`READ INVALID KEY` → create-flag → conditional PERFORM) but **not what each branch does to the balance**. The agent followed **both** children into RING-1 to get the create vs accrue semantics.

**Axis C — follow the DATA DIVISION / copybook, because PROCEDURE names are CLAIMS until their definitions are confirmed.**
- `1000-DALYTRAN-GET-NEXT`: the body references literal file-status codes (`'00'`/`'10'`) and level-88 names (`APPL-AOK`, `APPL-EOF`) that are meaningful only once their VALUE clauses are confirmed (`cobol.md` guardrail b). The agent read `CBTRN02C.cbl` directly (find + grep) for **L29-32** (`SELECT ... FILE STATUS IS DALYTRAN-STATUS`) and **L142-144** (`88 APPL-AOK VALUE 0`, `88 APPL-EOF VALUE 16`) — confirming `'10'`→16→EOF is a *normal* termination, not an error. This is a same-compilation-unit DATA-DIVISION hop, not a PERFORM hop.
- `2700-A`/`2700-B`/`2700-UPDATE-TCATBAL` also took a copybook hop to `CVTRA01Y.cpy` to confirm `TRAN-CAT-BAL PIC S9(09)V99` — required to ground the mandatory parity rule (the PIC is not in any PROCEDURE-DIVISION node; it lives in the data layout).

**The rule of thumb the evidence proves:** crawl one hop along the axis that grounds the *specific unverified clause* — abend consequence (A), firing precondition (B), or numeric precision / status-code meaning (C). Never crawl for completeness; crawl for a named gap.

### A caution the evidence flags: doc-sourced "neighbor" facts are NOT ring-verified

Several RING-0 nodes *cited* parent/sibling context that came from a **reference-doc worked example**, not from a graph crawl. `1500-A-LOOKUP-XREF` reused `decomposition.md`'s documented parent (`1500-VALIDATE-TRAN`) and sibling (`1500-B`→`VAL-ACCT-001`) framing **without reading those nodes**. `2900-WRITE-TRANSACTION-FILE` inferred its parent-rule boundary (`RULE-POST-001`) from the doc's canonical example, not by reading `2000-POST`. `Z-GET-DB2-FORMAT-TIMESTAMP` lifted its caller link from the doc example. `2800-UPDATE-ACCOUNT-REC` took its PIC widths from `cobol.md`/CVACT01Y prior knowledge, **not** a copybook re-read — and explicitly self-flagged that as the soft spot. **Treat doc-example corroboration as a citation, not as ring-verified evidence**; if a parity PIC is load-bearing, re-read the copybook (as the 2700 family did) rather than trusting the doc.

---

## 3. Vocabulary usage — Read the small glossary, what got used, propose-don't-coin in practice

### Read the glossary directly — it is small by design

**(Historical:)** in the original run the glossary stored ~25 SymbolId provenance rows per term, ballooning `vocabulary.json` to ~1.7MB, and the 256KB Read cap FAILED on every node that attempted a direct Read (`0000-DALYTRAN-OPEN`, `0400`, `0500`, `1000`, `1500-A`, `1500-B`, `1500-VALIDATE-TRAN`, `2500`, `2700-A`, `2700-B`, `2700-UPDATE`, `2800`, `2900`, `Z-GET-DB2-FORMAT-TIMESTAMP` — and structurally for `CBTRN02C` which fell back to `head`).

**That bloat is fixed at the source, not papered over with a query seam.** The bootstrap now persists **no** per-term provenance by default (`config.vocabulary.max_sources_per_term = 0`) — each record carries only `freq` + `mined_from` + the authored meaning — so the file stays comfortably under the Read cap. **Read `.anti-legacy/vocabulary.json` directly** to check a token's `status` / `verification` / `definition` before naming it. Anything beyond meaning is a code-graph question for the engine:

```bash
# where-used / evidence for a token:
python3 .anti-legacy/run.py wicked_estate query <token> --db .anti-legacy/graphs/<app>.db
# categorization / capability communities:
python3 .anti-legacy/run.py wicked_estate cluster --db .anti-legacy/graphs/<app>.db
# after `vocabulary project`: which nodes carry a domain term:
python3 .anti-legacy/run.py wicked_estate read-kv <name> --db .anti-legacy/graphs/<app>.db
```

A token's record gives exactly the fields you need to decide name-with vs flag-as-unproven: `canonical / term_type / definition / aliases / pseudonyms_slang / status / verification`.

**(Retired:)** an interim substring-`lookup` subcommand (and the earlier scripted needle-grep that had to filter on `t['canonical']` — keying on `t['term']`/`t['name']` returned nothing, the dead end `1500-B` and `Z-GET-DB2-FORMAT-TIMESTAMP` hit) are no longer the path. Querying a parallel copy of the file is the parallel-engine smell; the small-glossary + engine-owned where-used replaces both.

### Which terms got used

Across the estate the same posting-domain vocabulary recurs and was used **to name entities and actions only**, never to coin definitions:

- File/entity terms: `DALYTRAN` (daily transaction), `DALYREJS` (daily rejects), `TCATBAL`/`TCATBALF` (transaction-category balance), `ACCT` (account), `CARDXREF`/`XREF`/`CCXREF` (card cross-reference), `TRAN`/`TRANSACTION`.
- Money/field terms: `BAL`, `AMT`, `CREDIT`, `DEBIT`, `LIMIT`, `CYC`, `EXPIRY`.
- Action/concept terms: `ABEND` (abnormal termination), `VALIDATE`, `EOF`, `TS`/`DB2`/`PROC` (the timestamp rule's tokens).

### Propose-don't-coin, as actually practiced

**Not one of the 16 nodes minted a new `proposed` vocabulary record.** In every case the tokens the rule needed *already existed* as records — so the procedure was "name with the existing record as-is," never "coin inline." Critically, **every term used carried `status: proposed` / `verification: unverified`** (e.g. `0000`, `0400`, `1000`, `1500-*`, `2700-*` all note this explicitly). The agents used them as *naming handles* but did **not** treat them as confirmed definitions — which is exactly why most confidences landed ~0.92 and not 1.0 (see §5). Internal control flags (`APPL-RESULT`, `APPL-AOK`, `IO-STATUS`, `TRANFILE-STATUS`, `WS-VALIDATION-FAIL-REASON`) were **deliberately kept out** of the business statement as non-domain mechanics (explicit on `0400`, `1000`, `2900`, `1500-VALIDATE-TRAN`).

The one mint that *did* happen is not a vocabulary mint: `1500-B-LOOKUP-ACCT` coined **rule-ids** (`VAL-ACCT-002`, `ERR-ACCT-001`, `VAL-ACCT-003`) per the `writing-standard.md` `RULE-/VAL-/ERR-` taxonomy — those are requirement identifiers, not vocabulary canonicals, so no `proposed` record was needed.

---

## 4. Reference usage — how the three forming docs shaped each rule

**Three docs loaded on every node: `cobol.md`, `writing-standard.md`, `decomposition.md`. `search-tips.md` was NEVER loaded (0/16)** — because the crawl was always a hand-followed PERFORM/parent/copybook hop, never a search. Each doc has a distinct, observable job:

### `cobol.md` — the idiom→shape map (drives WHAT shape the rule takes)
- `IF/ELSE` error branch + `DISPLAY` + `PERFORM ABEND` → an **`ERR-###`** error-path rule; level-88 `APPL-AOK(0)` identified as the **success gate** (`0000`, `2900`).
- `READ ... INVALID KEY` → a data-access **RULE** + an `ERR-###` for the miss path (`1500-A`, `1500-B`, `1000`).
- `ADD`/`COMPUTE` on a money field → a RULE **plus a mandatory `parity_rule`** ("silent-loss territory") (`2700-A`, `2700-B`, `2800`).
- File OPEN/CLOSE/status-check paragraphs → **plumbing, not behavior** → MERGE, don't mint a standalone RULE (`0400`, `0500`).
- And the universal guardrail it enforces: **body is ground truth; comments and data-names are CLAIMS** — this is what triggered the §2 Axis-C DATA-DIVISION hop on `1000` and the "ignore the commented-out DISPLAY" calls on `2500`/`1500-A`. **Watch the read cap on this doc too:** on `2000-POST-TRANSACTION` the `cobol.md` Read **failed** (also 1.7MB-class oversize, not retried with offset), so the COBOL idiom mappings there were applied from the model's own knowledge — a documented degradation, not the norm.

### `writing-standard.md` — the rule shape (drives the statement voice + fields)
Supplied on every node: the language-agnostic "When <context>, the system <behavior>" template; the `RULE-/VAL-/ERR-###` id taxonomy; the required field set (`business_rules`, `parity_rules`, `provenance`, `legacy_components` non-empty); and guardrail c (unknown token → `proposed` record, never inline coined). `2900-WRITE-TRANSACTION-FILE` and `2000-POST-TRANSACTION` both cite this doc's *Example 2* / canonical example as using their exact node.

### `decomposition.md` — the M:N cardinality call (drives SPLIT vs MERGE)
This doc was **decisive on the cardinality of nearly every node**. Its worked examples literally name several of these nodes:
- `2000-POST-TRANSACTION` (`RULE-POST` + `ERR-TRANFILE`, with 9910/9999 MERGED) is the doc's canonical SPLIT+MERGE diagram — mirrored verbatim by `2700-A`, `2500`, `2900`.
- `1500-VALIDATE-TRAN` is named at lines 42-43 as the canonical SPLIT example — which is *why* the orchestrator keeps the ordering rule and the child gates split off.
- The "OPEN/CLOSE/status-check = plumbing" MERGE signal drove `0400`, `0500`, and the CBTRN02C merge-the-six-OPENs decision.

---

## 5. The RESOLVE-vs-RISK calculus — what made trusted_verified vs RISK-flagged

### The numbers

**15 of 16 RESOLVED; 1 RISK.** All 16 are `verification: trusted_verified` (including the RISK one — see below). Confidences:

| Confidence | Nodes |
|---|---|
| 0.93 | `1500-A-LOOKUP-XREF`, `1500-B-LOOKUP-ACCT`, `2700-B-UPDATE-TCATBAL-REC`, `2800-UPDATE-ACCOUNT-REC`, `2900-WRITE-TRANSACTION-FILE`, `Z-GET-DB2-FORMAT-TIMESTAMP` |
| 0.92 | `0000-DALYTRAN-OPEN`, `0400-ACCTFILE-OPEN`, `1000-DALYTRAN-GET-NEXT`, `2000-POST-TRANSACTION`, `2700-A-CREATE-TCATBAL-REC`, `CBTRN02C` |
| 0.90 | `1500-VALIDATE-TRAN`, `2500-WRITE-REJECT-REC`, `2700-UPDATE-TCATBAL` |
| **0.55** | **`0500-TCATBALF-OPEN` (the one RISK)** |

### What earns `trusted_verified` + RESOLVED

The rule is `trusted_verified` when **every load-bearing clause is grounded in code read directly** (the body via `source`, plus any RING-1 neighbor needed to prove a clause), and **no gap is left to assumption**. The literals are *copied, not paraphrased*: `1500-B` copied reason codes 101/102/103, their verbatim strings, the `>=` boundary direction, the exact COMPUTE, and the `(1:10)` substring straight out of the body. `2800` confirmed the descriptive comment against the actual `ADD/IF/ELSE` (guardrail b) rather than trusting it blind.

### SOURCE-KIND pins the trust tier — comment/doc grounding is NOT trusted_verified (GOTCHA 3)

The §5 numbers above record *all 16* as `trusted_verified`, and that was *correct for those 16 only because every load-bearing clause was grounded in code* — the body via `source` (SOURCE-KIND `code-body`) or a copybook PIC / level-88 VALUE (SOURCE-KIND `data-def`, e.g. the `1000` DATA-DIVISION hop and the `2700`-family `CVTRA01Y.cpy` PIC reads). That coincidence hid a real distinction the real run exposed: a fact read from a **copybook PIC / level-88** (a verifiable DATA DEFINITION) and a fact read from a **comment** (prose CLAIM) carried *different trust* yet were both folded into `trusted_verified`.

The mechanical rule, now enforced (see `writing-standard.md` → "SOURCE-KIND drives the trust tier"): tag each grounding fact with its **SOURCE-KIND** — `code-body` | `data-def` | `comment` | `doc` — and record the set in `provenance.source_kinds`.

- `verification = trusted_verified` **only if** every load-bearing clause is grounded in `code-body` and/or `data-def`.
- If any load-bearing clause rests on a `comment`/`doc` claim **not confirmed against code**, `verification = untrusted_verified` (RISK-eligible) — *regardless of how convincing the comment reads*. Confirm the claim against a `code-body`/`data-def` fact to **promote** to `trusted_verified` (the `2800` move: it confirmed its descriptive comment against the actual `ADD/IF/ELSE` *before* claiming trusted; an unconfirmed comment would have stayed `untrusted_verified`). The schema (`schemas/requirements-graph.enriched.schema.json`) carries `provenance.source_kinds` as an optional enum array on every rule/validation/error-path item — additive, non-breaking.

### Why confidence holds *below* 1.0 even when RESOLVED

The 0.92/0.93 ceiling is consistent and explained: residual uncertainty that is **not a code-grounding gap**. The recurring reasons:
- **Vocabulary terms are still `proposed`/`unverified`** (named-with, not confirmed) — explicit on `1000`, `Z-GET-DB2-FORMAT-TIMESTAMP`.
- **Name-twin disambiguation** resolved by cluster choice rather than reading both twins (`0000`, `1000`).
- **PERFORM-target bodies assumed by name** rather than read (`0000`: `Z-DISPLAY-IO-STATUS`/`Z-ABEND-PROGRAM` never opened).
- **PIC widths taken from a doc, not a copybook re-read** (`2800` — its self-flagged soft spot).
None of these dropped the terminal to RISK; they are confidence haircuts, not gaps.

### The one RISK node — `0500-TCATBALF-OPEN`, and why

`0500-TCATBALF-OPEN` is the instructive exception. Note its profile: **`verification: trusted_verified` but `terminal: RISK`, confidence 0.55.** That split is deliberate and is the whole lesson:

- The **code is not in doubt** — the OPEN mechanics, the `APPL-RESULT` sentinel, the `APPL-AOK` success gate, and the abend path are unambiguous and fully grounded in the read body. Hence `trusted_verified`.
- But there is **no standalone business rule *at this node* to resolve.** It is pure plumbing (a file OPEN/status-check), so per `decomposition.md` it does not become its own bare requirement — it must **MERGE upward** into the parent `ERR-` requirement (abend-on-TCATBAL-open-failure, anchored at `2700-UPDATE-TCATBAL`).
- **That parent requirement was not yet written/linked.** Until this node is attached to its merge anchor's `legacy_components`, it would be **unaccounted by coverage**. RISK is the honest terminal: "the code is understood, but there is nothing here to resolve in isolation and the home it must merge into doesn't exist yet."
- The **0.55 confidence reflects the unwritten parent link, NOT any doubt about the code.**

Contrast this directly with the *other* OPEN nodes that resolved cleanly: `0000-DALYTRAN-OPEN` (0.92) and `0400-ACCTFILE-OPEN` (0.92) are *also* plumbing, but each produced a self-standing `ERR-###` outcome (`ERR-DALYTRAN-001`, `ERR-ACCTFILE-001`) that the agent wrote *as its own requirement* with the abend sub-paragraphs merged in. `0500` differs only in that its failure semantic was folded into a *not-yet-existing parent* rather than minted as a standalone ERR — so it had nothing to terminate on. **The rule: a plumbing node is RESOLVED when its failure path is captured as a self-standing `ERR-###` (with sub-paragraphs merged in); it is RISK when its only home is a merge anchor that has not been written yet.** The fix for a `0500`-shaped RISK is not to re-read the code — it is to write/link the parent requirement and attach this node to its `legacy_components`.

---

## 6. Decomposition + parity patterns observed

### Decomposition (cardinality)

The M:N call landed in four observable patterns:

- **SPLIT (1 node → N outcomes), all sharing one SymbolId in `legacy_components`.** `1000-DALYTRAN-GET-NEXT` → `RULE-DALYTRAN-001` (read-next + graceful EOF) + `ERR-DALYTRAN-001` (abend on bad status). `1500-B-LOOKUP-ACCT` → **four** outcomes (data-access read + `ERR-101` + `VAL-102` overlimit + `VAL-103` post-expiration). `2800` → balance-add RULE + credit/debit routing clause + `ERR-ACCT-REWRITE-109`. `2500` → `RULE-REJECT-001` (persist) + `ERR-REJECT-001` (abend).
- **MERGE (N nodes → 1 outcome).** The abend-support paragraphs (`9910-DISPLAY-IO-STATUS` / `9999-ABEND-PROGRAM`, or their `Z-` aliases) are **always merged by reference into the `ERR-` outcome's `legacy_components`, almost never read** — they are mechanical plumbing with no independent business meaning. `Z-GET-DB2-FORMAT-TIMESTAMP`'s eleven MOVEs MERGE into one timestamp-format outcome (no SPLIT — no branch, no error path).
- **SPLIT-anchor orchestrators.** `1500-VALIDATE-TRAN` keeps the **ordered/short-circuit composition rule on itself** while the child gates split into separate `VAL-` requirements that *also* list the orchestrator in their `legacy_components`. `2000-POST-TRANSACTION` anchors `RULE-POST-001` only and splits the three fan-out targets off. `CBTRN02C` owns two driver rules (`RULE-POST-DRIVER-001` routing + `RULE-RC-001` return-code) and splits out `1500`/`2000`/`2500` while merging the six OPENs, six CLOSEs, GET-NEXT, and the two abend utilities.
- **Plumbing-merges-upward.** The `0500` case (§5): a file OPEN with no standalone outcome MERGES into the parent posting/update requirement rather than becoming a bare node.

The governing principle from `decomposition.md`, proven repeatedly: **paragraph boundaries reflect COBOL structuring, not business outcomes** — `2700-A`/`2700-B` did *not* become their own bare requirements; they realize the parent upsert (`RULE-TCATBAL-001`).

### Parity

The pattern is binary and disciplined: **parity is attached exactly when a numeric domain value crosses the boundary, and explicitly justified-as-absent otherwise.**

**Parity REQUIRED — and present** (all signed, fixed-scale, COMP-3 silent-loss zone):
- `2800-UPDATE-ACCOUNT-REC`: three `parity_rules` — `ACCT-CURR-BAL`, `ACCT-CURR-CYC-CREDIT`, `ACCT-CURR-CYC-DEBIT`, all `PIC S9(10)V99`; addend `DALYTRAN-AMT` is `S9(09)V99 COMP-3`. **The critical sign-semantics flag**: the legacy does **not** negate before accumulating — a negative amount is `ADD`ed (signed) into `CYC-DEBIT`, and the `>= 0` boundary is literal so a **zero amount accrues to CREDIT, not DEBIT**. A re-implementer assuming positive-debit convention or excluding zero would break parity.
- `2700-A`/`2700-B`/`2700-UPDATE-TCATBAL`: `TRAN-CAT-BAL PIC S9(09)V99` (confirmed from `CVTRA01Y.cpy`) — target decimal ≥ 11 digits, scale **exactly 2**, signed; never widen scale; assert legacy-vs-target equality **to the cent**.
- `1500-B-LOOKUP-ACCT`: parity on the projected-balance COMPUTE (`WS-TEMP-BAL = ACCT-CURR-CYC-CREDIT − ACCT-CURR-CYC-DEBIT + DALYTRAN-AMT`); **boundary direction is load-bearing** — the test is `ACCT-CREDIT-LIMIT >= WS-TEMP-BAL`, so equal-to-limit **passes**; reject only on strict exceed.

**Parity COPY-FIDELITY (not arithmetic):** `2000-POST-TRANSACTION` on `TRAN-AMT` — preserve signed value and exactly 2-dp scale across a 1:1 copy, no rounding/truncation/scale-widening. `Z-GET-DB2-FORMAT-TIMESTAMP` records a **format** preservation contract (not money): exact separators, milliseconds-only resolution with the `'0000'` microsecond pad **not** silently widened to true microseconds.

**Parity explicitly ABSENT — and reasoned, never silently skipped:**
- File-OPEN/status nodes (`0000`, `0400`, `0500`, `1000`): the only numerics are internal control sentinels (`APPL-RESULT` 8/0/12, file status `'00'`, `ABCODE` 999) — status flags, not domain values.
- Validation/identifier nodes (`1500-A`): output is a discrete fail code; the key is a card-number **identifier**, not money.
- Record-image / orchestration nodes (`2500`, `2900`, `1500-VALIDATE-TRAN`): group MOVE / WRITE of an already-assembled record — any COMP-3 fields inside cross as an **opaque record image**; the parity obligation belongs to the paragraph that *computes* them (`2000`/`2800`), not the writer.
- The driver `CBTRN02C`: the numerics it writes are **integer counts** (`WS-TRANSACTION-COUNT`, `WS-REJECT-COUNT`, `PIC 9(09)`, scale 0) and the discrete `RETURN-CODE 4` exit signal — preserve as non-negative integers / exact exit code; explicitly notes COMP-3 money parity lives in the 2700/2800 children.

---

## 7. Per-node evidence table

| Node (CBTRN02C scope) | Ring | Crawl axis / neighbor | Refs loaded | Vocab (all `proposed`) | Conf | Terminal | Decomp | Parity |
|---|---|---|---|---|---|---|---|---|
| `0000-DALYTRAN-OPEN` | RING-0 | — (PERFORM targets merged by name) | cobol, w-std, decomp | DALYTRAN, ABEND, IO-STATUS, APPL-AOK | 0.92 | RESOLVED | MERGE+SPLIT → `ERR-DALYTRAN-001` | none (control sentinels) |
| `0400-ACCTFILE-OPEN` | RING-1 | A: read `9999-ABEND-PROGRAM` (`CALL CEE3ABD`), `9910` | cobol, w-std, decomp | ACCT, ABEND | 0.92 | RESOLVED | structural/MERGE → `ERR-ACCTFILE-001` | none (control codes) |
| `0500-TCATBALF-OPEN` | RING-0 | — (PERFORM targets named only) | cobol, w-std, decomp | TCATBALF, APPL, ABEND, TRAN | **0.55** | **RISK** | MERGE upward into unwritten parent `ERR-TCATBAL-OPEN-001` | none (control flags) |
| `1000-DALYTRAN-GET-NEXT` | RING-1 | C: read `CBTRN02C.cbl` DATA DIV (L29-32, L142-144 88-levels) | cobol, w-std, decomp | DALYTRAN, EOF, ABEND, TRAN | 0.92 | RESOLVED | SPLIT → `RULE-DALYTRAN-001` + `ERR-DALYTRAN-001` | none (control code, 88-named) |
| `1500-A-LOOKUP-XREF` | RING-0 | — (parent/sibling from doc, not crawl) | cobol, w-std, decomp | CARDXREF, CARD, ACCT | 0.93 | RESOLVED | collapsed to ONE `VAL-XREF-001` (fail 100 inline) | none (card-num identifier) |
| `1500-B-LOOKUP-ACCT` | RING-0 | — (leaf gate, all literals inline) | cobol, w-std, decomp | ACCT, CCXREF, CREDIT, LIMIT, CYC, DEBIT, BAL, EXPIRY | 0.93 | RESOLVED | SPLIT → read + `ERR-101` + `VAL-102` + `VAL-103` | **REQUIRED** — projected-balance COMPUTE, `>=` boundary |
| `1500-VALIDATE-TRAN` | RING-1 | A: read both children `1500-A` + `1500-B` | cobol, w-std, decomp | XREF, ACCT, VALIDATE | 0.90 | RESOLVED | SPLIT-anchor (ordering rule on self; children split) | none (reads integer flag only) |
| `2000-POST-TRANSACTION` | RING-1 | A: read `Z-GET-DB2-FORMAT-TIMESTAMP` (1 of 4) | w-std, decomp (**cobol.md Read FAILED**) | DALYTRAN, TRAN, TCATBAL, ACCT, TRAN-AMT | 0.92 | RESOLVED | SPLIT-anchor `RULE-POST-001`; 2700/2800/2900 split off | copy-fidelity on `TRAN-AMT` (S9(09)V99 COMP-3) |
| `2500-WRITE-REJECT-REC` | RING-0 | — (9910/9999 merged by name) | cobol, w-std, decomp | DALYTRAN, DALYREJS, TRAN | 0.90 | RESOLVED | SPLIT `RULE-REJECT-001` + `ERR-REJECT-001` (MERGE 9910/9999) | none (record-image MOVE) |
| `2700-A-CREATE-TCATBAL-REC` | RING-1 | B: parent `2700-UPDATE-TCATBAL`; C: `TRAN-CAT-BAL` PIC | cobol, w-std, decomp | TCATBAL, DALYTRAN, XREF, TRANCAT, ACCT | 0.92 | RESOLVED | SPLIT `RULE-TCATBAL-002` + `ERR-TCATBAL-002` | **REQUIRED** — `TRAN-CAT-BAL S9(09)V99`, seed-from-zero |
| `2700-B-UPDATE-TCATBAL-REC` | RING-1 | B: parent `2700-UPDATE-TCATBAL`; C: `CVTRA01Y.cpy:9` | cobol, w-std, decomp | TCATBAL, DALYTRAN, AMT, BAL | 0.93 | RESOLVED | SPLIT `RULE-TCATBAL-002` + `ERR-TCATBAL-002` (MERGE 9910/9999) | **REQUIRED** — `S9(09)V99`, accumulate-not-replace |
| `2700-UPDATE-TCATBAL` | RING-1 | A: both children 2700-A/2700-B; C: `CVTRA01Y.cpy` | cobol, w-std, decomp | TCATBAL, TRANCAT, DALYTRAN, XREF, ACCT | 0.90 | RESOLVED | SPLIT `RULE-TCATBAL-001` (upsert) + `ERR-TCATBAL-001`; status `'23'`=create-trigger | **REQUIRED** — `S9(09)V99`, signed addend |
| `2800-UPDATE-ACCOUNT-REC` | RING-0 | — (self-contained; PICs from doc, self-flagged) | cobol, w-std, decomp | ACCT, DALYTRAN, BAL, CREDIT, DEBIT, ACCTFILE | 0.93 | RESOLVED | SPLIT — add + credit/debit routing + `ERR-ACCT-REWRITE-109` | **REQUIRED ×3** — `S9(10)V99`; signed-debit, zero→credit |
| `2900-WRITE-TRANSACTION-FILE` | RING-0 | — (9910/9999 merged; parent from doc) | cobol, w-std, decomp | TRAN, TRANFILE, TRANSACTION | 0.93 | RESOLVED | SPLIT (WRITE→parent `RULE-POST-001`) + `ERR-TRANFILE-001` (MERGE 9910/9999) | none (record persist; parity upstream) |
| `CBTRN02C` (PROGRAM-ID) | RING-0 | — (own body inlines driver; children split out) | cobol, w-std, decomp | DALYTRAN, DALYREJS, VALIDATE, TRANSACTION | 0.92 | RESOLVED | SPLIT+MERGE — `RULE-POST-DRIVER-001` + `RULE-RC-001`; merge OPEN/CLOSE/GET-NEXT/abend | integer COUNTS `9(09)` + `RETURN-CODE 4` |
| `Z-GET-DB2-FORMAT-TIMESTAMP` | RING-0 | — (straight-line; caller from doc) | cobol, w-std, decomp | TS, DB2, TRAN, PROC, DATE | 0.93 | RESOLVED | MERGE — eleven MOVEs → one `RULE-TS-001` | format-fidelity (ms resolution, `'0000'` pad) |

**Constants across all 16:** `verification = trusted_verified` (all 16, including the RISK node); `search-tips.md` loaded **0/16**; `blast-radius` / `context` used **0/16**; `vocabulary.json` direct Read **failed on the 256KB cap every time** — because the file was bloated with per-term provenance; that bloat is now removed at the source (default-0 provenance), so the small glossary is Read directly and where-used goes to the engine; **no new `proposed` vocabulary record minted** on any node.

---

## TL;DR — the procedure in one paragraph

`source` the body **first**; `resolve-symbol-id` to bind the SymbolId (it disambiguates real `CBTRN01C`/`CBTRN02C` name-twins — and the twin can carry the *semantic* delta, e.g. `OPEN INPUT` vs `OPEN I-O`). Load `cobol.md` + `writing-standard.md` + `decomposition.md` (not `search-tips.md`); Read the small `vocabulary.json` directly to ground each token (the file is default-0 provenance, well under the Read cap; where-used/categorization go to the engine, not the file). **Resolve from the body alone if you can — 9 of 16 did.** Crawl exactly one hop only to ground a specific clause: follow a PERFORM target for an abend *consequence*, a parent for a firing *precondition*, or the DATA-DIVISION/copybook for a numeric *PIC* or a level-88 *meaning* — never `blast-radius`. Name with existing (`proposed`) vocab, never coin. SPLIT outcomes / MERGE plumbing per `decomposition.md`; attach a `parity_rule` to every signed-decimal money output (preserving sign and scale **to the cent**) and explicitly justify its absence elsewhere. RESOLVE when every load-bearing clause is code-grounded (confidence ~0.92, haircut for `proposed` vocab + un-read merges); RISK only when there is no standalone rule to resolve *and* the merge anchor it belongs to has not been written yet — that, not any code doubt, is the `0500-TCATBALF-OPEN` lesson.
