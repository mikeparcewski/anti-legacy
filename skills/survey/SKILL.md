---
name: "anti-legacy:survey"
description: >
  Indexes one or more legacy source apps with wicked-estate (the code-graph engine)
  into per-app SQLite graphs under .anti-legacy/graphs/, then registers a deterministic
  wicked-estate stats digest as the checksummed `legacy-graph` evidence. wicked-estate
  natively captures the mainframe estate (COBOL/JCL/CICS/IMS/DB2 — module/function/field,
  JCL step/dataset, cics_program/cics_map, ims_database/segment, db2_table) AND modern
  languages (Java, C#, Python, TypeScript, Go, Kotlin, Rust, and others) in one pass,
  with {confidence, provenance} on every edge. Multiple repos stay as separate per-app
  DBs and federate via cross-graph (the merge case). No language routing, no batch
  extractor, no hand-rolled JSON graph.
  Use when: "scan the codebase", "discover the legacy code", "build the call graph",
  "index the source apps", "survey the source apps".
---

# anti-legacy:survey

Indexes every legacy source directory in `config.json` with **wicked-estate**, the
MIT code-graph engine. Each source repo becomes its own structural graph DB under
`.anti-legacy/graphs/<app>.db`; the survey registers a deterministic **stats digest**
as the checksummed `legacy-graph` evidence the rest of the pipeline traces against.

**The invariant**: every source app indexes to a non-empty graph. wicked-estate
records `file`/`line` provenance on every node natively, so traceability back to the
original source is a property of the engine, not something this skill has to assert
per-node. The extraction phase (`anti-legacy:extraction`) reads each node's source
slice via the helper (`wicked-estate source <name>`) to understand how the legacy
system implemented each behavior.

**Why wicked-estate, not a hand-rolled graph**: one engine captures both the mainframe
estate and modern languages, resolves cross-language edges automatically (JCL `EXEC
PGM` → COBOL, `CALL` → COBOL), and exposes a stable CLI (`index`/`query`/`blast-radius`/
`stats`/`rank`/`source`/`cross-graph`) plus a writable native `requirement` field for
the annotations the extraction phase produces. The graph **is** the evidence.

---

## Deliverable: the code graph + its digest

This skill produces structural graph DBs and a checksummable evidence digest — not a
JSON graph blob. The field contract is the wicked-estate graph itself.

**Per-app graph DB** — `.anti-legacy/graphs/<app>.db`, one per `source_apps` entry:
- Built by `wicked-estate index <path> --db .anti-legacy/graphs/<app>.db`.
- Carries `module`/`function`/`field` (+ estate `jcl step`/`dataset`,
  `cics_program`/`cics_map`, `ims_database`/`segment`, `db2_table` for mainframe;
  `class`/`method`/`struct`/`interface` etc. for modern languages) as nodes, with
  `file`/`line` provenance, and typed edges (`calls`/`uses`/`references`/`accesses`/
  …) each carrying `{confidence, provenance}`.
- These DBs are **gitignored** (rebuilt by re-running survey); they live under
  `.anti-legacy/graphs/`.

**`legacy-graph.digest.txt`** — `.anti-legacy/legacy-graph.digest.txt`, the committed
thin-seam evidence:
- The deterministic `stats_digest` of each app's DB (the canonical node/edge-count
  block, with volatile lines — `STALENESS:`, `repo:` git provenance, `db=NN.NMB` — 
  stripped so the digest is byte-stable across re-indexes of the same source).
- Its SHA-256 (computed by `manifest.file_checksum` at register time) is the
  checksummed `legacy-graph` evidence the gate/audit contract consumes.

**Assertions** (enforced by the stats-based done-gate in Step 6, surfaced in Step 4):
- Every app in `config.json` indexes to a DB with **≥1 node** (an app with zero nodes
  means wicked-estate did not recognise the source — flag it; check the path/language).
- Every app's DB has a **non-zero behavior-bearing node count** (`module`/`function`/
  `method`/`class`/… — the kinds the extraction phase will annotate). Zero
  behavior-bearing nodes means nothing downstream can carry a rule — flag it.
- Assets (DB2 tables, datasets, files) accessed by more than one app are surfaced to
  the user as cross-app coupling (a major modernisation risk), discovered via
  cross-domain `blast-radius`/`cross-graph` over the estate `accesses`/`uses` edges.

---

## Cross-Platform Notes

All graph construction is delegated to the `wicked-estate` binary via the
`run.py wicked_estate` helper (`scripts/wicked_estate.py`), which is pure-Python
subprocess + `shutil.which` (no shell builtins) and resolves the binary
cross-platform. Any glue here uses `python3` with a `python` fallback. The helper
runs every invocation with `cwd` = repo root and `--db` always passed explicitly.

---

## Config

```bash
python3 -c "import json; cfg=json.load(open('.anti-legacy/config.json')); [print(a['name'], a.get('language','?'), a['path']) for a in cfg['source_apps']]"
```

If config.json is missing, halt and ask the user to run `anti-legacy:setup` first.

---

## Step 1: Check prerequisites

```bash
python3 .anti-legacy/run.py manifest status
```

If manifest is missing or phase is before `setup`, halt.

Confirm the engine resolves (config `wicked_estate_path` → `WICKED_ESTATE_PATH` →
PATH → the wicked-estate fallback). If it does not resolve, the helper raises a clear
error telling the user to set `wicked_estate_path` or install wicked-estate — surface
that and stop (never silently degrade):

```bash
python3 .anti-legacy/run.py wicked_estate resolve_binary
```

---

## Step 2: Index each source app with wicked-estate

For **every** source app in `config.json` (no language routing — wicked-estate handles
the mainframe estate and modern languages in one pass), index the repo into its own
DB. One DB per source repo is what enables `cross-graph` federation = the merge case.

```bash
python3 .anti-legacy/run.py wicked_estate index --fresh \
  --app {app1_name} {app1_path} \
  --app {app2_name} {app2_path}
```

This shells `wicked-estate index <path> --embeddings --db .anti-legacy/graphs/<app>.db`
once per app (deleting any prior db first) and returns the parsed stats (node/edge
counts) for each. The helper creates `.anti-legacy/graphs/` if needed.

**Always survey with `--fresh`.** Incremental indexing skips unchanged files, so
structure/spans computed by an OLDER engine binary persist stale after an engine
upgrade (e.g. a pre-fix binary's label-only COBOL paragraph spans survive a plain
re-index — verified). `--fresh` deletes the db first for a full re-parse, so the
survey graph always reflects the CURRENT engine. The graph DBs are gitignored and
rebuilt on demand, so a fresh build is cheap and authoritative.

**Embeddings are ON by default** (config `embeddings: true`; pass `--no-embeddings`
to skip). They are local (no API key) and power semantic `correspond` / `semantic`
— the cross-language merge-alignment signal. Without embeddings, cross-repo
`correspond` degrades to name-only and finds nothing across COBOL↔Java. Note: for
the merge, run `correspond` with a code-kind filter (`--kind Function`) — the
unfiltered default is dominated by README/markdown nodes.

There is **no** mainframe-vs-modern split, **no** `graph_builder.py`, **no**
`survey-modern` track, and **no** `legacy_graph.json`. wicked-estate auto-detects
language by content and records the language per node. Cross-language edges (JCL
`EXEC PGM` → COBOL, `CALL` → COBOL, modern imports) resolve automatically.

### 3a — Multiple repos: keep per-app DBs, federate via cross-graph

Each source app keeps its **own** DB. Single-repo consumers use the primary
(first) app's DB. Cross-repo queries (shared assets, blast-radius spanning repos)
federate over the per-app DB list with `cross_graph`:

```bash
python3 .anti-legacy/run.py wicked_estate cross_graph {asset_or_symbol} \
  --db .anti-legacy/graphs/{app1_name}.db \
  --db .anti-legacy/graphs/{app2_name}.db
```

This is the merge case: two source repos are NOT concatenated into one graph; they
stay separate and join only through the federated query surface.

### 3b — Language or path unclear

If an app indexes to **0 nodes**, the path is likely wrong or the source isn't where
config says. Enumerate the directory to confirm there is indexable source, fix the
`config.json` `path`, and re-run Step 3 for that app:

```bash
find {app_path} -type f | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -10
```

wicked-estate recognises COBOL, JCL, CICS/BMS, IMS, DB2 DDL, and modern languages by
content; an empty graph almost always means a bad path, not an unsupported language.

---

### 3c — Re-project confirmed domain terms onto the fresh graph

A `--fresh` index **deletes and rebuilds** each DB, which **wipes every `domain_*` term
annotation** a prior `vocabulary project` wrote (the tags live only in the gitignored DB,
never in a sidecar). So after re-indexing, re-apply the confirmed glossary onto the new
graph so the engine's domain resolution (`cluster` / `by-requirement` / `read-kv`) stays
term-aware:

```bash
python3 .anti-legacy/run.py vocabulary project --db .anti-legacy/graphs/<app>.db
```

This is a **clean no-op on the first survey** (no terms are `confirmed` yet →
`confirmed_available=0`), so it is safe to run every time. On a **re-survey after
extraction has confirmed terms**, it is **mandatory** — skip it and the engine silently
degrades to name-only resolution with no error. Read the summary: a `GAP:` line for an
`unbound` or `all_skipped` confirmed term is a coverage signal, not a no-op. See
`anti-legacy:vocabulary` (Project confirmed terms) for the two-axes model — the committed
glossary is the durable record; these graph tags are a disposable re-derived projection.

---

## Step 3: Summarise and surface key findings

Read each app's graph stats via the helper (no JSON file to parse — the stats come
straight off the DB):

```bash
python3 .anti-legacy/run.py wicked_estate stats \
  --db .anti-legacy/graphs/{app1_name}.db
```

Repeat per app (or pass the helper the app list). Summarise the node-kind breakdown,
edge counts, and behavior-bearing totals across all apps.

Flag to the user:
- Any app with **zero** nodes (path/source problem — fix and re-index; see Step 3b).
- Any app with **zero behavior-bearing** nodes (`module`/`function`/`method`/`class`/
  …) — nothing downstream can carry a rule; investigate.
- Any app with a **very large** node count (large estate — the extraction crawl will
  take longer; note it).
- **Shared assets** — DB2 tables, datasets, or files accessed by **multiple apps**
  (cross-app coupling, a major modernisation risk). Discover these with cross-domain
  `blast-radius` / `cross_graph` over the estate `accesses`/`uses` edges (Step 3a).
- **Dead-End / Uncalled Entry Points**: programs, classes, or interfaces with
  in-degree 0.
  - Cross-reference uncalled COBOL programs against JCL to check if they are batch
    job-step targets.
  - Cross-reference uncalled COBOL programs against CSD/CICS configurations to check
    if they are online transaction/screen entry points.
  - Cross-reference uncalled COBOL programs against MQ configurations to check if they
    consume asynchronous messages.
  - For modern languages (Java/C#), check for isolated interfaces (implemented by a
    class but never imported/referenced elsewhere).
  - For each uncalled entry point identified, decide its modernization disposition
    **in-band** (inline) before proceeding to analyze.

Dead-ends are answered **in-band**: decide the modernization disposition for each
uncalled entry point inline as part of this step. There is no separate question queue
and no downstream blocking — resolve every dead-end here before the analyze phase.

Run the dead-end detection script to automate the above checks:

```bash
python3 .anti-legacy/run.py detect_dead_ends
```

`detect_dead_ends` now reads the graph **via the wicked-estate helper** (in-degree-0
detection through `blast_radius`/`query`, no `legacy_graph.json`), cross-references
against JCL/CSD/BMS/MQ configurations, detects isolated interfaces, and prints the
resulting dead-ends.

The agent resolves each printed dead-end in-band — deciding the modernization
disposition for every uncalled entry point — before proceeding to the analyze phase.

---

## Step 4: Register the digest as evidence and advance phase

**Done-gate — assert the graph is real before registering.** The gate is now
**stats-based** (not a per-node `file_path` check, since wicked-estate carries
provenance natively): every app's DB must have **≥1 node** and a **non-zero
behavior-bearing count**. If the assertion FAILS, do NOT register `--status final`
and do NOT `advance` — surface the specific app(s) that indexed empty (or with no
behavior-bearing nodes) and stop. The user fixes the source/path and re-runs Step 3.
The `register` and `advance` calls below are CONDITIONAL on this assertion passing.

Write the deterministic stats digest for all app DBs and assert the gate in one step
via the helper (the helper computes `stats_digest` per DB, strips volatile lines, and
fails non-zero if any app has 0 nodes or 0 behavior-bearing nodes):

```bash
python3 .anti-legacy/run.py wicked_estate write_digest \
  --out .anti-legacy/legacy-graph.digest.txt \
  --db .anti-legacy/graphs/{app1_name}.db \
  --db .anti-legacy/graphs/{app2_name}.db
```

If `write_digest` exits non-zero, it prints which app DB is empty / has no
behavior-bearing nodes. Fix and re-index — do NOT register or advance.

Only if the digest was written and the gate passed (exit 0), register the digest and
advance. Note: `--format text` (not `json`), and **no** `--schema` (the
`code-graph.schema.json` is retired):

```bash
python3 .anti-legacy/run.py manifest register legacy-graph \
  --path legacy-graph.digest.txt \
  --format text \
  --produced-by anti-legacy:survey \
  --status final

python3 .anti-legacy/run.py manifest advance survey
```

The registered digest's SHA-256 (computed by `manifest.file_checksum`) is the
checksummed `legacy-graph` evidence. After the extraction phase writes `requirement`
fields, survey/extraction re-registers this digest so it reflects the annotated graph
— making code↔annotation drift checksum-detectable (the §I6 keystone; wired here,
gated in a later workflow).

---

## Output

- `.anti-legacy/graphs/<app>.db` — one wicked-estate structural graph per source app
  (gitignored; rebuilt by re-running survey). The estate + modern code graph with
  `{confidence, provenance}` per edge.
- `.anti-legacy/legacy-graph.digest.txt` — deterministic, checksummable stats digest
  (the committed thin-seam evidence).
- Manifest: phase = `survey`, artifact `legacy-graph` registered (`format=text`,
  produced-by `anti-legacy:survey`, status `final`).

**Next step**: `anti-legacy:analyze` to apply structural lenses by querying
wicked-estate (entry points via `rank`/`blast-radius`/`query`, shared-asset coupling
via cross-domain `blast-radius`, batch-vs-online via JCL/CICS estate node kinds).
