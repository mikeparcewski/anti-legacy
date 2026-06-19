---
name: "anti-legacy:analyze"
description: >
  Apply structural lenses to the legacy code graph to identify entry points, domain
  boundaries, shared data coupling, operational modes (batch vs online), and risk
  hotspots. Queries the wicked-estate code graph via the helper (never raw SQLite,
  never legacy_graph.json). Produces an analysis report.
  Use when: "analyze the legacy code", "identify entry points", "find domain boundaries",
  "what are the hotspots", "classify the programs".
---

# anti-legacy:analyze

Applies four structural lenses to the legacy code graph. Each lens extracts a
different view of the system — together they give enough signal to drive the
extraction model (`anti-legacy:extraction`) that annotates the graph next.

The graph itself lives in **wicked-estate** (the code-graph engine adopted in §H).
This skill never reads `legacy_graph.json` (deleted) and never touches raw SQLite —
all structure comes from the `wicked_estate` helper, which wraps the wicked-estate
CLI (`query` / `blast-radius` / `rank` / `stats` / `source` / `cross-graph`).

## Cross-Platform Notes

All graph access goes through `python3 .anti-legacy/run.py wicked_estate <cmd>`
(the importable `scripts/wicked_estate.py` helper, CLI-exposed for skills). The
helper is pure-Python subprocess + `shutil.which` with a `python3 → python`
fallback, so it runs identically on macOS/Linux and Windows. No raw shell graph
parsing, no `legacy_graph.json` read.

## Config

Read project config:

```bash
python3 -c "import json; print(json.dumps(json.load(open('.anti-legacy/config.json'))))"
```

Each `source_apps` entry has a per-app wicked-estate DB written by
`anti-legacy:survey` at `.anti-legacy/graphs/<app>.db` (gitignored, rebuilt by
survey). The DB path for an app is `.anti-legacy/graphs/<app-name>.db`. Multi-repo
estates keep **one DB per repo** so cross-repo questions go through `cross_graph`
over the per-app DB list (the federated / merge case). The primary single-repo DB
for any single-app consumer is the first `source_apps` entry's DB.

## Parameters

None. The graph is read from the per-app wicked-estate DBs under
`.anti-legacy/graphs/` via the `wicked_estate` helper. There is no
`legacy_graph.json` input.

## Step 1: Verify prerequisite

The upstream evidence is the **legacy-graph digest** (`legacy-graph.digest.txt` —
the deterministic `wicked-estate stats` block survey checksums and registers).
Check the manifest for the `legacy-graph` artifact:

```bash
python3 .anti-legacy/run.py manifest status
```

If the `legacy-graph` artifact is missing or not `final`, **halt** and instruct the
user to run `anti-legacy:survey` first (survey is what indexes the source repos with
`wicked-estate index` and registers the digest). Also confirm the per-app DBs exist:

```bash
python3 -c "
import json, os, sys
cfg = json.load(open('.anti-legacy/config.json'))
missing = [a['name'] for a in cfg['source_apps']
           if not os.path.isfile(os.path.join('.anti-legacy/graphs', a['name'] + '.db'))]
if missing:
    sys.stderr.write('MISSING wicked-estate DBs (run anti-legacy:survey): %s\n' % missing)
    sys.exit(1)
print('OK: per-app wicked-estate DBs present')
"
```

If DBs are missing, halt and instruct the user to re-run `anti-legacy:survey`.

## Step 2: Rank importance and identify entry points (per app)

Do **not** hand-roll an in/out-degree pass over a JSON blob. The wicked-estate
graph already carries importance (PageRank) and full cross-domain edges. For each
app, drive the helper:

**Importance (PageRank — replaces the old in/out-degree heuristic):**

```bash
# Most-important symbols first; high-leverage orchestrators / entry programs surface at the top.
python3 .anti-legacy/run.py wicked_estate rank \
  --db .anti-legacy/graphs/<app>.db
```

`rank` returns the symbols ordered by PageRank. The top-ranked behavior-bearing
nodes (programs / classes / paragraphs) are the high-leverage orchestrators — these
seed Lens A's fan-out view and the extraction worklist later.

**Entry points (in-degree 0 that drive others):** use `blast_radius` —
in-degree-0 detection is exactly the "nobody calls this, but it calls/uses others"
shape, and `blast-radius` follows ALL edge kinds (including estate `uses` /
`accesses` / `protects`), so JCL/CICS/MQ targets are caught:

```bash
# For a candidate top-ranked program, see who depends on it (its dependents / blast radius).
python3 .anti-legacy/run.py wicked_estate blast_radius \
  --db .anti-legacy/graphs/<app>.db \
  --name <PROGRAM_NAME>
```

A node whose blast-radius shows **no dependents** (in-degree 0) but which itself
`query` shows outgoing `calls` / `uses` edges is an **entry point** (a JCL step
target, a CICS transaction program, an MQ handler, or a batch driver). Confirm a
candidate's outgoing edges with `query`:

```bash
python3 .anti-legacy/run.py wicked_estate query \
  --db .anti-legacy/graphs/<app>.db \
  --name <PROGRAM_NAME>
```

`query` returns the node plus its neighborhood (the edges where it is the source —
its dependencies — and the edge kinds). Use the existing dead-end logic
(`scripts/detect_dead_ends.py`, now rewired to read the graph via the helper's
`blast_radius` in-degree-0 detection) to enumerate uncalled programs that are
nevertheless JCL/CICS targets — those are always in-scope entry points.

Record, per app: the ranked importance list (top N), the entry-point set, and each
entry point's immediate downstream (from `query`).

## Step 3: Apply the four lenses (helper-driven)

Work through each lens, sourcing every fact from the helper — `rank`, `query`,
`blast_radius`, `source`, and `cross_graph` for cross-repo questions.

### Lens A — Architecture
Map how programs/classes call each other at a module level and find the
orchestrators and cycles.

- **Orchestrators / fan-out:** the top of `rank` plus the nodes whose `query`
  shows the most outgoing `calls`/`uses` edges. High PageRank + high out-degree =
  an orchestrator.
- **Module groupings:** group nodes by their `file` / package prefix (returned in
  each `query` / `rank` row — `Kind name (file:line)`). Namespace/division prefixes
  cluster the architecture.
- **Cycles:** walk `query` neighborhoods one hop at a time looking for a call chain
  that returns to its origin (A→B→A). Keep the walk bounded — one hop per step,
  honoring the engine's bounded-traversal contract; never request an unbounded
  whole-graph walk.

### Lens B — Domain
Cluster programs around shared data assets — programs that touch the same
table/file/dataset are likely the same business capability.

- **Shared-asset coupling (single repo):** for each estate data node (db2_table,
  VSAM file, dataset, `table`/`file` model), run `blast_radius` on the asset — its
  blast radius is every program that `accesses`/`uses` it. An asset whose
  blast-radius lists **more than one** program is **shared** = a coupling risk and a
  domain seam.

```bash
python3 .anti-legacy/run.py wicked_estate blast_radius \
  --db .anti-legacy/graphs/<app>.db \
  --name <TABLE_OR_FILE_NAME>
```

- **Shared-asset coupling (cross-repo / merge case):** when two repos share a
  logical asset (e.g. both touch the same account table), use **cross-graph** to
  federate the blast radius across the per-app DBs — this is how the COBOL repo and
  the Java repo are shown to converge on the same domain:

```bash
python3 .anti-legacy/run.py wicked_estate cross_graph \
  --name <SHARED_ASSET_NAME> \
  --db .anti-legacy/graphs/<appA>.db \
  --db .anti-legacy/graphs/<appB>.db
```

Cross-language edges (JCL `EXEC PGM` → COBOL, `CALL` → COBOL) resolve
automatically, so a cross-domain blast-radius can cross languages and repos.

Assign each cluster a business-analyst name (`Billing`, `CustomerMgmt`,
`Inventory`, …): what would a business analyst call this cluster of programs that
all hit the same data?

### Lens C — Technical
Classify each program by technical role, using the **estate node kinds** the graph
already carries instead of guessing:

- **Batch programs:** reachable from a **JCL step** (estate `jcl` step → COBOL
  `EXEC PGM` edge); no CICS map/transaction edges. `query` the program and check
  for a JCL-step dependent in its `blast_radius`.
- **Online programs:** carry a **CICS** edge — `cics_program` / `cics_map` node
  kinds in the program's neighborhood (or, for modern code, a web-endpoint edge such
  as `@RestController` / `@WebServlet`). The estate kind makes this deterministic.
- **Service programs:** called-only — `blast_radius` shows dependents (in-degree >
  0) but `query` shows no DB/data access edges.
- **Data-access programs:** `query` shows `EXEC SQL` / `db2_table` access or VSAM
  `file` access edges as the bulk of their out-edges.
- **Controller programs:** high fan-out (from `query`) to service programs +ranked
  high.

### Lens D — Ops
Identify operational boundaries from the estate's batch/online split and build
units:

- **Batch boundaries:** the set of `jcl` step / job nodes (one DB per repo;
  `cross_graph` if a job spans repos) and the programs each step drives.
- **Compile units:** distinct COBOL programs / Java packages — the `file` grouping
  from `query`/`rank` rows.
- **Config dependencies:** datasets, MQ queues, db2_table nodes referenced across
  the estate (estate `uses`/`accesses` edges via `blast_radius`).

## Step 4: Write analysis report

Write `.anti-legacy/analysis-report.md` with findings from all four lenses:

- **Architecture map** — orchestrators (top PageRank + fan-out), module groupings,
  any circular call chains.
- **Domain cluster assignments** — `program_name → domain_name`, one line per
  program, grounded in shared-asset coupling (single-repo `blast_radius` and
  cross-repo `cross_graph`).
- **Technical classification table** — program → {batch | online | service |
  data-access | controller}, justified by the estate node kinds.
- **Ops boundary summary** — batch jobs, compile units, config dependencies.
- **Risk hotspots** — highest-coupling programs (assets shared by the most
  programs), circular deps, and unknown/orphan entry points.

Every claim in the report must trace to a helper call (`rank` / `query` /
`blast_radius` / `cross_graph`) — do not invent structure the graph did not return.

## Step 5: Done-gate, register artifact, and advance phase

First run the content assertion. This proves the artifact is real before any
register/advance happens. The assertion verifies: `analysis-report.md` exists,
contains domain cluster assignments (>=1 domain — detected by a "Domain"
heading/section plus at least one domain mapping line, an arrow form such as
`→`/`->`/`&rarr;` or a bulleted/numbered domain entry under that section), and
that the upstream `legacy-graph` artifact is `final` in the manifest.

```bash
python3 -c "
import json, os, re, sys

report = '.anti-legacy/analysis-report.md'

# 1) Report file exists and is non-empty
if not os.path.isfile(report):
    sys.stderr.write('DONE-GATE FAIL: .anti-legacy/analysis-report.md is missing\n')
    sys.exit(1)
text = open(report, encoding='utf-8').read()
if not text.strip():
    sys.stderr.write('DONE-GATE FAIL: analysis-report.md is empty\n')
    sys.exit(1)

# 2) Domain cluster assignments present (>=1 domain)
#    Require a Domain section heading PLUS at least one concrete domain
#    assignment line. Accept the common arrow forms ('->', '→', '&rarr;',
#    '&#8594;') OR a bulleted/numbered domain entry under that section.
has_domain_section = re.search(r'(?im)^\s*#{1,6}.*domain', text) is not None
arrow_mappings = re.findall(r'(?m)^.*(?:->|→|&rarr;|&#8594;).*$', text)
section = re.search(r'(?ims)^\s*#{1,6}.*domain.*?(?=^\s*#{1,6}\s|\Z)', text)
bullet_domains = re.findall(r'(?m)^\s*(?:[-*]|\d+\.)\s', section.group(0)) if section else []
if not (has_domain_section and (len(arrow_mappings) >= 1 or len(bullet_domains) >= 1)):
    sys.stderr.write('DONE-GATE FAIL: no domain cluster assignments (>=1 domain) found in analysis-report.md\n')
    sys.exit(1)

# 3) Upstream legacy-graph artifact is final in the manifest
try:
    m = json.load(open('.anti-legacy/manifest.json', encoding='utf-8'))
except Exception as e:
    sys.stderr.write('DONE-GATE FAIL: cannot read manifest: %s\n' % e)
    sys.exit(1)
arts = m.get('artifacts', {})
lg = arts.get('legacy-graph') if isinstance(arts, dict) else None
if lg is None and isinstance(arts, list):
    lg = next((a for a in arts if a.get('id') == 'legacy-graph'), None)
if not lg or lg.get('status') != 'final':
    sys.stderr.write('DONE-GATE FAIL: legacy-graph artifact is not final\n')
    sys.exit(1)

print('DONE-GATE PASS: analysis-report.md has domain assignments and legacy-graph is final')
sys.exit(0)
"
```

**If this assertion FAILS, do NOT run `register --status final` and do NOT run
`advance`.** Surface the specific gap to the user (missing report, no domain
cluster assignments, or non-final `legacy-graph`) and stop — the user may
retry/fix. The `register --status final` and `advance` steps below are
CONDITIONAL on the assertion passing.

Only on a passing assertion, register the artifact and advance:

```bash
python3 .anti-legacy/run.py manifest register analysis-report \
  --path analysis-report.md \
  --format markdown \
  --produced-by anti-legacy:analyze \
  --status final \
  --depends-on legacy-graph

python3 .anti-legacy/run.py manifest advance analyze
```




## Output

- `.anti-legacy/analysis-report.md` — four-lens analysis with domain assignments,
  every claim traced to a `wicked_estate` helper query
- Manifest: phase = `analyze`, artifact `analysis-report` registered

**Next step**: `anti-legacy:extraction` — crawl the wicked-estate graph via
adaptive ring-expansion, annotate each behavior-bearing node (resolve-or-risk), and
emit the coverage report.
