---
name: "anti-legacy:planner"
description: >
  Decompose the blueprint into a concrete, ordered task list for the build swarm.
  One task per requirement node, scoped to ≤150 lines of target code. Tasks are
  topologically sorted by dependency order (data layer before service layer before API layer).
  Produces task.md for the swarm. Requires GATE_2_PLAN sign-off.
  Use when: "create the task list", "plan the build", "decompose the blueprint",
  "what order do we build things", "generate task.md".
---

# anti-legacy:planner

Decomposes the approved blueprint into a concrete, ordered task list. The output
drives the `anti-legacy:swarm` build phase — each task is a self-contained unit
of work for a single developer subagent.

## Cross-Platform Notes

All file operations use the agent's native Read/Write tools.

## Parameters

- **max_lines** (optional): max target lines per task. Defaults to 150. Tasks that
  would exceed this are automatically split into sub-tasks.

## Step 1: Verify GATE_1_DESIGN is cleared

```bash
python3 -c "
import json, sys
m = json.load(open('.anti-legacy/manifest.json'))
g = m['gates']['GATE_1_DESIGN']
if g['status'] != 'passed':
    print(f'BLOCKED: GATE_1_DESIGN is {g[\"status\"]}. Run anti-legacy:gatekeeper to verify sign-off first.')
    sys.exit(1)
print('GATE_1_DESIGN: cleared ✓')
"
```

Halt if GATE_1_DESIGN is not `passed`.

## Step 2: Query git-brain for planning patterns

```bash
python3 .anti-legacy/run.py git_brain search \
  --query "task decomposition planning {target_stack} build order dependency" \
  --limit 5
```

## Step 3: Read blueprint and requirements

Read:
- `.anti-legacy/requirements/blueprint.json` — components, build order, dependencies
- `.anti-legacy/requirements/requirements_graph.json` — business rules per node

## Step 4: Topological sort of tasks

Compute the build order respecting inter-requirement dependencies. The ordering follows the `traversal_strategy` (bottom-up, top-down, vertical-slice) defined in `.anti-legacy/config.json` (defaults to `bottom-up`).

Run the sorting tool:

```bash
python3 .anti-legacy/run.py planner_utils \
  --requirements-graph .anti-legacy/requirements/requirements_graph.json \
  --config .anti-legacy/config.json
```

Or run the equivalent Python sorting logic:

```python
python3 -c "
import json, os
from scripts.planner_utils import sort_requirements, get_dependencies_and_domains

# Load config to get traversal strategy
strategy = 'bottom-up'
if os.path.exists('.anti-legacy/config.json'):
    cfg = json.load(open('.anti-legacy/config.json'))
    strategy = cfg.get('traversal_strategy', 'bottom-up')

rg = json.load(open('.anti-legacy/requirements/requirements_graph.json'))
order = sort_requirements(rg, strategy)
_, req_to_domain = get_dependencies_and_domains(rg)

print(f'Build order (Strategy: {strategy}):')
for i, req_id in enumerate(order, 1):
    print(f'  {i}. {req_id} ({req_to_domain.get(req_id, \"unknown\")})')
"
```

## Step 5: Estimate task scope and split if needed

For each requirement node in build order, estimate line count AND an hours figure
(the task.md contract records **hours**, not lines — see Step 7). Rough mapping:

- **Data model class** (Layer 0): ~30–50 lines / ~1–2h per entity
- **Repository interface** (Layer 1): ~20–40 lines / ~1–2h
- **Service class** (Layer 2): ~50–150 lines / ~3–8h per requirement (varies by rule count)
- **REST controller / batch entry point** (Layer 3): ~30–60 lines / ~2–4h per endpoint
- **Unit test class**: ~50–100 lines (folded into the owning task's hours)

No task may exceed **8h** (GATE_2 checklist rejects any estimate > 8h). If a service node has >10 business rules, split into:
- `{req_id}-core`: primary calculation/processing logic
- `{req_id}-validation`: input validation rules
- `{req_id}-error-paths`: error handling and rollback

## Step 6: Assign task layers

Group tasks into four build layers:

1. **Layer 0 — Data models**: entity classes and ORM mappings (no dependencies)
2. **Layer 1 — Repositories**: data access interfaces and implementations
3. **Layer 2 — Services**: business logic components (depends on Layer 1)
4. **Layer 3 — API / Entry points**: controllers, batch runners, listeners (depends on Layer 2)

## Step 7: Write task.md

Each checkbox is **one line** in the contract format, then optional sub-bullets for
the build metadata the swarm needs:

```
- [ ] [REQ_ID] File.java — Nh [— depends: REQ_OTHER]
```

- `[REQ_ID]` — the requirement node id (always set; this is the traceability anchor).
- `File.java` — the primary target file for the task.
- `Nh` — the HOURS estimate (mandatory on every task; ≤ 8h, see Step 5). Not a line count.
- `— depends: REQ_OTHER` — optional, the requirement(s) this task depends on. A
  dependency MUST resolve to a task in the **same or an earlier layer** — never a later
  one (topological order; asserted in Step 8).

The four layers are fixed and **Layer 3 is always last**:
Layer 0 = data models · Layer 1 = repositories · Layer 2 = services · Layer 3 = API/batch entry points.

Write `.anti-legacy/task.md`:

```markdown
# Build Tasks — {project_name}

**Target stack**: {target_stack}  
**Total tasks**: {task_count}  
**Build layers**: 4  

## Layer 0 — Data Models
These tasks have no dependencies and can be built first or in parallel.

- [ ] [{req_id}] {ClassName}.{ext} — {N}h
  - Source: `{legacy_file}`
  - Target: `{target_path}/{package}/model/{ClassName}.{ext}`
  - Schema: {entity schema from blueprint}
  - Pattern: `{source_lang}-to-{target_stack}/entity-mapping`
  - Owner: `unassigned`
  - Verification Status: `UNTESTED`
  - Audit Trail: `not-started`

## Layer 1 — Repositories
Build after Layer 0.

- [ ] [{req_id}] {Name}Repository.{ext} — {N}h — depends: {req_id_layer0}
  - Owner: `unassigned`
  - Verification Status: `UNTESTED`
  - Audit Trail: `not-started`
  ...

## Layer 2 — Services
Build after Layer 1. These are the primary translation targets.

- [ ] [{req_id}] {ClassName}Service.{ext} — {N}h — depends: {req_id_layer1}
  - Source: `{legacy_file}` (COBOL program / Java class / VB6 module)
  - Target: `{target_path}/{package}/service/{ClassName}.{ext}`
  - Business rules: {rule_count} rules (see requirements_graph.json: {req_id})
  - Data access: {tables_accessed}
  - Test contract: `.anti-legacy/contracts/{domain}/{req_id}.contract.json`
  - Owner: `unassigned`
  - Verification Status: `UNTESTED`
  - Audit Trail: `not-started`

## Layer 3 — API / Entry Points
Build after Layer 2. Always the last layer.

- [ ] [{req_id}] {ClassName}Controller.{ext} — {N}h — depends: {req_id_layer2}
  - Owner: `unassigned`
  - Verification Status: `UNTESTED`
  - Audit Trail: `not-started`
  ...

## Completion Criteria

Each task is DONE when:
1. Target file compiles without errors
2. Unit test file exists with at least 1 happy path + 1 error scenario
3. Tests pass: `{stack_test_command}`
4. Task checkbox is ticked in this file and committed to git
```

## Step 8: Done-gate, register artifact, and advance to GATE_2

**Done-gate (BLOCKING).** Before registering or advancing, assert the planner's own
contract. All three checks must pass; if any fails, do NOT run `register --status draft`
and do NOT run `advance` — surface the specific gap to the user and stop. The user may
fix the plan and retry. The register and advance steps below are CONDITIONAL on this
assertion passing.

1. **One task per active requirement** — the number of checkbox tasks in `task.md`
   MUST equal the number of active requirements in `requirements_graph.json` (no more,
   no fewer). Surface which requirements have no task, or which tasks have no requirement.
2. **Hours on every task** — every checkbox MUST carry an `Nh` estimate. A task with no
   hours figure is incomplete.
3. **Valid topological order** — no task may `depends:` on a requirement that lives in a
   LATER layer. Layer order is 0 → 1 → 2 → 3; a dependency must point to the same or an
   earlier layer.

```bash
python3 -c "
import json, re, sys
rg = json.load(open('.anti-legacy/requirements/requirements_graph.json'))
active = set()
for dom in rg.get('domains', {}).values():
    for req_id, req in dom.get('requirements', {}).items():
        if req.get('status', 'active') != 'inactive':
            active.add(req_id)

task_md = open('.anti-legacy/task.md', encoding='utf-8').read()

# Map each task's req_id to its layer, and capture its hours + declared deps.
# Format: '- [ ] [REQ_ID] File.ext — Nh [— depends: REQ_A, REQ_B]'
req_layer, missing_hours, deps = {}, [], {}
cur_layer = None
for line in task_md.splitlines():
    h = re.match(r'##\s*Layer\s*(\d+)', line)
    if h:
        cur_layer = int(h.group(1)); continue
    m = re.match(r'-\s*\[[ xX]\]\s*\[([^\]]+)\]', line)
    if not m:
        continue
    req = m.group(1)
    req_layer[req] = cur_layer
    if not re.search(r'\b\d+(?:\.\d+)?\s*h\b', line):
        missing_hours.append(req)
    dep_m = re.search(r'depends:\s*(.+)$', line)
    deps[req] = [d.strip() for d in re.split(r'[,;]', dep_m.group(1))] if dep_m else []

tasks = set(req_layer)
errors = []

# 1. one task per active requirement
if tasks != active:
    no_task = active - tasks
    no_req = tasks - active
    if no_task: errors.append(f'requirements with no task: {sorted(no_task)}')
    if no_req:  errors.append(f'tasks with no active requirement: {sorted(no_req)}')

# 2. hours on every task
if missing_hours:
    errors.append(f'tasks missing an hours (Nh) estimate: {sorted(missing_hours)}')

# 3. valid topological order — no dep on a later layer
for req, dlist in deps.items():
    for d in dlist:
        if d in req_layer and req_layer[d] > req_layer.get(req, -1):
            errors.append(f'{req} (layer {req_layer.get(req)}) depends on {d} in later layer {req_layer[d]}')

if errors:
    print('BLOCKED: task.md fails the planner done-gate:')
    for e in errors:
        print('  - ' + e)
    print('Fix task.md before proceeding; do NOT advance.')
    sys.exit(1)
print(f'Done-gate: {len(tasks)} tasks == {len(active)} active requirements; hours present; topological order valid ✓')
"
```

If the done-gate passes, register and advance:

```bash
python3 .anti-legacy/run.py manifest register task-plan \
  --path task.md \
  --format markdown \
  --produced-by anti-legacy:planner \
  --status draft \
  --depends-on blueprint-json

python3 .anti-legacy/run.py manifest advance planning
python3 .anti-legacy/run.py learn_coordinator --phase "planner"
```

Tell the user:
- Task plan is at `.anti-legacy/task.md` — review it before proceeding
- **Pipeline paused at GATE_2_PLAN** — share for PM + Tech Lead review
- After sign-off, record the gate with
  `python3 .anti-legacy/run.py manifest gate GATE_2_PLAN --opinion passed --evaluator "<reviewer>" --rationale "<note>" --evidence task-plan`
  (the `task-plan` artifact is the GATE_2_PLAN evidence), then run `anti-legacy:swarm`

## Step 9: Adversarially self-review the task plan (advisory — AGENTS.md §8)

The Step 8 done-gate is mechanical — it counts tasks against active requirements,
checks every task carries hours, and verifies no dependency points at a later layer. It
cannot see a task whose hours are wildly under-scoped for its rule count, a layer
assignment that misreads a service as a model, or a build order that will deadlock the
swarm. Before you report done, adversarially review the `task.md` you just produced —
the topological check is trusting; this is the loop that distrusts it. Resolve the
single-artifact critic target, then dispatch the read-only critic against it:

```bash
python3 .anti-legacy/run.py refine_loop descriptor --artifact task-plan --json
```

That resolves the rendered file + the source data the critic must cross-check (the
requirements-graph §2 spine + this artifact's manifest `depends_on` — the blueprint).
Dispatch `anti-legacy:adversarial-review` (single-artifact mode) against the descriptor.
On `REVISE`/`BLOCK`, run the bounded loop — `refine_loop decide --verdict <v> --attempt
<n> --artifact task-plan` — re-running `anti-legacy:planner` to fix at source and
re-reviewing, capped at §7's three attempts (then recon), or proceed under a **stated**
`--forced` override. **Advisory: it clears no gate (GATE_2_PLAN is still a human
sign-off) and advances no phase.**

## Output

- `.anti-legacy/task.md` — ordered, layered task list with completion checkboxes
- Manifest: phase = `planning`, artifact `task-plan` registered

**Next step**: Human review of task.md → `anti-legacy:gatekeeper` for GATE_2_PLAN sign-off → `anti-legacy:swarm`.
