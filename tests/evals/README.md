# Theory Evals (`tests/evals/`)

This directory holds the **theory-encoding eval harness** for the four
lead-engineer defects (T1–T4) in the anti-legacy pipeline. Each eval is written
to **fail today (red)** against the unfixed scripts/schemas and to **pass (green)
only after the corresponding fix lands**. The eval *is* the executable definition
of "fixed" for its theory — it is not a regression guard for current behavior.

> The four theories, in one line each:
> - **T1 (mode):** the pipeline defaults to `structural` (a 1:1 code skeleton); the intended `functional` (capability plan) mode is never read from `config.json`.
> - **T2 (domains):** domains come from data files, so a requirement and the entities it accesses land in *different* domains — a requirement cannot see its own entity.
> - **T3 (schema):** `business_rules` / `validations` / `error_paths` are off-schema, unvalidated.
> - **T4 (done-check):** `compare_graphs.py` marks a requirement "done" iff a class with the mapped name *exists* — it never checks the rules are implemented.

## How the harness is wired

- **`conftest.py`** puts `<repo>/scripts` on `sys.path` so eval test files can
  `import graph_normalizer`, `import compare_graphs`, etc. by bare module name
  (the scripts import each other the same way). It also exposes session-scoped
  `repo_root`, `scripts_dir`, and `fixtures_dir` path fixtures so every eval
  resolves paths the same way regardless of the cwd pytest is launched from.
- **`fixtures/`** holds small, hand-authored synthetic JSON inputs (described
  below). They are deliberately tiny so the expected outputs can be reasoned
  about by hand.

> [!IMPORTANT]
> The evals run **only** against these synthetic fixtures (and tmp files they
> write). They must **never** read or mutate anything under `.anti-legacy/`.
> The real artifacts (real `requirements_graph.json`, `blueprint.json`,
> `target_graph.json`) are evidence-free / structural and would make the evals
> non-deterministic. Keep all eval inputs in `fixtures/` or `tmp_path`.

## Fixtures

### `fixtures/code_graph.json` — synthetic legacy/code graph
Conforms to `schemas/code-graph.schema.json`
(`applications -> {path, nodes, edges}`).

- `PROG_A` calls `PROG_B`; **both** `read_write` the shared `CUSTOMER` table.
- `PROG_A` additionally accesses `CONFIG` and `LEDGER` (so one requirement
  touches **three** assets).
- `PROG_C` is isolated (no callers) and accesses only `ARCHIVE`.

Used by **T1** (functional mode should leaf-merge `PROG_B` into `PROG_A` →
**one** capability node, vs **two** `REQ_` nodes in structural mode) and by
**T2** (the co-location invariant: every name in a requirement's `data_access`
must resolve to an entity in the requirement's **own** domain — today the
shared/extra assets strand in separate entity-only domains).

### `fixtures/requirements_graph_enriched.json` — enriched requirements graph
Conforms to the **base** `requirements-graph.schema.json` and additionally
carries the rich fields in the **new object form** `{id, statement, ...}`
(not the legacy `"RULE-001: text"` strings).

- One requirement `REQ_X` (blueprint-mapped to `XService`) with
  `business_rules` `RULE-001`, `RULE-002`; `validations` `VAL-001`;
  `error_paths` `ERR-001` — **4 rule ids** total.
- `VAL-001.error_ref` points at `ERR-001` in the **same** requirement (the
  intra-req join the T4 done-check uses).
- IDs follow `RULE-/VAL-/ERR-` + 3 zero-padded digits, unique **within** the
  requirement.

Used by **T3** (presence + `$defs` + enriched-profile validation of the rich
fields) and **T4** (the requirement whose rules a target graph must cover).

### `fixtures/blueprint.json` — synthetic blueprint
Matches the real `blueprint.json` shape `compare_graphs.py` reads
(`domains -> components -> {class_name, type, api}` keyed by `req_id`).
Maps `REQ_X -> class_name "XService"`.

### `fixtures/target_graph_no_evidence.json` — "hollow" target graph
Mirrors the **real** `target_graph.json` shape
(`domains -> {package, components, entities}`, components keyed by **class
name** with `{type, file_path}`).

- It **contains** the mapped class `XService` …
- … but carries **no** `implemented_rules` anywhere (zero rule-level evidence).

This is the centerpiece of **T4**: under today's `compare_graphs.py`, `REQ_X`
is marked **PASS** simply because a component named `XService` exists, even
though `XService` implements **none** of `REQ_X`'s rules. The fixed
done-check, reading `implemented_rules`, must score this graph as 0/4 →
**PARTIAL/FAIL**, never PASS. (T4's *PASS* case supplies a separate target
graph that carries full `implemented_rules` evidence.)

## Expected red / green per theory

| Theory | Eval file (authored separately) | Red now (unfixed) | Green after fix |
| --- | --- | --- | --- |
| T1 | `test_t1_mode_wiring.py` | no-`--mode` run with a `functional` config still emits `structural` (2 reqs) | `mode = args.mode or cfg['migration_mode'] or 'structural'` → functional config yields 1 merged capability |
| T2 | `test_t2_colocation.py` | a req's `data_access` assets are split across other entity-only domains (subset fails) | accessed entities are co-located into the req's own domain; `data_access` de-duped |
| T3 | `test_t3_schema_rules.py` | base schema has no `$defs`/rich props; enriched schema file is absent | base schema models the three optional rich arrays; enriched overlay requires them with ID patterns |
| T4 | `test_t4_rule_coverage.py` | `XService` exists → **PASS**, no coverage, no `functional_comparison_report.json` | uncovered `error_path` → never PASS; coverage computed; JSON report emitted; exit code from coverage |

## Running

```bash
python3 -m pytest tests/evals -q
```

Until the theory test files are added, this **collects 0 tests with no import
errors** — that is the expected state of the harness on its own.

## Dependency note

T3's enriched-schema validation uses `jsonschema`, which is **not currently
installed** in this environment. The T3 eval must `try/except` the import and
**skip with a clear message** when it is absent (the dependency is to be added
to the project requirements as part of the T3 fix). The harness itself
(`conftest.py` + fixtures) has **no** third-party dependencies.
