---
name: "anti-legacy:deliverables"
description: >
  Render the full stakeholder DELIVERABLES package once the requirements graph is ready —
  product requirements (PRD), architecture diagrams, detailed test strategy, functional test
  scripts, end-to-end migration plan, and the living risk / decisions / evidence logs — into
  .anti-legacy/deliverables/, then compile an index. Register-only; does not advance the
  pipeline. Use when: "produce the deliverables", "generate all the deliverables",
  "the deliverables package", "PRD + diagrams + plan + risk/decision logs".
---

# anti-legacy:deliverables

The umbrella that produces the **human-facing deliverables package** from the pipeline's
structured artifacts. It runs when **the graph is ready** (after `graph-translate`, ideally
with `blueprint` + `test-strategy` available) and writes everything under
`.anti-legacy/deliverables/`.

This is **not** the `review-packet` (that is the single offline GATE_1 review document). These
are the detailed, individually-consumable deliverables a delivery team needs:

| Deliverable | Skill | Output |
|---|---|---|
| Product Requirements (PRD) | `anti-legacy:prd` | `deliverables/product-requirements.md` |
| Architecture Diagrams (Mermaid) | `anti-legacy:diagrams` | `deliverables/diagrams/*.mmd` + index |
| Test Strategy (data-parity / UAT / E2E / API) | `anti-legacy:test-plan` | `deliverables/test-strategy.md` |
| Functional Test Scripts | `anti-legacy:test-scripts` | `deliverables/tests/{data-parity,uat,e2e,api}/…` |
| Migration Plan (epics→subtasks) + Jira CSV | `anti-legacy:migration-plan` | `deliverables/migration-plan.md` + `.jira.csv` |
| Risk Log *(living)* | `anti-legacy:risk-log` | `deliverables/risk-log.md` |
| Decisions Log / ADRs *(living)* | `anti-legacy:decisions-log` | `deliverables/decisions-log.md` |
| Evidence Log with receipts *(living)* | `anti-legacy:evidence-log` | `deliverables/evidence-log.md` |

Each deliverable **registers** its artifact (`deliverable-*`) in the manifest and **never
advances the phase** — phase advancement stays owned by the phase skills. The *living*
deliverables (risk / decisions / evidence) reflect current state, so **re-run them at each
gate**.

## Cross-Platform Notes

Every command runs through the dispatcher (`python3 .anti-legacy/run.py <stem>`), which is
pure Python — identical on macOS, Linux, WSL, and Windows.

## When it runs / prerequisites

- **Required**: `.anti-legacy/requirements/requirements_graph.json` (produced by
  `anti-legacy:graph-translator`). Without it the renders refuse to write hollow output.
- **Enriches when present** (degrade gracefully when absent, naming the gap): `blueprint.json`
  (diagrams, test-plan, migration-plan), `contracts/` (test-plan, test-scripts), the manifest +
  `audit.jsonl` (decisions-log, evidence-log), `coverage-report.json` + `annotations.jsonl`
  (risk-log).
- Natural home in the pipeline: the **`review-packet` stage** (graph + blueprint + test-strategy
  ready, feeding GATE_1). Also runnable standalone anytime the graph exists.

## Step 1: Verify the graph is ready

```bash
python3 .anti-legacy/run.py manifest status
```

Confirm `.anti-legacy/requirements/requirements_graph.json` exists. If it does not, stop and run
`anti-legacy:graph-translator` first — the deliverables render FROM the graph.

Then run the **readiness gate** — the authoritative precondition. Unlike a file-exists check it
refuses on an incomplete or state-desynced pipeline (a confidence-less rule, coverage < 1.0, or
an orphaned requirements-graph whose `legacy-graph` evidence is gone — ROOT A/B):

```bash
python3 .anti-legacy/run.py precheck deliverables
```

If it exits non-zero, STOP and resolve the named blockers. The Tier-A renders below call this
same gate internally and will refuse on their own; `--force` is the explicit, loud override
(only for a deliberate partial/preview run). The living logs (`risk_log`, `decisions_log`,
`evidence_log`) are intentionally **not** gated — they must run on an incomplete pipeline to
SURFACE its gaps.

## Step 2: Render the deliverables

Run each render through the dispatcher, in this order. A render that must degrade (e.g.
`test-scripts` on a stack it cannot script yet, or any Tier-A render with no blueprint) prints a
note and still exits 0 — **do not abort the suite on a soft note**; collect it for the report.

```bash
python3 .anti-legacy/run.py prd
python3 .anti-legacy/run.py diagrams
python3 .anti-legacy/run.py test_plan
python3 .anti-legacy/run.py test_scripts
python3 .anti-legacy/run.py migration_plan
python3 .anti-legacy/run.py risk_log
python3 .anti-legacy/run.py decisions_log
python3 .anti-legacy/run.py evidence_log
```

A hard non-zero exit means a true precondition failure (no graph, or no manifest for the
evidence log) — surface it and stop; do not paper over it.

**Optional — richer output via parallel subagents.** The renders above are deterministic and
fast. Two deliverables benefit from agent reasoning on top of the deterministic skeleton: the
**PRD** (narrative framing of each requirement) and the **functional test scripts** (concrete
assertions fleshed out from each contract scenario). When you want that, dispatch
`anti-legacy:prd` and `anti-legacy:test-scripts` as subagents — they have no ordering
dependency on each other, so run them **in parallel**. The `decisions-log` step also wants a
git-brain dump first — see `anti-legacy:decisions-log` for the `git_brain list/read` step that
feeds its `--git-brain` input.

## Step 3: Compile the index

```bash
python3 .anti-legacy/run.py deliverables_index
```

This writes + registers `.anti-legacy/deliverables/README.md` — the package table of contents:
each deliverable's path, status, produced-at, and a present/absent receipt, plus the canonical
expected set so anything **not yet produced is named, not silently absent**.

## Step 4: Adversarial review (advisory — not a gate)

The renders are deterministic and *trusting* — they lay out what the data says. Before sharing
the package, run the **adversarial review** that distrusts them: it dispatches one read-only
critic subagent **per deliverable, in parallel**, to challenge each rendered file against the
source data it was rendered from — hunting unsupported/optimistic claims, dropped §2
traceability, empty/weak sections, missing parity rules on numeric outputs, "done" that hides a
gap, and divergence from the requirements graph.

Run `anti-legacy:adversarial-review` (see that skill for the critic micro-context and the
parallel-dispatch protocol). It assembles the worklist via:

```bash
python3 .anti-legacy/run.py deliverable_review_worklist --json
```

then dispatches the critics and returns a structured per-deliverable verdict
(`findings[]` with severity → **PASS / REVISE / BLOCK**) plus an aggregated package verdict.

**This is advisory adversarial review, NOT a gate.** It clears no gate, advances no phase, and
registers no artifact. On `REVISE`/`BLOCK`, re-run the named producing deliverable to fix the
render at its source and re-review, or proceed with an explicit, stated `--force` reason
(mirroring the precheck override — never a silent skip). Collect the verdicts for the report
in Step 5.

## Step 5: Done-gate, then report

**Done-gate (BLOCKING).** Assert the index exists and is non-empty and at least one deliverable
was produced:

```bash
python3 -c "import os,sys; \
idx='.anti-legacy/deliverables/README.md'; \
ok=os.path.isfile(idx) and os.path.getsize(idx)>0; \
sys.stderr.write('' if ok else 'deliverables done-gate FAILED: index missing/empty\n'); \
sys.exit(0 if ok else 1)"
```

If it fails, surface which renders errored and stop. This skill **never** calls `manifest
advance` — it registers artifacts only.

Report to the user:
- Deliverables package: `.anti-legacy/deliverables/` (open `README.md` for the index)
- **Produced N / 9** and any deliverable that degraded (which input it was missing)
- **Adversarial review verdict** (Step 4): the package verdict (PASS / REVISE / BLOCK) and any
  CRITICAL/MAJOR findings per deliverable — advisory, cleared no gate
- Reminder: re-run `risk-log`, `decisions-log`, `evidence-log` at each gate to refresh the living
  deliverables

## Output

- `.anti-legacy/deliverables/` — the full package (PRD, diagrams/, test-strategy, tests/,
  migration-plan + Jira CSV, risk/decisions/evidence logs)
- `.anti-legacy/deliverables/README.md` — the registered index (`deliverables-index`)
- Manifest: `deliverable-*` artifacts registered; **phase unchanged**
- Adversarial-review verdicts (`anti-legacy:adversarial-review`, Step 4) — advisory, no artifact

**Next step**: address any REVISE/BLOCK from the adversarial review (re-run the named producing
deliverable, then re-review), then share the package via git/fileshare and feed it into the
`review-packet` / GATE_1 design review. Re-run the living logs at later gates.
