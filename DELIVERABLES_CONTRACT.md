# DELIVERABLES_CONTRACT — shared contract for the anti-legacy deliverable skills

> **Scratch/design doc** (not committed yet). Every builder subagent reads this in full
> before writing code, so the 8 new deliverable skills are consistent and convention-clean.

## 0. Mission

Add **deliverable skills** that render detailed, human-facing artifacts FROM the pipeline's
structured data, produced **"when the graph is ready"** — i.e. once
`.anti-legacy/requirements/requirements_graph.json` exists (and, for some, the blueprint /
contracts). They **complement** the existing `review-packet` (which feeds GATE_1); they do
**not** replace the pipeline-internal producers (`graph-translator`, `blueprint`,
`test-strategy`, `functional-tests`, `planner`).

All outputs land under **`.anti-legacy/deliverables/`**. Each skill **registers** its artifact
in the manifest and **never advances the phase** (phase advancement is owned by the phase
skills). No new mainline phase or gate is added.

## 1. The suite (who owns what)

| Skill (frontmatter `name`) | Output file(s) under `.anti-legacy/deliverables/` | Artifact id(s) |
|---|---|---|
| `anti-legacy:prd` | `product-requirements.md` | `deliverable-prd` |
| `anti-legacy:diagrams` | `diagrams/*.mmd` + `diagrams/README.md` (index) | `deliverable-diagrams` (→ the index) |
| `anti-legacy:test-plan` | `test-strategy.md` | `deliverable-test-strategy` |
| `anti-legacy:test-scripts` | `tests/{data-parity,uat,e2e,api}/…` + `tests/README.md` | `deliverable-test-scripts` (→ the index) |
| `anti-legacy:migration-plan` | `migration-plan.md` **+** `migration-plan.jira.csv` | `deliverable-migration-plan`, `deliverable-migration-plan-csv` |
| `anti-legacy:risk-log` | `risk-log.md` | `deliverable-risk-log` |
| `anti-legacy:decisions-log` | `decisions-log.md` | `deliverable-decisions-log` |
| `anti-legacy:evidence-log` | `evidence-log.md` | `deliverable-evidence-log` |

Your detailed per-skill spec is in YOUR task prompt. The table above is the whole suite so you
don't duplicate a sibling's job. (An umbrella `anti-legacy:deliverables` + wiring is built
separately — not your concern.)

## 2. User decisions (baked in — honor exactly)

- **Functional test types** (test-plan + test-scripts): **data-parity / equivalence**,
  **UAT acceptance (Given/When/Then)**, **end-to-end business journeys**, **API / contract**.
  These are **functional, not unit** tests.
- **Diagrams**: **Mermaid only** (`.mmd` / fenced ```mermaid```). No PlantUML.
- **Execution plan**: **Markdown hierarchy** (epics→stories→tasks→subtasks) **+ Jira-importable CSV**.
  No JSON backlog, no GitHub Issues.

## 3. The shared library — `antilegacy_core.deliverables` (USE IT; do not re-implement loaders)

Your leaf script imports it (`PYTHONPATH` is set to the core dir by `run.py`, precedent:
`skills/document/scripts/document.py`):

```python
from antilegacy_core import deliverables as D
```

Verified API (all paths default to the right `.anti-legacy/...` location; all loaders degrade
gracefully — absent/unreadable source returns `{}` / `[]`, never raises):

```
D.load_config()                 -> dict   (.anti-legacy/config.json)
D.load_requirements_graph()     -> dict   (.anti-legacy/requirements/requirements_graph.json)
D.load_blueprint()              -> dict   (.anti-legacy/requirements/blueprint.json)
D.load_coverage()               -> dict   (.anti-legacy/coverage-report.json)
D.load_manifest()               -> dict   (.anti-legacy/manifest.json)
D.load_audit()                  -> list[dict]  (.anti-legacy/audit.jsonl)
D.load_annotations()            -> list[dict]  (.anti-legacy/annotations.jsonl)
D.load_contracts()              -> dict {(domain, req_id): contract}
D.evidence_files()              -> list[str]   abs paths under .anti-legacy/evidence/

D.iter_requirements(graph)      -> yields (domain, req_id, node)
D.iter_entities(graph)          -> yields (domain, entity_name, entity)
D.active_requirements(graph)    -> list[(domain, req_id, node)]   (not dropped/unresolvable)
D.dropped_requirements(graph)   -> list[(domain, req_id, node)]   (disposition == "drop")
D.rule_confidences(node)        -> list[float]
D.audit_events(audit, "gate-signed-off")  -> filtered rows (short or full event id)
D.manifest_artifacts(manifest)  -> {artifact_id: row}

D.deliverables_dir()                       -> abs path to .anti-legacy/deliverables/ (mkdir)
D.write_deliverable(relname, content)      -> abs path  (relname may include subdirs)
D.register_deliverable(artifact_id, abs_path, produced_by,
                       fmt="markdown", status="final", depends_on=[...]) -> stored path
                       # reuses manifest helpers; appends audit event; NEVER advances phase;
                       # no-ops if manifest absent. Use fmt="text" for the .csv artifact.

D.mermaid_id(text) -> safe node id    D.md_escape(s)    D.md_table(headers, rows)    D.now_iso()
```

Anchor everything on the workspace via the library (it uses `os.getcwd()`); **never** anchor on
`__file__`. If you need a schema/template, load it as package data via
`importlib.resources.files("antilegacy_core") / "schemas" / "..."` — never a relative path.

## 4. Skill anatomy (match the house style — see `skills/document/`, `skills/review-packet/`)

**`skills/<skill-dirname>/SKILL.md`** — the skill dirname is the un-prefixed name
(`prd`, `diagrams`, `test-plan`, `test-scripts`, `migration-plan`, `risk-log`,
`decisions-log`, `evidence-log`). Frontmatter:

```yaml
---
name: "anti-legacy:<name>"
description: >
  <what it produces, from what inputs>. Use when: "<trigger phrase>", "<trigger phrase>".
---
```

SKILL.md body (100–300 lines; a 20-line skill is a stub — §4 of AGENTS.md): purpose & mental
model · **Cross-Platform Notes** (python3, os.path) · when it runs ("graph is ready") &
prerequisites · Parameters · numbered Steps (every command is
`python3 .anti-legacy/run.py <stem> <args>`) · **Done-gate** (assert the artifact exists &
non-empty BEFORE registering; if it fails, surface the gap and STOP — do not register) ·
Output · failure cases.

**`skills/<skill-dirname>/scripts/<stem>.py`** — one leaf script. Rules:
- Pure standard library + `from antilegacy_core import deliverables as D`. Cross-platform
  `os.path`. No shell-isms, no third-party deps.
- `argparse` CLI. Provide a `--no-register` flag (write but don't touch the manifest) for
  hermetic tests. Default source paths = the `D.P_*` defaults (let the loaders own them).
- Render deterministically from the data; **degrade gracefully** (missing source → render the
  section as "not available / not yet produced", never crash).
- Write via `D.write_deliverable(...)`; register via `D.register_deliverable(...)`.
- Exit non-zero with a clear stderr message only on a real failure (e.g. no requirements graph
  at all). Print the written path(s) on success.
- The stem must be unique across the whole repo (it's the dispatch key). Use your skill name
  as the stem (`prd`, `diagrams`, `test_plan`, `test_scripts`, `migration_plan`, `risk_log`,
  `decisions_log`, `evidence_log` — underscores in the stem, hyphens in the skill name).

## 5. Traceability is mandatory (§2 of AGENTS.md — the thread never breaks)

Every requirement-bearing line in a deliverable must trace back: **req_id → legacy_components →
business_rule id(s)**. PRD/test-plan/test-scripts/migration-plan/risk-log all carry these
links (e.g. a PRD requirement cites its `legacy_components` and `RULE-xxx` ids; a test cites the
`req_id` + rule it covers; a plan task cites its `req_id`). A deliverable that drops the trace
is broken.

## 6. Voice (§Voice of AGENTS.md)

Factual. Name the file, the req_id, the rule id. **Surface gaps, do not soften them.** Every
deliverable that can be incomplete must state what is NOT yet covered (e.g. risk-log lists
RISK-flagged + low-confidence nodes as open; evidence-log states which phases have NO receipts
yet; test-plan names requirements with no contract). "Rendered N of M; the other M−N have no
business_rules — listed below" is correct. A clean-looking doc that hides holes is not.

## 7. Data-source field shapes (from recon — don't re-discover)

**requirements_graph.json**: `metadata.migration_mode` ("structural"|"functional");
`domains{<domain>}` → `.requirements{<req_id>}` and `.entities{<name>}`.
Requirement node: `title`, `description`, `legacy_components[]` (req, non-null),
`data_access[]`, `dependencies[]` (req_ids), `status` ("active"|"review"|"unresolvable"),
`disposition` ("keep"|"modify"|"drop"|"new"), `disposition_reason`, `merged_programs[]`,
`business_rules[]` `{id "RULE-\d+", statement, source_ref?, confidence 0–1?, provenance?}`,
`validations[]` `{id "VAL-\d+", statement, field?, error_ref "ERR-\d+"?, confidence?}`,
`error_paths[]` `{id "ERR-\d+", statement, code?, confidence?}`.
Entity: `description`, `fields[]` `{name, type, description}`.

**annotations.jsonl** row: `db_id`, `symbol_id` (key), `status` ("resolved"|"risk"),
`confidence` 0–1, `requirement`, `statement`, `description`, `provenance`, `source_kinds[]`,
`risk_reason`, `ring_depth`, `cluster`, `verification`
("unverified"|"untrusted_verified"|"trusted_verified").

**coverage-report.json**: `total`, `behavior_bearing`, `resolved`, `risk_flagged`,
`unaccounted`, `coverage` 0–1, `resolved_rate`, `mean_confidence`, `resolve_threshold`,
`per_app[]` `{app, db, total, behavior_bearing, resolved, risk_flagged, unaccounted, coverage}`,
`unaccounted_nodes[]` `{symbol_id, name, kind, file, app}`.

**blueprint.json**: `project`, `target_stack`, `target_path`, `style?`
("layered"|"hexagonal"|"cqrs"|"microservices"), `build_order[]?`,
`domains{<domain>}` → `package`, `components{<req_id>}`
`{target_file, class_name, component_type ("model"|"repository"|"service"|"controller"|"batch"),
methods[]?, api{method,path}?, dependencies[]?}`,
`entities{<name>}` `{table_name, columns[] {name, type, source_type?, pk?}}`.

**contracts/{domain}/{req_id}.contract.json**: `req_id`, `domain`, `legacy_components[]`,
`scenarios[]` `{id "TC-\d+", type ("happy_path"|"boundary"|"error"|"parity"), description,
inputs{}, expected_output{}, expected_error?}`,
`parity_rules[]` `{field, precision ("exact"|N), source_type}`.

**config.json** (FLAT — verified against what `setup` writes; do NOT use `project.name`, that's
the *manifest's* shape, a different file): `project_name`, `source_apps[] {name, path, language, repo?}`, `target_stack`,
`target_path`, `deployment_target?`, `migration_mode`, `roles {architect, uat_reviewer}`,
`coverage{behavior_kinds, estate_behavior_kinds, structural_kinds, resolve_threshold,
capability_partition}`, `crawl{max_rings, context_budget_chars}`, `wicked_estate_path?`.

**git-brain decisions** (decisions-log only): orphan branch `brain/anti-legacy/decisions`,
append-only. Read via the dispatcher: `python3 .anti-legacy/run.py git_brain search --query "..."
--category decisions` (and `git_brain` has list/search). Records carry `id, path, tags[], type,
title?, created_at`, body Markdown. Do NOT read brain branches with raw git.

**manifest.json** (evidence-log): `phase {current, completed[], blocked_reason?}`,
`gates {<GATE_ID>: {opinion/status, evaluator, rationale, evidence[]}}`,
`artifacts {<id>: {path, format, produced_by, status, produced_at, depends_on[], checksum?}}`.
**audit.jsonl** events: `{event "anti-legacy:<type>", timestamp, details{}}` —
`phase-advanced {from,to}`, `gate-signed-off {gate_id, opinion, evaluator, rationale}`,
`gate-kicked-back {gate_id, from_phase, reset_to_phase, re_run_skill}`,
`artifact-registered {artifact_id, path, status}`.

## 8. Test your leaf script (don't hand-wave — §"actually functionally test")

From the repo root, with a scratch workspace fixture:

```bash
CORE="$(pwd)/skills/anti-legacy-expert/scripts"
WS="$(mktemp -d)"; cd "$WS"
PYTHONPATH="$CORE" python3 -m antilegacy_core.manifest init --name t --target-stack java --target-path ./t
mkdir -p .anti-legacy/requirements
# write a small fixture .anti-legacy/requirements/requirements_graph.json (1–2 domains, rules,
# a dropped req, an entity) + any other source your skill reads (blueprint/contracts/coverage)
PYTHONPATH="$CORE" python3 "$OLDPWD/skills/<your-skill>/scripts/<stem>.py"   # add --no-register first
cat .anti-legacy/deliverables/<your output>     # eyeball it
```

Run it twice (idempotent: re-render overwrites, re-register updates the same artifact id). Then
with registration on, confirm your artifact id + an `artifact-registered` audit row landed.
Prove a missing-source case renders gracefully. Report the real output, not a claim.

## 9. Universal DON'Ts (from AGENTS.md — apply here)

- DON'T call any script by file path in SKILL.md — only `python3 .anti-legacy/run.py <stem>`.
- DON'T `manifest advance` from a deliverable skill — register only.
- DON'T anchor on `__file__` — workspace is `os.getcwd()` (the library handles it).
- DON'T drop the traceability trace (req_id → legacy_components → rule).
- DON'T hide gaps — surface RISK / low-confidence / missing-contract / no-receipt items.
- DON'T add third-party deps or shell-isms; stdlib + os.path, cross-platform.
- DON'T edit `audit.jsonl` by hand or build a Python parser for any language.
- DON'T duplicate a sibling deliverable's job (see the suite table, §1).
