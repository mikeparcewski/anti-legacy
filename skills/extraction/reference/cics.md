# Tier 3 language reference — CICS

Load this when the node under crawl is a `cics_program`, a `cics_map`, or a COBOL program
whose body contains `EXEC CICS ... END-EXEC`. CICS is the **online (interactive)
transaction layer** — the mainframe equivalent of a web request handler. A CICS program
serves a screen, reads the user's input from the terminal, does work, and returns control,
all under a **pseudo-conversational** model.

The business logic between the `EXEC CICS` calls is ordinary COBOL — read it with
`cobol.md`. This file covers the CICS-specific idioms: screen I/O, program transfer, CICS
file/data access, and the pseudo-conversational control flow that the target service must
preserve as request/response + session state.

All examples are verbatim from carddemo (`COSGN00C` signon, `COACTVWC` account-view,
`COACTUPC` account-update; the `cics_program` nodes `COSGN00C`, `COADM01C`, `COMEN01C`).

---

## The pseudo-conversational model (read this first)

A CICS transaction does **not** hold the terminal across a user keystroke. It runs a short
burst, `SEND`s a screen, then `RETURN`s control to CICS with a transaction id and a saved
state block (`COMMAREA`). When the user presses Enter/a PF key, CICS re-invokes the program;
the program `RECEIVE`s the input and inspects the saved `COMMAREA` to know "where it was."

So one CICS program runs **many times per logical conversation**, branching on a state flag
in the COMMAREA. This is the single most important thing to capture: the **state machine**.
In the target it becomes a stateless request handler + an explicit session/state object —
the COMMAREA *is* the session state, and the `EVALUATE`/level-88 on the entry state is the
router. Read `cobol.md` for how the EVALUATE branches become one rule each.

---

## Idiom → rule map

| EXEC CICS idiom | What it means | Rule shape |
|---|---|---|
| `EXEC CICS RECEIVE MAP('m') MAPSET('ms') RESP(rc)` | read the user's input from screen map `m` into the program's input fields | a RULE: receives screen input (input-binding); `RESP` non-normal = an ERR- (map fail / no data) |
| `EXEC CICS SEND MAP('m') MAPSET('ms') FROM(...) ERASE CURSOR` | paint screen map `m` with output fields | a RULE: presents screen `m` with the named fields; `ERASE`/`CURSOR`/`FREEKB` are presentation attributes |
| `EXEC CICS SEND TEXT FROM(msg) ...` | send a plain-text message (no map) — usually an error/info line | a RULE: emits message text (often an ERR- surface for a validation failure) |
| `EXEC CICS RETURN TRANSID(t) COMMAREA(c) LENGTH(...)` | end this burst; **next** input re-invokes transaction `t` with state `c` | the pseudo-conversational hand-off RULE — names the next transaction and the saved state. This is the state-machine edge |
| `EXEC CICS RETURN` (no TRANSID) | end the transaction entirely — control back to CICS / caller | terminal RULE: conversation/transaction ends |
| `EXEC CICS XCTL PROGRAM(p) COMMAREA(c)` | transfer control to program `p` (no return) — like a redirect | a RULE: navigates to capability `p`, passing state `c`. `p` is a separate node (`calls`/transfer edge) |
| `EXEC CICS LINK PROGRAM(p) COMMAREA(c)` | call program `p` and **return** here (like a subroutine) | a RULE: invokes sub-capability `p` and resumes |
| `EXEC CICS READ DATASET('f') RIDFLD(k) INTO(rec) KEYLENGTH(...) RESP(rc)` | keyed VSAM read via CICS file control | a data-access RULE (reads `f` by key); `RESP` = NOTFND → ERR- |
| `EXEC CICS WRITE / REWRITE / DELETE DATASET('f') ...` | CICS file-control persistence | a data-access RULE (insert/update/delete); non-normal `RESP` → ERR- |
| `EXEC CICS STARTBR / READNEXT / ENDBR` | browse a VSAM file (cursor over a key range) | a RULE describing the browse/iteration + its key range |
| `EXEC CICS ASSIGN APPLID(...) / SYSID(...)` | fetch environment attributes (region id, system id) | usually environment/header population — a thin RULE or fold into the header rule |
| `EXEC CICS SYNCPOINT` / `SYNCPOINT ROLLBACK` | commit / roll back the unit of work | a transaction-boundary RULE; ROLLBACK on error is an ERR- / compensation path |
| `EXEC CICS ABEND ABCODE(...)` | abnormally terminate the transaction | an ERR- error path with the literal abend code |
| `RESP(rc) RESP2(rc2)` + `IF rc = DFHRESP(NORMAL)` | the CICS response-code check after a command | turns each command into success-path + ERR- (NOTFND, MAPFAIL, etc.) — capture the non-NORMAL branches |

---

## Worked example — COSGN00C (signon), pseudo-conversational round trip

Verbatim `EXEC CICS` calls from `COSGN00C.cbl` (the signon `cics_program`):

```
*  re-arm: end this burst, come back as the same transaction with saved state
EXEC CICS RETURN
          TRANSID (WS-TRANID)
          COMMAREA (CARDDEMO-COMMAREA)
          LENGTH(LENGTH OF CARDDEMO-COMMAREA)
END-EXEC.

*  read the signon screen the user just filled in
EXEC CICS RECEIVE
          MAP('COSGN0A')  MAPSET('COSGN00')
          RESP(WS-RESP-CD) RESP2(WS-REAS-CD)
END-EXEC.

*  paint the signon screen
EXEC CICS SEND
          MAP('COSGN0A')  MAPSET('COSGN00')
          FROM(COSGN0AO)  ERASE  CURSOR
END-EXEC.

*  send a plain message line (e.g. "Invalid credentials")
EXEC CICS SEND TEXT
          FROM(WS-MESSAGE) LENGTH(LENGTH OF WS-MESSAGE)
          ERASE  FREEKB
END-EXEC.
```

How to read it as rules:

1. **The conversation loop** (the `RETURN TRANSID ... COMMAREA`): on each burst the signon
   transaction re-arms itself, carrying `CARDDEMO-COMMAREA` as the saved state.
   > RULE — Signon is pseudo-conversational: after presenting the screen it returns control
   > re-armed to the same transaction, carrying the CARDDEMO commarea as session state.

2. **RECEIVE → validate → branch** (the COBOL between calls): receives the user id/password
   from map `COSGN0A`, validates them (read with `cobol.md` — the validation IF/level-88
   logic), and on success transfers onward.
   > RULE — Signon receives the user id and password from the signon screen and validates
   > the credentials.
   > ERR — invalid credentials → emits a message line via SEND TEXT (and re-presents the
   > screen).

3. **SEND MAP / SEND TEXT** are the presentation surface — one rule for "presents the signon
   screen", one ERR-surface for "emits the error message line". `ERASE`/`CURSOR`/`FREEKB`
   are attributes, not separate rules.

4. **On success** the program `XCTL`s to the main menu program (carddemo navigation pattern,
   verbatim from COACTVWC):

   ```
   EXEC CICS XCTL PROGRAM(CDEMO-TO-PROGRAM) COMMAREA(CARDDEMO-COMMAREA) END-EXEC
   ```
   > RULE — On successful signon, control transfers to the target program named in the
   > commarea, passing the session state. (CDEMO-TO-PROGRAM is set by the routing EVALUATE.)

---

## CICS file access — COACTVWC (account view), keyed reads

Verbatim from `COACTVWC.cbl` — three keyed reads that compose the account-view capability:

```
EXEC CICS READ DATASET(LIT-CARDXREFNAME-ACCT-PATH)   *> card→account cross-ref
     RIDFLD(WS-CARD-RID-ACCT-ID-X) KEYLENGTH(...)
     INTO(CARD-XREF-RECORD) LENGTH(...) END-EXEC

EXEC CICS READ DATASET(LIT-ACCTFILENAME)             *> account master
     RIDFLD(WS-CARD-RID-ACCT-ID-X) ... INTO(ACCOUNT-RECORD) END-EXEC

EXEC CICS READ DATASET(LIT-CUSTFILENAME)             *> customer master
     RIDFLD(WS-CARD-RID-CUST-ID-X) ... INTO(CUSTOMER-RECORD) END-EXEC
```

> RULE — Account-view reads, by key: the card cross-reference (to resolve the account id),
> then the account master, then the customer master — assembling the full account-and-holder
> view. legacy_components: [COACTVWC/<read paragraphs>].
> ERR — any RESP ≠ NORMAL (NOTFND) on a keyed read → "record not found", screen message.

Note these are `EXEC CICS READ` (file control), **not** `EXEC SQL` — same intent (keyed
lookup), different engine. A program may mix both; classify each by its verb. The dataset
literals (`LIT-ACCTFILENAME` etc.) resolve to the same VSAM files JCL binds via DD names —
that is the online/batch shared-data seam.

---

## Reading-order checklist for a CICS node

1. **Find the state machine first.** Locate the entry `EVALUATE`/level-88 on the COMMAREA
   state (the router); each branch is a rule (`cobol.md`). The COMMAREA layout = the session
   state contract.
2. Map each screen `cics_map` (`SEND`/`RECEIVE MAP(...)`) to a present/receive rule — these
   become the target's view/form contract.
3. Capture every `XCTL`/`LINK` as a navigation/sub-capability edge to another node.
4. Treat each `EXEC CICS READ/WRITE/REWRITE/DELETE` as a data-access rule; capture every
   non-`DFHRESP(NORMAL)` (`NOTFND`, `MAPFAIL`, `DUPREC`) as an ERR-.
5. Numeric fields shown/updated on screen still get parity rules (`cobol.md` Parity) — a
   balance on a map is the same `PIC S9(10)V99`, not free text.
6. `SYNCPOINT`/`ROLLBACK`/`ABEND` are transaction-boundary and error paths — never normal
   flow. End RESOLVED or RISK-flag (e.g. a map whose `cics_map` node is not indexed → flag).
