---
name: "anti-legacy:decisions-log"
description: >
  Render an ADR-style (Architecture Decision Record) decisions log from the
  pipeline's structured decision sources — gate sign-offs (audit.jsonl +
  manifest), the blueprint/config architecture (style, per-domain package,
  migration mode), and dropped-requirement scope cuts. Writes
  .anti-legacy/deliverables/decisions-log.md as a living draft and registers it;
  never advances the phase. Use when: "decisions log", "ADR",
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

A gate that is still `pending` is **not** a decision — only resolved opinions
become ADRs. Sources 1-3 are read automatically.

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

- `--no-register` — write the file but do not touch the manifest (hermetic/dry
  run).
- `--requirements / --blueprint / --config / --audit / --manifest <path>` —
  override a source path (rarely needed).

## Step 1: render the log

```bash
python3 .anti-legacy/run.py decisions_log
```

The script reads the three sources, numbers the ADRs in a stable grouped order
(gate → architecture → scope), writes
`.anti-legacy/deliverables/decisions-log.md`, and registers the artifact. It
prints the written path and the ADR count.

## Step 2: read it and confirm the sources line is honest

Open `.anti-legacy/deliverables/decisions-log.md` and check:

- The **Sources** section states which of the three sources contributed and which
  did not — e.g. "Gate sign-offs: **none recorded yet**". If decisions are missing
  a source, a "_This log is **partial**_" line names the gap. That is correct,
  surfaced behavior — not an error.
- The **Index** table lists every ADR (id | title | status | source).
- Each ADR carries Context / Decision / Consequences and a **Source** line.
- A dropped-requirement ADR cites its `req_id`, `legacy_components`, and the
  `disposition_reason` — the scope cut is explicit, traceable, and reasoned.

If a section reads thin, the upstream source is thin — fix the source (sign the
gate, finish the blueprint) and re-run. Do not hand-edit the log; it overwrites
on the next render.

## Step 3: confirm registration

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
- The log is only as complete as its sources. If a section reads thin, the
  upstream source is thin — sign the gate, finish the blueprint, or record the
  scope cut, then re-run.
- This skill does not advance the phase, clear a gate, or modify any source. It
  renders and registers only.

## Failure cases

- **"rendered decisions log is empty; refusing to write"** — should not happen
  (the header always renders); indicates the renderer was called wrongly. Re-run
  through the dispatcher.
- **"No manifest found; wrote the log but did not register it."** — the workspace
  has no `manifest.json` yet. Run `anti-legacy:setup` first, or accept the
  unregistered file for a preview.
- **`manifest check` reports drift** — the log was hand-edited after
  registration. Re-run `decisions_log` to regenerate and re-register rather than
  patching by hand.
