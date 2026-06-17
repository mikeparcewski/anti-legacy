---
name: "anti-legacy:expert"
description: >
  The anti-legacy pipeline internals SME, and the home of the shared
  antilegacy_core Python library. Use when: understanding or operating the
  pipeline core, diagnosing a core module (estate seam, requirements-graph
  builder, coverage, manifest state machine, validator), checking pipeline
  readiness, or extending antilegacy_core.
---

# anti-legacy-expert — pipeline internals & shared core

This skill is the subject-matter reference for the anti-legacy pipeline's shared
Python core, and it physically hosts that core at `scripts/antilegacy_core/`. The
library ships with the portable bundle because the skills standard delivers a
skill's entire directory subtree (symlink or `--copy`).

## The shared core (`antilegacy_core`)

All cross-cutting pipeline logic lives in one namespaced, collision-proof package
(no more bare `import coverage` shadowing stdlib / `pytest-cov`):

| Module | Responsibility |
|--------|----------------|
| `estate` | The wicked-estate engine seam — resolve binary, index/query/source/annotate the code graph. |
| `coverage` | Resolved-or-flagged coverage over the graph + `annotations.jsonl`. |
| `extract` | Business-rule extraction from source slices. |
| `vocabulary` | Glossary / domain-term mining + curation. |
| `domain_graph` | The requirements-graph builder (§I5). |
| `normalizer` | Code-graph → draft requirements scaffold. |
| `comparator` | Graph diff / round-trip comparison. |
| `manifest` | The pipeline state machine — phases, gates, audit. |
| `validator` | The build / semantic / UAT evidence verifier. |
| `planner` | Task-list planning helpers. |
| `preflight()` (in `__init__`) | Host-agnostic readiness check (engine, deps, workspace). |

The pipeline JSON schemas ship as package data under `antilegacy_core/schemas/`
and are read via `importlib.resources` — one source of truth, versioned with the
code.

## How it is invoked

Skills never import the core directly. They dispatch through the workspace seam:

```
python3 .anti-legacy/run.py <stem> <args...>
```

`run.py` resolves `<stem>` to a library module and runs
`python -m antilegacy_core.<stem>`, with the package on `PYTHONPATH`. **Workspace
state (config, annotations, graphs) is anchored on the current working directory —
never on the package's `__file__`** — so the core behaves identically whether the
bundle was installed as a plugin (whole repo) or via the skills standard (flat
per-skill dirs).

## Readiness

```
python3 .anti-legacy/run.py preflight
```

Reports the engine (wicked-estate ≥ 0.5.1), Python deps, and workspace state, with
the exact remediation for anything missing. Fails fast — never silently degrades.

## Extending the core

Add a module under `scripts/antilegacy_core/`. If it imports another core module,
it belongs here (the cross-importing core stays together). Single-consumer leaf
logic belongs in its owning phase skill's `scripts/`, not here.
