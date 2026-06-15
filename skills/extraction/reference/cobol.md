# Tier 3 language reference — COBOL

Load this when the node under crawl is `language: cobol` (COBOL program, paragraph,
or copybook). It maps COBOL idioms to the rule shapes in
`reference/writing-standard.md` (RULE-### / VAL-### / ERR-### / parity_rule). It does
not replace the writing standard — it tells you *which* idiom produces *which* rule
shape so you read the verbatim paragraph body and extract behavior, not syntax.

Ground truth is the **code logic** (the paragraph body from
`wicked_estate source <name>`). Comments and the data-division names are CLAIMS — confirm
them against the PROCEDURE DIVISION before you assert them (guardrail b). Name every
term with confirmed vocabulary; propose, never coin (guardrail c).

All examples below are verbatim from the real carddemo graph — cited by node so you can
re-read them with `wicked_estate source <para> --db .anti-legacy/graphs/carddemo.db`.

---

## Idiom → rule map

| COBOL idiom | What it means | Rule shape |
|---|---|---|
| `PERFORM <para>` / `PERFORM <para> THRU <para>` | invokes a sub-behavior; the orchestrating paragraph composes its children | a RULE on the parent ("performs X, then Y, then Z"); each performed paragraph is its own node → its own rule(s). Decompose M:N — see `reference/decomposition.md` |
| `PERFORM ... UNTIL` / `VARYING` | a loop over a file or table | one RULE describing the iteration invariant + termination condition |
| `EVALUATE TRUE / WHEN <cond>` | multi-branch decision | **one rule per WHEN branch** (incl. `WHEN OTHER`). A 6-branch EVALUATE = 6 rules, not 1 |
| `IF ... / ELSE / END-IF` | binary decision | one RULE for the condition; if the branch sets a fail code, also an ERR-### |
| level-88 condition-name (`88 APPL-AOK VALUE 0`) | a named boolean over a field's value | the 88 *names* a domain state; use it as the condition vocabulary in the rule ("when APPL-AOK", not "when APPL-RESULT = 0") |
| `MOVE a TO b` (data shuffle) | field-to-field copy / record mapping | usually NOT its own rule — fold a block of MOVEs into the enclosing behavior rule ("maps daily-transaction fields onto the posted-transaction record"). A MOVE of a *computed/derived* value IS rule-bearing |
| `ADD / SUBTRACT / COMPUTE / MULTIPLY` on a money/rate/count field | arithmetic on a numeric output | a RULE **plus a parity_rule** on the output field (precision is silent-loss territory) |
| `PIC S9(n)V99` / `... COMP-3` | fixed-point packed-decimal numeric | the field is a numeric output → its writer gets a parity_rule. Record the exact PIC (digits, scale, sign) — see Parity below |
| copybook (`COPY <name>` / `01 ... INTO record`) | shared record layout pulled into many programs | the copybook is a data node; merge it with its readers/writers per `decomposition.md`. Its field stems (ACCT, CARD, TRAN…) are vocabulary entities |
| `READ <file> [INTO rec] INVALID KEY ... NOT INVALID KEY ...` | keyed file lookup | a data-access RULE ("reads X by key") + an ERR-### for the INVALID KEY path |
| `WRITE` / `REWRITE` / `DELETE` `<rec> FROM <rec> INVALID KEY ...` | record persistence | a data-access RULE (insert/update/delete) + ERR-### for INVALID KEY |
| `CALL '<pgm>' USING ...` | invoke another program/subroutine | a RULE naming the called capability; the called program is a separate node (cross-language edge `calls`). `CALL 'CEE3ABD'` etc. are runtime ABEND/util calls → error-path |
| `EXEC SQL ... END-EXEC` | embedded DB2 access | a data-access RULE on the table; the `SQLCODE` check after it is an ERR-### (see EXEC SQL below) |
| `EXEC CICS ... END-EXEC` | online transaction service | see `reference/cics.md` — screen I/O, transfers, and CICS file access are CICS rules |
| `DISPLAY ... / MOVE <code> TO ...-REASON` then `ABEND`/`9999-ABEND-PROGRAM` | failure handling | an ERR-### carrying the literal reason code/message; abnormal-termination is an error path, not normal flow |
| `MOVE <n> TO WS-...-FAIL-REASON` | sets a numeric failure reason code | the literal code (100, 101, 102…) is the error identity — capture it verbatim in the ERR-### |

---

## Parity — numeric outputs (guardrail e, the silent-loss zone)

COBOL money/rate/count fields are fixed-point packed decimal. Re-implementing them in
binary float silently corrupts cents. **Any paragraph that writes a numeric field gets a
`parity_rule` on that output.** Record from the PIC:

- **integer digits** and **fractional digits** — `PIC S9(10)V99` = 10 integer + 2 fraction (the `V` is an implied, not stored, decimal point).
- **sign** — leading `S` = signed; no `S` = unsigned (negative collapses to positive).
- **storage** — `COMP-3` = packed decimal (two digits/byte), `COMP`/`BINARY` = binary integer, display (no usage) = zoned. Storage does not change the value but flags how the legacy rounds/truncates.

Real carddemo money fields (copybook `CVACT01Y` / `CVEXPORT`):

```
05  ACCT-CURR-BAL           PIC S9(10)V99.          (account current balance)
05  ACCT-CREDIT-LIMIT       PIC S9(10)V99.
05  ACCT-CURR-CYC-CREDIT    PIC S9(10)V99.
05  ACCT-CURR-CYC-DEBIT     PIC S9(10)V99.
10  EXP-ACCT-CURR-BAL       PIC S9(10)V99 COMP-3.   (same value, packed for export)
10  EXP-TRAN-AMT            PIC S9(09)V99 COMP-3.
```

Parity rule for a balance writer: target decimal type ≥ 12 digits / 2 scale, half-up (or
the verified COBOL rounding mode), signed; assert legacy-vs-target equality to the cent on
the posted balance. Never widen scale silently — `V99` means exactly 2 fractional digits.

---

## Worked example — CBTRN02C `2000-POST-TRANSACTION` (the canonical posting paragraph)

Verbatim body (`wicked_estate source 2000-POST-TRANSACTION`):

```
2000-POST-TRANSACTION.
    MOVE DALYTRAN-ID            TO TRAN-ID
    MOVE DALYTRAN-TYPE-CD       TO TRAN-TYPE-CD
    MOVE DALYTRAN-CAT-CD        TO TRAN-CAT-CD
    ... (12 MOVEs: DALYTRAN-* onto TRAN-*) ...
    MOVE DALYTRAN-ORIG-TS       TO TRAN-ORIG-TS
    PERFORM Z-GET-DB2-FORMAT-TIMESTAMP
    MOVE DB2-FORMAT-TS          TO TRAN-PROC-TS
    PERFORM 2700-UPDATE-TCATBAL
    PERFORM 2800-UPDATE-ACCOUNT-REC
    PERFORM 2900-WRITE-TRANSACTION-FILE
    EXIT.
```

How to read it:

1. **The 12 MOVEs are ONE rule, not twelve.** They map every daily-transaction field
   (`DALYTRAN-*`) onto the posted-transaction record (`TRAN-*`). Fold them:
   > RULE-001 — Posting maps each daily-transaction field onto the posted-transaction
   > record (id, type, category, source, description, amount, merchant id/name/city/zip,
   > card number, original timestamp), then stamps the processing timestamp from the DB2
   > timestamp format. legacy_components: [CBTRN02C/2000-POST-TRANSACTION]

2. **`MOVE DB2-FORMAT-TS TO TRAN-PROC-TS` after `PERFORM Z-GET-DB2-FORMAT-TIMESTAMP`** is a
   *derived* value (a computed timestamp), so it earns its own clause in the rule.

3. **The three `PERFORM`s are the orchestration.** Posting = update the transaction-category
   balance, then update the account record, then write the transaction. Capture the
   **order** (TCATBAL → ACCOUNT → TRANSACTION) — it is the posting contract. Each performed
   paragraph is a separate node with its own rules (decompose M:N):

   - `2800-UPDATE-ACCOUNT-REC` (verbatim): `ADD DALYTRAN-AMT TO ACCT-CURR-BAL`, then
     `IF DALYTRAN-AMT >= 0 ADD ... TO ACCT-CURR-CYC-CREDIT ELSE ADD ... TO ACCT-CURR-CYC-DEBIT`,
     then `REWRITE ... INVALID KEY MOVE 109 TO WS-VALIDATION-FAIL-REASON`. That is:
     > RULE — Posting adds the transaction amount to the account current balance; a
     > non-negative amount accrues to the cycle credit, a negative amount to the cycle
     > debit; the updated account record is rewritten in place.
     > **parity_rule** on ACCT-CURR-BAL / ACCT-CURR-CYC-CREDIT / ACCT-CURR-CYC-DEBIT
     > (all `PIC S9(10)V99`, signed, 2-scale, to the cent).
     > **ERR** — account record not found on rewrite → fail reason 109.

   - `2900-WRITE-TRANSACTION-FILE` (verbatim): `WRITE FD-TRANFILE-REC FROM TRAN-RECORD`,
     `IF TRANFILE-STATUS = '00' MOVE 0 TO APPL-RESULT ELSE MOVE 12`, then
     `IF APPL-AOK CONTINUE ELSE DISPLAY 'ERROR WRITING...' PERFORM 9999-ABEND-PROGRAM`. That is
     a data-access RULE (writes the posted transaction) **plus** an ERR — on non-`'00'` file
     status the program ABENDs (uses the level-88 `APPL-AOK`, value 0, as the success gate).

This one paragraph yields ~4 RULE-, ~3 parity_rule, ~2 ERR- items across its node and its
children — that is correct granularity. A single "posts a transaction" rule would be a
placeholder (Universal Don't: no bare nodes).

---

## Reading-order checklist for a COBOL node

1. `wicked_estate source <para>` — read the **body**, not the name. Names lie (carddemo has
   `MAIN-PARA` ×21); the SymbolId-keyed annotation binds the exact scoped one.
2. Resolve every numeric output's PIC from its copybook/working-storage definition → parity.
3. Map each EVALUATE/WHEN and IF/ELSE branch to its own rule; capture level-88 names as the
   condition vocabulary.
4. Capture every INVALID KEY / SQLCODE-nonzero / ABEND path as an ERR- with its literal code.
5. End RESOLVED (≥ threshold, every behavior covered) or RISK-flag with a reason
   (guardrail a/f). A god-program below threshold is a flag, not a guess.
