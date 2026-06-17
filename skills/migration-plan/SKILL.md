---
name: "anti-legacy:migration-plan"
description: >
  Render the end-to-end migration EXECUTION plan — epics -> stories -> tasks ->
  subtasks covering everything to deliver the modernization (test-prep, build,
  deployment, testing) — from the requirements graph + blueprint + config. Two
  formats: a Markdown hierarchy and a Jira-importable CSV. Complements
  anti-legacy:planner (the build-only swarm task.md): this is the broader,
  program-level delivery backlog. Use when: "migration plan", "execution plan",
  "epics and stories", "end-to-end delivery plan", "Jira import".
---

# anti-legacy:migration-plan

This deliverable renders the **whole delivery plan** the team executes to ship the
modernization: not just the code build, but the test-prep that precedes it, the
data migration and parity work, the deployment + cutover, and UAT sign-off. It is
a *deliverable* (it renders FROM committed pipeline data; it never advances a
phase), produced "when the graph is ready" — once
`.anti-legacy/requirements/requirements_graph.json` exists. It **complements**
`anti-legacy:planner`: the planner emits the build-only `task.md` (the swarm's
contract); this emits the program-level backlog (prep -> build -> deploy -> test),
in two formats a delivery team and a PM can both use.

## Mental model

The plan is a four-level hierarchy, decomposed **deterministically** from the data
(no LLM free-writing — re-running the same inputs yields the same plan):

- **Epics** — one *delivery* epic per domain, plus four *cross-cutting* epics:
  **Environment & Test Prep**, **Data Migration & Parity**, **Deployment &
  Cutover**, **UAT & Sign-off**.
- **Stories** — under a domain epic, one story per **active** requirement
  (`req_id` + title). Under a cross-cutting epic, the concrete workstream stories
  (set up CI, provision envs, build the parity harness, deploy to staging, run
  UAT, cutover, ...).
- **Tasks** — per requirement story: **test-prep** (author the contract + tests,
  shift-left) -> **build** (implement the blueprint component) -> **integrate**
  -> **verify** (run functional + parity tests). Workstream stories get concrete
  workstream tasks.
- **Subtasks** — under a build task: the **layers** taken from the blueprint
  `component_type` (model -> repository -> service -> controller/api), then a
  final functional+parity test subtask.

Two output formats (the user decision — Markdown hierarchy **+** Jira CSV; no
JSON, no GitHub Issues):

| File (under `.anti-legacy/deliverables/`) | What it is | Artifact id |
|---|---|---|
| `migration-plan.md` | Nested hierarchy (headings + checkbox lists) with explicit ordering, dependencies, and the req_id -> legacy_components trace | `deliverable-migration-plan` (markdown) |
| `migration-plan.jira.csv` | Jira-importable CSV (standard parent/Epic-Name hierarchy convention) | `deliverable-migration-plan-csv` (text) |

**Ordering is explicit and load-bearing.** Requirements are topologically sorted
by their `dependencies` (Kahn's algorithm); within a requirement, subtasks follow
layer order; across the whole plan, phase order is **prep -> build -> deploy ->
test**. Every item is numbered so the order is unambiguous: `EPIC-1`,
`STORY-1.1`, `TASK-1.1.1`, `SUB-1.1.1.1`.

**Traceability never breaks** (§2 of AGENTS.md). Each requirement story carries its
`req_id` -> `legacy_components` -> `dependencies`; each task inherits the `req_id`.
A `drop`/`unresolvable` requirement gets **no** delivery story (it is out of build
scope) but is surfaced — with its reason and legacy provenance — in the Markdown's
trailing "Out of scope" section, so a scope cut is explicit, never silent.

## Cross-Platform Notes

The renderer is pure standard-library Python (`os.path`, and the stdlib `csv`
module for correct quoting/escaping). No shell-isms; identical behavior on macOS,
Linux, WSL, and Windows. Files are written with `\n` line endings via the shared
`antilegacy_core.deliverables` writer. Always invoke through the dispatcher
(`python3 .anti-legacy/run.py <stem>`), never by file path.

## When it runs & prerequisites

Runs any time after the requirements graph exists. The blueprint and config are
**optional** — the plan degrades gracefully:

- **Requirements graph present, blueprint absent** — build subtasks fall back to a
  default layer set (model -> repository -> service -> controller). The plan is
  still complete; refine after `anti-legacy:blueprint` runs.
- **No requirements graph (or no active requirements)** — there is nothing to plan;
  the script exits non-zero with a clear message. Run the pipeline through
  graph-translate first.

## Parameters

- `--no-register` — write both files but do **not** touch the manifest (hermetic
  dry run / preview). Default behavior registers both artifacts.

## Step 1: Confirm the graph is ready

```bash
python3 .anti-legacy/run.py manifest status
```

You want `requirements-graph` registered (and ideally `blueprint-json`). The plan
reads `.anti-legacy/requirements/requirements_graph.json`,
`.anti-legacy/requirements/blueprint.json`, and `.anti-legacy/config.json` from
the standard locations.

## Step 2: Render the plan

Through the dispatcher (the stem is `migration_plan`):

```bash
python3 .anti-legacy/run.py migration_plan
```

This writes both files under `.anti-legacy/deliverables/` and registers them. For
a preview that does not touch the manifest:

```bash
python3 .anti-legacy/run.py migration_plan --no-register
```

## Step 3: Read what was produced and sanity-check it

Open both artifacts and confirm:

- `migration-plan.md` opens with a **Summary** (counts + scope), then one
  `## EPIC-n` per domain followed by the four cross-cutting epics, each with
  numbered `### STORY-n.m` sections and `- [ ]` checkbox **tasks** + nested
  **subtasks**. Each requirement story shows its `Traceability:` line
  (req_id -> legacy_components -> depends).
- The trailing **"Out of scope"** section lists any dropped/unresolvable
  requirements with their reason — or states none were dropped.
- `migration-plan.jira.csv` has the header
  `Issue Type,Summary,Description,Epic Name,Parent,Labels,Order` and one row per
  item. Epics carry an `Epic Name`; every non-epic carries a `Parent` (its
  parent's Summary). Spot-check that it opens cleanly in a spreadsheet / Jira's
  CSV importer.

```bash
python3 .anti-legacy/run.py manifest status | grep deliverable-migration-plan
```

## Done-gate (BLOCKING — assert before trusting the output)

The script enforces these and exits non-zero if any fails (it will not register a
broken plan):

1. **A requirements graph with ≥ 1 active requirement exists.** No graph / no
   active set -> hard error (nothing to plan).
2. **Both files are non-empty** after rendering. An empty artifact aborts before
   registration.

On success it prints the two written paths and the decomposition counts (epics /
stories / tasks / subtasks) and scope (active vs dropped). If the script errors,
surface the message and STOP — do not hand-write a plan or register anything.

## Output

- `.anti-legacy/deliverables/migration-plan.md` — the nested execution plan.
- `.anti-legacy/deliverables/migration-plan.jira.csv` — the Jira-importable backlog.
- Manifest: `deliverable-migration-plan` (markdown) and
  `deliverable-migration-plan-csv` (text) registered, each `produced_by`
  `anti-legacy:migration-plan`, `depends_on` `["requirements-graph",
  "blueprint-json"]`. An `artifact-registered` audit row is appended for each.
  The phase is **not** advanced — registration only.

## Failure cases

- **"No requirements graph found … nothing to plan"** — the graph is absent or has
  no `domains`. Run survey -> extraction -> graph-translate first.
- **"… has no ACTIVE requirements"** — every requirement is dropped/unresolvable.
  There is no build scope; revisit the graph-translator dispositions.
- **Sparse build subtasks** — the blueprint has no component for a requirement, so
  the default layer set was used. Run `anti-legacy:blueprint`, then re-render.
- **Manifest not updated** — you passed `--no-register`, or no manifest exists in
  the workspace (the renderer no-ops registration rather than crashing). Re-run
  without the flag in an initialized workspace to register.
