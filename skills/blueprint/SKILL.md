---
name: "anti-legacy:blueprint"
description: >
  Map the Requirements Graph to a target state architecture blueprint. Defines the
  modern package structure, API surface (REST/gRPC), database schemas, class boundaries,
  and repository patterns for the target stack. Queries git-brain for architecture
  standards and prior blueprint patterns. Produces blueprint.json + blueprint.md.
  Use when: "design the target architecture", "create the blueprint", "map requirements to target stack",
  "design the Spring Boot structure", "what does the modern version look like".
---

# anti-legacy:blueprint

Designs the Target State Blueprint by mapping the enriched Requirements Graph to
your target stack's architecture conventions. The blueprint is the contract between
analysis and implementation — it defines exactly what will be built before any code
is written.

## Cross-Platform Notes

All file operations use the agent's native Read/Write tools — no shell dependencies.

## Config

```bash
python3 -c "import json; c=json.load(open('.anti-legacy/config.json')); print(c['target_stack'], c['target_path'])"
```

## Parameters

- **target_stack** (optional): override from config. One of: `java`, `go`, `dotnet`, `kotlin`, `python`, `typescript`
- **style** (optional): architecture style. One of: `layered` (default), `hexagonal`, `cqrs`, `microservices`

## Step 1: Verify prerequisites

```bash
python3 .anti-legacy/run.py manifest status
```

Confirm `requirements-graph` artifact exists. If it still has `[TBD]` descriptions,
halt and tell the user to complete `anti-legacy:graph-translator` enrichment first.

## Step 2: Query git-brain for target stack patterns and anti-patterns

```bash
python3 .anti-legacy/run.py git_brain search \
  --query "{target_stack} architecture patterns blueprint structure {style}" \
  --limit 5

# Query modernization anti-patterns to enforce constraints
python3 .anti-legacy/run.py git_brain search \
  --query "anti-pattern architecture modular monolith microservices line-by-line" \
  --category patterns \
  --limit 3
```

Also read any standards files in the patterns directory:

Read `.anti-legacy/patterns/{source_lang}-to-{target_stack}/index.md` for each source language.

Verify the designed blueprint does not violate the modernization anti-patterns:
- Do not default to microservices if the team size is small (1-5 developers); use a modular monolith.
- Do not perform line-by-line translation; group legacy modules into logical business domains and capabilities (especially if migration mode is `functional`).
- Inventory and account for existing infrastructure capacity and license limits.

## Step 3: Read the requirements graph

Read `.anti-legacy/requirements/requirements_graph.json`.

Index all:
- Domains → will become top-level packages/modules
- Entities → will become database tables + ORM models
- Requirements → will become service classes / use-case handlers
- Dependencies between requirements → will determine build order

Do not treat normalizer domains as final package boundaries — collapse empty
domains and re-home `Domain_*_core` reqs into the business domain that owns
their entities.

## Step 4: Design the target structure

For each domain, define:

### 4a. Package/Module structure

**Java (Spring Boot) example**:
```
com.{company}.{project}/
  {domain}/
    api/          # REST controllers
    service/      # Business logic (one class per requirement node)
    repository/   # JPA repositories
    model/        # JPA entities (one per logical entity)
    dto/          # Request/Response DTOs
```

**Go (clean architecture) example**:
```
internal/
  {domain}/
    handler.go    # HTTP handlers
    service.go    # Business logic
    repository.go # DB access interface
    model.go      # Domain types
```

**C# (.NET) example**:
```
{Project}.{Domain}/
  Controllers/
  Services/
  Repositories/
  Models/
  DTOs/
```

Apply the appropriate structure for the configured `target_stack`.

### 4b. API surface

For each requirement node classified as **online** (from analysis-report):
- Define the HTTP method (POST for commands, GET for queries, PUT for updates)
- Define the path: `/{domain}/{resource}` (derived from the legacy entry point name)
- Document request/response shape derived from the legacy WORKING-STORAGE or input parameters

For each requirement node classified as **batch**:
- Define as a scheduled job or CLI-triggered operation
- Document trigger mechanism (cron, message queue, manual)

### 4c. Database schema

For each entity in the requirements graph:
- Map legacy field types to target types (COMP-3 PIC 9(9)V99 → DECIMAL(11,2), etc.)
- Define primary key strategy (sequence → auto-increment, VSAM key → unique index)
- Document relationships (foreign keys derived from cross-program data access)

Write the entity→schema mapping table to the blueprint.

### 4d. Migration boundary decisions

For each shared table accessed by multiple legacy programs:
- Decide: single shared schema, or duplicate with sync?
- Flag cross-domain data ownership conflicts (the domain with the most writes owns it)

## Deliverable contract (blueprint.json)

`blueprint.json` is the primary artifact this skill produces — the contract between
analysis and implementation. The canonical fields the gatekeeper and downstream
phases (test-strategy, planner, swarm) read are:

- `target_stack` — the target language/stack (e.g. `java`, `go`, `dotnet`).
- `package_structure` — the root package/module layout the target code lives under.
- `components.{req_id}` — one entry per active requirement node, each:
  - `target_file` — path of the file that will be generated for this requirement.
  - `class_name` — the target class/type name.
  - `component_type` — one of `model` | `repository` | `service` | `controller` | `batch`.
  - `methods[]` — `{name, signature}` for each method the component exposes.
- `schema.{table}.columns[]` — one entry per column, each:
  - `name` — target column name.
  - `type` — target column type (e.g. `DECIMAL(11,2)`, `BIGINT`).
  - `source_type` — the ORIGINAL legacy type, carried verbatim (e.g.
    `COMP-3 PIC 9(9)V99`). This is what parity rules and the COMP-3 precision
    checks downstream depend on — never drop it.

**Assertion**: every active requirement node in `requirements_graph.json` has a
`components` entry. A requirement with no `components` entry means the blueprint
is incomplete — surface the gap, do not advance.

The illustrative JSON in Step 5 below shows the per-domain nesting and human-facing
fields (`api`, `dependencies`, table grouping); when you write the file, keep the
canonical field names above so downstream readers and the gate validator resolve
every requirement to a named component and every column to its `source_type`.

## Step 5: Write blueprint.json

Write `.anti-legacy/requirements/blueprint.json`:

```json
{
  "project": "{project_name}",
  "target_stack": "{target_stack}",
  "target_path": "{target_path}",
  "style": "{style}",
  "domains": {
    "{domain_name}": {
      "package": "{package_path}",
      "components": {
        "{req_id}": {
          "target_file": "{path/to/TargetClassName.java}",
          "class_name": "{TargetClassName}",
          "component_type": "model|repository|service|controller|batch",
          "methods": [
            { "name": "process", "signature": "BillingResult process(BillingRequest req)" }
          ],
          "api": { "method": "POST", "path": "/billing/process" },
          "dependencies": ["{other_req_id}"]
        }
      },
      "schema": {
        "{table_name}": {
          "columns": [
            { "name": "id", "type": "BIGINT", "source_type": "VSAM-KEY", "pk": true },
            { "name": "amount", "type": "DECIMAL(11,2)", "source_type": "COMP-3 PIC 9(9)V99" }
          ]
        }
      }
    }
  },
  "build_order": ["{req_id_1}", "{req_id_2}"]
}
```

## Step 6: Write blueprint.md (human-readable)

Write `.anti-legacy/requirements/blueprint.md` with:
- Executive summary (what is being built)
- Domain-by-domain breakdown with package structure diagram
- API surface table (method, path, legacy equivalent, requirement id)
- Database schema per entity (field mappings from legacy types)
- Build order list with rationale
- Open decisions requiring human review (flag any data ownership conflicts)

## Step 6b: Generate NFRs document

Read `templates/nfrs.md`, customize it by replacing `{project_name}`, `{target_stack}`, and `{deployment_target}` with details from `config.json`, and save the result as `.anti-legacy/requirements/nfrs.md`.

## Step 7: Store blueprint decisions in git-brain

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "Blueprint [{project_name}] → {target_stack} {style}: {domain_count} domains, {component_count} components, {entity_count} entities, {api_count} API endpoints. Package root: {package_root}. Build order: {first_5_tasks}..." \
  --tags "decision,blueprint,{target_stack}" \
  --category decisions
```

## Step 8: Done-gate, register artifacts, and advance phase

### 8a. Content assertion (DONE-GATE)

Before registering anything or advancing, prove the blueprint is real. The
assertion checks that `blueprint.json` has at least one domain with at least one
component AND that `nfrs.md` was written and is non-empty:

```bash
python3 -c "import json,os,sys; \
b=json.load(open('.anti-legacy/requirements/blueprint.json')); \
doms=b.get('domains',{}); \
ok_dom=any(isinstance(d,dict) and d.get('components') for d in doms.values()); \
nfrs='.anti-legacy/requirements/nfrs.md'; \
ok_nfrs=os.path.isfile(nfrs) and os.path.getsize(nfrs)>0; \
sys.exit(0 if (ok_dom and ok_nfrs) else 1)"
```

If this assertion FAILS, do NOT run `register --status ...` and do NOT run
`advance`; surface the specific gap to the user and stop (the blueprint has no
domain with components, or `nfrs.md` is missing/empty — the user may retry/fix).
The register and advance calls below are CONDITIONAL on the assertion passing.

### 8b. Register and advance (only if 8a passed)

```bash
python3 .anti-legacy/run.py manifest register blueprint-json \
  --path requirements/blueprint.json \
  --format json \
  --produced-by anti-legacy:blueprint \
  --status draft \
  --depends-on requirements-graph

python3 .anti-legacy/run.py manifest register blueprint-md \
  --path requirements/blueprint.md \
  --format markdown \
  --produced-by anti-legacy:blueprint \
  --status draft \
  --depends-on requirements-graph

python3 .anti-legacy/run.py manifest register nfrs \
  --path requirements/nfrs.md \
  --format markdown \
  --produced-by anti-legacy:blueprint \
  --status draft \
  --depends-on requirements-graph

python3 .anti-legacy/run.py manifest advance blueprint
```



## Output

- `.anti-legacy/requirements/blueprint.json` — machine-readable target architecture
- `.anti-legacy/requirements/blueprint.md` — human-readable blueprint for review
- `.anti-legacy/requirements/nfrs.md` — target Non-Functional Requirements spec
- Manifest: phase = `blueprint`, artifacts `blueprint-json`, `blueprint-md`, and `nfrs` registered
- git-brain: blueprint decisions stored

**Next step**: `anti-legacy:test-strategy` to generate test contracts from the blueprint, then `anti-legacy:review-packet` to compile the team review package.
