---
name: "anti-legacy:setup"
description: >
  Initialize the .anti-legacy/ workspace and manifest for a new modernization project.
  Run once per project before any other anti-legacy skill. Creates directory structure,
  config.json template, audit trail, and seeds git-brain with project context.
  Use when: "start a modernization", "init anti-legacy", "set up a new migration project".
---

# anti-legacy:setup

One-time initialization of the `.anti-legacy/` workspace. Creates all required
directories, the manifest, the audit trail, and seeds git-brain with
project context for future recall.

## Cross-Platform Notes

All shell commands use `python3` for JSON operations and path manipulation —
this works on macOS, Linux, WSL, and Windows Git Bash.

## Dispatcher: how EVERY command runs

Setup is the ONLY skill that calls scripts directly (it has to, because it
writes the dispatcher). After Step 1.5 below, the workspace-relative
dispatcher `.anti-legacy/run.py` exists, and **EVERY subsequent command in
EVERY skill (including the rest of this one) MUST be invoked as**:

```bash
python3 .anti-legacy/run.py <script> <args...>
```

where `<script>` is the bare script stem (no `scripts/` prefix, no `.py`
suffix) — e.g. `python3 .anti-legacy/run.py manifest status`,
`python3 .anti-legacy/run.py git_brain store ...`. The CWD is ALWAYS the
workspace, so `.anti-legacy/run.py` always resolves. `run.py` is a thin exec
shim that re-targets the bare stem to `<plugin_root>/scripts/<script>.py`
using the absolute plugin root baked in at setup time.

## Parameters

- **name** (required): project name slug (e.g. `billing-modernization`)
- **target_stack** (required): target language/framework — `java`, `go`, `dotnet`, `python`, `kotlin`, `typescript`
- **target_path** (required): relative path where generated code will live (e.g. `./target/billing-service`)
- **source_apps** (required): one or more `<name>:<path>:<language>` triples (e.g. `billing:./legacy/cobol-billing:cobol`)
- **architect** (required): name of the human architect / design-review owner (written to `roles.architect`; must be non-empty so the GATE_4 reviewer-independence check is not vacuous). Prompt the user if not supplied.
- **uat_reviewer** (required): name of the independent UAT reviewer (written to `roles.uat_reviewer`; MUST differ from `architect`). Prompt the user if not supplied.
- **deployment_target** (optional): e.g. `gcp-cloud-run`, `aws-ecs`, `azure-aks`, `kubernetes`
- **force** (optional): pass `--force` to overwrite an existing workspace

## Step 1: Initialize workspace and manifest

Run the manifest initializer. The dispatcher (`.anti-legacy/run.py`) does not
exist yet — it is written in Step 1.5 — so this bootstrap call addresses
`manifest.py` by the **absolute plugin root** (`{plugin_root_abs}`, the parent
of `skills/` and `scripts/`, which the agent knows from this skill's own path
`<plugin_root>/skills/setup/SKILL.md`). Do **not** use a bare `scripts/...`
path here: the current working directory is the target workspace, not the
plugin install dir, so a relative path would not resolve.

```bash
python3 {plugin_root_abs}/scripts/manifest.py init \
  --name "{name}" \
  --target-stack "{target_stack}" \
  --target-path "{target_path}"
```

If `--force` was passed, add `--force` to the command.

On success, `.anti-legacy/manifest.json` and `.anti-legacy/audit.jsonl` are created.

## Step 1.5: Write the dispatcher (`.anti-legacy/run.py`)

This is the bootstrap that makes every downstream skill portable. Resolve the
**absolute plugin root** — the directory that contains this skill's parent,
i.e. the parent of `skills/` and `scripts/` (the agent knows it from this
skill's own path: `<plugin_root>/skills/setup/SKILL.md`). Then copy
`templates/run.py` from the plugin root, replacing the `__PLUGIN_ROOT__`
sentinel with the resolved absolute path, and write the result to
`.anti-legacy/run.py` (chmod +x):

```bash
python3 -c "
import os, stat
plugin_root = r'{plugin_root_abs}'   # absolute parent of skills/ and scripts/, resolved by the agent
src = os.path.join(plugin_root, 'templates', 'run.py')
content = open(src).read().replace('__PLUGIN_ROOT__', plugin_root)
dst = '.anti-legacy/run.py'
open(dst, 'w').write(content)
os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print('dispatcher written:', dst, '-> plugin root', plugin_root)
"
```

After this step, `.anti-legacy/run.py` exists and **every command from here
on (in this skill and all other skills) MUST use
`python3 .anti-legacy/run.py <script> <args>`** — never the direct
`scripts/<x>.py` path or any `${CLAUDE_PLUGIN_ROOT}` form.

## Step 2: Write config.json

Parse the `source_apps` triples and write `.anti-legacy/config.json`:

```python
python3 -c "
import json, sys
apps = []
for triple in {source_apps_list}:
    parts = triple.split(':')
    apps.append({'name': parts[0], 'path': parts[1], 'language': parts[2]})
cfg = {
    'project_name': '{name}',
    'source_apps': apps,
    'target_stack': '{target_stack}',
    'target_path': '{target_path}',
    'deployment_target': '{deployment_target}',
    'migration_mode': 'functional',
    'roles': {'architect': '{architect}', 'uat_reviewer': '{uat_reviewer}'}
}
json.dump(cfg, open('.anti-legacy/config.json', 'w'), indent=2)
print('config.json written')
"
```

`roles.architect` and `roles.uat_reviewer` MUST both be non-empty (prompt the
user for any missing value before writing) — the downstream GATE_4 UAT
reviewer-independence check compares these two names and is vacuous if either
is blank or if they are identical. If `architect == uat_reviewer`, stop and
ask the user for a distinct independent reviewer before continuing.

`migration_mode` (default `functional`) is written here and config.json is the
**single source of truth** for it — every downstream skill reads the mode from
config.json. Do not assert a different default elsewhere.

## Step 3: Create patterns seed directories

```bash
python3 -c "
import os
# One patterns dir per source→target pair from config.json
import json
cfg = json.load(open('.anti-legacy/config.json'))
target = cfg['target_stack']
for app in cfg['source_apps']:
    lang = app['language']
    path = f'.anti-legacy/patterns/{lang}-to-{target}'
    os.makedirs(path, exist_ok=True)
    # Write empty index
    idx = f'{path}/index.md'
    if not os.path.exists(idx):
        open(idx, 'w').write(f'# Translation Patterns: {lang} → {target}\n\nAdd patterns here as you discover them.\n')
os.makedirs('.anti-legacy/patterns/learnings', exist_ok=True)
print('Pattern directories created')
"
```

## Step 4: Initialize git-brain

First create the brain's orphan storage branches. Without this `init`, the
subsequent `store` / `ingest` calls exit 1 because their target branches do
not exist:

```bash
python3 .anti-legacy/run.py git_brain init
```

Store project context so future sessions can recall it without re-reading config:

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "Project {name}: modernizing {source_apps} → {target_stack} at {target_path}. Deployment: {deployment_target}. Gates: GATE_1_DESIGN, GATE_2_PLAN, GATE_3_BUILD, GATE_4_UAT. Workspace: .anti-legacy/" \
  --tags "decision,setup,project-context" \
  --category decisions
```

Also, seed the brain with modernization anti-patterns and traversal strategies to enforce architectural best practices. These template files live under the **plugin root**, not the workspace, so they MUST be referenced by the absolute plugin root (`{plugin_root_abs}/templates/...`) exactly like Step 1's `init` and Step 1.5's dispatcher write — a workspace-relative `templates/...` path does NOT resolve here (the CWD is the target workspace) and `git_brain ingest` would fail with `File not found`:

```bash
python3 .anti-legacy/run.py git_brain ingest \
  --file {plugin_root_abs}/templates/anti_patterns.md \
  --category patterns \
  --tags "anti-pattern,architecture,infrastructure" \
  --type reference

python3 .anti-legacy/run.py git_brain ingest \
  --file {plugin_root_abs}/templates/traversal_strategies.md \
  --category patterns \
  --tags "traversal,architecture,planning,risk" \
  --type reference
```

## Step 5: Done-gate, advance, and confirm

Before advancing, assert the setup actually produced its two load-bearing
artifacts — the dispatcher (`run.py`) and the manifest (`manifest.json`).
**If this assertion FAILS, do NOT run `advance`; surface the missing
artifact(s) to the user and stop** (they can re-run setup). `advance` is
CONDITIONAL on this assertion passing:

```bash
python3 -c "
import os, sys
required = ['.anti-legacy/run.py', '.anti-legacy/manifest.json']
missing = [p for p in required if not os.path.isfile(p)]
if missing:
    sys.stderr.write('SETUP INCOMPLETE — missing: ' + ', '.join(missing) + '\n')
    sys.exit(1)
print('setup done-gate OK:', ', '.join(required))
sys.exit(0)
"
```

Only on success, advance to the `survey` phase (the legal first post-setup
enum value — there is no `setup` phase) and confirm:

```bash
python3 .anti-legacy/run.py manifest advance survey
python3 .anti-legacy/run.py learn_coordinator --phase setup
python3 .anti-legacy/run.py manifest status
```

Report to the user:
- Workspace path: `.anti-legacy/`
- Dispatcher written: `.anti-legacy/run.py` (all skills invoke scripts via `python3 .anti-legacy/run.py <script> <args>`)
- Roles recorded: architect=`{architect}`, uat_reviewer=`{uat_reviewer}`
- Next step: run `anti-legacy:survey` to scan the legacy codebase
- Pattern directories created for each source→target language pair
- Brain seeded with modernization anti-patterns (line-by-line, microservices, infrastructure)
