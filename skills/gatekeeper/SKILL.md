---
name: "anti-legacy:gatekeeper"
description: >
  Enforce transition gates. Verify that required sign-offs and evidence exist before
  the pipeline can advance. Eight gates: GATE_0_DISCOVERY (automated survey integrity),
  GATE_1_DESIGN (design review), GATE_1B_SEMANTIC_JOIN (semantic-join review),
  GATE_2_PLAN (plan review), GATE_3_BUILD (automated build integrity + round-trip
  rule-coverage), GATE_3B_SEMANTIC (semantic validation review), GATE_4_UAT
  (UAT acceptance, reviewer-independence enforced), GATE_5_COMPLETENESS (automated
  final completeness gate). A `failed` opinion on any gate triggers a generalized,
  guided kick-back that rewinds the pipeline to the gate's producing phase.
  Blocks pipeline execution if any required gate is not cleared.
  Use when: "check gate status", "verify gate 1", "can we proceed to build",
  "has the design been signed off", "record a sign-off", "gate check".
---

# anti-legacy:gatekeeper

Enforces rigid phase transitions. Every gate must be explicitly cleared before
the pipeline can advance to the next phase. No gate = no progress.

## Cross-Platform Notes

All gate operations go through `manifest.py` — works on macOS, Linux, WSL,
and Windows Git Bash. Evidence verification uses `manifest.py check`.

## Parameters

- **gate_id** (required): one of `GATE_0_DISCOVERY`, `GATE_1_DESIGN`, `GATE_1B_SEMANTIC_JOIN`, `GATE_2_PLAN`, `GATE_3_BUILD`, `GATE_3B_SEMANTIC`, `GATE_4_UAT`, `GATE_5_COMPLETENESS`
- **action**: `check` (default) — verify gate status | `record` — record a sign-off | `status` — print all gates

## Gate Definitions

| Gate | When | Required Evidence | Who Signs |
|------|------|-------------------|-----------|
| `GATE_0_DISCOVERY` | After survey | project name + target stack + non-empty legacy imports + legacy-graph digest seam written | Automated — no human required |
| `GATE_1_DESIGN` | After review-packet | review-packet, requirements-graph, blueprint-json, roundtrip-coverage, legacy-graph digest not DRIFTED | Lead Architect + Tech Lead |
| `GATE_1B_SEMANTIC_JOIN`| After semantic-join | semantic_join_report.md + gate status passed | Lead Architect + Tech Lead |
| `GATE_2_PLAN` | After planner | task-plan, blueprint-json | PM + Tech Lead |
| `GATE_3_BUILD` | After target-review | build-integrity (PASS) + functional-comparison-report (0 FAIL, coverage>=1.0) | Automated — no human required |
| `GATE_3B_SEMANTIC`| After semantic-validation| semantic-validation-report | Lead Architect + Tech Lead |
| `GATE_4_UAT` | After uat-crew | uat-summary, uat-verdicts (both registered by uat-crew) | UAT Lead (independent of architect — HARD FAIL if same) |
| `GATE_5_COMPLETENESS` | After document, at `final-review` | completeness-report (status PASS) | Automated — no human required |

## Generalized gate kick-back (failed opinion)

Recording any gate `failed` (`manifest gate <ID> --opinion failed`) does not just stamp
the gate — `manifest.py` performs a **guided kick-back**: it resets `phase.current` back
to that gate's producing phase (`GATE_PRODUCING_PHASE` in `manifest.py`, the inverse of
the advance-precondition map), drops that phase from `completed`, writes a `blocked_reason`,
appends an `anti-legacy:gate-kicked-back` audit event, prints the producing skill to re-run,
and exits **non-zero (code 3)** so the orchestrator/CI can branch. It is a GUIDED reset — it
names the skill but does NOT auto-dispatch it. This applies to ALL gates:

| Gate failed | Pipeline resets to phase | Re-run skill |
|---|---|---|
| `GATE_0_DISCOVERY` | `survey` | `anti-legacy:survey` |
| `GATE_1_DESIGN` | `graph-translate` | `anti-legacy:graph-translator` |
| `GATE_1B_SEMANTIC_JOIN` | `semantic-join` | `anti-legacy:semantic-join` |
| `GATE_2_PLAN` | `planning` | `anti-legacy:planner` |
| `GATE_3_BUILD` | `build` | `anti-legacy:swarm` |
| `GATE_3B_SEMANTIC` | `semantic-validation` | `anti-legacy:semantic-validation` |
| `GATE_4_UAT` | `uat` | `anti-legacy:uat-crew` |
| `GATE_5_COMPLETENESS` | `document` | `anti-legacy:document` |

`passed`/`waived` never reset the phase. A `failed` GATE_1 whose rationale names specific
requirement nodes is still a targeted re-run (graph-translator re-runs for the named nodes
only) — the kick-back rewinds the phase; the producing skill narrows the scope.

## Action: status — Show all gate states

Print all gate states from the manifest:

```bash
python3 .anti-legacy/run.py manifest status
```

Look for the `Gates:` section and display each gate's status, evaluator, and timestamp.

> **Requirements-graph DRIFT note:** `semantic-validation` mutates
> `requirements_graph.json` in place (via `record-gap`). The producing skill
> (semantic-validation) MUST re-register `requirements-graph` after that
> mutation so `manifest check requirements-graph` stays green. The gatekeeper
> treats a DRIFTED `requirements-graph` as EXPECTED after semantic-validation —
> do NOT block on it; instead instruct the producing skill to re-register
> (`run.py manifest register requirements-graph --path ... --format json --produced-by anti-legacy:semantic-validation --status final`)
> and re-run the check.

## Action: check — Verify a gate is cleared

### Shared check: legacy-graph digest drift (ISS-12)

The §I6 keystone: rule annotations are written against a graph whose deterministic
stats digest was checksummed and registered as the `legacy-graph` evidence at survey
time. If the legacy CODE changes and the graph is re-indexed WITHOUT re-running
extraction, those annotations are STALE — and that staleness is checksum-detectable.
This is **not a new human gate**; it is an automated checksum CHECK reused by the
gates below (GATE_0 post-survey, GATE_1 pre-design). It composes the existing
`stats-digest` primitive — it never re-implements the comparison.

The committed seam is `.anti-legacy/legacy-graph.digest.txt` (the deterministic
digest survey writes and extraction re-writes post-annotation). The check recomputes
the CURRENT digest from the live graph DB and compares it to the registered baseline:

```bash
# DB = the per-app graph the survey indexed (under .anti-legacy/graphs/); baseline =
# the committed digest seam (or pass the manifest's registered legacy-graph checksum).
python3 .anti-legacy/run.py wicked_estate drift \
  --db .anti-legacy/graphs/<app>.db \
  --against .anti-legacy/legacy-graph.digest.txt
```

Exit codes are the gate signal: **0** = no drift (annotations match the current
graph), **2** = DRIFT (the graph changed since the digest was registered — annotations
are stale), **1** = check error (bad `--against`/missing DB). On exit 2 the verdict
JSON's `changed` array names the exact digest facts (node/edge counts, edge kinds)
that moved.

If `drift` exits **2** → halt: `BLOCKED — legacy-graph drift: the code graph changed
since the digest was registered; re-run extraction to re-annotate the changed nodes
(then survey/extraction re-write the digest seam) before advancing`. Exit **0** →
the drift check passes.

> The baseline may also be given as the manifest's registered checksum instead of the
> file: `--against $(python3 -c "import json;print(json.load(open('.anti-legacy/manifest.json'))['artifacts']['legacy-graph']['checksum'])")`.
> With a bare checksum the verdict has no per-line `changed` detail (checksum-only),
> but the drift bool + exit code are identical.

### For GATE_0_DISCOVERY

Verifies the survey produced a real discovery (project name + target stack +
non-empty legacy imports directory). No human sign-off required.

```bash
python3 .anti-legacy/run.py validator_discovery run --gate GATE_0_DISCOVERY
```

If the validator exits non-zero → halt: `GATE_0_DISCOVERY: BLOCKED — {reason}`.
Otherwise → `GATE_0_DISCOVERY: CLEARED ✓`.

> **Note:** at GATE_0 (immediately post-survey) the digest seam was just written, so
> the drift check is trivially clean — its value is at GATE_1 (below), where it catches
> a graph that changed AFTER annotation. Run it here only to confirm the seam was
> written: a missing `.anti-legacy/legacy-graph.digest.txt` (drift exits 1) means survey
> did not register the seam.

### For GATE_1B_SEMANTIC_JOIN

1. Verify the semantic-join report artifact exists on disk:
   ```bash
   python3 -c "import os,sys; sys.exit(0 if os.path.isfile('.anti-legacy/requirements/semantic_join_report.md') else 1)"
   ```
   (If your semantic-join skill writes the report elsewhere, check that path;
   the file MUST exist — a missing report BLOCKS the gate.)

2. Verify the manifest gate status is `passed` (semantic-join records it via
   `manifest gate`):
   ```bash
   python3 -c "import json,sys; m=json.load(open('.anti-legacy/manifest.json')); g=m.get('gates',{}).get('GATE_1B_SEMANTIC_JOIN',{}); sys.exit(0 if g.get('status')=='passed' else 1)"
   ```

If both pass → `GATE_1B_SEMANTIC_JOIN: CLEARED ✓`. If the report is missing or
the gate status is not `passed` → halt: `GATE_1B_SEMANTIC_JOIN: BLOCKED — {reason}`.

### For GATE_1_DESIGN

> **Advisory review queue (wicked-estate ≥ 0.5.0).** Before signing, the reviewer
> should clear the open `question`/`assumption` annotations the extraction/translation
> agents left — they are the human work-list. Read them per app DB with
> `python3 .anti-legacy/run.py wicked_estate advisory-nodes --db .anti-legacy/graphs/<app>.db`
> (or `wicked_estate.advisory_nodes(db)` in-process), which returns every node carrying
> an advisory annotation (gated on the engine-computed `advisory` flag, not the type).
> Unresolved `question`s on a capability are a natural reason to record `failed` and
> kick back. Graceful no-op on older engines.

1. Read manifest — verify `gates.GATE_1_DESIGN.status == "passed"`:
   ```bash
   python3 -c "import json,sys; m=json.load(open('.anti-legacy/manifest.json')); g=m['gates']['GATE_1_DESIGN']; sys.exit(0 if g['status']=='passed' else 1)"
   ```

2. Verify audit trail has the sign-off event:
   ```bash
   python3 -c "
   import json
   events = [json.loads(l) for l in open('.anti-legacy/audit.jsonl') if l.strip()]
   signed = [e for e in events if e.get('event')=='anti-legacy:gate-signed-off' and e.get('details',{}).get('gate_id')=='GATE_1_DESIGN']
   print(f'Sign-offs found: {len(signed)}')
   for s in signed: print(f'  {s[\"details\"][\"evaluator\"]} at {s[\"timestamp\"]}: {s[\"details\"][\"opinion\"]}')
   "
   ```

3. Verify evidence for review-packet:
   ```bash
   python3 .anti-legacy/run.py manifest check review-packet
   ```

4. Run design compliance and NFR checks:
   ```bash
   python3 .anti-legacy/run.py validator_discovery run --gate GATE_1_DESIGN
   ```

4b. Run the legacy-graph drift check (ISS-12) — the annotations the requirements graph is built on MUST still match the indexed code. A non-zero (exit 2) means the graph changed since extraction registered the digest; the annotations are stale and the design is built on sand:
   ```bash
   python3 .anti-legacy/run.py wicked_estate drift \
     --db .anti-legacy/graphs/<app>.db \
     --against .anti-legacy/legacy-graph.digest.txt
   ```
   Exit 0 → no drift, continue. Exit 2 → halt: `GATE_1_DESIGN: BLOCKED — legacy-graph drift; re-run extraction to re-annotate the changed nodes before this gate`. (See the shared **legacy-graph digest drift** check above for the verdict shape and the checksum-baseline alternative.)

5. Verify the disposition-aware round-trip evidence is registered and complete — every legacy rule is represented OR explicitly dropped-with-reason (ISS-10):
   ```bash
   python3 .anti-legacy/run.py manifest check roundtrip-coverage
   python3 -c "import json,sys; r=json.load(open('.anti-legacy/requirements/roundtrip-coverage.json')); sys.exit(0 if r.get('roundtrip_coverage',0)>=1.0 else 1)" \
     || echo 'BLOCKED: round-trip coverage < 1.0 — a legacy rule is neither represented nor dropped-with-reason'
   ```

6. Surface the clustering diagnostic — domains must be real CAPABILITIES, not a 1:1 program map (ISS-07). This is NOT an auto-block (a batch/disconnected estate can legitimately partition into singletons), but the reviewer MUST see it and confirm checklist item 7:
   ```bash
   python3 -c "import json; r=json.load(open('.anti-legacy/requirements/roundtrip-coverage.json')); c=r.get('clustering',{}); print('DEGENERATE CLUSTERING — every behavior community is a singleton-per-program; confirm domains are real capabilities (checklist 7)' if c.get('degenerate') else 'clustering ok (capability communities present)')"
   ```

**Human review checklist** — the reviewer is the architect (`config.json roles.architect`), who verifies all seven points via `review_packet.md` (shared over git/fileshare; no external tool required). Evidence on a `passed` opinion: `requirements-graph,blueprint-json,roundtrip-coverage,test-strategy`. The reviewer verifies:
1. Every domain has ≥1 requirement with non-empty `business_rules`; no empty domain; each name in a requirement's `data_access` resolves to an entity in the SAME domain, and no req is stranded in a `Domain_*_core` catch-all with 0 entities
2. Numeric outputs (money, rates, counts) have `parity_rules` in their test contracts
3. No active requirement has empty `legacy_components` — every node traces to source
4. Entity field types are translated (COMP-3 → DECIMAL, packed → BIGINT, etc.)
5. Every active requirement maps to a named target component in the blueprint
6. Every intentionally-dropped legacy rule in `dispositions.json` carries a `disposition_reason` — a reimagine drop is explicit and justified, never a silent coverage gap (the round-trip evidence's `dropped` set must match deliberate curator decisions)
7. Domains are real CAPABILITIES, not file/program-derived (ISS-07). Each domain name reads as a capability (a verb+noun like `PostTranCapability`, ideally from a confirmed domain term), spans merged sources where applicable, and is NOT a 1:1 restatement of a legacy program id. If the clustering diagnostic (step 6) reports `degenerate=true`, confirm each singleton domain is a genuinely distinct capability — not the call-graph simply failing to resolve JCL-chained batch steps into call affinity

A `failed` opinion whose rationale names the wrong requirement nodes triggers a **targeted re-run**: `graph-translator` re-runs for the named nodes only, the review-packet regenerates, and GATE_1 is presented again — no full pipeline restart.

If all checks pass → print `GATE_1_DESIGN: CLEARED ✓` and allow pipeline to continue.
If any check fails → print the specific failure and halt: `GATE_1_DESIGN: BLOCKED — {reason}`.

### For GATE_2_PLAN

1. Read manifest — verify `gates.GATE_2_PLAN.status == "passed"`:
   ```bash
   python3 -c "import json,sys; m=json.load(open('.anti-legacy/manifest.json')); g=m['gates']['GATE_2_PLAN']; sys.exit(0 if g['status']=='passed' else 1)"
   ```

2. Verify audit trail has the sign-off event:
   ```bash
   python3 -c "
   import json
   events = [json.loads(l) for l in open('.anti-legacy/audit.jsonl') if l.strip()]
   signed = [e for e in events if e.get('event')=='anti-legacy:gate-signed-off' and e.get('details',{}).get('gate_id')=='GATE_2_PLAN']
   print(f'Sign-offs found: {len(signed)}')
   for s in signed: print(f'  {s[\"details\"][\"evaluator\"]} at {s[\"timestamp\"]}: {s[\"details\"][\"opinion\"]}')
   "
   ```

3. Verify evidence for task-plan (planner registers artifact id `task-plan`):
   ```bash
   python3 .anti-legacy/run.py manifest check task-plan
   ```

4. Verify that the generated task list complies with the configured `traversal_strategy`:
   ```bash
   python3 .anti-legacy/run.py validator_discovery run --gate GATE_2_PLAN
   ```

**Human review checklist** — the reviewers are PM **and** tech lead (`config.json` names them); **both must sign** before the pipeline advances and the gatekeeper enforces this. They review `task.md`. Evidence on a `passed` opinion: `task-plan`. The reviewers verify all five points:
1. Layer 0 tasks have no dependencies — they can start immediately
2. No task has a scope estimate > 8h (else it needs splitting before approval)
3. Total task count equals the active requirement count in `requirements_graph.json`
4. Team has capacity — the scope total is achievable in the target timeline
5. Traversal ordering complies with the checklist in `TRAVERSAL_STRATEGIES.md` (run `python3 .anti-legacy/run.py planner_utils verify-order` to check programmatically)

### For GATE_3_BUILD (automated)

GATE_3 is automated — no human sign-off required. **Build integrity (the
compiler tier) is NOT optional**: a WARNING on `build-integrity` is a BLOCK,
not a pass. Only the quality and security tiers may be WARNING (non-fatal).

1. Verify build evidence. `build-integrity` (compiler) MUST be exactly `PASS`;
   `code-quality` and `security-scan` may be `PASS` or `WARNING`:
   ```bash
   python3 -c "
   import json, sys
   # Compiler tier: must be exactly PASS (WARNING or FAIL -> BLOCKED).
   with open('.anti-legacy/evidence/build-integrity.json') as f:
       bi = json.load(f)
   if bi['status'] != 'PASS':
       print(f'BLOCKED: build-integrity status is {bi[\"status\"]} (compiler tier must be exactly PASS)')
       sys.exit(1)
   # Quality / security tiers: PASS or WARNING is acceptable (WARNING is non-fatal but surfaced).
   for art in ['code-quality', 'security-scan']:
       with open(f'.anti-legacy/evidence/{art}.json') as f:
           data = json.load(f)
       if data['status'] not in ['PASS', 'WARNING']:
           print(f'BLOCKED: {art} status is {data[\"status\"]}')
           sys.exit(1)
       if data['status'] == 'WARNING':
           print(f'WARNING (non-fatal, surfaced): {art} reported WARNING')
   "
   ```

2. **Independent-evaluator / round-trip rule-coverage precondition.**
   GATE_3_BUILD must NOT be self-signed by the producing skill without the
   round-trip proof. This rule is now ALSO MACHINE-ENFORCED (M1):
   `validator_discovery._run_gate_3_build` reads
   `.anti-legacy/evidence/functional_comparison_report.json` and blocks
   (returns False) if it is missing, has any FAIL requirement
   (`fail_count > 0`), or `rule_coverage < 1.0` — so the gate cannot pass
   even if a skill skips the check below. Additionally, an unknown/unsupported
   `target_stack` no longer phantom-passes (B3): the compiler tier returns FAIL
   (not a silent PASS) unless a validator is configured or the gate is waived.
   The check below is the human-readable mirror of that deterministic gate.
   `functional_comparison_report.json` (written by
   target-review via `compare_graphs.py`) MUST be present with ZERO FAIL
   requirements and `rule_coverage >= 1.0`. If it is missing, or has any FAIL,
   or `rule_coverage < 1.0` → BLOCK the gate and instruct re-dispatch of swarm:
   ```bash
   python3 -c "
   import json, os, sys
   p = '.anti-legacy/evidence/functional_comparison_report.json'
   if not os.path.isfile(p):
       print('BLOCKED: functional_comparison_report.json missing — target-review must run compare_graphs.py (round-trip rule-coverage proof) before GATE_3_BUILD can be recorded')
       sys.exit(1)
   r = json.load(open(p))
   agg = r.get('aggregate', r)
   fails = agg.get('fail_count')
   if fails is None:
       fails = sum(1 for req in r.get('requirements', []) if str(req.get('status','')).upper() == 'FAIL')
   cov = agg.get('rule_coverage', r.get('rule_coverage'))
   if fails and fails > 0:
       print(f'BLOCKED: functional_comparison_report has {fails} FAIL requirement(s) — re-dispatch swarm')
       sys.exit(1)
   if cov is None or float(cov) < 1.0:
       print(f'BLOCKED: rule_coverage is {cov} (< 1.0) — re-dispatch swarm to cover missing rules')
       sys.exit(1)
   print(f'Round-trip OK: 0 FAIL reqs, rule_coverage={cov}')
   "
   ```

3. Verify artifact checksums:
   ```bash
   python3 .anti-legacy/run.py manifest check build-integrity
   python3 .anti-legacy/run.py manifest check code-quality
   python3 .anti-legacy/run.py manifest check security-scan
   python3 .anti-legacy/run.py manifest check functional-comparison-report
   ```

If build checks AND the round-trip precondition pass → auto-record the gate
(evaluator is the producing skill, but the gate is only reachable because the
independent round-trip proof above passed):
```bash
python3 .anti-legacy/run.py manifest gate GATE_3_BUILD \
  --opinion passed \
  --evaluator "anti-legacy:target-review" \
  --rationale "Automated build compilation (PASS), quality/security checks, and round-trip rule-coverage proof (0 FAIL, coverage>=1.0) passed" \
  --evidence "build-integrity,code-quality,security-scan,functional-comparison-report"
```

If `build-integrity` is WARNING/FAIL, or the round-trip precondition fails, do
NOT record the gate → `GATE_3_BUILD: BLOCKED — {reason}` and re-dispatch
`anti-legacy:swarm` / re-run `anti-legacy:target-review`.

### For GATE_3B_SEMANTIC

1. Read manifest — verify `gates.GATE_3B_SEMANTIC.status == "passed"`:
   ```bash
   python3 -c "import json,sys; m=json.load(open('.anti-legacy/manifest.json')); g=m['gates']['GATE_3B_SEMANTIC']; sys.exit(0 if g['status']=='passed' else 1)"
   ```

2. Verify audit trail has the sign-off event:
   ```bash
   python3 -c "
   import json
   events = [json.loads(l) for l in open('.anti-legacy/audit.jsonl') if l.strip()]
   signed = [e for e in events if e.get('event')=='anti-legacy:gate-signed-off' and e.get('details',{}).get('gate_id')=='GATE_3B_SEMANTIC']
   print(f'Sign-offs found: {len(signed)}')
   for s in signed: print(f'  {s[\"details\"][\"evaluator\"]} at {s[\"timestamp\"]}: {s[\"details\"][\"opinion\"]}')
   "
   ```

3. Verify evidence for semantic-validation-report:
   ```bash
   python3 .anti-legacy/run.py manifest check semantic-validation-report
   ```

4. Verify that no unresolved high-severity semantic gaps exist in requirements graph:
   ```bash
   python3 .anti-legacy/run.py validator_discovery run --gate GATE_3B_SEMANTIC
   ```

**Human review checklist** — the reviewers are architect + tech lead. They review the `semantic-validation-report`, which is where the round-trip rule-coverage verdict in `functional_comparison_report.json` is judged: GATE_3_BUILD proves the classes EXIST, GATE_3B proves the rules INSIDE them are implemented. The reviewers verify:
1. No unresolved high-severity semantic gaps remain in the requirements graph
2. Rule coverage is 100% (or every open gap is triaged) — no uncovered `error_path` or `validation` ids
3. The verifiable-parity verdict in `functional_comparison_report.json` is satisfied

> **Note on requirements-graph drift at this gate:** `semantic-validation`
> records gaps directly into `requirements_graph.json`, so a bare
> `manifest check requirements-graph` will report DRIFTED here. That drift is
> EXPECTED — do NOT block GATE_3B on it. The semantic-validation skill must
> RE-REGISTER `requirements-graph` after recording gaps so the checksum is
> refreshed; once re-registered, `manifest check requirements-graph` is green
> again.

### For GATE_4_UAT

1. Verify `gates.GATE_4_UAT.status == "passed"` in manifest

2. Verify that all UAT tests and verdicts are passing:
   ```bash
   python3 .anti-legacy/run.py validator_discovery run --gate GATE_4_UAT
   ```

3. Verify sign-off is from a role different from the dev team. This is a
   **HARD FAIL**: if the UAT evaluator is the same as the configured architect
   (now populated by setup), the gate is BLOCKED (exit 1), not merely warned:
   ```bash
   python3 -c "
   import json, sys
   m = json.load(open('.anti-legacy/manifest.json'))
   cfg = json.load(open('.anti-legacy/config.json'))
   g = m['gates']['GATE_4_UAT']
   evaluator = g.get('evaluator', '')
   architect = cfg.get('roles', {}).get('architect', '')
   if architect and evaluator == architect:
       print(f'BLOCKED: UAT signed by same person as architect ({evaluator}) — independence violation')
       sys.exit(1)
   print(f'UAT signed by: {evaluator} (independent of architect {architect!r})')
   "
   ```
   If this exits non-zero → halt: `GATE_4_UAT: BLOCKED — UAT independence violation`.

**Human review checklist** — the reviewer is the UAT lead, who **must not be the GATE_1_DESIGN signer** and **must not be the configured `roles.architect`**. This independence is now MACHINE-ENFORCED (M2): `validator_discovery._run_gate_4_uat` loads `config.json roles.architect`, reads the `GATE_4_UAT` evaluator from the manifest, and scans `audit.jsonl` for the recorded `GATE_1_DESIGN` signer; it hard-fails (returns False) if the UAT evaluator equals the architect OR the GATE_1_DESIGN signer. The prose checklist below is the human-judgment layer ON TOP OF that deterministic check. The reviewer reviews `uat-summary.md` + each `evidence/uat/{domain}.json`. Evidence on a `passed` opinion: the registered ids `uat-summary,uat-verdicts` (both registered by uat-crew). The reviewer verifies all four points:
1. All domains have a verdict (no skipped domains)
2. No open CRITICAL or MAJOR findings
3. All MINOR findings triaged — accepted (with rationale) or assigned with a tracking note
4. Parity rules tested: every contract with `parity_rules` has a TC marked pass/fail (not untested)

If MINOR findings are accepted (not fixed), the rationale must name them, e.g. `--rationale "Accepted UAT-003 (logging, MINOR) — tracked in #12. All CRITICAL/MAJOR clear."`

### For GATE_5_COMPLETENESS (automated)

GATE_5 is the final completeness gate at the `final-review` phase. It is automated —
no human sign-off required. It auto-clears when the `final-review` phase has produced a
`completeness-report` artifact whose top-level `status` is `PASS`, and kicks back to the
`document` phase on a FAIL (the generalized kick-back above). The producing skill/script
for the report (`anti-legacy:final-review` / its `completeness-report` writer) is built
separately; the gatekeeper only verifies the evidence here.

1. Verify the completeness report exists and reports `PASS`:
   ```bash
   python3 -c "
   import json, os, sys
   p = '.anti-legacy/evidence/completeness_report.json'
   if not os.path.isfile(p):
       print('BLOCKED: completeness_report.json missing — anti-legacy:final-review must write it before GATE_5_COMPLETENESS can clear')
       sys.exit(1)
   r = json.load(open(p))
   status = str(r.get('status', '')).upper()
   if status != 'PASS':
       print(f'BLOCKED: completeness-report status is {status!r} (must be PASS) — re-run anti-legacy:document then anti-legacy:final-review')
       sys.exit(1)
   print('Completeness OK: status=PASS')
   "
   ```

2. Verify the registered artifact checksum:
   ```bash
   python3 .anti-legacy/run.py manifest check completeness-report
   ```

If the report is PASS and the checksum verifies → auto-record the gate (the evaluator is
the producing skill, the gate is only reachable because the completeness evidence passed):
```bash
python3 .anti-legacy/run.py manifest gate GATE_5_COMPLETENESS \
  --opinion passed \
  --evaluator "anti-legacy:final-review" \
  --rationale "Automated completeness check passed (completeness-report status: PASS)" \
  --evidence "completeness-report"
```

If the report is missing or status is not PASS → do NOT record passed; record `failed`
(which kicks back to the `document` phase) and re-run `anti-legacy:document`:
```bash
python3 .anti-legacy/run.py manifest gate GATE_5_COMPLETENESS \
  --opinion failed \
  --evaluator "anti-legacy:final-review" \
  --rationale "Completeness check FAILED — {reason}"
# ^ exits non-zero (code 3) and resets phase.current to 'document'
```

## Action: record — Record a manual sign-off

```bash
python3 .anti-legacy/run.py manifest gate {gate_id} \
  --opinion passed \
  --evaluator "{your-name-or-role}" \
  --rationale "{your rationale}" \
  --evidence "{comma-separated-artifact-ids}"

# Compile the audit trail report
python3 .anti-legacy/run.py manifest audit-report
```

Then instruct the user to commit the updated manifest, audit trail, and audit report:

```bash
git add .anti-legacy/manifest.json .anti-legacy/audit.jsonl .anti-legacy/audit_report.md
git commit -m "gate: {gate_id} cleared by {evaluator}"
```

## Gate cleared — store decision and advance

After any gate clears, store the decision in git-brain:

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "Gate {gate_id} cleared for project {project_name} by {evaluator}. Rationale: {rationale}. Evidence: {evidence_ids}." \
  --tags "decision,gate,{gate_id}" \
  --category decisions
```

Report to the user what phase comes next after the cleared gate:

| Gate Cleared | Next Phase |
|---|---|
| GATE_1_DESIGN | `anti-legacy:planner` |
| GATE_2_PLAN | `functional-tests` (blocking pre-build validation), then `anti-legacy:swarm` |
| GATE_3_BUILD | `anti-legacy:semantic-validation` |
| GATE_3B_SEMANTIC| `anti-legacy:uat-crew` |
| GATE_4_UAT | `document`, then `final-review` (GATE_5_COMPLETENESS) |
| GATE_5_COMPLETENESS | `anti-legacy:deploy` |
