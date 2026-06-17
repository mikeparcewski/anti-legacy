---
name: "anti-legacy:decisions-log"
description: >
  Render an ADR-style (Architecture Decision Record) decisions log from the
  pipeline's structured decision sources — gate sign-offs (audit.jsonl +
  manifest), the blueprint/config architecture (style, per-domain package,
  migration mode), dropped-requirement scope cuts, and (optionally) git-brain
  decisions. Writes .anti-legacy/deliverables/decisions-log.md as a living draft
  and registers it; never advances the phase. Use when: "decisions log", "ADR",
  "architecture decision record", "why did we decide".
---

# anti-legacy:decisions-log

## Purpose & mental model

This skill renders an **ADR-style decisions log** — one numbered Architecture
Decision Record (ADR-NNN) per decision the pipeline has actually made, each
citing the source it came from. It answers "why did we decide X" with evidence,
not memory.

It is a **deliverable**, not a phase: it renders FROM committed pipeline data and
**registers** its artifact, but it **never advances the phase** (phase
advancement is owned by the phase skills). The log is **living** — registered at
`status: draft` — because decisions accrue over the whole run: gates clear, the
blueprint settles the architecture, scope gets cut, and the team records
rationale. Re-run it whenever a new decision lands; it overwrites and
re-registers the same artifact id.

Decisions are **derived, never coined**. The renderer pulls from four sources and
labels every ADR with its origin:

| # | Source | What becomes an ADR | Status |
|---|---|---|---|
| 1 | **Gate sign-offs** — `audit.jsonl` `gate-signed-off` events + `manifest.gates` | each resolved gate opinion (passed/waived/failed) | Accepted / Accepted (waived) / Superseded |
| 2 | **Architecture** — `blueprint.json` `style` + per-domain `package`; `migration_mode` (config/requirements) | each architectural choice | Accepted |
| 3 | **Scope cuts** — dropped requirements (`disposition == "drop"`) | each explicit drop + its reason | Accepted |
| 4 | **git-brain** — the `decisions` category (OPTIONAL) | each recorded decision | as recorded (default Accepted) |

A gate that is still `pending` is **not** a decision — only resolved opinions
become ADRs. Sources 1-3 are read automatically; source 4 is optional and
agent-supplied (see Step 1).

## Cross-Platform Notes

The renderer is pure standard-library Python — `os.path` for every path, no
shell-isms — so it runs identically on macOS, Linux, WSL, and Windows. It is
invoked through the workspace dispatcher (`python3 .anti-legacy/run.py
decisions_log`), which sets `PYTHONPATH` so `from antilegacy_core import
deliverables` resolves. Workspace state is anchored on the current working
directory, never on `__file__`.

## When it runs & prerequisites

Run it **once the graph is ready** — i.e. once
`.anti-legacy/requirements/requirements_graph.json` exists — and re-run it any
time after a gate is signed off, the blueprint is (re)produced, or a requirement
is dropped. There is no gate or phase precondition; it degrades gracefully when a
source is thin (the section says so).

Minimum to produce a meaningful log: at least one of the four sources has
content. With none, the renderer still writes a non-empty file stating the log is
empty and what will fill it.

## Parameters

The leaf script defaults every path to the standard `.anti-legacy/...` location
(the loaders own the defaults). Flags:

- `--git-brain <file.json>` — a JSON dump of git-brain decisions (Step 1). Absent
  ⇒ the log renders from sources 1-3 alone.
- `--no-register` — write the file but do not touch the manifest (hermetic/dry
  run).
- `--requirements / --blueprint / --config / --audit / --manifest <path>` —
  override a source path (rarely needed).

## Step 1 (optional): dump git-brain decisions to JSON

`git_brain` stores decisions on an orphan branch and its read commands print
**human-readable text, not JSON** — so there is no `--json` flag to pipe. To feed
them in, list the decisions, read the bodies, and assemble a small JSON file.

First check whether a decisions branch even exists and what it holds:

```bash
python3 .anti-legacy/run.py git_brain list --category decisions
```

If that prints `No brain branches found` (or no `decisions` section), skip to
Step 2 — the log will render from sources 1-3 and state that git-brain was not
provided.

If decisions exist, read each one's full body (the `list` output gives you each
`<date>_<slug>.md` path and title):

```bash
python3 .anti-legacy/run.py git_brain read --category decisions --path <YYYY-MM-DD_slug.md>
```

Then assemble a JSON file (e.g. `/tmp/brain-decisions.json`) with this shape —
every field optional; supply what the brain records carry:

```json
{
  "decisions": [
    {
      "id": "2026-06-17_architecture-hexagonal",
      "title": "Adopt hexagonal architecture",
      "statement": "We chose hexagonal over layered to isolate the domain core from adapters.",
      "tags": ["architecture", "hexagonal"],
      "created_at": "2026-06-17T13:27:28+00:00",
      "context": "optional — extra context if the record has it",
      "consequences": "Adapters depend inward on the domain port interfaces."
    }
  ]
}
```

Field mapping the renderer accepts (so you can copy the brain record verbatim):
the decision text may be in `statement`, `body`, `content`, or `decision`; the
date in `created_at` or `date`; plus optional `title`, `tags[]`, `context`,
`consequences`, `status`. A bare top-level list (no `"decisions":` wrapper) is
also accepted. Do **not** read the brain orphan branch with raw `git` — only the
`git_brain` dispatcher subcommands above.

## Step 2: render the log

```bash
python3 .anti-legacy/run.py decisions_log
```

With the git-brain dump from Step 1:

```bash
python3 .anti-legacy/run.py decisions_log --git-brain /tmp/brain-decisions.json
```

The script reads the four sources, numbers the ADRs in a stable grouped order
(gate → architecture → scope → git-brain), writes
`.anti-legacy/deliverables/decisions-log.md`, and registers the artifact. It
prints the written path and the ADR count.

## Step 3: read it and confirm the sources line is honest

Open `.anti-legacy/deliverables/decisions-log.md` and check:

- The **Sources** section states which of the four sources contributed and which
  did not — e.g. "Gate sign-offs: **none recorded yet**" or "git-brain decisions:
  **not provided**". If decisions are missing a source, a "_This log is
  **partial**_" line names the gap. That is correct, surfaced behavior — not an
  error.
- The **Index** table lists every ADR (id | title | status | source).
- Each ADR carries Context / Decision / Consequences and a **Source** line.
- A dropped-requirement ADR cites its `req_id`, `legacy_components`, and the
  `disposition_reason` — the scope cut is explicit, traceable, and reasoned.

If a section reads thin, the upstream source is thin — fix the source (sign the
gate, finish the blueprint, record the brain decision) and re-run. Do not
hand-edit the log; it overwrites on the next render.

## Step 4: confirm registration

```bash
python3 .anti-legacy/run.py manifest check deliverable-decisions-log
python3 .anti-legacy/run.py manifest status | grep deliverable-decisions-log
```

`manifest check` re-checksums the file against the registered artifact; a clean
result means the registered `draft` artifact matches the file on disk.

## Done-gate (assert BEFORE relying on the artifact)

- `.anti-legacy/deliverables/decisions-log.md` exists and is **non-empty** — true
  even when only the config/blueprint-derived decisions exist, and even when zero
  decisions exist (the file still carries the header + Sources section). The
  script refuses to write an empty file.
- The script printed the written path and the ADR count.
- Unless `--no-register` was passed: `deliverable-decisions-log` is registered
  `draft` in the manifest (`produced_by: anti-legacy:decisions-log`, `depends_on:
  ["requirements-graph"]`) and an `anti-legacy:artifact-registered` row was
  appended to `audit.jsonl`. `manifest check` verifies it.

If the file is missing or empty, surface the gap and STOP — do not register.

## Output

| File | Artifact id | Format | Status |
|---|---|---|---|
| `.anti-legacy/deliverables/decisions-log.md` | `deliverable-decisions-log` | markdown | `draft` (living) |

## Still not done (callers should not assume)

- An ADR's **Status** reflects the source signal, not an independent re-review:
  a `passed` gate ⇒ Accepted, a `failed` gate ⇒ Superseded. The log records what
  was decided; it does not re-litigate it.
- The log is only as complete as its sources. "git-brain decisions: not
  provided" means Step 1 was skipped, **not** that no decisions were ever
  recorded — re-run with `--git-brain` to include them.
- This skill does not advance the phase, clear a gate, or modify any source. It
  renders and registers only.

## Failure cases

- **"rendered decisions log is empty; refusing to write"** — should not happen
  (the header always renders); indicates the renderer was called wrongly. Re-run
  through the dispatcher.
- **`--git-brain` file not found / unreadable** — the script prints a warning to
  stderr and renders from sources 1-3 (exit 0). Re-create the JSON (Step 1) if
  you intended to include git-brain.
- **"No manifest found; wrote the log but did not register it."** — the workspace
  has no `manifest.json` yet. Run `anti-legacy:setup` first, or accept the
  unregistered file for a preview.
- **`manifest check` reports drift** — the log was hand-edited after
  registration. Re-run `decisions_log` to regenerate and re-register rather than
  patching by hand.
