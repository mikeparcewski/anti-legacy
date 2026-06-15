# Tier 3 language reference — JCL

Load this when the node under crawl is a JCL job, a `step` node, or a `dataset` node
(`language: jcl` / kind `{"other":"step"}`). JCL is not business logic — it is the
**batch orchestration and data-binding layer**. The business rules live in the COBOL the
job runs; JCL tells you *which capability runs*, *in what order*, *over which datasets*,
and *under what success conditions*.

Read it through the engine's real edges, not by re-parsing: an `EXEC PGM=` resolves to a
`calls` / cross-language edge into the COBOL program (wicked-estate resolves these
automatically — §3). Use `wicked_estate context <step>` to see the bound datasets and the
program it invokes.

All examples are verbatim from carddemo (`app/jcl/*.jcl`).

---

## Idiom → rule map

| JCL idiom | What it means | Rule shape |
|---|---|---|
| `//STEPnn EXEC PGM=<program>` | a batch step runs a program — the **capability entry point** | a RULE binding this step to the capability the program implements ("the daily-posting batch runs CBTRN02C"). The program's own rules carry the logic; the step contributes *invocation + position in the job* |
| `//STEPnn EXEC <proc>` | runs a cataloged procedure (a reusable step bundle) | a RULE naming the proc; expand it if the proc source is indexed |
| `//ddname DD DSN=...,DISP=...` | binds a logical file name (the `ddname` the COBOL `SELECT ... ASSIGN`s to) to a physical dataset | a data-binding RULE: `ddname` ↔ dataset ↔ the COBOL FD. This is the seam that tells you which physical file a COBOL `READ <ddname>` touches |
| `DISP=SHR` / `DISP=OLD` / `DISP=(NEW,CATLG,DELETE)` | dataset disposition — input (shared/exclusive) vs newly created output | input vs output classification on the data-binding rule; `(NEW,CATLG,DELETE)` = a step *produces* this dataset |
| `DSN=...(+1)` | next generation of a GDG (generation data group) — versioned output | the step appends a new generation; note it (the target equivalent is an append/version, not an overwrite) |
| `EXEC PGM=...,COND=(code,op[,stepname])` | conditional execution — **skip this step if** the prior return code satisfies `code op RC` | a control-flow RULE on the job: step gating. `COND=(4,LT)` = skip if 4 < RC (i.e. run only when RC ≤ 4); `COND=(0,NE)` = skip if 0 ≠ RC (run only when the prior step ended RC=0) |
| `IF (RC ... ) THEN / ENDIF` (JCL IF) | newer conditional construct, same intent as COND | same control-flow RULE — step gating on prior return codes |
| `//SYSIN DD *` ... `/*` | inline control input (e.g. IDCAMS/SORT statements) | the inline stream is the utility's parameters — capture it as the data/transform spec for that step |
| `PGM=IDCAMS` / `IEBGENER` / `SORT`/`ICETOOL` | z/OS utility programs (VSAM define/copy, generic copy, sort) | a data-management RULE (define/load/copy/sort a dataset); not custom business logic, but it shapes the data the business steps consume |
| `//STEPLIB DD DSN=...LOADLIB` | where the executable load modules are found | environment binding — note it for the build/deploy spec, not a business rule |

---

## EXEC PGM — the capability entry point

A job's `EXEC PGM=` statements are the **traceability bridge from batch to behavior**. The
step node carries the position; the program node carries the rules. Always link them so a
swarm agent can trace `task → req → step → program → paragraph → source`.

Verbatim — `POSTTRAN.jcl` (the daily-posting job that runs the CBTRN02C example from
`cobol.md`):

```
//* Process and load daily transaction file and create transaction
//* category balance and update transaction master vsam
//STEP15  EXEC PGM=CBTRN02C
//STEPLIB  DD DISP=SHR,DSN=AWS.M2.CARDDEMO.LOADLIB
//SYSPRINT DD SYSOUT=*
//TRANFILE DD DISP=SHR,DSN=AWS.M2.CARDDEMO.TRANSACT.VSAM.KSDS
//DALYTRAN DD DISP=SHR,DSN=AWS.M2.CARDDEMO.DALYTRAN.PS
//XREFFILE DD DISP=SHR,DSN=AWS.M2.CARDDEMO.CARDXREF.VSAM.KSDS
//DALYREJS DD DISP=(NEW,CATLG,DELETE),...,DSN=AWS.M2.CARDDEMO.DALYREJS(+1)
//ACCTFILE DD DISP=SHR,DSN=AWS.M2.CARDDEMO.ACCTDATA.VSAM.KSDS
//TCATBALF DD DISP=SHR,DSN=AWS.M2.CARDDEMO.TCATBALF.VSAM.KSDS
```

How to read it:

1. **The capability:** `STEP15 EXEC PGM=CBTRN02C` is the daily transaction-posting batch.
   The rules are CBTRN02C's (`2000-POST-TRANSACTION` et al, see `cobol.md`). The step's own
   rule:
   > RULE — The daily-posting batch (POSTTRAN/STEP15) executes the transaction-posting
   > program over the day's transactions. legacy_components: [POSTTRAN.jcl/STEP15, CBTRN02C]

2. **The DD bindings are the data contract** — each `ddname` is exactly the logical name the
   COBOL FD reads/writes. This tells you the *inputs* and *outputs* of the capability:
   - inputs (`DISP=SHR`): `DALYTRAN` (daily transactions, the PS sequential input),
     `XREFFILE` (card→account cross-reference), `ACCTFILE` (account master), `TCATBALF`
     (transaction-category balances).
   - in/out (`DISP=SHR`, updated in place): `TRANFILE` (transaction master VSAM KSDS),
     plus `ACCTFILE`/`TCATBALF` are rewritten by the posting logic.
   - output (`DISP=(NEW,CATLG,DELETE)` + `(+1)`): `DALYREJS` — a **new GDG generation** of
     rejected transactions. That `(NEW,CATLG,DELETE)` + `(+1)` tells you posting *produces*
     a rejects file each run.
   > RULE — Posting consumes daily transactions, the card cross-reference, the account
   > master, and category balances; updates the transaction master, account master, and
   > category balances in place; and emits a new generation of rejected transactions.

   This is the I/O spec the target service must honor (which datasets in, which out).

3. **No COND on STEP15** → unconditional. Where COND appears, it gates the step (below).

---

## COND — step gating (job control flow)

`COND=(code,op)` is *backwards* from intuition: it says **bypass the step when `code op RC`
is TRUE** (RC = the highest prior return code). Translate to the positive condition for the
rule.

Verbatim from carddemo:

```
//STEP10 EXEC PGM=IDCAMS,COND=(4,LT)      (TRANBKP.jcl)
//STEP20 EXEC PGM=IEBGENER,COND=(0,NE)    (DEFGDGD.jcl)
//STEP30 EXEC PGM=IDCAMS,COND=(0,NE)
```

Read them as the *run* condition:

- `COND=(4,LT)` → "bypass if 4 < RC" → **run only when RC ≤ 4** (run unless a prior step
  failed harder than a warning). RULE: the backup step runs only when prior steps ended with
  at most a warning.
- `COND=(0,NE)` → "bypass if 0 ≠ RC" → **run only when RC = 0** (run only on clean success
  of all prior steps). RULE: this define/load step runs only when every prior step succeeded.

These are control-flow rules on the *job*, not data rules. In the target they become
pipeline step-gating / fail-fast conditions. Capture them so the batch orchestration parity
is preserved — silently dropping a `COND` changes when a step runs.

---

## Reading-order checklist for a JCL node

1. List the steps in order — the job is a sequence; order is a rule.
2. For each step: resolve `EXEC PGM=` to its program node (the `calls` edge) and link them.
3. Map every `DD` to its dataset + DISP → inputs vs outputs of the step's capability.
4. Capture every `COND=` / JCL `IF` as a step-gating control-flow rule (translate to the
   positive run-condition).
5. The business logic is in the COBOL — do not invent rules from JCL alone. JCL rules are
   orchestration + data binding. End RESOLVED or RISK-flag (e.g. a proc whose source is not
   indexed → flag "proc PROCxxx not in tree").
