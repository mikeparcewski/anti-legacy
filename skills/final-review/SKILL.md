---
name: "anti-legacy:final-review"
description: >
  Final completeness-review swarm (B1b). Scans the BUILT target app for mocked /
  half-done / incomplete work across four dimensions — CODE, DOCS, CONFIG, BUILD —
  with one parallel reviewer subagent per dimension. Runs the deterministic
  completeness_scanner, reasons over its findings, and emits
  evidence/completeness-report.json (status PASS|FAIL; FAIL on any HIGH finding).
  Runs LAST, after docs + functional tests exist, so it reviews those too. On FAIL
  it kicks back to the phase that owns the gap. Clears GATE_5_COMPLETENESS only on PASS.
  Use when: "final review", "completeness review", "is the build actually done",
  "scan for stubs / mocks / TODOs in the target", "did we ship any half-done work".
---

# anti-legacy:final-review

The last review pass before sign-off. Earlier gates prove the target COMPILES
(GATE_3_BUILD), is SEMANTICALLY faithful to the legacy rules (GATE_3B_SEMANTIC),
and passes UAT (GATE_4_UAT). None of them ask the blunt question this phase asks:

> **Did we actually finish, or did a swarm agent leave a stub behind?**

A method that compiles and passes a thin test can still be a `return null;`
placeholder. A README can ship with an empty "## Setup" section. A `.env` can
carry `DB_PASSWORD=changeme`. A pom can quietly `<skipTests>true</skipTests>`.
This phase hunts all four classes of half-done work across the whole target tree
— **including the docs and tests the pipeline itself just produced** — which is
exactly why it runs LAST.

It is a *completeness* review, not a *correctness* review: it does not re-judge
business logic (that is GATE_3B / UAT). It asks whether every surface the
deliverable exposes is real, filled-in, and shippable.

## Where it sits

```
... → target-review (GATE_3_BUILD)
    → semantic-validation (GATE_3B_SEMANTIC)
    → uat-crew (GATE_4_UAT)
    → final-review (GATE_5_COMPLETENESS)   ← you are here, LAST
    → complete
```

Running last is load-bearing: by now the docs exist, the functional/UAT tests
exist, and the config is whatever the build settled on — so all four dimensions
have something real to review.

## The four dimensions

| Dimension | What it hunts |
|---|---|
| **CODE** | `TODO`/`FIXME`/`XXX`/`HACK` markers; `stub`/`mock`/`placeholder` called out in comments; trivially-short method bodies that just `return null/0/""/empty`; `throw new UnsupportedOperationException`, `NotImplementedError`, `panic("TODO")`, `todo!()`/`unimplemented!()`. |
| **DOCS** | Empty or `TODO`/`TBD` doc sections; a README with no setup/run instructions (no setup heading, no run command). |
| **CONFIG** | Hardcoded test values (localhost, `h2:mem`, `test_user`); placeholder env vars (`changeme`, `your-…-here`, `<…>`); empty values for sensitive keys (password/secret/token/url). |
| **BUILD** | Skipped/disabled tests (`@Disabled`, `@Ignore`, `it.skip`, `t.Skip`, `@pytest.mark.skip`, `[Ignore]`); a build configured to skip tests (`<skipTests>true`, `-DskipTests`, `test.enabled = false`); commented-out build/test steps. |

Severity drives the gate: a finding is **HIGH** when it would ship broken
behavior (a real-source stub, a skipped test, a missing README, a placeholder
secret), **MEDIUM** for review-worthy-but-not-blocking smells (a TODO inside a
test file, an empty doc section, a hardcoded test value in non-sensitive config),
**LOW** for placeholders in files that are *meant* to carry them (`.env.example`).

**The scanner sets `status: FAIL` if ANY finding is HIGH.** GATE_5_COMPLETENESS
clears only on `status: PASS`.

## Prerequisites

- GATE_4_UAT is `passed`/`waived` (UAT ran, so its tests exist to be reviewed).
- The target tree exists at `target_path` from `.anti-legacy/config.json`.

Confirm the upstream gate before reviewing:

```bash
python3 -c "
import json, sys
m = json.load(open('.anti-legacy/manifest.json'))
g = m['gates'].get('GATE_4_UAT', {})
if g.get('status') not in ('passed', 'waived'):
    print('BLOCKED: GATE_4_UAT is %s. Run anti-legacy:uat-crew first.' % g.get('status'))
    sys.exit(1)
print('GATE_4_UAT: cleared - final completeness review may proceed.')
"
```

## Step 1: Resolve the target tree

```bash
python3 -c "import json; c=json.load(open('.anti-legacy/config.json')); print(c['target_path'])"
```

Use that path as `{target_path}` below (the scanner defaults to it when
`--workspace` is omitted, but pass it explicitly so the reviewers agree).

## Step 2: Dispatch the parallel-reviewer swarm (one reviewer per dimension)

This is a **swarm**, not a single linear scan: dispatch four reviewer subagents
**in parallel**, one per dimension. Each reviewer runs the deterministic scanner
restricted to its dimension, then *reasons* over the raw findings — confirming
real gaps, demoting obvious false positives, and naming the owning phase for each
confirmed HIGH. Micro-context per §5: a reviewer gets ONLY its dimension's
findings + the target slice, not the whole report.

Dispatch all four at once (do not serialize them):

```
@reviewer (CODE)   — run: python3 .anti-legacy/run.py completeness_scanner \
                       --workspace {target_path} --dimension CODE \
                       --output .anti-legacy/evidence/completeness-CODE.json
   Then read the findings and for each HIGH confirm it is a real stub (not a
   legitimate empty-collection return that the contract actually wants). Name the
   owning phase: source stubs → `build` (re-dispatch swarm for that node).

@reviewer (DOCS)   — run: ... --dimension DOCS  --output .../completeness-DOCS.json
   Confirm empty/TODO sections and missing setup/run steps are real gaps in a
   shippable doc. Owning phase for doc gaps → `build` (the doc-authoring task).

@reviewer (CONFIG) — run: ... --dimension CONFIG --output .../completeness-CONFIG.json
   Confirm placeholder/empty/hardcoded-test values. Distinguish a real `.env`
   from an `.env.example`. Owning phase → `build` (the config task) or
   `blueprint` if the deployment shape itself is wrong.

@reviewer (BUILD)  — run: ... --dimension BUILD  --output .../completeness-BUILD.json
   Confirm skipped/disabled tests and skip-tests build flags. Owning phase →
   `build` (re-enable the test) — a disabled test is a hole in the proof, not a
   passing test.
```

Each reviewer is read-only over the target: it never edits the target tree, it
reports. The fix happens on kick-back (Step 5), in the owning phase.

## Step 3: Produce the consolidated report

After the reviewers finish, run the scanner once over ALL four dimensions to
write the single authoritative evidence envelope the gate consumes:

```bash
python3 .anti-legacy/run.py completeness_scanner \
  --workspace {target_path} \
  --output .anti-legacy/evidence/completeness-report.json
```

The script exits **0** on PASS and **non-zero** on FAIL (any HIGH finding), so
the orchestrator can branch on the exit code directly.

## Step 4: Read the report

```bash
python3 -c "
import json
r = json.load(open('.anti-legacy/evidence/completeness-report.json'))
c = r['counts']; d = r['dimension_counts']
print('status :', r['status'])
print('counts : HIGH=%d MEDIUM=%d LOW=%d' % (c['HIGH'], c['MEDIUM'], c['LOW']))
print('by dim : CODE=%d DOCS=%d CONFIG=%d BUILD=%d' % (d['CODE'], d['DOCS'], d['CONFIG'], d['BUILD']))
for f in r['findings']:
    if f['severity'] == 'HIGH':
        loc = '%s:%s' % (f['path'], f['line']) if f.get('line') else f['path']
        print('  HIGH [%s] %s - %s' % (f['dimension'], loc, f['what']))
"
```

### Report schema — `evidence/completeness-report.json`

```json
{
  "status": "PASS | FAIL",
  "scanned_root": "<abs path of the target tree>",
  "generated_at": "<iso8601>",
  "dimensions": ["BUILD", "CODE", "CONFIG", "DOCS"],
  "counts": { "HIGH": 0, "MEDIUM": 0, "LOW": 0 },
  "dimension_counts": { "CODE": 0, "DOCS": 0, "CONFIG": 0, "BUILD": 0 },
  "findings": [
    { "dimension": "CODE", "path": "<rel path>", "line": 42,
      "severity": "HIGH", "what": "trivial method body returns a no-op value: ..." }
  ]
}
```

`status` is `FAIL` iff `counts.HIGH > 0`. `path` is relative to `scanned_root`.
`line` may be `null` for whole-file findings (e.g. a README with no setup steps).

## Step 5: Branch on status

### FAIL — kick back to the owning phase

Do **NOT** clear GATE_5_COMPLETENESS. For each confirmed HIGH finding, the owning
reviewer named the phase that produced the gap; kick back there via the
generalized kick-back, then re-run final-review from Step 1 once the gap closes:

- **CODE / BUILD** HIGH (source stub, disabled test, skip-tests flag) →
  re-dispatch `anti-legacy:swarm` for the named node(s) to replace the stub /
  re-enable the test, then re-run `anti-legacy:target-review` so the round-trip
  re-proves coverage.
- **DOCS** HIGH (missing setup/run, empty section) → re-run the doc-authoring
  task in `build` for that file.
- **CONFIG** HIGH (placeholder/empty secret, hardcoded test datasource) → fix the
  config task in `build`; if the deployment shape itself is wrong, kick back to
  `anti-legacy:blueprint`.

The kick-back is targeted and idempotent — only the named node/file is
re-touched, the scan re-runs, and the gate is re-presented. Do not restart the
pipeline. (Like GATE_1's targeted re-run, a FAIL that names the wrong node just
re-touches that node; it is not a full rebuild.)

Surface the gap plainly per the Voice contract — name the file and line:

> "final-review FAILED: PaymentService.java:7 returns Collections.emptyList()
> (CODE/HIGH); PaymentServiceTest.java:5 @Disabled (BUILD/HIGH); README.md has no
> setup/run steps (DOCS/HIGH). Kicking back PaymentService to swarm, re-enabling
> the test, and routing the README to doc-authoring. Not clearing GATE_5."

### PASS — register evidence and clear GATE_5_COMPLETENESS

Only when `status: PASS` (zero HIGH findings). Register the artifact, then record
the gate. GATE_5_COMPLETENESS auto-clears on the PASS evidence — it is not a
human gate — but a registered evidence id is mandatory:

```bash
python3 .anti-legacy/run.py manifest register completeness-report \
  --path evidence/completeness-report.json \
  --format json \
  --produced-by anti-legacy:final-review \
  --status final

python3 .anti-legacy/run.py manifest gate GATE_5_COMPLETENESS \
  --opinion passed \
  --evaluator "anti-legacy:final-review" \
  --rationale "Completeness scan PASS: zero HIGH findings across CODE/DOCS/CONFIG/BUILD." \
  --evidence "completeness-report"

git add .anti-legacy/ && git commit -m "gate: GATE_5_COMPLETENESS cleared by anti-legacy:final-review"
```

Store the result in git-brain so future runs learn the patterns that recur:

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "Final completeness review [{project_name}]: PASS. Zero HIGH stubs/mocks/skips across CODE/DOCS/CONFIG/BUILD." \
  --tags "final-review,completeness,{target_stack}" \
  --category learnings
```

## Step 6: Advance

Only after GATE_5_COMPLETENESS is `passed`/`waived`:

```bash
python3 .anti-legacy/run.py manifest advance final-review
python3 .anti-legacy/run.py learn_coordinator --phase final-review
```

## Done-gate (BLOCKING)

Before recording GATE_5_COMPLETENESS, assert the report exists and is clean. If
this exits non-zero, do NOT run the `gate`/`register --status final`/`advance`
commands — kick back per Step 5 instead:

```bash
python3 -c "
import json, sys
r = json.load(open('.anti-legacy/evidence/completeness-report.json'))
ok = r.get('status') == 'PASS' and r.get('counts', {}).get('HIGH', 1) == 0
sys.stdout.write('OK\n' if ok else 'BLOCKED: status=%s HIGH=%s\n' % (r.get('status'), r.get('counts', {}).get('HIGH')))
sys.exit(0 if ok else 1)
"
```

## Cross-Platform Notes

The scanner is pure Python (no shell-isms, `os.path` throughout, UTF-8 reads with
`errors='ignore'`) and works identically on macOS/Linux/WSL/Windows. It needs no
build toolchain — it reads text, it does not compile. It prunes build-output and
vendored directories (`target/`, `build/`, `dist/`, `node_modules/`, `vendor/`,
`.venv/`, …) so it reviews authored source, never generated artifacts.

## Output

- `.anti-legacy/evidence/completeness-report.json` — the consolidated evidence envelope (status PASS|FAIL, findings[])
- `.anti-legacy/evidence/completeness-{CODE,DOCS,CONFIG,BUILD}.json` — the per-reviewer slices (optional, from Step 2)
- GATE_5_COMPLETENESS: auto-cleared ONLY on `status: PASS` (zero HIGH findings)
- Manifest: phase = `final-review`, artifact `completeness-report` registered

**Next step**: If GATE_5_COMPLETENESS cleared → `anti-legacy:deploy` (or `manifest advance` to `complete`). On FAIL → kick back to the owning phase (Step 5), then re-run final-review.
