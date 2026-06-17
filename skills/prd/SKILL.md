---
name: "anti-legacy:prd"
description: >
  Render a detailed, stakeholder-facing Product Requirements Document from the
  requirements graph (the resolved-or-flagged spine) plus the coverage report —
  domains, per-requirement business rules / validations / error paths, entity
  tables, the explicit dropped-scope list, a coverage-&-gaps section, and a
  req_id → legacy_components → rule-id traceability appendix. Deterministically
  derived (no LLM prose), registered as a manifest artifact, never advances the
  phase.
  Use when: "generate the PRD", "product requirements document", "detailed
  requirements deliverable".
---

# anti-legacy:prd

The PRD is a **deliverable**: a polished, product-/business-facing rendering of
what the pipeline already decided. It is the readable face of
`requirements_graph.json` — one document a product owner, BA, or sponsor can
read end-to-end to see every capability the target system will implement, the
business rules behind it, what was deliberately cut, and where the analysis is
still soft.

## Mental model

This skill does **not** think. It **renders**. The requirements graph (produced
by `anti-legacy:graph-translator`) is the source of truth; the PRD is a
deterministic projection of it for humans. Every requirement-bearing line traces
back the same thread the rest of the pipeline uses — **req_id → legacy_components
→ business-rule ids** (AGENTS.md §2). There is no free-written prose: edit the
upstream artifacts and re-render, never hand-edit the PRD.

It is a **complement** to `anti-legacy:review-packet` (which feeds GATE_1), not a
replacement — the review packet is the gate's working document; the PRD is the
standing stakeholder deliverable. It lands under `.anti-legacy/deliverables/` and
registers itself; it **never** calls `manifest advance` (phase advancement is
owned by the phase skills).

The voice is factual (AGENTS.md §6): the **Out of scope (dropped)** section names
every dropped requirement WITH its reason, and the **Coverage & gaps** section
names requirements with no business rules, anything flagged `review` /
`unresolvable`, and every low-confidence rule. A clean-looking PRD that hides
holes is wrong.

## Cross-Platform Notes

Pure standard-library Python via `from antilegacy_core import deliverables as D`.
Every path is built with `os.path`; no shell-isms. Runs identically on macOS,
Linux, WSL, and Windows. The output is plain Markdown with GitHub-flavored tables
— readable in any viewer, git diff, or text editor.

## When it runs & prerequisites

Run it **once the graph is ready** — i.e. after
`.anti-legacy/requirements/requirements_graph.json` exists (post
graph-translate). Coverage and config are optional enrichments:

- **Required:** `requirements_graph.json` with ≥ 1 requirement. Without it the
  skill exits non-zero — it will not write a hollow PRD.
- **Optional:** `coverage-report.json` (executive-summary coverage % +
  mean_confidence; absent → "coverage not yet computed"), `config.json` (project
  name, `migration_mode`, `coverage.resolve_threshold` for the low-confidence
  flag — defaults to 0.75).

Confirm the graph is registered:

```bash
python3 .anti-legacy/run.py manifest status
```

You want to see `requirements-graph` listed. (Coverage is produced earlier by
`anti-legacy:extraction`; if it is not present yet, the PRD still renders.)

## Parameters

The script reads the standard `.anti-legacy/...` locations by default; override
only when pointing at a non-standard path.

- `--requirements` — path to `requirements_graph.json`
  (default `.anti-legacy/requirements/requirements_graph.json`).
- `--coverage` — path to `coverage-report.json`
  (default `.anti-legacy/coverage-report.json`).
- `--config` — path to `config.json` (default `.anti-legacy/config.json`).
- `--no-register` — write the PRD but do not touch the manifest (dry run / preview).

## Step 1: Render the PRD

Run through the dispatcher. With no flags it reads the standard inputs, writes
the PRD, and registers it:

```bash
python3 .anti-legacy/run.py prd
```

Dry run (no manifest write) while you eyeball the output first:

```bash
python3 .anti-legacy/run.py prd --no-register
```

## Step 2: Read what was produced

Open `.anti-legacy/deliverables/product-requirements.md` and confirm:

- **Header** names the project, the `migration_mode`, the graph it was generated
  from, and the timestamp.
- **Executive summary** reports the domain count and the requirement counts
  (active / dropped / unresolvable) and, when `coverage-report.json` is present,
  the coverage % + mean rule confidence. If it reads "coverage not yet computed",
  run `anti-legacy:extraction` first if you want that line populated.
- **Requirements by domain** — one section per domain; per active requirement a
  title, description, **Legacy components** (the mandatory trace), a Business
  rules table (RULE id · statement · confidence, with ⚠ on any confidence below
  the resolve threshold), and Validations / Error paths tables where present;
  plus an Entities table per domain.
- **Out of scope (dropped)** lists every dropped requirement with its
  `disposition_reason`.
- **Coverage & gaps** names the soft spots: active requirements with no business
  rules, anything `review` / `unresolvable`, and every low-confidence rule.
- **Appendix: traceability** tabulates req_id → legacy_components → rule ids.

```bash
python3 .anti-legacy/run.py manifest status | grep deliverable-prd
```

## Done-gate

Assert BEFORE trusting the deliverable:

- `requirements_graph.json` exists and has ≥ 1 requirement. If not, the script
  prints a stderr error and exits non-zero — it does **not** write a PRD. (Do not
  paper over this: produce the graph with `anti-legacy:graph-translator` first.)
- The written `product-requirements.md` is **non-empty** before registration; the
  script re-checks file size and refuses to register an empty file.
- With registration on (no `--no-register`), the manifest carries a
  `deliverable-prd` artifact (fmt `markdown`, `depends_on: ["requirements-graph"]`)
  and `audit.jsonl` has gained one `anti-legacy:artifact-registered` row. The
  phase is unchanged — a deliverable never advances the pipeline.

If any assertion fails, surface the gap and stop — do not register a partial or
hollow document.

## Output

- File: `.anti-legacy/deliverables/product-requirements.md`
- Artifact: `deliverable-prd` (fmt `markdown`, `status: final`, `produced_by:
  anti-legacy:prd`, `depends_on: ["requirements-graph"]`).

## Still not done (callers should not assume)

- The PRD is only as rich as the graph. Thin `business_rules` or missing
  `legacy_components` upstream yield a thin PRD — the **Coverage & gaps** section
  names exactly what is missing. Fix the graph (re-run extraction /
  graph-translator) and re-render rather than editing the PRD.
- The PRD does not gate anything and does not replace GATE_1's review packet
  (`anti-legacy:review-packet`). It is a stakeholder artifact, not a sign-off.
- Coverage numbers come from `coverage-report.json`; if that file is absent the
  PRD says so rather than inventing a figure.

## Failure cases

- **"no requirements graph … (or it has no domains)"** — the graph is missing or
  empty. Run `anti-legacy:graph-translator` (after extraction) and re-run.
- **"requirements graph … has 0 requirements"** — the graph parsed but contains
  no requirement nodes. The upstream translate produced nothing to document —
  investigate graph-translator before re-running.
- **"manifest absent — PRD written but not registered"** — you ran outside a
  workspace (no `.anti-legacy/manifest.json`). The PRD was still written; run from
  an initialized workspace to register it.
- **Executive summary says "coverage not yet computed"** — `coverage-report.json`
  is absent. This is graceful, not a failure; run `anti-legacy:extraction` to
  populate the coverage line.
