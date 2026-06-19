---
name: "anti-legacy:swarm"
description: >
  Spawn developer subagents to translate legacy code into target stack code, one
  task at a time. Each subagent receives a micro-context (requirement, data schemas,
  translation patterns) and writes the target file + unit tests. Works task-by-task
  through task.md in dependency order. Requires GATE_2_PLAN sign-off.
  Use when: "build the code", "start translating", "run the swarm", "translate task X",
  "implement the requirements".
---

# anti-legacy:swarm

Coordinates the build phase. Reads tasks from `task.md` in dependency order,
assembles micro-context for each task, and dispatches a developer subagent to
write the target code and tests.

Each subagent gets only what it needs — no full codebase context — to minimize
token usage and keep outputs deterministic and scoped.

## Cross-Platform Notes

Git-brain calls use `python3`. Source file reading uses the native Read tool.

## Parameters

- **task_id** (optional): run a single task by ID (e.g. `TASK-020`). Defaults to next uncompleted task.
- **layer** (optional): run all uncompleted tasks in a specific layer (`0`, `1`, `2`, `3`).
- **dry_run** (optional): print the context that would be sent to the subagent without spawning.

## Step 1: Verify GATE_2_PLAN is cleared

```bash
python3 -c "
import json, sys
m = json.load(open('.anti-legacy/manifest.json'))
g = m['gates']['GATE_2_PLAN']
if g['status'] != 'passed':
    print(f'BLOCKED: GATE_2_PLAN is {g[\"status\"]}. Run anti-legacy:gatekeeper to verify sign-off first.')
    sys.exit(1)
print('GATE_2_PLAN: cleared ✓')
"
```

## Step 2: Select the next task

Read `.anti-legacy/task.md` and find the first uncompleted task (`- [ ]`) in
the current layer (Layer 0 → 1 → 2 → 3 sequentially).

If a specific `task_id` was passed, jump to that task.

If all tasks in a layer are complete, advance to the next layer. If all layers
are complete, report completion and suggest `anti-legacy:target-review`.

## Step 3: Assemble micro-context for the task

This is critical for token efficiency — the subagent should receive the minimum
context needed to write correct code.

Gather:

**3a. The requirement node**

Read the requirement from `.anti-legacy/requirements/requirements_graph.json`:
- `business_rules` list
- `validations` list
- `data_access` list
- `dependencies` list
- `legacy_components` (the source file path)

**3b. The target specification from blueprint**

Read the component spec from `.anti-legacy/requirements/blueprint.json`:
- `class_name`, `type` (service/handler/batch)
- `api` spec (if applicable)
- Target file path
- Entity schemas for entities in `data_access`

**3c. The legacy source file**

Read the legacy source file using the native Read tool (the agent does this,
not the subagent — the subagent gets the extracted rules, not the raw legacy code).

**3d. Translation patterns**

Read `.anti-legacy/patterns/{source_lang}-to-{target_stack}/index.md` and
any matching pattern files for this task's categories.

**3e. The test contract**

Read `.anti-legacy/contracts/{domain}/{req_id}.contract.json` for the expected
test scenarios.

## Step 4: Dispatch the developer subagent

**Model tier: strongest — see [AGENTS.md](../../AGENTS.md) §9 (model-tier routing).** Code
translation must preserve every business rule and numeric precision faithfully;
a silent rule drop or COMP-3 slip is catastrophic, so the *dispatched developer
subagent* is recommended to run on the strongest available tier. This is a
recommendation the host/orchestrator SHOULD honor — the plugin cannot force a
model; a single-model runtime simply runs it on what it has. (The swarm
coordinator's own work — context assembly + sequencing — is mid-tier per §9.)

Using Antigravity's agent dispatch mechanism, invoke the `developer` agent with
the assembled micro-context:

```
anti-legacy:developer

## Task: {task_id} — {task_title}

### Target file
{target_file_path}

### Target language / stack
{target_stack}

### Class name
{ClassName}

### Business rules to implement
{business_rules_numbered_list}

### Validation rules
{validation_rules_list}

### Data entities accessed
{entity_names_with_schemas_json}

### Dependencies (already implemented classes you can import)
{dependency_class_names}

### API specification (if applicable)
Method: {http_method} {path}
Request: {request_shape}
Response: {response_shape}

### Translation patterns to follow
{pattern_content}

### Test contract
File: .anti-legacy/contracts/{domain}/{req_id}.contract.json
Implement tests for all scenarios in the contract.
Test file path: {test_file_path}

### Completion criteria
1. Target file compiles
2. All test scenarios in contract pass
3. No business rules omitted
4. No legacy-specific constructs (COMP-3, GOTO, PERFORM) in target code
5. **RULE COVERAGE**: Done = every `business_rule` has a conditional, every
   `validation` a check, every `error_path` a handler, each annotated with
   `@ImplementsRule("<id>")` (the rule/validation/error id, e.g.
   `@ImplementsRule("RULE-001")`). Report `rules_implemented/total`; anything
   `<100%` is NOT done — re-dispatch.
6. **STUB DETECTION**: The generated file MUST contain real business logic.
   A file that simply returns a hardcoded string (e.g., `return "...executed successfully"`) 
   or a `System.out.println()` is NOT an implementation — it is a stub.
   The file MUST have:
   - Method bodies that reference entity fields from the data_access schemas
   - Conditional logic implementing the business_rules
   - Validation checks implementing the validations array
   - Error handling implementing the error_paths array
   - Minimum 30 LOC for services, 40 LOC for controllers, 50 LOC for batch jobs
```

For CLIs that do not support `@agent` dispatch, run inline with the above
context as your own instruction set.

### Java rule-annotation setup (multi-rule mappings)

The round-trip scanner (`generate_target_graph.py`) proves rule coverage by
reading `@ImplementsRule("<id>")` annotations (alias `@SatisfiesRule`) from the
target source and joining each rule id back to the requirements graph. One
component frequently implements **several** rules — a service that enforces
`RULE-001`, `RULE-002`, and `VAL-003`. To map all of them onto one element you
**stack** the annotation:

```java
@ImplementsRule("RULE-001")
@ImplementsRule("RULE-002")
@ImplementsRule("VAL-003")
public class InterestCalculator { ... }
```

Plain Java does **not** allow the same annotation twice on one element. The
anti-legacy standard library ships the two annotation declarations that make
the repeat legal — `@ImplementsRule` declared `@Repeatable(ImplementsRules.class)`
with a `String value()`, and the container `@ImplementsRules` with an
`ImplementsRule[] value()`. The compiler folds the stacked repeats into the
container automatically; without the container, stacking is a compile error
(`ImplementsRule is not a repeatable annotation type`).

**When the target stack is Java/Kotlin (or any JVM `target_stack`), the
coordinator MUST ensure these templates exist in the target tree before
dispatching annotation-bearing tasks.** They are bundled in the developer skill (`assets/`), so copy
them once (idempotent) into a `annotations` package under the target source
root, then change each file's `package` line to match the target's package
layout:

```bash
# Copy the two standard rule-annotation templates into the target tree.
# {plugin_root_abs} is the resolved plugin root (same one setup baked into run.py);
# {target_path} comes from config.json. The destination package dir is created if absent.
python3 -c "
import os, shutil, sys
plugin_root = r'{plugin_root_abs}'
dest = os.path.join(r'{target_path}', 'src', 'main', 'java', 'annotations')
os.makedirs(dest, exist_ok=True)
for name in ('ImplementsRule.java', 'ImplementsRules.java'):
    src = os.path.join(plugin_root, 'skills', 'developer', 'assets', name)
    out = os.path.join(dest, name)
    if not os.path.exists(out):
        shutil.copyfile(src, out)
        print('copied', name, '->', out)
    else:
        print('exists', out)
print('NOTE: edit the package line in both copies to match the target package layout.')
"
```

Then, in the dispatch context for a multi-rule task, instruct the subagent
explicitly: *"Import `<your.package>.annotations.ImplementsRule` and stack one
`@ImplementsRule("<id>")` per rule/validation/error id you implement on the
class or method — the annotation is `@Repeatable`, so multiple are allowed.
Do not invent a new annotation name; the scanner only matches `@ImplementsRule`
and `@SatisfiesRule`."* Stacking N annotations records N rule ids; a single
component therefore covers all of its rules without a compiler error and the
round-trip coverage proof sees every one.

For non-JVM stacks the scanner uses other anchors — C# attribute
`[ImplementsRule("<id>")]` (also stackable in C#), or a bare rule-id
line-comment (`// RULE-001`, `# RULE-001`, etc.) for Go/Python/TypeScript. The
two Java templates apply only to the JVM target path.

## Step 5: Review and merge subagent output

After the subagent completes:

1. Read the generated target file — verify it exists and is non-empty
2. **STUB DETECTION PRE-FLIGHT** — reject the output if ANY of these are true:
   - The entire method body is a single `return "...";` statement
   - The entire method body is a single `System.out.println("...");` statement
   - The file has fewer than 30 lines (services), 40 lines (controllers/handlers),
     or 50 lines (batch jobs)
   - The file does not import any entities from the `data_access` list
   - The file contains zero conditional statements (`if`, `switch`, ternary)
   
   If stub detected: re-dispatch the subagent with an explicit note:
   "Your output was a stub. You MUST implement the business_rules/validations/
   error_paths from your micro-context — each annotated with
   @ImplementsRule(\"<id>\"). Do not read the legacy source; the extracted rules
   in your context are the contract."
3. Check that the test file exists and references the test scenarios from the contract
4. Run the stack's syntax check via the validator dispatcher (non-blocking here —
   capture errors but don't halt the swarm; GATE_3_BUILD enforces compilation later
   at target-review):
   ```bash
   python3 .anti-legacy/run.py validator_discovery run \
     --gate GATE_3_BUILD --workspace {target_path} --config .anti-legacy/config.json
   ```
4. If syntax errors exist, pass them back to the subagent for correction (one correction loop)

## Step 6: Tick the task and record learning

In `task.md`, change `- [ ]` to `- [x]` for the completed task.

Append non-obvious translation patterns to the patterns index:

```bash
python3 .anti-legacy/run.py manifest learn "{task_id}-pattern" \
  --path "patterns/{source_lang}-to-{target_stack}/learnings/{task_id}.md" \
  --tags "{source_lang},{target_stack},{construct_type}"
```

## Step 7: Continue to next task

Ask: "Continue with the next task?" and repeat from Step 2. When all tasks
in all layers are complete:

1. **DONE-GATE before advancing the phase.** The swarm only advances the
   pipeline from `planning` to `build` when the build is genuinely complete.
   Run a content assertion that proves EVERY task is ticked AND no task file
   still carries a stub marker (the Step 5 stub-detection signal). This is
   cross-platform (pure python, nonzero exit on a broken assertion):
   ```bash
   python3 -c "
   import re, sys
   text = open('.anti-legacy/task.md', encoding='utf-8').read()
   open_tasks = re.findall(r'(?m)^\s*-\s*\[ \]', text)
   if open_tasks:
       print(f'NOT DONE: {len(open_tasks)} task(s) still unchecked in task.md — re-dispatch the swarm.')
       sys.exit(1)
   # Stub guard: any task explicitly flagged STUB (Step 5 stub-detection) blocks the advance.
   if re.search(r'(?im)\bSTUB\b', text):
       print('NOT DONE: a task is flagged as a STUB (Step 5 stub-detection) — re-dispatch the swarm.')
       sys.exit(1)
   print('DONE: all tasks ticked and no stub flagged ✓')
   sys.exit(0)
   "
   ```
   - If this assertion FAILS (any task incomplete OR any stub detected), do NOT
     run `manifest advance build`. Surface the specific gap to the user and
     re-dispatch the swarm from Step 2 for the incomplete/stubbed task. The
     advance is CONDITIONAL on this assertion passing.
   - Only on success, advance the phase from `planning` to `build`:
     ```bash
     python3 .anti-legacy/run.py manifest advance build
     ```

2. Report:
- Total tasks completed
- Tasks with correction loops (quality signal)
- Suggest running `anti-legacy:target-review` to compile the full target codebase

## Token efficiency rules

- Never send full legacy source code to subagents — send extracted rules only
- Never send the full requirements graph — send only the single node being built
- Never send all translation patterns — send only the relevant category
- Limit entity schemas to fields actually accessed by this requirement

## Output

- Target code files in `{target_path}/...`
- Unit test files alongside target code
- `.anti-legacy/task.md` updated with completion checkboxes
- Patterns library: new patterns indexed per task

**Next step** (after all tasks): `anti-legacy:target-review` to compile and verify the full codebase.
