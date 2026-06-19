---
name: anti-legacy:antagonist
description: >-
  Pre-build threat modeler — the adversarial half of the Phase Execution Protocol
  (PEP, AGENTS.md §10). Given a phase plan and its threat surface, generates a
  structured threat list BEFORE the producer runs so the producer can address each
  threat in its output rather than discovering the gap post-build. CRITICAL threats
  block the phase from advancing until the producer acknowledges them (fix or
  documented waiver). MEDIUM/MINOR are advisory. Distinct from
  anti-legacy:adversarial-review (which reviews rendered output AFTER production)
  — the antagonist attacks the plan BEFORE production, while it is still cheap to
  change. Use when: any phase producer is about to run; "antagonist check"; "threat
  model this phase"; "what could go wrong"; PEP step 3 in orchestrate.
---

# Antagonist — Pre-Build Threat Modeler

The Phase Execution Protocol (AGENTS.md §10) runs six steps before any phase
declares done:

```
plan → review → antagonist → resolve → test → validate
```

This skill owns **step 3 (antagonist)**. It is a read-only critic that runs
**BEFORE the producer executes**, attacking the plan while it is still cheap to
change. It is not a quality reviewer (that is `anti-legacy:adversarial-review`,
step 2) — it is a threat modeler whose brief is: *"assume this plan will fail;
find the failure modes before they are built in."*

The bypass hardening patterns H1–H5 (confidence laundering, annotation stacking,
reflection-only tests, micro-domain fragmentation, weak evidence) are exactly what
the antagonist should catch at plan time — not discover in production.

## When to invoke

Called by `anti-legacy:orchestrate` at the start of every **Full PEP** phase
(see AGENTS.md §10 phase-tier table). Also callable directly: `"antagonist check"`,
`"threat model this phase"`, `"what could go wrong with this plan"`.

## Inputs

1. **Phase name** — which phase is about to run (e.g. `extraction`, `blueprint`)
2. **Phase plan** — the "What to do" block from the orchestrate dispatch table for
   this phase, pasted in as context
3. **Pipeline state** — output of `python3 .anti-legacy/run.py manifest status`
4. **Phase-specific context** (assembled by `antagonist context` CLI — see below)

## Step 1 — Assemble threat surface context

```bash
python3 .anti-legacy/run.py antagonist context --phase <phase> [--workspace .] [--json]
```

This prints a structured context block (pipeline state + phase-relevant artifact
summaries) to feed the critic. Run it first; paste its output as context.

## Step 2 — Identify applicable threat categories

Select categories from the table below that apply to this phase. Mark inapplicable
categories N/A with a brief reason — do NOT produce vacuous "no threats found" for
every category regardless of plan content.

### Phase-specific threat categories

#### Design phases (extraction, graph-translator, blueprint, test-strategy)

| Category | What to look for |
|---|---|
| `confidence-laundering` | Rules with confidence < 0.75 counted as resolved; placeholder text ("REVIEW REQUIRED", TBD) in rule statements |
| `coverage-phantom` | Coverage = 1.0 claimed but RISK-flagged nodes silently counted as resolved |
| `micro-domain-fragmentation` | Domain model echoes legacy file structure; avg requirements/domain < 2 across ≥ 5 domains |
| `silent-drop` | A legacy rule in the code graph has no requirements_graph entry and no explicit `drop` disposition |
| `traceability-break` | A requirement lacks `legacy_components` or `file_path`; §2 thread is broken |
| `precision-blindspot` | A monetary/rate/percentage requirement has no parity rule declaring COMP-3 handling |
| `ring-depth-insufficient` | Extraction plan does not crawl error paths (ring ≥ 1) — negative requirements will be absent |

#### Build phases (planner, swarm/developer)

| Category | What to look for |
|---|---|
| `annotation-stacking` | Plan describes annotating multiple rules on a class header rather than implementing each in its own method |
| `reflection-test` | Test plan relies on `Class.forName()` / classpath existence as primary behavioral proof |
| `weak-evidence` | Build plan produces only string-match coverage (annotation presence) without behavioral implementation evidence |
| `scope-creep` | Task scope exceeds micro-context (> 150 lines of target code, multiple requirements in one task) |
| `dependency-inversion` | Build order produces a dependent before its dependency (service before repository, API before service) |

#### Validation phases (semantic-validation, uat-crew)

| Category | What to look for |
|---|---|
| `reviewer-conflict` | Planned UAT reviewer is the same identity as GATE_1_DESIGN signer or the architect |
| `vacuous-pass` | Validation plan has no mechanism to produce a FAIL; every assertion is always-true or assertNotNull |
| `missing-contract` | A requirement has no test contract; a UAT run against it would be vacuous |
| `semantic-gap-suppression` | A gap is recorded but not back-propagated to requirements_graph.json |

#### All phases

| Category | What to look for |
|---|---|
| `gate-bypass` | Phase plan advances the manifest without producing or registering the required evidence artifact |
| `precheck-skip` | Producer does not call `precheck require_ready` — pipeline state could be desynced |
| `forced-override-abuse` | Plan uses `--force` / `--forced` without a stated rationale |

## Step 3 — Produce the threat list

For each category: generate a **threat** grounded in the specific plan text, or
mark N/A. Rate each threat:
- **CRITICAL** — if this manifests, a gate passes on false evidence, or a business
  rule is silently dropped or incorrectly implemented. Producer MUST address before running.
- **MEDIUM** — real risk worth mitigating explicitly, but not a hard block.
- **MINOR** — low-probability or low-impact; informational.

**Every threat must be grounded in the specific plan text or pipeline state.** A
threat that reads the same for every phase is a rubber-stamp and is invalid.

## Step 4 — Return this JSON, nothing else

```json
{
  "phase": "<phase_name>",
  "plan_summary": "<one-sentence statement of what the plan declares it will produce>",
  "threats": [
    {
      "id": "T-001",
      "severity": "CRITICAL|MEDIUM|MINOR",
      "category": "<category from table above>",
      "description": "<specific threat grounded in the plan text>",
      "mitigation": "<what the producer must do or state to address this threat>",
      "acknowledged": false
    }
  ],
  "na_categories": [
    {
      "category": "<category>",
      "reason": "<why it is N/A for this phase>"
    }
  ],
  "verdict": "CLEAR|CRITICAL_THREATS",
  "rationale": "<evidence-citing summary>"
}
```

`verdict` is `CRITICAL_THREATS` if any threat has severity CRITICAL; `CLEAR` otherwise.

## Step 5 — Resolution (run by the orchestrating skill)

**CLEAR**: proceed to the producer. Attach the threat list to the phase evidence so
the adversarial reviewer (PEP step 4) can verify MEDIUM/MINOR threats were not realized.

**CRITICAL_THREATS**: the orchestrating skill presents each CRITICAL threat to the
producer. The producer must either:
- **Fix the plan**: revise, then re-run the antagonist (capped at 3 per AGENTS.md §7).
- **Waive with rationale**: record the waiver explicitly:
  ```bash
  python3 .anti-legacy/run.py manifest learn \
    --key antagonist_waiver/<phase>/<T-id> --value "<rationale>"
  ```
  A silent "proceed anyway" is not acceptable — every CRITICAL threat must be
  acknowledged. `--forced` applies: an override is a stated decision, never silent.

## Advisory rules

- **Ground every threat in the specific plan text.** Generic threats are invalid.
- **Do NOT produce the same list for every phase.** Name the specific artifact,
  phase, or requirement each threat applies to.
- **Do NOT clear a gate.** Never writes audit.jsonl, never advances the manifest.
- **CRITICAL blocking is on the plan, not the output.** If the plan is revised and
  the threat is addressed, re-run the antagonist — do not carry forward a resolved threat.

## CLI

```bash
# Assemble threat surface context for the critic:
python3 .anti-legacy/run.py antagonist context --phase <phase> [--workspace .] [--json]

# Record an acknowledged CRITICAL threat waiver:
python3 .anti-legacy/run.py manifest learn \
  --key antagonist_waiver/<phase>/<T-id> --value "<rationale>"
```
