# Requirement Writing Standard

> **Load when:** you are *forming* a rule statement for a node and need the canonical shape — the template, the voice, the granularity, and the `business_rules` / `parity_rules` / provenance fields that go on every requirement.
>
> This is Tier 2. It assumes you have already loaded the vocabulary (`.anti-legacy/vocabulary.json`, Tier 0/1). A rule statement names things with **confirmed + trusted_verified** vocabulary terms; an unknown token gets a `proposed` vocabulary record, never an inline coined definition (guardrail c).

A requirement is a **language-agnostic statement of WHAT a node does** — the business behavior, not the COBOL/Java mechanics. The target system is built against the requirement, never against the legacy code. If a developer who has never seen the legacy source can implement the requirement from its statement + contract, it is written correctly.

---

## The canonical form

Every behavior node resolves to **one or more** requirement objects. Each requirement carries:

```jsonc
{
  "rule_id": "RULE-POST-001",            // RULE- (behavior) | VAL- (validation) | ERR- (error path)
  "statement": "When posting a daily transaction, copy the transaction's identity, "
             + "merchant, amount, and timestamp fields onto the permanent TRANSACTION "
             + "record, stamping it with the current processing timestamp.",
  "legacy_components": [                  // guardrail d — the M:N trace back to source. NEVER empty.
    { "symbol_id": "<full SymbolId>", "name": "2000-POST-TRANSACTION", "file": "app/cbl/CBTRN02C.cbl", "line": 412 }
  ],
  "vocabulary_terms": ["DALYTRAN", "TRAN", "TCATBAL", "ACCT"],  // canonical terms used in the statement
  "business_rules": [                     // >=1 always (enriched schema). A node with none is a placeholder.
    "DALYTRAN identity, merchant, amount and original-timestamp fields map 1:1 onto the TRAN record.",
    "TRAN-PROC-TS is set from the system DB2-format timestamp at post time, not copied from the input."
  ],
  "parity_rules": [                       // guardrail e — REQUIRED whenever a numeric value crosses the boundary
    { "field": "TRAN-AMT", "source_type": "COMP-3", "rule": "preserve signed value and 2-dp scale exactly; no rounding" }
  ],
  "confidence": 0.9,                      // 0..1 — your strength of evidence for THIS statement
  "verification": "trusted_verified",     // unverified | untrusted_verified | trusted_verified — DETERMINED by source_kinds (see spectrum below)
  "provenance": {                         // the ring that grounded the rule (mirrors annotations.jsonl)
    "read": ["2000-POST-TRANSACTION body"],
    "ring_edges": ["calls:2700-UPDATE-TCATBAL", "calls:2800-UPDATE-ACCOUNT-REC", "calls:2900-WRITE-TRANSACTION-FILE"],
    "source_kinds": ["code-body", "data-def"],  // SOURCE-KIND of the grounding facts → pins verification (GOTCHA 3). code-body|data-def ⇒ trusted_verified eligible; comment/doc-only ⇒ untrusted_verified
    "claims_confirmed": ["copybook comment 'permanent transaction record' confirmed against WRITE in 2900"]
  }
}
```

### `rule_id` prefixes — one prefix per outcome kind

| Prefix | Use for | Example |
|---|---|---|
| `RULE-` | a business behavior / state change / computation | `RULE-POST-001` — posts a transaction |
| `VAL-`  | an input/state validation gate (a rule that can *reject*) | `VAL-ACCT-001` — account id must be present and numeric |
| `ERR-`  | an error / abort / rollback path | `ERR-TRANFILE-001` — abend on transaction-file write failure |

Number within a node-or-capability family (`-001`, `-002`). IDs are stable handles — downstream tasks, contracts, and UAT verdicts reference them, so do not renumber on re-run.

---

## Voice, tense, granularity

**Voice — declarative present, business subject.** State what the system does, in the domain's language.
- Yes: *"The system rejects a transaction whose card number has no matching cross-reference."*
- No: *"`1500-A-LOOKUP-XREF` performs a READ on XREF-FILE and moves 1 to WS-VALIDATION-FAIL-REASON."* (that is mechanics, not a requirement — it names paragraphs and working-storage flags a target developer can't and shouldn't reproduce.)

**Tense — present, unconditional for invariants; `When <trigger>, …` for event-driven behavior.** Lead with the trigger so the condition is unmissable: *"When the TCATBAL record for the key does not exist, the system creates it before applying the balance update."*

**Granularity — one business outcome per requirement.** If you can join two clauses with "and also" and a developer would test them separately, split them (see `decomposition.md`). A statement that needs three "and"s is usually three requirements. Conversely, do not fragment one atomic outcome (a single field-by-field copy) into one requirement per field.

**Name with confirmed vocabulary, not raw symbols.** Use the canonical term from `vocabulary.json` (`TRAN`, `DALYTRAN`, `TCATBAL`, `ACCT`, `XREF`) — never the raw working-storage name (`FD-TRAN-CAT-KEY`, `WS-VALIDATION-FAIL-REASON`). Working-storage names are implementation; canonical terms are domain. If a token isn't in the vocabulary, propose it (`status: proposed`); don't invent a definition mid-statement.

---

## The two axes you must carry — and never conflate

A requirement statement is only as trustworthy as the **evidence under it**. Carry both axes:

- **`confidence` (0..1)** — your strength of evidence for *this specific statement*. Threshold-gated: below the resolve threshold (guardrail f) you do **not** assert it as resolved — you flag it RISK with the gap named.
- **`verification` (the trust spectrum)** — whether the *meaning of the terms and the claim* is proven, mirroring guardrail b (CODE LOGIC is ground truth; docs/comments are unproven CLAIMS):

| level | means | when |
|---|---|---|
| `unverified` | statement leans on a token/claim not yet checked against code | mined name, or a copybook/README remark taken at face value |
| `untrusted_verified` | corroborated by a *secondary* source (naming convention + one caller, or a doc that matches usage) but not the authoritative code logic | you read a comment that agrees with the call graph but didn't open the body |
| `trusted_verified` | proven against **CODE LOGIC** — you read the paragraph body / DB2 usage — or ratified by a human SME | the only level you may assert as fact |

**Every behavior node ends RESOLVED or RISK — never bare (guardrail a).** RESOLVED = `confidence ≥ threshold` AND `verification = trusted_verified`. Anything else is RISK-flagged with the specific gap ("amount sign behavior inferred from PIC clause, not confirmed against a runtime example").

### SOURCE-KIND drives the trust tier (guardrail b, made mechanical)

The trust tier is **not a free choice** — it is *determined by the grounding source* of the load-bearing facts. A fact read from a copybook PIC or a `level-88 VALUE` (a **verifiable DATA DEFINITION**) is not the same evidence as a fact read from a comment (**prose CLAIM**), and folding both into `trusted_verified` is the GOTCHA-3 error. Record each grounding fact's **SOURCE-KIND** and let it pin `verification`:

| SOURCE-KIND | what it is | counts as code? |
|---|---|---|
| `code-body` | executable logic read directly (the paragraph / method body via `source`) | **yes** |
| `data-def` | copybook `PIC` / `level-88 VALUE` / DB2 column type — a verifiable structural definition | **yes** |
| `comment` | an inline `*`/`//` prose remark | **no — CLAIM** |
| `doc` | a README / design doc / external prose | **no — CLAIM** |

**The rule (mechanical, not discretionary):**

- `verification = trusted_verified` **only if** every load-bearing clause is grounded in `code-body` and/or `data-def`. Code logic and data definitions are ground truth.
- If any load-bearing clause rests on a `comment`/`doc` claim **not confirmed against code**, `verification = untrusted_verified` (RISK-eligible per the spectrum) — even when the statement *reads* convincing. A comment-only grounding is a claim until a `code-body`/`data-def` fact confirms it; once confirmed, record the confirming SOURCE-KIND and **promote** to `trusted_verified` (log the confirmation in `provenance.claims_confirmed`).

Carry the SOURCE-KINDs in provenance: `"source_kinds": ["code-body", "data-def"]` lists the grounding kinds behind the rule. The enriched schema (`schemas/requirements-graph.enriched.schema.json`) accepts `provenance.source_kinds` as an optional `enum`-constrained array (`code-body | data-def | comment | doc`) on every rule / validation / error-path item — additive and non-breaking. Worked example: the `2700-UPDATE-TCATBAL` comment `* Update the balances …` alone is `comment` → `untrusted_verified`; reading the `READ … INVALID KEY … Creating` body adds `code-body` → promote to `trusted_verified`.

**Comments and docs are CLAIMS, not facts (guardrail b).** The `* Update the balances in transaction balance file.` comment on `2700-UPDATE-TCATBAL` is a *claim*; you promote it to `trusted_verified` only after reading the body and seeing the `READ TCATBAL-FILE … INVALID KEY … Creating` logic that confirms it. Record the confirmation in `provenance.claims_confirmed`.

---

## Worked examples — real carddemo paragraphs

### Example 1 — `RULE-` (a posting behavior)
**Node:** `2000-POST-TRANSACTION` (`CBTRN02C.cbl`). Body: 12 `MOVE DALYTRAN-* TO TRAN-*`, `PERFORM Z-GET-DB2-FORMAT-TIMESTAMP`, then `PERFORM 2700/2800/2900`.

```jsonc
{
  "rule_id": "RULE-POST-001",
  "statement": "When posting a daily transaction, the system copies the transaction's identity, type, category, source, description, amount, merchant details, card number and original timestamp onto a permanent TRANSACTION record, and stamps it with the current processing timestamp.",
  "business_rules": [
    "All DALYTRAN business fields map 1:1 onto the corresponding TRAN fields.",
    "TRAN-PROC-TS is assigned from the system DB2-format timestamp at post time — it is generated, not carried from input.",
    "A single posting fans out to three downstream effects: update TCATBAL, update the ACCT record, write the TRAN record."
  ],
  "parity_rules": [{ "field": "TRAN-AMT", "rule": "COMP-3 signed, scale 2 preserved exactly; no rounding or truncation" }],
  "legacy_components": [{ "name": "2000-POST-TRANSACTION", "file": "...CBTRN02C.cbl" }],
  "vocabulary_terms": ["DALYTRAN", "TRAN", "TCATBAL", "ACCT"],
  "confidence": 0.92, "verification": "trusted_verified"
}
```
*Why trusted:* body read directly; the three fan-out effects are the literal `PERFORM` targets, not inferred. `TRAN-AMT` is money → `parity_rule` is mandatory.

### Example 2 — `ERR-` (an error / abort path)
**Node:** `2900-WRITE-TRANSACTION-FILE`. Body: `WRITE FD-TRANFILE-REC`; if `TRANFILE-STATUS` not `'00'` → `DISPLAY 'ERROR WRITING TO TRANSACTION FILE'`, `PERFORM 9910-DISPLAY-IO-STATUS`, `PERFORM 9999-ABEND-PROGRAM`.

```jsonc
{
  "rule_id": "ERR-TRANFILE-001",
  "statement": "If the permanent TRANSACTION record cannot be written (file status not '00'), the system reports the I/O status and aborts processing — the posting is not silently dropped.",
  "business_rules": [
    "A non-'00' file status on the transaction write is an unrecoverable error.",
    "On failure the run abends after logging the I/O status; no partial-success path continues."
  ],
  "legacy_components": [{ "name": "2900-WRITE-TRANSACTION-FILE", "file": "...CBTRN02C.cbl" }],
  "vocabulary_terms": ["TRAN"],
  "confidence": 0.9, "verification": "trusted_verified"
}
```
*Why this is its own `ERR-` requirement, not folded into RULE-POST-001:* it is a distinct outcome (failure semantics) that a target developer must implement and a contract must test independently. The happy-path write is mechanical detail of `RULE-POST-001`; the abend-on-failure is the requirement.

### Example 3 — `VAL-` (a validation gate, flagged RISK)
**Node:** `2210-EDIT-ACCOUNT`. Body: sets `FLG-ACCTFILTER-BLANK`; `IF CC-ACCT-ID = LOW-VALUES OR SPACES OR CC-ACCT-ID-N = ZEROS` → treat as not-supplied. Comment `* Not supplied`.

```jsonc
{
  "rule_id": "VAL-ACCT-001",
  "statement": "An account-id filter is treated as 'not supplied' when it is blank, low-values, or numeric zero; in that case the system clears the account-id and skips account-specific filtering.",
  "business_rules": [
    "Blank, low-values, and numeric-zero are all equivalent to 'no account filter'.",
    "A not-supplied account filter is not an error — it widens the result set rather than rejecting input."
  ],
  "legacy_components": [{ "name": "2210-EDIT-ACCOUNT", "file": "...COACTVWC.cbl" }],
  "vocabulary_terms": ["ACCT"],
  "confidence": 0.6,
  "verification": "untrusted_verified",
  "provenance": { "gap": "downstream effect of FLG-ACCTFILTER-BLANK not yet traced; 'widens result set' inferred from name, not confirmed against the filter consumer." }
}
```
*Why RISK, not RESOLVED:* `confidence 0.6` is below threshold and `verification` is only `untrusted_verified` — the blank-detection is read from code, but what "skips filtering" *does* downstream was inferred from the flag name. The gap is named explicitly (guardrail a: no silent maybe-correct). Resolve it by reading the flag's consumer (use `search-tips.md` / `context()`), then promote to `trusted_verified`.

---

## Checklist before you write the requirement

1. Statement reads as **business behavior**, not COBOL/Java mechanics — no paragraph names, no working-storage flags.
2. Named with **confirmed + trusted_verified** vocabulary terms; any new token has a `proposed` vocab record.
3. `business_rules` has **≥ 1** concrete rule (guardrail: none = placeholder → mark unresolvable with reason).
4. **Every numeric output** carries a `parity_rule` (guardrail e — COMP-3 precision loss is silent).
5. `legacy_components` is **non-empty** and resolves to a real SymbolId (guardrail d).
6. `confidence` set honestly; `verification` reflects the *strongest source actually read* (code > doc) — and is **pinned by `provenance.source_kinds`**: comment/doc-only grounding ⇒ `untrusted_verified`; confirm against `code-body`/`data-def` to promote (GOTCHA 3).
7. Node ends **RESOLVED or RISK** — if RISK, the gap is named in `provenance` (guardrail a).
