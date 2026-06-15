---
name: "anti-legacy:document"
description: >
  Synthesize the target application's human-facing documentation (README,
  ARCHITECTURE, DEPENDENCIES, ENVIRONMENTS) from the committed pipeline
  artifacts — config.json, blueprint.json, requirements_graph.json,
  target_graph.json — NOT by coining prose with an LLM. Writes the docs INSIDE
  the target app directory so the delivered repo is self-describing, and
  registers each as a manifest artifact. Runs in the DOCUMENT phase, after
  GATE_4_UAT.
  Use when: "document the target app", "generate the README", "write the
  architecture doc", "produce app docs", "document the modernized service",
  "DOCUMENT phase".
---

# anti-legacy:document

The DOCUMENT phase is the last authoring step of the pipeline. After UAT has
signed off (GATE_4_UAT) and the target codebase is verified, this phase writes
the four standing documents that ship *inside* the modernized application so the
delivered repository explains itself to the next engineer.

The hard rule: these docs are **derived, not coined**. Every sentence is grounded
in a committed artifact. There is no LLM free-writing here — the script reads the
graphs and renders deterministic Markdown. This keeps the docs honest (they
describe what was actually designed and built) and re-runnable (re-run after the
artifacts change and the docs track them).

## What gets produced

Four files under the target app dir (`config.target_path`):

| Doc | Content | Derived from |
|---|---|---|
| `README.md` | What the app does, how to set up, how to run — overviews only | config (stack, db, deploy target) + requirements/blueprint domains |
| `ARCHITECTURE.md` | Domains / services / package layout + component & domain boundaries | `blueprint.json` (fallback: `target_graph.json`) |
| `DEPENDENCIES.md` | Service / database / file dependencies (infra-level, NOT a code callgraph) | `requirements_graph.json` `data_access` + `dependencies` |
| `ENVIRONMENTS.md` | Deployment targets + per-environment config / setup | config (deployment_target, database, embeddings) |

Each is registered as a manifest artifact (`doc-readme`, `doc-architecture`,
`doc-dependencies`, `doc-environments`) with `status: final`.

## Cross-Platform Notes

The synthesizer is pure standard-library Python — no shell-isms, every path built
with `os.path`. It runs identically on macOS, Linux, WSL, and Windows. The docs
are written with `\n` line endings and forward-slash links.

## Config

```bash
python3 -c "import json; c=json.load(open('.anti-legacy/config.json')); print(c['project_name'], c['target_stack'], c['target_path'], c['deployment_target'], c['database'])"
```

`target_path` is where the docs land. If it is absent, the script falls back to
`blueprint.target_path`, then `target_graph.target_path`. If none resolve, it
exits non-zero — there is nowhere to write the docs.

## Step 1: Verify prerequisites

This phase runs after the build is verified and UAT has signed off. Confirm the
upstream artifacts exist:

```bash
python3 .anti-legacy/run.py manifest status
```

You want to see (at minimum) `blueprint-json`, `requirements-graph`, and
`target_graph` registered. `config.json` must exist under `.anti-legacy/`.

The docs degrade gracefully when an *optional* artifact is missing (the section
renders as "not available"), but a missing `target_path` is fatal — there is no
target directory to write into.

## Step 2: Synthesize the docs

Run the synthesizer through the dispatcher. With no flags it reads the standard
artifact paths and writes the docs into `config.target_path`, registering each
in the manifest:

```bash
python3 .anti-legacy/run.py document
```

Override any input or the output dir when needed:

```bash
python3 .anti-legacy/run.py document \
  --config .anti-legacy/config.json \
  --blueprint .anti-legacy/requirements/blueprint.json \
  --requirements .anti-legacy/requirements/requirements_graph.json \
  --target-graph .anti-legacy/target_graph.json \
  --target-dir ./target/credit-card-service
```

Use `--no-register` to write the docs without touching the manifest (for a dry
run or a preview).

## Step 3: Read what was produced and sanity-check it

Open the four files under the target app dir and confirm:

- `README.md` lists the real business domains (from the requirements graph), the
  real stack and database (from config), and links to the other three docs.
- `ARCHITECTURE.md` shows one row per domain with its package, component count,
  and entity count, plus per-domain component tables and any declared
  boundaries (intra-domain dependencies) and build order.
- `DEPENDENCIES.md` lists the database, the data-access assets mapped to the
  requirements that touch them, the inter-requirement service dependencies, and
  the source-system provenance. This is infra-level — if you see method names or
  call edges, the wrong input was fed in.
- `ENVIRONMENTS.md` names the deployment target and renders the
  local/staging/production ladder with the per-environment config keys.

If a section reads "not available" or "_none_", that is the artifact telling you
an upstream input was thin — go back and check the named artifact, do not
hand-edit the doc.

## Step 4: Confirm registration

```bash
python3 .anti-legacy/run.py manifest check doc-readme
python3 .anti-legacy/run.py manifest status | grep doc-
```

`manifest check` resolves each doc's stored path and re-checksums it; a clean
result means the registered artifact matches the file on disk.

## Done looks like

- Four files exist under `config.target_path`: `README.md`, `ARCHITECTURE.md`,
  `DEPENDENCIES.md`, `ENVIRONMENTS.md`.
- Four artifacts are registered `final` in the manifest: `doc-readme`,
  `doc-architecture`, `doc-dependencies`, `doc-environments`, each with a
  checksum that `manifest check` verifies.
- Every doc traces to a committed artifact — no free-written prose. README
  domains come from the requirements graph, architecture from the blueprint,
  dependencies from the requirements graph's `data_access`/`dependencies`,
  environments from config.

## Still not done (callers should not assume)

- This phase does **not** generate deployment artifacts (Dockerfile, CI/CD,
  manifests) — that is `anti-legacy:deploy`. ENVIRONMENTS.md *describes* the
  environments; it does not provision them.
- The docs are only as rich as the artifacts. A blueprint with empty domains
  yields a sparse ARCHITECTURE.md — fix the blueprint, then re-run, rather than
  editing the doc.

## Failure cases

- **"No target directory could be resolved"** — `config.target_path` is unset
  and neither the blueprint nor the target graph carries a `target_path`. Set
  `target_path` in config (or pass `--target-dir`) and re-run.
- **Sparse ARCHITECTURE.md** — the blueprint has no `domains`; the script falls
  back to `target_graph.json`. If that is also empty, run/finish `blueprint` and
  `target-review` first.
- **`manifest check` reports drift** — a doc was hand-edited after registration.
  Re-run `document` to regenerate and re-register rather than patching by hand.
