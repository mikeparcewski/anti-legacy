---
name: "anti-legacy:adversarial-review"
description: >
  Universal adversarial review of ANY generated / AI-derived output — a READ-ONLY critic that
  challenges a produced artifact against the SOURCE DATA it was derived from, INDIVIDUALLY (one
  artifact) or in PARALLEL batch (e.g. every rendered deliverable). It hunts unsupported/optimistic
  claims, dropped §2 traceability (req_id→legacy_components→rule), empty/weak sections, missing
  parity rules on numeric outputs, "done" that hides gaps, and divergence from the requirements
  graph. Targets: deliverables (.anti-legacy/deliverables/), skill-forge's generated build skills
  (.anti-legacy/generated-skills/), and any single produced artifact. Returns a structured verdict
  (findings[] with severity → PASS / REVISE / BLOCK). Advisory: it never clears a gate. The
  pre-build analog of anti-legacy:uat-reviewer (which critiques built code).
  Use when: "adversarial review", "critique this output", "review the deliverables", "review the
  generated build skills", "challenge the PRD / risk log / plan", "is this output honest".
---

# anti-legacy:adversarial-review

A **universal adversarial review pass** over *any* generated / AI-derived output. Every producer
in this pipeline emits artifacts FROM structured data; this skill dispatches a **read-only critic
subagent** to try to *break* one — to find the claim the data does not support, the requirement
whose traceability thread snapped, the section that is empty, the numeric output with no parity
rule, the "complete" that is hiding a gap. Run it **individually** on a single output (point the
critic at the artifact + the data it came from), or in **parallel batch** over a producer's
outputs. The renders are deterministic and trusting; this is the loop that distrusts them
(`make → adversarial-review → refine`, per MIGRATION_FACTORY_MINING). It is the pre-build analog
of `anti-legacy:uat-reviewer` (which critiques *built* code) — the same independent read-only
critic contract, applied to pre-build artifacts.

**Targets** (any one, individually, or a batch): rendered deliverables (`.anti-legacy/deliverables/`,
via the `deliverable_review_worklist` batch helper), skill-forge's generated build skills
(`.anti-legacy/generated-skills/build-<domain>/SKILL.md`), and any other produced artifact (a
blueprint, a requirements graph, a doc) — point the critic at the file + its source data.

The mental model: a deliverable is rendered FROM structured data (the requirements graph,
blueprint, contracts, coverage, manifest/audit). Its only legitimate content is content that
data supports. A critic reads BOTH — the rendered file and its source data — and reports
every place the deliverable says more than, less than, or other than what the data says.

**This is advisory, NOT a gate.** It clears no gate, advances no phase, registers no artifact.
A `REVISE`/`BLOCK` verdict is a finding the human/orchestrator acts on (re-run the producing
deliverable, or `--force` past it for a deliberate partial), exactly like the `precheck`
contract the renders already obey. It never synthesizes a human gate approval (AGENTS.md
Universal Don'ts).

## Cross-Platform Notes

The one command (`deliverable_review_worklist`) is pure standard-library Python through the
dispatcher — `os.path`, no shell-isms, identical on macOS / Linux / WSL / Windows. Subagent
dispatch uses the host-integrated agent runtime.

## When it runs / prerequisites

- **After** `anti-legacy:deliverables` has rendered ≥1 deliverable into
  `.anti-legacy/deliverables/` and compiled the index. With no rendered deliverable on disk
  there is nothing to review — the worklist exits non-zero and names the gap.
- It reads only what exists; a deliverable that degraded (missing input) is still reviewed —
  the critic checks whether it *honestly named* the gap (§6) rather than papering over it.

## Two modes

- **Batch (deliverables):** review every rendered deliverable at once.
  `deliverable_review_worklist` assembles the worklist (Step 1); `--deliverable <id>`
  restricts to one deliverable.
- **Single artifact — ANY producer's output (ISS-12):** review one arbitrary registered
  artifact — `requirements-graph`, `blueprint-json`, `task-plan`, a generated build skill,
  a target doc. `refine_loop descriptor --artifact <id>` resolves its rendered file + the
  source data the critic must cross-check (the requirements graph §2 spine + the artifact's
  manifest `depends_on` edges). This is how **every producer self-reviews its output** at its
  done-gate — the universal application of *"adversarial review for all outputs, even
  individually."* Dispatch the SAME critic (Step 2) against that one descriptor.

## Step 1: Assemble the critic worklist

```bash
python3 .anti-legacy/run.py deliverable_review_worklist --json
```

This emits one entry per canonical deliverable: its `rendered_path` (the file the critic
reviews), `present` (is the file actually on disk), `producing_skill` (what to re-run on
REVISE/BLOCK), `living` (re-rendered each gate), and **`source_data`** — the workspace-relative
files the critic must cross-check the deliverable against (only files that exist; a critic is
never sent a dead path). Exit 0 = ≥1 reviewable; exit 1 = nothing rendered yet (run
`anti-legacy:deliverables` first); exit 2 = no manifest (run `anti-legacy:setup`).

A row that is `registered` but not `present` is a **registered-but-missing-file** gap (the
render's file vanished) — surface it; do not dispatch a critic against a missing file.

## Step 2: Dispatch one read-only critic per deliverable — IN PARALLEL

The critics are independent — each judges a different file against a different source set —
so dispatch them **in a single batch (parallel)**, not one at a time. For each worklist entry
with `present: true`, dispatch a critic with this micro-context (the rendered file + its
`source_data` + the deliverable's identity — NOT the whole workspace, §5):

```
anti-legacy:adversarial-review (critic)

## Adversarial review — {label} ({artifact_id})

You are a READ-ONLY adversarial critic. You have a Read tool ONLY — you cannot edit,
write, or run anything. You did not produce this deliverable and you owe it no benefit
of the doubt. Your job is to BREAK it: find every claim its source data does not support.

### Rendered deliverable to challenge
{rendered_path}        ← read this in full

### Source data (ground truth — read these; the deliverable may not exceed them)
{source_data list}     ← the requirements graph is the §2 traceability spine

### What to hunt — the REQUIRED checklist (tick ALL SIX, every artifact)
These six finding classes are a MANDATORY checklist, not a menu. For EVERY artifact you
MUST consciously cover all six and record the per-class result in the `checklist` object of
your return JSON (each class → `covered: true` + `result: "clean"` if nothing found, or
`"finding"` if you raised ≥1 finding of that class). "Covered" means you actively looked for
that class against the source data — not that you found something. A class is `"clean"` only
after you looked and the data supports the deliverable on that axis; never leave a class
`covered: false` (an unexamined class is a coverage gap, not a pass). The six classes:

1. UNSUPPORTED / OPTIMISTIC CLAIM — a statement the source data does not back
   (e.g. "all rules resolved" when coverage-report shows risk_flagged > 0; a confidence
   asserted that no rule carries; a "low risk" with no basis).
2. BROKEN §2 TRACEABILITY — a requirement-bearing line that does not trace
   req_id → legacy_components → business_rule id(s); a req_id, RULE-/VAL-/ERR- id, or
   legacy_component cited in the deliverable that is ABSENT from the requirements graph
   (divergence), or present in the graph (active, not dropped) but DROPPED from the deliverable.
3. EMPTY / WEAK SECTION — a heading with no content, a placeholder, a table with only a
   header row, "TBD"/"N/A" where the data exists to fill it.
4. MISSING PARITY RULE ON A NUMERIC OUTPUT — a money / rate / percentage / count
   requirement with no parity rule (COMP-3 precision loss is silent and catastrophic).
5. "DONE" THAT HIDES A GAP — a clean/complete framing that omits a RISK-flagged node, a
   low-confidence rule, a dropped requirement, an unresolved item, or a no-receipt phase
   that the source data shows. A deliverable that CANNOT be complete must say what is NOT
   yet covered; silence on a known gap is a finding (§6).
6. DIVERGENCE FROM THE REQUIREMENTS GRAPH — counts, domain/requirement names, statuses,
   or dispositions in the deliverable that contradict the graph.

### Severity (assign one per finding)
- CRITICAL — the deliverable is materially WRONG or would mislead a stakeholder into a
  bad decision (fabricated claim, dropped traceability on an active requirement, missing
  parity rule on a money output, a hidden gap presented as done).
- MAJOR — a real defect that needs fixing before sign-off (weak/empty required section,
  a count that diverges from the graph, a cited id absent from the graph).
- MINOR — quality/clarity nit that does not mislead (wording, ordering, a cosmetic gap).

### Verdict rules
- ANY CRITICAL finding → verdict = BLOCK.
- ANY MAJOR finding (no CRITICAL) → verdict = REVISE.
- MINOR only, or zero findings → verdict = PASS (note the minors).

### Anti-rubber-stamp (a verdict that violates these is INVALID)
- You MUST have read the rendered file AND at least one source-data file before judging.
- Every finding MUST quote or cite the specific line/section in the rendered file AND the
  specific source fact it contradicts (a req_id, RULE-id, coverage number, count).
- `rationale` MUST reference specific evidence — a generic "looks good"/"reads well" is not
  a valid PASS rationale.
- The `checklist` object MUST be present and carry ALL SIX classes, each `covered: true`. A
  verdict that omits the `checklist`, omits any of the six classes, or leaves any class
  `covered: false` is INVALID and is NOT a pass — it is an incomplete review (re-run it). A
  PASS is only legitimate when every one of the six classes was covered and came back
  `"clean"`; every class marked `"finding"` MUST have a matching entry in `findings[]`.

### Return this JSON, nothing else
```json
{
  "artifact_id": "{artifact_id}",
  "deliverable": "{label}",
  "rendered_path": "{rendered_path}",
  "verdict": "PASS" | "REVISE" | "BLOCK",
  "checklist": {
    "unsupported-claim":    { "covered": true, "result": "clean" | "finding" },
    "broken-traceability":  { "covered": true, "result": "clean" | "finding" },
    "empty-section":        { "covered": true, "result": "clean" | "finding" },
    "missing-parity":       { "covered": true, "result": "clean" | "finding" },
    "hidden-gap":           { "covered": true, "result": "clean" | "finding" },
    "graph-divergence":     { "covered": true, "result": "clean" | "finding" }
  },
  "findings": [
    {
      "id": "DR-001",
      "severity": "CRITICAL|MAJOR|MINOR",
      "category": "unsupported-claim|broken-traceability|empty-section|missing-parity|hidden-gap|graph-divergence",
      "rendered_ref": "section/line in the deliverable",
      "source_contradiction": "the specific source fact it violates",
      "description": "..."
    }
  ],
  "rationale": "specific, evidence-citing summary"
}
```

The `checklist` is REQUIRED and carries all six classes (the keys above are exactly the
`category` enum values). Each class's `result` MUST agree with `findings[]`: a class marked
`"finding"` has ≥1 matching finding of that `category`; a class marked `"clean"` has none.
A verdict missing the `checklist`, missing any of the six classes, or leaving any class
`covered: false` is INVALID per the anti-rubber-stamp rules — re-run that critic.

For CLIs without `@agent` dispatch, run the critics inline — adopt the critic persona for
each deliverable in turn and produce the same JSON. Do not let inline mode collapse into a
single rubber-stamp; each deliverable gets its own honest pass.

## Step 3: Aggregate the verdicts and report

Collect the JSON verdicts. The package verdict is the worst single verdict:
**BLOCK** if any critic returned BLOCK, else **REVISE** if any returned REVISE, else **PASS**.

Report to the user (§6 — what is true, what is not, what is next):
- **Package verdict** (PASS / REVISE / BLOCK) and the per-deliverable verdict table.
- Every CRITICAL and MAJOR finding, each naming `artifact_id`, the `rendered_ref`, and the
  `source_contradiction` — the exact file/line and the source fact it broke.
- Any deliverable that was `registered` but **not present** (a render's file is missing).
- The explicit reminder that **this is advisory** — it cleared no gate and advanced no phase.

## Step 4: On REVISE / BLOCK — the bounded refine loop (ISS-8)

For each non-PASS artifact the finding names its `producing_skill`. The loop is
`make → adversarial-review → refine`, **bounded** by the `refine_loop` primitive so it can
never spin forever. After each critic verdict, ask the primitive what to do next:

```bash
python3 .anti-legacy/run.py refine_loop decide --verdict <PASS|REVISE|BLOCK> \
  --attempt <n> --artifact <id>     # n = the review attempt just completed (1-based)
```

It returns the next move (and a distinct exit code so orchestrate/CI can branch):

- **PASS → stop, converged** (exit 0). The output agrees with its source data; proceed.
- **REVISE/BLOCK, attempts remain → refine** (exit 3). Re-run the `producing_skill` so the
  output is corrected **at its source**, then re-review (single artifact:
  `refine_loop descriptor --artifact <id>` → re-dispatch the critic; deliverable:
  `deliverable_review_worklist --deliverable <id>`). Increment the attempt.
- **REVISE/BLOCK at the §7 cap (default 3) → stop, recommend recon** (exit 4). Three failed
  attempts is evidence the *model of the problem* is wrong, not the fix — send a read-only
  recon agent before a 4th try (AGENTS.md §7). Do NOT blind-retry past the cap.
- **Force (deliberate, loud):** `refine_loop decide --forced` stops past a non-PASS, but you
  MUST state the override in your report (mirrors `precheck --force` — never a silent skip).
  A `BLOCK` carried forward without a stated reason is a §6 violation.

This skill never edits an artifact itself, never runs `manifest gate`, and never runs
`manifest advance`. Correcting the output is the producing skill's job; clearing a gate is
the human's. `refine_loop` is likewise advisory — it computes the decision, it does not act.

## Done-gate

The review pass is "done" when **every `present` deliverable in the worklist has a critic
verdict** and the aggregated package verdict + all CRITICAL/MAJOR findings are reported. A
review that skipped a present deliverable, returned a verdict that read no source file, or
returned a verdict whose `checklist` is missing / incomplete / has any class `covered: false`
(all anti-rubber-stamp violations), is NOT done — re-dispatch the missing/invalid critic.
Surfacing a BLOCK is a *successful* review, not a failed one.

## Output

- No file artifact and no manifest change — this is an **advisory, read-only** pass.
- The structured per-deliverable verdicts + the aggregated package verdict, reported to the
  caller (and, if the caller persists them, written under `.anti-legacy/evidence/` — but this
  skill does not require or register that).

**Next step**: on PASS → share the package / feed it into the `review-packet` / GATE_1 design
review. On REVISE/BLOCK → re-run the named producing deliverable(s) and re-review, or force
with a stated reason.

## Failure cases

- **Nothing rendered yet** (`deliverable_review_worklist` exits 1): run
  `anti-legacy:deliverables` first — there is nothing to adversarially review.
- **No manifest** (exits 2): run `anti-legacy:setup`, then render the deliverables.
- **Registered-but-missing file**: the manifest claims a deliverable but its file is gone —
  re-run the producing skill to re-render it; do not dispatch a critic against a missing file.
- **A critic cannot read a source file**: report it as a MAJOR finding (the deliverable's
  traceability cannot be verified), not a silent PASS.
