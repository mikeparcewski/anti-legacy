# GATING_REVIEW — why the pipeline let an incomplete run produce confident deliverables

> **Review, not a fix.** Scratch/design doc (uncommitted), part of the design-doc lineage
> (`SCRIPT_REORG_SPEC.md`, `RESTRUCTURE_SPEC*.md`, `PANEL_REVIEW.md`, `DELIVERABLES_CONTRACT.md`).
> Extracts the underlying issues behind a real run where `document` / deliverable tools rendered
> a **plausible-but-incomplete** picture against a half-finished, state-desynced pipeline — and
> nothing stopped it. Remedy is sketched as *direction* only; no code changed here.

## What happened (the canary)

An operator bootstrapped a fresh manifest and immediately ran the documentation / deliverable
tools. The manifest said `phase: uninitialized`, `0` gates cleared; the wicked-estate evidence
DBs were absent; extraction had only reached `ring[0]` (no validations / error paths); multiple
parallel source apps had not merged. The tools ran anyway and produced output that is **accurate
to the current graph** — but the graph is a fraction of reality.

The operator called this "my mistake — I should have checked `manifest status` first." That is
true, **and it is the wrong place to put the blame.** A pipeline that calls itself *gated* must
not depend on the human remembering to check state. The fact that a human *could* run docs at
`phase: uninitialized`, and the docs *complied and looked right*, is the bug. **The mistake is
the proof the gate is missing**, not the root cause.

## How gating actually works today (grounded)

- `manifest.py` enforces gates in exactly **one** place: `cmd_advance` (`manifest.py:263`) refuses
  to *leave* a `gate-*` phase until its bound gate is `passed`/`waived` (`GATE_PHASE_PRECONDITIONS`).
- The `manifest` subcommands are `init / status / advance / register / gate / learn / check /
  audit-report`. There is **no** `can-run` / `precheck` — nothing a producer can call to ask
  "may I run at this phase?"
- A grep of all deliverable producers + `document` for any phase/gate precondition returns
  **zero enforcement** — every match is prose ("feeds GATE_4_UAT") or `evidence-log` *reporting*
  on gates. `document.py:5` says "runs AFTER GATE_4_UAT is cleared" — a **comment, not a check**.
- `manifest check` (`manifest.py:460`) verifies checksums of **registered artifacts** only — not
  phase-vs-reality, not evidence-spine presence, not completeness.

So gate enforcement exists **only** when you both use `orchestrate` *and* pass through `advance`.
Direct skill invocation — which the dispatch table actively invites ("Or run individual phases")
— bypasses all of it.

## The two co-equal root causes

### ROOT A — Gating is transition-time, not execution-time; producers trust their inputs blindly

The state machine gates *transitions between phases*, never *the execution of a producer*. And
every producer's done-gate asserts only **"I wrote a non-empty file"** — never "my inputs are
complete and my upstream gates are approved."

- *Evidence*: `manifest.py:263` is the sole gate check; no `can-run` subcommand; producers carry
  no phase/gate precondition; the deliverable done-gates (and `document`) check own-output only.
- *Why it matters*: garbage-in produces **confident** garbage-out. `document` at
  `phase: uninitialized` is not an error state to the tool — it has a graph file, so it renders.
- *Consequence*: out-of-order and premature execution is unguarded by construction.

### ROOT B — Manifest state desyncs from disk reality, and nothing reconciles or detects it

The manifest can claim one thing while the workspace is another, and no check closes the gap.

- *Evidence*: the evidence spine `.anti-legacy/graphs/*.db` is **gitignored / ephemeral**
  (`.gitignore:27`), rebuilt by survey — so the derived `requirements_graph.json` **outlives its
  source DBs**. A fresh manifest can be `init`-ed over a half-run workspace. `manifest check` only
  re-hashes registered artifacts; nothing ties `requirements_graph` → a *present, checksummed*
  `legacy-graph` → the manifest `phase`.
- *Why it matters*: consumers trust a manifest/graph that is lying about how complete or
  grounded it is. The graph-translator can't even re-derive once the DBs are gone.
- *Consequence*: "the graph" can be a stale or ungrounded shell, and every downstream tool treats
  it as authoritative.

## Consequences (real, but downstream of A + B)

| # | Issue | Evidence / why it's a consequence |
|---|---|---|
| **C1** | **"Ready/done" is undefined per phase.** "The graph is ready" = "a JSON with ≥1 requirement exists." No machine-checkable criteria for extraction depth, coverage terminal, populated validations/error_paths, blueprint/contracts presence. | the deliverables' precondition is literally that; coverage measures *resolved-or-flagged*, not richness/depth. Directly enables A (nothing to check) and the "thin extraction" symptom. |
| **C2** | **Rich signals aren't aggregated into a blocking verdict.** `confidence`, `ring_depth`, `source_kinds`, empty `error_paths`, multi-app-no-merge all exist (or absently-exist) but never roll up to "this graph is too thin to proceed." | `risk_log.py:139-140` silently skips a rule whose `confidence` is absent — and nothing checks that every rule *carries* one. |
| **C3** | **The deliverables suite widened A.** The 9 new skills are explicitly *register-only, never advance, precondition = own-output* — 9 more ways to render an incomplete/desynced pipeline as a polished artifact. | `DELIVERABLES_CONTRACT.md` §0/§9 ("register, not advance"); each skill's done-gate. **Owned: this is from the work just completed.** |

## Correction to the trace (for accuracy)

> "Low-conf rule not flagged — `risk_log.py` only reads a top-level `risk_flag` boolean."

The current `risk_log` **does** read rule-level confidence: `mine_low_confidence`
(`risk_log.py:133-158`) reads `rule.get("confidence")` and flags `< threshold`. The real hole is
C2, and it is worse than a boolean bug: a rule whose `confidence` is **absent/null is silently
skipped** (`risk_log.py:139-140` — `None` can't be compared), and nothing requires every rule to
carry a confidence. So the low-conf rule escaped because either (a) extraction never recorded a
confidence on it, or (b) `risk_log` was never run (pipeline stopped) — both downstream of A/B/C1.

## The reported symptoms, mapped

| Symptom | Traces to | Note |
|---|---|---|
| Ran docs on an incomplete pipeline | **ROOT A + ROOT B** | the canary; the system permitted it at `phase: uninitialized` |
| No validations / error paths | **C1** (extraction depth unenforced) + A | `ring[0]` produced thin rules; nothing flagged the systematic emptiness |
| Low-conf rule not flagged | **C2** | not a `risk_log` boolean bug — confidence absent or `risk_log` not run |
| No cross-app merging | capability gap **+ C2** | graph-translator merges only call-related capabilities; parallel APIs (e.g. Kafka/Pulsar) → 1:1 singletons. Needs human-authored unified design — **and** nothing flags "N source apps, 0 merges — confirm intended" |

## Remedy direction (NOT a spec, NOT implemented)

One missing primitive sits under all of ROOT A: **an execution-time readiness gate that producers
must consult and that can refuse.** Sketch only:

1. **`manifest precheck <phase|skill>`** — asserts (a) the required upstream gate(s) are
   `passed`/`waived`, (b) the required input artifacts exist *and* pass an integrity check, (c)
   per-phase machine-checkable completeness (C1) is met. Exits non-zero with the specific reason.
   Producers call it at start and **refuse** otherwise; `orchestrate` stops being the only enforcer.
2. **Phase-vs-reality reconcile** (ROOT B) — detect when the manifest's `phase`/artifacts diverge
   from disk (missing evidence spine, derived graph without a present checksummed `legacy-graph`,
   `init` over a populated workspace) and refuse or force re-sync.
3. **Per-phase completeness criteria** (C1) — each phase publishes a falsifiable "done" predicate
   (survey: DBs present + digest matches; extraction: coverage == 1.0 **and** ring depth
   sufficient **and** every rule carries a confidence **and** validations/error_paths populated
   where the source warrants; graph-translate: roundtrip == 1.0 **and** multi-source merge
   reviewed). These feed `precheck` and a single completeness verdict (C2).
4. **Multi-source merge review flag** — when >1 source app yields 0 merges, surface it for an
   explicit human keep/merge/author-unified-design decision instead of silently emitting singletons.

## Scope notes / open questions

- This review covers the **gating/enforcement** failure. The cross-app-merge *capability* (auto vs
  human-authored unified API design) is a separate design question, touched only where it
  intersects gating.
- Open: should `precheck` **hard-block** producers, or **warn-and-record** (advisory) with a
  `--force` escape? Hard-block is truer to "gated"; advisory is friendlier to partial/iterative
  runs. This is the central design decision for the remedy.
- Open: do we retrofit `precheck` into the existing phase skills + `document`, or only the new
  deliverables suite first? (The deliverables are the freshest offenders and the easiest pilot.)
- Not addressed here: whether any of this should change the committed-vs-ephemeral status of the
  evidence spine (ROOT B could also be mitigated by persisting a graph snapshot, not just the DB).
