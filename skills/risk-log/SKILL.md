---
name: "anti-legacy:risk-log"
description: >
  Render a living migration risk log / register, mined deterministically from the
  committed pipeline data — RISK-flagged graph nodes, low-confidence rules, dropped
  requirements, COMP-3 / parity precision risks, coverage holes, unresolvable /
  rule-less requirements, and cross-language seams. Every row traces back to its
  source (req_id, graph node, or file). Output is .anti-legacy/deliverables/risk-log.md,
  registered as a manifest deliverable (status=draft) — never advances the phase.
  Use when: "risk log", "risk register", "what are the migration risks",
  "risk assessment".
---

# anti-legacy:risk-log

A **living risk register** for the modernization. It does not coin prose — it
**mines** risks deterministically from the structured artifacts the pipeline has
already produced and lays them out as a numbered register a reviewer can act on.

The mental model: the pipeline accumulates evidence (the annotated graph, the
coverage report, the requirements graph with dispositions, the blueprint, the
test contracts). Each of those carries *latent risk signals* — a node extraction
could not resolve, a rule stated below the confidence threshold, a capability
that was dropped, a COMP-3 field that loses precision silently. This skill reads
those signals and renders them as a single risk log, so nobody has to eyeball six
JSON files to answer "what could go wrong in this migration?".

Because it is **living**, it is registered with status **draft** and is meant to
be re-rendered after each phase that changes the evidence (extraction,
graph-translate, blueprint, test-strategy). Re-running overwrites the file and
updates the same artifact id.

**Traceability is mandatory** (§2 of AGENTS.md): every risk row carries a
**Source** column that points back to a `req_id`, a graph node (`db_id/symbol_id`),
or a file — the thread to evidence never breaks.

## Cross-Platform Notes

The renderer is pure standard-library Python plus `antilegacy_core.deliverables`
— every path is built with `os.path`, no shell-isms. It runs identically on
macOS, Linux, WSL, and Windows. Output is plain Markdown with GitHub tables,
readable in any viewer or `git diff`. It anchors on the workspace
(`os.getcwd()`), never on `__file__`.

## When it runs and prerequisites

This is a **"graph is ready"** deliverable: it runs any time after
`graph-translate` has produced `.anti-legacy/requirements/requirements_graph.json`.
The graph is the **only hard prerequisite** — it is the risk spine. Every other
source (annotations, coverage report, blueprint, contracts, config) is *optional*
and only enriches the log; when one is absent the renderer mines what it can and
**states the gap** in a "Sources assessed" section (it never crashes on a partial
workspace and never pretends an unassessed source is risk-free).

The richest log is rendered late (after blueprint + test-strategy, so the parity
and contract sources are populated), but an early run right after graph-translate
is valid and useful.

## Risk sources mined

| # | Source | From | Default L / I |
|---|---|---|---|
| 1 | RISK-flagged nodes | `annotations.jsonl` rows with `status=="risk"` | H / M |
| 2 | Low-confidence rules | `business_rules` with `confidence < coverage.resolve_threshold` (default 0.75) | M / M |
| 3 | Dropped requirements | `disposition=="drop"` (intentional scope cut) | M / M |
| 4 | Parity / COMP-3 precision | contract `parity_rules` + numeric (DECIMAL/COMP-3) entity fields (blueprint + graph) | M / **H** |
| 5 | Coverage holes | `coverage-report.json` `unaccounted_nodes` | M / M |
| 6 | Unresolvable / rule-less | `status=="unresolvable"` or a requirement with **no** `business_rules` | H / M |
| 7 | Cross-language / cross-repo seams | `config.source_apps` spanning >1 language; requirements merging >1 source app | M / M |

The likelihood/impact heuristics are documented **inside** the rendered doc (a
Methodology table) so a reader can audit the scoring. The two non-obvious rules:
COMP-3 / parity is always **High impact** (silent, catastrophic precision loss),
and a RISK-flagged node is always **High likelihood** (extraction already failed
to resolve it). Severity is the higher of likelihood/impact, **Critical** when
both are High. All rows start **Open**; the default Owner is
`config.roles.architect`.

## Parameters

This skill takes no positional parameters — it reads the standard pipeline paths
via the shared library. One flag:

- `--no-register` — write the risk log but do **not** touch the manifest (for a
  hermetic test or a preview).

## Step 1: Verify the prerequisite

```bash
python3 .anti-legacy/run.py manifest status
```

You need `requirements-graph` registered (the risk spine). If `blueprint-json`,
`test-strategy`, and a `coverage-report.json` are also present the log will be
richer — but they are not required.

## Step 2: Render the risk log

Run the renderer through the dispatcher. With no flags it reads the standard
artifact paths, writes `.anti-legacy/deliverables/risk-log.md`, and registers it:

```bash
python3 .anti-legacy/run.py risk_log
```

For a hermetic preview that does not touch the manifest:

```bash
python3 .anti-legacy/run.py risk_log --no-register
```

The renderer prints the written path and the number of risks identified.

## Step 3: Read what was produced and sanity-check it

Open `.anti-legacy/deliverables/risk-log.md` and confirm:

- The **Methodology** table documents the seven sources and the L/M/H heuristics.
- The **Sources assessed** section is honest — every input is marked *present* or
  *ABSENT*, and an ABSENT input explicitly says its risk source was *not assessed*
  (e.g. "coverage-report.json absent — coverage-hole risks NOT assessed"). If you
  expected a source to be present and it reads ABSENT, the upstream artifact is
  missing — go fix the phase, do not hand-edit this doc.
- The **Top risks** callout lists the highest-severity items first.
- The **Risk register** table carries a **Source** ref on every row (a `req_id`, a
  `db_id/symbol_id`, or a file) — that is the traceability thread. A row with a
  blank source is a bug.

## Step 4: Confirm registration (when not using `--no-register`)

```bash
python3 .anti-legacy/run.py manifest check deliverable-risk-log
python3 .anti-legacy/run.py manifest status | grep deliverable-risk-log
```

`manifest check` resolves the stored path and re-checksums it; a clean result
means the registered artifact matches the file on disk. You should also see one
`anti-legacy:artifact-registered` row appended to `audit.jsonl`.

## Done-gate

Before registering, the script asserts (and STOPS, exiting non-zero, if either
fails):

1. A requirements graph **exists** and has domains
   (`.anti-legacy/requirements/requirements_graph.json`) — without it there is
   nothing to mine.
2. The rendered Markdown is **non-empty** and the written file exists and is
   non-empty.

The log renders its Methodology + Sources-assessed sections **even when zero
risks are mined**, so the artifact is auditable (and honest about what it did
*not* assess) rather than a blank file. Registration uses `status=draft` and
`depends_on=["requirements-graph"]`, and the skill **never** calls
`manifest advance`.

## Output

- `.anti-legacy/deliverables/risk-log.md` — the living risk register.
- Manifest artifact `deliverable-risk-log` (format markdown, status **draft**,
  produced_by `anti-legacy:risk-log`, depends_on `["requirements-graph"]`), plus
  an `artifact-registered` audit row.

## Failure cases

- **"no requirements graph found …"** — `requirements_graph.json` is absent or has
  no domains. Run the pipeline through `graph-translate` first. The renderer exits
  non-zero and writes nothing.
- **Sources read ABSENT that you expected present** — the named upstream artifact
  was not produced (or is at a non-standard path). This is the log being honest,
  not a bug — produce the artifact (run `extraction` for annotations + coverage,
  `blueprint`, `test-strategy`) and re-render.
- **`manifest check` reports drift** — the doc was hand-edited after registration.
  Re-run `risk_log` to regenerate and re-register rather than patching by hand —
  the log is derived, not authored.
