# GATING_REMEDIATION — build spec (increment 1)

> Implements the remedy from `GATING_REVIEW.md` using the liftable patterns in
> `MIGRATION_FACTORY_MINING.md`. **Increment 1** is the highest-leverage coherent slice:
> an execution-time **readiness gate (`precheck`)** + **signal-completeness (C2)** + **wiring
> producers to refuse** (closes ROOT A, the C1/C2 consequences, and partially ROOT B). Larger
> structural bets are designed-and-deferred to increment 2. No commit until the user says go.

## Ways-of-working compliance (from recon C)

- **Test-first** (`develop-plugin` Step 3): regression tests written with the code (`tests/test_precheck.py`, a C2 case).
- **unittest, hermetic**: mirror `tests/test_manifest.py` — `tempfile.mkdtemp` + `cwd=tmpdir` + `PYTHONPATH=core_parent` + invoke via `python -m antilegacy_core.precheck`; the `graph_normalizer` lesson (cwd=tmpdir so ambient `config.json` can't contaminate) applies.
- **Placement/dispatch**: `antilegacy_core/precheck.py` with `main()` → `run.py precheck`. Reuses `manifest.load_manifest` / `file_checksum` / `_artifact_full_path` + constants `GATE_PHASE_PRECONDITIONS` / `GATE_PRODUCING_PHASE` / `_SATISFIED_GATE_STATUSES` / `PHASE_ENUM`.
- **Don'ts**: `precheck` is **read-only** — it does NOT write `audit.jsonl`, does NOT `advance`, does NOT clear gates. §6 done/still-not-done in its report. Cross-platform stdlib + `os.path`.
- **Commit**: `develop-plugin` Step 6 says commit; the user's standing instruction is *don't commit until we test*. **Resolution: do Steps 1–5, hold the commit, report readiness.** (Surfaced, not silently skipped.)
- **git-brain learning step** (`develop-plugin` Step 1): the "learnings" here are the two committed-in-spirit review docs (`GATING_REVIEW.md`, `MIGRATION_FACTORY_MINING.md`), not a workspace `git_brain` search — this is a source-tree change, not a workspace run.

---

## 1. `antilegacy_core/precheck.py` — the readiness gate

**CLI:** `python3 .anti-legacy/run.py precheck <phase> [--advisory] [--json]`
- default = **blocking**: exit `1` if not ready (the producer gate), `0` if ready, `2` on bad arg.
- `--advisory`: always exit `0`, just print the report (the MF `engine-scan` "exit-code flips by caller" pattern — reporting vs gating).
- `--json`: machine-readable report (for producers / CI).

**Probe data model** (one row per check):
```
{ id, category, ok: bool, severity: "block"|"warn", detail, fix }
```
`category ∈ {gate, artifact, completeness, reconcile}`. A phase is **READY** iff no `block`-severity probe failed. `warn` failures never block (advisory signals, e.g. shallow extraction).

**Per-phase registry** `PHASE_READINESS: {phase: {required_gates, required_artifacts, predicates, reconcile}}` — extensible; phases not in the registry get the **generic fallback** (required input artifacts present + integrity-verified). Concrete entries for the load-bearing phases:

| phase | required_gates (cleared) | required_artifacts (present + checksum-verified) | completeness predicates (C1/C2) | reconcile (ROOT B) |
|---|---|---|---|---|
| `graph-translate` | — | `legacy-graph`, `coverage-report` | front-half `coverage == 1.0`; every annotation that resolved a behavior node carries a numeric confidence | `legacy-graph` digest present + matches; `graphs/*.db` present OR digest is the committed seam |
| `blueprint` | GATE_1_DESIGN¹ | `requirements-graph` | graph has ≥1 active requirement; **every active requirement's `business_rules` carry a numeric `confidence`** (C2); no active requirement with empty `business_rules` | `requirements-graph` not orphaned from `legacy-graph` (its source digest present + unchanged) |
| **`deliverables`** | — (runs pre-GATE_1) | `requirements-graph` | same C2 rule-confidence check; `coverage == 1.0` if a `coverage-report` is registered | `requirements-graph` ↔ `legacy-graph` reconcile |
| `document` | GATE_4_UAT | `requirements-graph`, `blueprint-json` | target build exists (target_path non-empty) | derived docs' inputs present + verified |

¹ blueprint runs after review-packet/GATE_1 in the sequence; precheck asserts the *upstream* gate is cleared as an entry-readiness check (distinct from `manifest advance`'s exit-gating).

**Reconcile = the ROOT B probe (the part neither repo had):** for each registered upstream artifact a phase depends on, assert the file exists AND (if a checksum was recorded) recompute and compare — catching the "derived `requirements_graph.json` outlived its gitignored `legacy-graph` evidence" case. Reuses `manifest.file_checksum` + `_artifact_full_path` (same predicate as `manifest check`), extended with the **dependency-freshness** angle: a derived artifact whose declared `depends_on` source is missing/changed ⇒ `block` "stale/orphaned — re-run <producing phase>".

**Output:** `cmd_check`-style — print each failing probe with its `fix`, then the verdict line; `READY`/`NOT READY (<n> blockers)`. §6: the report names what's verified, what's blocking, and the exact next action.

---

## 2. C2 — signal completeness (surface, never silently skip)

Two coordinated changes (the data model already carries `confidence`; the gap is *consumers tolerate its absence*):

1. **`precheck` completeness predicate** — `block` when any active requirement's `business_rules` lacks a numeric `confidence` (a rule you can't score is a rule you can't gate on). This is the entry-gate version of the fix.
2. **`risk_log.py` — stop the silent skip.** Today `mine_low_confidence` (`risk_log.py:138-142`) `continue`s past a rule whose `confidence` isn't a number. Change: emit a distinct risk row — category **"Rule missing confidence (un-scoreable)"**, source = `req_id`/`RULE-id` — instead of skipping. The signal becomes visible, per §6.

*Not changing the schema's `required` set this increment* (would break existing graphs); the fix is to **surface** the gap at the gate + in the risk log, which is the honest, non-breaking move. (Schema tightening is a deferred option, see §5.)

Advisory (warn, not block): a behavior-bearing requirement with **zero** `validations` AND zero `error_paths` ⇒ `warn` "extraction may be shallow (ring[0]) — no exception/validation behavior captured." This makes the "ran extraction at ring[0]" symptom visible without hard-blocking.

---

## 3. Producer wiring (A3 — refuse, don't fabricate; closes C3)

The 9 deliverable producers + `document` are the freshest ROOT A offenders (they render whatever exists). Wire them to **consult `precheck` and refuse**:

- **Shared helper** `deliverables.require_ready(phase, force=False)` in `antilegacy_core/deliverables.py`: runs the precheck probes for `phase`; if blocked and not `force`, prints the blockers + exits non-zero; if `force`, prints a **loud** warning and proceeds. (One import + one call per producer — minimal coupling, uniform behavior.)
- Each deliverable leaf script (`prd`, `diagrams`, `test_plan`, `test_scripts`, `migration_plan`, `risk_log`, `decisions_log`, `evidence_log`) calls `D.require_ready("deliverables", force=args.force)` at the top of `main()`, gaining a `--force` flag. `evidence_log` is exempt from the C2/coverage predicates (it reports *on* state, so it must run even on an incomplete pipeline) — it reconcile-checks only the manifest's presence.
- The **umbrella** `deliverables` SKILL.md Step 1 becomes: `run.py precheck deliverables` and STOP on non-zero (the human-facing gate); `--force` documented as the explicit, loud escape.
- `document.py` calls the same gate for phase `document` at the top of `synthesize()`.

**Hard-block default + loud `--force` escape** resolves the `GATING_REVIEW` open question: hard-block the automated readiness gate; `--force` is the explicit override, never absence-of-evidence.

---

## 4. Test plan (functional, thorough — test-first)

`tests/test_precheck.py` (unittest, hermetic — `tmpdir` + `cwd` + `-m antilegacy_core.precheck`):
- **blocks** at `phase=uninitialized` / no `requirements-graph` → exit 1, names the missing artifact.
- **passes** when graph present + complete + gates cleared → exit 0.
- **reconcile / ROOT B**: register `requirements-graph` with a `depends_on: legacy-graph`; delete/alter the `legacy-graph` file → precheck `block` "orphaned/stale" (the exact GATING_REVIEW scenario).
- **C2**: a graph with a `business_rules` entry lacking `confidence` → precheck `block`; and `risk_log` emits a "missing confidence" row (not a skip).
- **`--advisory`** exits 0 with the same report; **`--json`** is parseable.
- **producer refusal**: a deliverable leaf script exits non-zero when precheck blocks, `0` (with warning) under `--force`.
- **shallow-extraction warn**: requirement with no validations/error_paths → `warn`, not `block`.
- Full suite (`python3 -m unittest discover -s tests`) stays green; install-level run of the wired deliverables via `run.py` from a real install.

---

## 5. Designed-and-deferred (increment 2 — NOT built now)

- **A1 full `loopStage`** (make→gate→review→refine→escalate *inside* each producer) — increment 1 gives the *gate* (`precheck`); the bounded make/review/refine loop is the larger port.
- **B1 differential-equivalence gate** for numeric requirements (golden-master capture/replay/diff) — the highest-value new capability, but a contained project of its own.
- **A7 deterministic control driver** (`run.py orchestrate-step`) so sequencing leaves the agent's head — the structural root fix; precheck is the primitive it would call.
- **B2 model-tier routing**; **schema tightening** to make `confidence` `required` (breaking — needs a migration).

These are real and sequenced; increment 1 stops the bleeding (producers can no longer render an incomplete/orphaned pipeline) without the larger rewrites.

---

## Still-not-done after increment 1 (§6, stated up front)

- `precheck` covers the load-bearing phases + a generic fallback; it does **not** yet have bespoke predicates for every one of the 21 phases.
- It mitigates ROOT B (detects orphaned/stale derived artifacts) but does not *fix* the root cause — the evidence spine is still gitignored; the durable fix (snapshot/commit a graph digest, or persist the DBs) is a separate decision.
- Human gates remain human; precheck never clears them.
- The producer gate is opt-in per script via `require_ready`; the *phase* skills (survey/extraction/etc.) are not yet wired (deliverables + document first, as the freshest offenders).
