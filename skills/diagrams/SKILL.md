---
name: "anti-legacy:diagrams"
description: >
  Render a Mermaid architecture diagram set (C4 context + container, requirement
  dependency flowchart, ERD, per-domain sequence diagrams, deployment topology)
  from the requirements graph + blueprint + config. Writes .mmd files plus an
  index README that embeds each as a fenced mermaid block, and registers the
  index as a manifest artifact. Mermaid only — no PlantUML.
  Use when: "architecture diagrams", "draw the diagrams", "C4 diagrams", "ERD".
---

# anti-legacy:diagrams

Render the modernized system as a set of **Mermaid** diagrams, derived
deterministically from the committed pipeline data — not hand-drawn. This is a
**deliverable** skill: it produces human-facing artifacts under
`.anti-legacy/deliverables/` once the graph is ready, complements (does not
replace) `review-packet`, and **never advances the phase**.

## Mental model

The requirements graph is the structural spine; the blueprint is the target
design. The diagram set reads both and emits one Mermaid file per view, plus an
index README that links each file **and** embeds it as a ```` ```mermaid ````
fenced block so it renders in GitHub, VS Code, or any Markdown viewer with no
tooling. Mermaid is the only syntax (a baked-in user decision — there is no
PlantUML path).

Every diagram is regenerated from data: change the graph or blueprint, re-run,
and the diagrams track. Do not hand-edit the `.mmd` files — they are overwritten
on every run.

| File | View | Derived from |
|---|---|---|
| `context.mmd` | C4 L1 — system context | target system (config) + source apps + deployment target |
| `containers.mmd` | C4 L2 — containers | blueprint `domains[d].components` (class + type + deps); falls back to graph requirements |
| `domain-deps.mmd` | Requirement dependency flowchart | graph `node.dependencies` (req_id → req_id), grouped by domain |
| `erd.mmd` | Entity-relationship diagram | blueprint entities (columns) else graph entities (fields); relationships from `data_access` |
| `sequence-<domain>.mmd` | Per-domain interaction (top 3 by req count) | blueprint component_type chain (controller→service→repository); falls back to req → legacy_components |
| `deployment.mmd` | Deployment topology | config `deployment_target` + `target_stack` (client → service → datastore) |
| `README.md` | Index (links + embedded blocks) | all of the above; flags any degraded diagram |

The index `README.md` is registered as artifact id **`deliverable-diagrams`**.

## Cross-Platform Notes

The renderer is pure standard-library Python plus `antilegacy_core.deliverables`
— no shell-isms, every path built with `os.path`, workspace anchored on
`os.getcwd()` (the library owns anchoring). It runs identically on macOS, Linux,
WSL, and Windows. Files are written with `\n` line endings and forward-slash
links.

**Mermaid node ids are always sanitized** via `D.mermaid_id()` — raw legacy
names (dots, hyphens, spaces, leading digits) break the Mermaid parser, so every
node id is alnum/underscore. Labels keep the human-readable name (pipe- and
newline-escaped).

## When it runs & prerequisites

Run any time **the graph is ready** — i.e. once
`.anti-legacy/requirements/requirements_graph.json` exists with ≥1 domain
(produced by `anti-legacy:graph-translator`). The blueprint
(`.anti-legacy/requirements/blueprint.json`) and config
(`.anti-legacy/config.json`) are **optional**: when the blueprint is absent the
container/sequence diagrams render at the requirement level and the index flags
the degradation; when config is thin the context/deployment diagrams render what
is known and say what is not. The requirements graph is the one hard
prerequisite — without it there is nothing to draw and the script exits non-zero.

## Parameters

The renderer reads the standard artifact locations through the shared loaders;
there is nothing to pass on the happy path.

- `--no-register` — write the diagrams but do not touch the manifest (use for a
  dry run, a preview, or a hermetic test).

## Steps

1. **Confirm the graph is ready.** The diagram set needs the requirements graph
   with at least one domain. The blueprint enriches it but is not required.

   ```bash
   python3 .anti-legacy/run.py manifest status
   ```

   You want `requirements-graph` registered (and ideally `blueprint-json`). If
   the blueprint is missing, the diagrams still render — degraded, and the index
   will say so.

2. **Render the diagram set.** No flags reads the standard paths, writes the
   `.mmd` files + index under `.anti-legacy/deliverables/diagrams/`, and
   registers the index:

   ```bash
   python3 .anti-legacy/run.py diagrams
   ```

   For a preview without registering:

   ```bash
   python3 .anti-legacy/run.py diagrams --no-register
   ```

3. **Eyeball the output.** Open `diagrams/README.md` and confirm each embedded
   block renders. Spot-check that:
   - `context.mmd` shows the target system with the configured source apps and
     deployment platform as externals.
   - `containers.mmd` groups components under their domain subgraphs with the
     class name + component type (or, if degraded, one node per requirement).
   - `domain-deps.mmd` draws the requirement-dependency edges.
   - `erd.mmd` lists each entity with its columns/fields.
   - each `sequence-<domain>.mmd` walks actor → controller → service →
     repository (or, if degraded, req → legacy_components).
   - the **Degraded diagrams** section in the README names every view that fell
     back (do not hide this — it is the honest status).

4. **Confirm registration.**

   ```bash
   python3 .anti-legacy/run.py manifest check deliverable-diagrams
   python3 .anti-legacy/run.py manifest status | grep deliverable-diagrams
   ```

   A clean `manifest check` means the registered index path re-checksums against
   the file on disk.

## Done-gate

Before registering, the script asserts: the requirements graph exists and has
**≥1 domain** (else it exits non-zero — nothing to draw), and the index is
**non-empty**. Only then does it register `deliverable-diagrams`. If either
assertion fails, the script surfaces the gap on stderr and stops — it does **not**
register a hollow artifact and it never advances the phase.

Done looks like:

- `.anti-legacy/deliverables/diagrams/` contains `context.mmd`,
  `containers.mmd`, `domain-deps.mmd`, `erd.mmd`, one `sequence-<domain>.mmd`
  per top domain (up to 3), `deployment.mmd`, and `README.md`.
- Every `.mmd` is non-empty and syntactically valid Mermaid (node ids sanitized).
- `deliverable-diagrams` is registered `final` in the manifest with a checksum
  that `manifest check` verifies, `produced_by: anti-legacy:diagrams`,
  `depends_on: [requirements-graph, blueprint-json]`.
- The index names every degraded view (blueprint-absent fallbacks).

## Output

`.anti-legacy/deliverables/diagrams/` — the `.mmd` set + `README.md` index.
Registered artifact: `deliverable-diagrams` (the index).

## Still not done (callers should not assume)

- The diagrams are only as rich as the data. No blueprint ⇒ requirement-level
  containers and `legacy_components`-based sequences. Produce the blueprint and
  re-run for the component-level views rather than editing the `.mmd`.
- This renders **diagrams**, not prose. The narrative architecture document is
  `anti-legacy:document` (ARCHITECTURE.md inside the target app); the GATE_1
  review packet is `anti-legacy:review-packet`.
- Sequence diagrams cover the **top 3 domains by requirement count**, not every
  domain — a deliberate scope choice to keep the set legible.

## Failure cases

- **"No requirements graph with domains found"** — the graph is absent or empty.
  Run the pipeline through `anti-legacy:graph-translator` first, then re-run.
- **Containers/sequences look thin (requirement-level)** — the blueprint is not
  yet produced; the index's *Degraded diagrams* section says so. Finish
  `anti-legacy:blueprint`, then re-run for the component-level views.
- **`manifest check` reports drift** — a `.mmd` or the index was hand-edited
  after registration. Re-run `diagrams` to regenerate and re-register rather
  than patching by hand.
- **A Mermaid block won't render** — confirm you are viewing the embedded block
  in a Mermaid-aware viewer; the node ids are sanitized, so a parse error points
  at the viewer/version, not a raw-name id.
