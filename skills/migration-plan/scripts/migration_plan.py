#!/usr/bin/env python3
"""
migration_plan — render the end-to-end migration EXECUTION plan as a nested
hierarchy (epics -> stories -> tasks -> subtasks) in two formats:

  * migration-plan.md        — Markdown hierarchy (headings + checkbox lists),
                               with explicit ordering, dependencies, and the
                               req_id -> legacy_components traceability thread.
  * migration-plan.jira.csv  — Jira-importable CSV (standard parent/Epic-Name
                               hierarchy convention), escaped via the stdlib
                               csv module.

This is the FULL delivery plan — test-prep, build, deployment, testing — not
just the build-only task.md the planner produces. It complements
anti-legacy:planner (which is the swarm build contract); this renderer is the
program-level, Jira-importable execution backlog the delivery team works from.

DECOMPOSITION (deterministic, from the requirements graph + blueprint + config):

  EPICS
    - one DELIVERY epic per domain (its active requirements);
    - four CROSS-CUTTING epics: Environment & Test Prep, Data Migration & Parity,
      Deployment & Cutover, UAT & Sign-off.
  STORIES
    - under a domain epic: one story per ACTIVE requirement (req_id + title);
    - under a cross-cutting epic: workstream stories (CI, provision envs, parity
      harness, deploy to staging, run UAT, cutover, ...).
  TASKS
    - per requirement story: test-prep (author contracts/tests) -> build
      (implement the blueprint component) -> integrate -> verify (functional +
      parity tests);
    - per workstream story: concrete workstream tasks.
  SUBTASKS
    - under a BUILD task: layers from the blueprint component_type
      (model -> repository -> service -> controller/api -> functional+parity tests).

  ORDERING
    - requirements are topologically sorted by their `dependencies` (Kahn);
    - within a requirement, layer order (model->repository->service->controller/api);
    - across the plan, phase order: prep -> build -> deploy -> test.
    Every item is NUMBERED so order is explicit: EPIC-1, STORY-1.1, TASK-1.1.1,
    SUB-1.1.1.1.

A requirement that is `drop`/`unresolvable` is NOT given a delivery story (it is
out of the build scope); dropped requirements are surfaced in a trailing
"Out of scope" section of the Markdown so the trace is never silently lost.

Pure standard library + antilegacy_core.deliverables. Cross-platform: every path
is built with os.path; the CSV is written with the stdlib csv module so quoting
and escaping are correct on macOS / Linux / WSL / Windows.
"""
import argparse
import csv
import io
import os
import sys
from collections import defaultdict, deque

from antilegacy_core import deliverables as D

MD_NAME = "migration-plan.md"
CSV_NAME = "migration-plan.jira.csv"

ARTIFACT_MD = "deliverable-migration-plan"
ARTIFACT_CSV = "deliverable-migration-plan-csv"
PRODUCED_BY = "anti-legacy:migration-plan"
DEPENDS_ON = ["requirements-graph", "blueprint-json"]

# Layer order for the build subtasks — derived from a component's
# blueprint component_type. Lower index = built first.
LAYER_ORDER = ["model", "repository", "service", "controller", "batch", "api"]

# Default layer set used when the blueprint has no component for a requirement
# (so a missing blueprint still yields a sane, ordered subtask breakdown).
DEFAULT_LAYERS = ["model", "repository", "service", "controller"]

# Phase labels (also used as Jira `phase` labels). Ordered prep->build->deploy->test.
PHASE_PREP = "prep"
PHASE_BUILD = "build"
PHASE_DEPLOY = "deploy"
PHASE_TEST = "test"


# --------------------------------------------------------------------------- #
# Small data holder for one plan item (epic / story / task / subtask).
# --------------------------------------------------------------------------- #
class Item:
    __slots__ = ("kind", "num", "summary", "description", "epic_name",
                 "parent", "labels", "req_id", "legacy_components",
                 "dependencies", "children")

    def __init__(self, kind, num, summary, description="", epic_name="",
                 parent="", labels=None, req_id="", legacy_components=None,
                 dependencies=None):
        self.kind = kind                      # Epic | Story | Task | Sub-task
        self.num = num                        # EPIC-1, STORY-1.1, ...
        self.summary = summary
        self.description = description
        self.epic_name = epic_name            # epics only
        self.parent = parent                  # parent item's summary
        self.labels = list(labels or [])      # [domain, phase, ...]
        self.req_id = req_id
        self.legacy_components = list(legacy_components or [])
        self.dependencies = list(dependencies or [])
        self.children = []                    # nested Items (for Markdown)


# --------------------------------------------------------------------------- #
# Ordering — topological sort of requirements by their `dependencies` (Kahn's
# algorithm, deterministic), mirroring antilegacy_core.planner_utils.
# --------------------------------------------------------------------------- #
def topo_sort_requirements(active):
    """Return active requirement ids in dependency order (deps before dependents).

    `active` is the list of (domain, req_id, node) from D.active_requirements.
    Only dependencies that are themselves active participate in the ordering;
    a dependency on a dropped/unknown req is ignored for ordering (but still
    reported on the item). Cycles / orphans fall through in sorted order so the
    plan is always complete.
    """
    ids = [r for _d, r, _n in active]
    id_set = set(ids)
    deps = {}
    for _d, rid, node in active:
        deps[rid] = [x for x in (node.get("dependencies") or []) if x in id_set]

    indeg = {n: 0 for n in ids}
    adj = defaultdict(list)
    for node_id, node_deps in deps.items():
        for dep in node_deps:
            adj[dep].append(node_id)
            indeg[node_id] += 1

    queue = deque(sorted([n for n in ids if indeg[n] == 0]))
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for m in sorted(adj[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    if len(order) < len(ids):  # cycle / leftover — append deterministically
        order.extend(sorted(id_set - set(order)))
    return order


def _component_type(blueprint, domain, req_id):
    """The blueprint component_type for (domain, req_id), or None if absent."""
    doms = blueprint.get("domains") or {}
    d = doms.get(domain) or {}
    comps = d.get("components") or {}
    c = comps.get(req_id) or {}
    ct = c.get("component_type") or c.get("type")
    return str(ct) if ct else None


def _component(blueprint, domain, req_id):
    doms = blueprint.get("domains") or {}
    d = doms.get(domain) or {}
    comps = d.get("components") or {}
    return comps.get(req_id) or {}


def _layers_for(blueprint, domain, req_id):
    """Ordered build layers (subtasks) for a requirement.

    If the blueprint carries a component for the requirement we anchor the layer
    set on its component_type (a `controller` implies the full model->...->api
    stack; a `batch` swaps the api layer for batch). With no blueprint component
    we fall back to DEFAULT_LAYERS so a missing blueprint still decomposes.
    """
    ctype = _component_type(blueprint, domain, req_id)
    if not ctype:
        return list(DEFAULT_LAYERS)
    ctype = ctype.lower()
    if ctype == "model":
        return ["model"]
    if ctype == "repository":
        return ["model", "repository"]
    if ctype == "service":
        return ["model", "repository", "service"]
    if ctype == "batch":
        return ["model", "repository", "service", "batch"]
    # controller / api / anything else -> full layered stack
    return ["model", "repository", "service", "controller"]


# --------------------------------------------------------------------------- #
# Plan builder.
# --------------------------------------------------------------------------- #
class PlanBuilder:
    def __init__(self, graph, blueprint, config):
        self.graph = graph or {}
        self.blueprint = blueprint or {}
        self.config = config or {}
        self.project = (
            (self.config.get("project") or {}).get("name")
            if isinstance(self.config.get("project"), dict) else None
        ) or self.config.get("project_name") or self.blueprint.get("project") or "modernized-application"
        self.epics = []          # ordered list of top-level Item(kind=Epic)
        self._epic_n = 0

    # -- numbering helpers ------------------------------------------------- #
    def _next_epic(self):
        self._epic_n += 1
        return self._epic_n

    # -- public entry ------------------------------------------------------ #
    def build(self):
        active = D.active_requirements(self.graph)
        order = topo_sort_requirements(active)
        node_by_id = {rid: (dom, node) for dom, rid, node in active}

        # Group ordered requirements by domain, preserving global topo order.
        domain_reqs = defaultdict(list)        # domain -> [req_id in topo order]
        domain_first_seen = []                 # domains in first-appearance order
        for rid in order:
            dom = node_by_id[rid][0]
            if dom not in domain_reqs:
                domain_first_seen.append(dom)
            domain_reqs[dom].append(rid)

        # (1) one delivery epic per domain (PHASE: build) ------------------- #
        for dom in domain_first_seen:
            en = self._next_epic()
            epic = Item(
                "Epic", "EPIC-{0}".format(en),
                summary="Deliver domain: {0}".format(dom),
                description="Modernize and deliver all active capabilities in the "
                            "`{0}` domain. {1} requirement(s) in dependency "
                            "order.".format(dom, len(domain_reqs[dom])),
                epic_name="Domain {0}".format(dom),
                labels=[dom, PHASE_BUILD],
            )
            self._build_domain_stories(epic, en, dom, domain_reqs[dom], node_by_id)
            self.epics.append(epic)

        # (2) four cross-cutting epics ------------------------------------- #
        self._build_env_prep_epic(domain_first_seen)
        self._build_data_parity_epic(active, node_by_id)
        self._build_deploy_epic(domain_first_seen)
        self._build_uat_epic(domain_first_seen)

        return self.epics

    # -- domain epic: one story per active requirement --------------------- #
    def _build_domain_stories(self, epic, en, dom, req_ids, node_by_id):
        for si, rid in enumerate(req_ids, 1):
            _dom, node = node_by_id[rid]
            title = node.get("title") or rid
            legacy = [str(x) for x in (node.get("legacy_components") or [])]
            deps = [str(x) for x in (node.get("dependencies") or [])]
            story_num = "STORY-{0}.{1}".format(en, si)
            story = Item(
                "Story", story_num,
                summary="[{0}] {1}".format(rid, title),
                description=self._story_desc(rid, node, legacy, deps),
                parent=epic.summary,
                labels=[dom, PHASE_BUILD],
                req_id=rid, legacy_components=legacy, dependencies=deps,
            )
            self._build_req_tasks(story, story_num, dom, rid, node, legacy, deps)
            epic.children.append(story)

    def _story_desc(self, rid, node, legacy, deps):
        parts = []
        desc = node.get("description")
        if desc:
            parts.append(str(desc).strip())
        rules = node.get("business_rules") or []
        rule_ids = [str(r.get("id")) for r in rules if isinstance(r, dict) and r.get("id")]
        if rule_ids:
            parts.append("Business rules: {0}.".format(", ".join(rule_ids)))
        if legacy:
            parts.append("Legacy components: {0}.".format(", ".join(legacy)))
        if deps:
            parts.append("Depends on: {0}.".format(", ".join(deps)))
        if not parts:
            parts.append("Requirement {0} has no business_rules in the graph — "
                         "treat as a placeholder until extraction resolves it.".format(rid))
        return " ".join(parts)

    # -- per-requirement task chain: prep -> build -> integrate -> verify -- #
    def _build_req_tasks(self, story, story_num, dom, rid, node, legacy, deps):
        # 1. test-prep
        t1 = self._task(story_num, 1, "Test-prep: author contracts & functional tests for {0}".format(rid),
                        dom, PHASE_PREP, story.summary, rid, legacy, deps,
                        desc="Author the per-requirement test contract and the "
                             "functional / parity tests for {0} BEFORE the build "
                             "(shift-left). Source of truth: contracts/{1}/{0}."
                             "contract.json.".format(rid, dom))
        # 2. build (with layer subtasks)
        t2 = self._task(story_num, 2, "Build: implement {0} per blueprint".format(rid),
                        dom, PHASE_BUILD, story.summary, rid, legacy, deps,
                        desc=self._build_task_desc(dom, rid))
        self._add_build_subtasks(t2, story_num, 2, dom, rid)
        # 3. integrate
        t3 = self._task(story_num, 3, "Integrate {0} into the target service".format(rid),
                        dom, PHASE_BUILD, story.summary, rid, legacy, deps,
                        desc="Wire {0} into the assembled target service and resolve "
                             "its declared dependencies ({1}).".format(
                                 rid, ", ".join(deps) if deps else "none"))
        # 4. verify
        t4 = self._task(story_num, 4, "Verify {0}: run functional + parity tests".format(rid),
                        dom, PHASE_TEST, story.summary, rid, legacy, deps,
                        desc="Execute the functional and parity tests authored in "
                             "test-prep against the built component; confirm numeric "
                             "outputs match legacy within their parity_rules.")
        story.children.extend([t1, t2, t3, t4])

    def _build_task_desc(self, dom, rid):
        comp = _component(self.blueprint, dom, rid)
        if comp:
            cls = comp.get("class_name") or comp.get("target_file") or rid
            ct = comp.get("component_type") or comp.get("type") or "component"
            return ("Implement `{0}` ({1}) for {2} as specified in blueprint.json. "
                    "Layered subtasks below follow component_type order.".format(cls, ct, rid))
        return ("Implement the component for {0}. No blueprint component found — "
                "default layered breakdown (model -> repository -> service -> "
                "controller) applied; refine once blueprint.json exists.".format(rid))

    def _add_build_subtasks(self, build_task, story_num, task_i, dom, rid):
        layers = _layers_for(self.blueprint, dom, rid)
        # Always append the test layer last so functional+parity tests are a subtask.
        seq = list(layers) + ["functional+parity tests"]
        for li, layer in enumerate(seq, 1):
            sub_num = "SUB-{0}.{1}.{2}".format(story_num.split("-", 1)[1], task_i, li)
            label_phase = PHASE_TEST if layer == "functional+parity tests" else PHASE_BUILD
            self_sub = Item(
                "Sub-task", sub_num,
                summary="{0}: {1} layer".format(rid, layer),
                description="Build the {0} layer for {1}.".format(layer, rid),
                parent=build_task.summary,
                labels=[dom, label_phase],
                req_id=rid,
            )
            build_task.children.append(self_sub)

    def _task(self, story_num, i, summary, dom, phase, parent, rid, legacy, deps, desc=""):
        num = "TASK-{0}.{1}".format(story_num.split("-", 1)[1], i)
        return Item("Task", num, summary=summary, description=desc, parent=parent,
                    labels=[dom, phase], req_id=rid, legacy_components=legacy,
                    dependencies=deps)

    # -- cross-cutting epics ----------------------------------------------- #
    def _workstream_story(self, epic, en, si, summary, phase, desc, tasks):
        story_num = "STORY-{0}.{1}".format(en, si)
        story = Item("Story", story_num, summary=summary, description=desc,
                     parent=epic.summary, labels=["cross-cutting", phase])
        for ti, (tsummary, tdesc) in enumerate(tasks, 1):
            tnum = "TASK-{0}.{1}.{2}".format(en, si, ti)
            story.children.append(Item(
                "Task", tnum, summary=tsummary, description=tdesc,
                parent=story.summary, labels=["cross-cutting", phase]))
        epic.children.append(story)
        return story

    def _build_env_prep_epic(self, domains):
        en = self._next_epic()
        stack = self.config.get("target_stack") or self.blueprint.get("target_stack") or "the target stack"
        epic = Item("Epic", "EPIC-{0}".format(en),
                    summary="Environment & Test Prep",
                    description="Stand up the build/test infrastructure and shift-left "
                                "test scaffolding before the build begins.",
                    epic_name="Environment & Test Prep", labels=["cross-cutting", PHASE_PREP])
        self._workstream_story(
            epic, en, 1, "Set up CI for the target ({0})".format(stack), PHASE_PREP,
            "Establish the build/test pipeline for the modernized {0} service.".format(stack),
            [("Bootstrap the build tooling and CI pipeline",
              "Configure the build tool and a CI workflow that compiles the target and runs tests."),
             ("Wire the test runner into CI",
              "Make the functional/parity test suite executable in CI on every change.")])
        self._workstream_story(
            epic, en, 2, "Provision development & test environments", PHASE_PREP,
            "Provision the environments the team builds and validates against.",
            [("Provision local & staging environments",
              "Provision the local and staging environments and their datastores."),
             ("Seed reference/test data",
              "Load referentially-consistent reference data for functional and parity testing.")])
        self._workstream_story(
            epic, en, 3, "Author the shared functional test harness", PHASE_PREP,
            "Build the harness the per-requirement functional/parity tests run inside.",
            [("Establish the functional test harness",
              "Stand up the scenario/contract test harness (Given/When/Then + parity)."),
             ("Define the test data fixtures contract",
              "Define fixtures/factories shared across domain functional tests.")])
        self.epics.append(epic)

    def _build_data_parity_epic(self, active, node_by_id):
        en = self._next_epic()
        epic = Item("Epic", "EPIC-{0}".format(en),
                    summary="Data Migration & Parity",
                    description="Migrate legacy data into the target schema and prove "
                                "numeric/behavioral parity against the legacy system.",
                    epic_name="Data Migration & Parity", labels=["cross-cutting", PHASE_TEST])
        # Collect entities (for the migration story) and parity-bearing data assets.
        entities = sorted({name for _d, name, _e in D.iter_entities(self.graph)})
        ent_note = ("Target entities: {0}.".format(", ".join(entities)) if entities
                    else "No entities declared in the requirements graph yet.")
        self._workstream_story(
            epic, en, 1, "Build the data-migration mapping", PHASE_DEPLOY,
            "Map legacy records into the target schema. " + ent_note,
            [("Author legacy->target field mappings",
              "Map each legacy field to its target column, including type/precision."),
             ("Implement & dry-run the migration job",
              "Implement the migration job and dry-run it against a copy of legacy data.")])
        self._workstream_story(
            epic, en, 2, "Build the parity (equivalence) harness", PHASE_TEST,
            "Run identical inputs through legacy and target and diff the outputs.",
            [("Capture legacy golden outputs",
              "Record legacy outputs for a representative input set as the parity baseline."),
             ("Run target vs legacy equivalence diff",
              "Execute the target over the same inputs and assert equivalence within parity_rules.")])
        self.epics.append(epic)

    def _build_deploy_epic(self, domains):
        en = self._next_epic()
        target = self.config.get("deployment_target") or "the configured platform"
        epic = Item("Epic", "EPIC-{0}".format(en),
                    summary="Deployment & Cutover",
                    description="Package, deploy to staging, and cut over from the "
                                "legacy system to {0}.".format(target),
                    epic_name="Deployment & Cutover", labels=["cross-cutting", PHASE_DEPLOY])
        self._workstream_story(
            epic, en, 1, "Deploy to staging", PHASE_DEPLOY,
            "Package the target and deploy it to the staging environment on {0}.".format(target),
            [("Produce deployment artifacts",
              "Generate the container image / deployable artifact and deployment manifests."),
             ("Deploy & smoke-test on staging",
              "Deploy to staging on {0} and run a connectivity/health smoke test.".format(target))])
        self._workstream_story(
            epic, en, 2, "Production cutover", PHASE_DEPLOY,
            "Cut over from legacy to the modernized service (after UAT sign-off).",
            [("Author the cutover & rollback runbook",
              "Document the cutover sequence and a tested rollback path."),
             ("Execute cutover",
              "Promote to production on {0} and decommission the legacy path.".format(target))])
        self.epics.append(epic)

    def _build_uat_epic(self, domains):
        en = self._next_epic()
        epic = Item("Epic", "EPIC-{0}".format(en),
                    summary="UAT & Sign-off",
                    description="Run user-acceptance testing per domain and capture the "
                                "human sign-off that clears GATE_4_UAT.",
                    epic_name="UAT & Sign-off", labels=["cross-cutting", PHASE_TEST])
        # One UAT story per domain so sign-off is traceable to a domain.
        if domains:
            for si, dom in enumerate(domains, 1):
                self._workstream_story(
                    epic, en, si, "Run UAT for domain: {0}".format(dom), PHASE_TEST,
                    "Independent UAT review of the {0} domain against its acceptance "
                    "criteria.".format(dom),
                    [("Execute UAT scenarios for {0}".format(dom),
                      "Run the acceptance scenarios for {0} and capture evidence.".format(dom)),
                     ("Record UAT verdict for {0}".format(dom),
                      "Record the independent UAT verdict (evidence for GATE_4_UAT).")])
        else:
            self._workstream_story(
                epic, en, 1, "Run UAT", PHASE_TEST,
                "Independent UAT review against the acceptance criteria.",
                [("Execute UAT scenarios", "Run acceptance scenarios and capture evidence."),
                 ("Record UAT verdict", "Record the independent UAT verdict for GATE_4_UAT.")])
        self.epics.append(epic)


# --------------------------------------------------------------------------- #
# Renderers.
# --------------------------------------------------------------------------- #
def render_markdown(builder, epics):
    graph = builder.graph
    md = []
    md.append("# Migration Execution Plan — {0}".format(builder.project))
    md.append("")
    md.append("> Generated by `anti-legacy:migration-plan` from "
              "`requirements_graph.json` + `blueprint.json` + `config.json`. "
              "Epics -> stories -> tasks -> subtasks, numbered for explicit "
              "ordering. Re-render after the inputs change rather than editing "
              "this file by hand.")
    md.append("")

    active = D.active_requirements(graph)
    dropped = D.dropped_requirements(graph)
    mode = (graph.get("metadata") or {}).get("migration_mode")
    n_epics = len(epics)
    n_stories = sum(len(e.children) for e in epics)
    n_tasks = sum(len(s.children) for e in epics for s in e.children)
    n_subs = sum(len(t.children) for e in epics for s in e.children for t in s.children)

    md.append("## Summary")
    md.append("")
    md.append("- Project: **{0}**{1}".format(
        builder.project, "  ·  migration mode: **{0}**".format(mode) if mode else ""))
    md.append("- {0} epic(s), {1} story(ies), {2} task(s), {3} subtask(s).".format(
        n_epics, n_stories, n_tasks, n_subs))
    md.append("- {0} active requirement(s) mapped to delivery stories; "
              "{1} dropped/out-of-scope (listed at the end).".format(len(active), len(dropped)))
    md.append("- Phase order: **prep -> build -> deploy -> test**. Requirements "
              "within a domain are in dependency (topological) order.")
    md.append("")
    md.append("Companion machine-importable backlog: "
              "[`migration-plan.jira.csv`]({0}).".format(CSV_NAME))
    md.append("")

    # The hierarchy.
    for epic in epics:
        md.append("## {0} — {1}".format(epic.num, epic.summary))
        md.append("")
        if epic.description:
            md.append(epic.description)
            md.append("")
        if epic.labels:
            md.append("_Labels: {0}_".format("; ".join(epic.labels)))
            md.append("")
        for story in epic.children:
            md.append("### {0} — {1}".format(story.num, story.summary))
            md.append("")
            trace = _trace_line(story)
            if trace:
                md.append(trace)
                md.append("")
            if story.description:
                md.append(story.description)
                md.append("")
            for task in story.children:
                dep = (" — depends: {0}".format(", ".join(task.dependencies))
                       if task.dependencies else "")
                md.append("- [ ] **{0}** {1} _(phase: {2}){3}_".format(
                    task.num, task.summary, _phase_of(task), dep))
                if task.description:
                    md.append("  - {0}".format(task.description))
                for sub in task.children:
                    md.append("  - [ ] **{0}** {1}".format(sub.num, sub.summary))
            md.append("")

    # Out-of-scope (dropped) requirements — never silently lost (§Voice).
    md.append("## Out of scope — dropped / unresolvable requirements")
    md.append("")
    if dropped:
        md.append("These requirements are NOT in the execution plan. Each is an "
                  "explicit scope cut with its reason and legacy provenance:")
        md.append("")
        rows = []
        for dom, rid, node in dropped:
            reason = node.get("disposition_reason") or node.get("status") or "dropped"
            legacy = ", ".join(str(x) for x in (node.get("legacy_components") or [])) or "—"
            rows.append([rid, dom, node.get("title") or "", reason, legacy])
        md.append(D.md_table(["req_id", "domain", "title", "reason", "legacy_components"], rows))
        md.append("")
    else:
        md.append("_None — every requirement in the graph is in the active build set._")
        md.append("")

    return "\n".join(md)


def _phase_of(item):
    for lbl in item.labels:
        if lbl in (PHASE_PREP, PHASE_BUILD, PHASE_DEPLOY, PHASE_TEST):
            return lbl
    return "build"


def _trace_line(story):
    if not story.req_id:
        return ""
    legacy = ", ".join(story.legacy_components) if story.legacy_components else "_none declared_"
    deps = ", ".join(story.dependencies) if story.dependencies else "none"
    return "Traceability: req_id **{0}** -> legacy_components: {1} -> depends: {2}.".format(
        story.req_id, legacy, deps)


def _iter_all_items(epics):
    """Yield every item depth-first in the canonical hierarchy order."""
    for epic in epics:
        yield epic
        for story in epic.children:
            yield story
            for task in story.children:
                yield task
                for sub in task.children:
                    yield sub


def render_csv(epics):
    """Render the Jira-importable CSV via the stdlib csv module.

    Columns: Issue Type, Summary, Description, Epic Name, Parent, Labels, Order.
    Standard Jira hierarchy convention: epics carry an `Epic Name`; every
    non-epic carries a `Parent` = its parent item's Summary. `Order` is the
    item's hierarchical number (EPIC-1, STORY-1.1, ...). The csv module handles
    all quoting/escaping (commas, quotes, semicolons, newlines).
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Issue Type", "Summary", "Description", "Epic Name",
                     "Parent", "Labels", "Order"])
    for it in _iter_all_items(epics):
        writer.writerow([
            it.kind,
            it.summary,
            it.description,
            it.epic_name,            # epics only; "" otherwise
            it.parent,               # "" for epics
            ";".join(it.labels),     # Jira splits labels on the configured sep
            it.num,
        ])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def generate(register=True):
    """Build the plan and write both artifacts. Returns (md_path, csv_path, stats).

    Raises ValueError when there is no requirements graph with an active set —
    there is nothing to plan.
    """
    graph = D.load_requirements_graph()
    if not graph or not graph.get("domains"):
        raise ValueError(
            "No requirements graph found at .anti-legacy/requirements/"
            "requirements_graph.json (or it has no domains). Run the pipeline "
            "through graph-translate first — there is nothing to plan."
        )
    active = D.active_requirements(graph)
    if not active:
        raise ValueError(
            "The requirements graph has no ACTIVE requirements (all dropped/"
            "unresolvable). There is no build scope to plan."
        )

    blueprint = D.load_blueprint()
    config = D.load_config()

    builder = PlanBuilder(graph, blueprint, config)
    epics = builder.build()

    md = render_markdown(builder, epics)
    csv_text = render_csv(epics)

    md_path = D.write_deliverable(MD_NAME, md)
    csv_path = D.write_deliverable(CSV_NAME, csv_text)

    # Done-gate: both artifacts must be non-empty before we register anything.
    for p in (md_path, csv_path):
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            raise ValueError("Render produced an empty artifact: {0}".format(p))

    stats = {
        "epics": len(epics),
        "stories": sum(len(e.children) for e in epics),
        "tasks": sum(len(s.children) for e in epics for s in e.children),
        "subtasks": sum(len(t.children) for e in epics for s in e.children for t in s.children),
        "active": len(active),
        "dropped": len(D.dropped_requirements(graph)),
        "blueprint": bool(blueprint),
    }

    if register:
        D.register_deliverable(ARTIFACT_MD, md_path, PRODUCED_BY,
                               fmt="markdown", depends_on=DEPENDS_ON)
        D.register_deliverable(ARTIFACT_CSV, csv_path, PRODUCED_BY,
                               fmt="text", depends_on=DEPENDS_ON)

    return md_path, csv_path, stats


def main():
    parser = argparse.ArgumentParser(
        prog="migration_plan",
        description="Render the end-to-end migration execution plan (epics -> "
                    "stories -> tasks -> subtasks) as a Markdown hierarchy + a "
                    "Jira-importable CSV, from the requirements graph + blueprint.",
    )
    parser.add_argument("--no-register", action="store_true",
                        help="Write the artifacts but do not register them in the manifest.")
    parser.add_argument("--force", action="store_true",
                        help="override a precheck BLOCK and render anyway (loud warning)")
    args = parser.parse_args()
    D.require_ready("deliverables", force=args.force)

    try:
        md_path, csv_path, stats = generate(register=not args.no_register)
    except ValueError as e:
        print("Error: {0}".format(e), file=sys.stderr)
        sys.exit(1)

    print("Migration execution plan written:")
    print("  {0:>30}  {1}".format(MD_NAME, md_path))
    print("  {0:>30}  {1}".format(CSV_NAME, csv_path))
    print("Decomposition: {epics} epic(s), {stories} story(ies), {tasks} task(s), "
          "{subtasks} subtask(s).".format(**stats))
    print("Scope: {active} active requirement(s), {dropped} dropped/out-of-scope. "
          "Blueprint present: {blueprint}.".format(**stats))
    if args.no_register:
        print("(--no-register: manifest not touched.)")
    else:
        print("Registered artifacts: {0} (markdown), {1} (text).".format(
            ARTIFACT_MD, ARTIFACT_CSV))


if __name__ == "__main__":
    main()
