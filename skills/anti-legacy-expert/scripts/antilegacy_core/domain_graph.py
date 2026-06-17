#!/usr/bin/env python3
"""
anti-legacy:domain-graph (§I5) — the TARGET-state domain-graph builder.

This is the §I5 re-think. It consumes the LIVE wicked-estate code graph + the
`.anti-legacy/annotations.jsonl` overlay (NOT a code-graph JSON file — that world
is deleted) and emits a capability-oriented TARGET requirements graph that
validates against schemas/requirements-graph.enriched.schema.json.

WHY A NEW SCRIPT (not an extension of graph_normalizer.py):
  * DIFFERENT INPUT. graph_normalizer reads a code-graph JSON file via --input
    (code_graph["applications"][app]["nodes"|"edges"]). §I5 reads the LIVE engine
    (wicked_estate.list_nodes / cluster / overlay) — there IS no code-graph JSON.
  * DIFFERENT OUTPUT. graph_normalizer emits EMPTY business_rules (the raw draft
    profile, enriched LATER). §I5 emits POPULATED object-form business_rules
    straight from the annotation overlay.
  * TEST PRESERVATION. graph_normalizer is pinned GREEN by a half-dozen suites
    driving its code-graph-JSON CLI. A new file leaves them untouched.
graph_normalizer.py is intentionally left exactly as-is.

THE CORE INVARIANT (no silent maybe-correct): every RESOLVED code-graph
requirement edge is COVERED by the domain graph honoring disposition. A KEPT or
MODIFIED legacy rule MUST appear in some requirement's legacy_components; a DROPPED
rule MUST appear in the drop manifest with a reason; a rule that is in NEITHER set
is a SILENT DROP and is a hard build failure. NET-NEW target requirements add
capability and trace to no legacy edge (exempt from the round-trip denominator).

ARTIFACTS (all under .anti-legacy/requirements/):
  * requirements_graph.json       — the primary, gate-validated artifact.
  * dispositions.json             — the explicit DROP manifest (the round-trip
                                    read-side proves "no silent omission").
  * roundtrip-coverage.json       — the disposition-aware round-trip gate evidence.

Capability DOMAINS come from cluster(weight="calls") communities (the only sound
mode — confidence/data-affinity collapse to group-by-file; documented shim limit).
Domains are NEVER file/copybook-derived (forbidden). Cross-app merge happens AFTER
per-db clustering (cluster() is single-db).

PARITY_RULES are NOT in the enriched requirements schema (rule objects are
additionalProperties:false). §I5 surfaces machine-readable `parity_hints` on the
requirement (additive optional) so the downstream test-strategy phase populates
every numeric output's contract parity_rules. Universal Don't ("parity rules on
every numeric output") is satisfied by surfacing the hint here.

Repo-agnostic: zero source-repo / program / copybook names baked into logic —
everything comes from config.source_apps + the DB + the overlay. stdlib + jsonschema.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# Import the sibling helper + coverage module the same way coverage.py does:
# the scripts dir on sys.path, then plain imports. This keeps the builder
# engine-independent (everything funnels through wicked_estate + coverage).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from antilegacy_core import coverage as cov          # noqa: E402
from antilegacy_core import wicked_estate as we      # noqa: E402
from antilegacy_core import vocabulary as vocab      # noqa: E402  (shared tokenizer + miner kind sets for glossary-direct naming)

REPO_ROOT = os.getcwd()  # workspace == cwd, not the package __file__ (ISS-23)
DEFAULT_CONFIG = os.path.join(REPO_ROOT, ".anti-legacy", "config.json")
# CWD-relative on purpose (resolved against the workspace at runtime via run.py),
# NOT __file__-anchored — so term-aware naming reads the WORKSPACE glossary, not
# the plugin-install copy (avoids the ISS-23 trap for this read).
DEFAULT_VOCAB_PATH = os.path.join(".anti-legacy", "vocabulary.json")
DEFAULT_OUTPUT = os.path.join(
    REPO_ROOT, ".anti-legacy", "requirements", "requirements_graph.json"
)
DEFAULT_COVERAGE_REPORT = os.path.join(
    REPO_ROOT, ".anti-legacy", "coverage-report.json"
)
# Per-app graph DBs default location. Like coverage.GRAPHS_DIR this is the
# __file__-anchored (plugin-install) fallback ONLY — production runs anchor the
# graphs dir on the WORKSPACE (the resolved --config dir / CWD), never here.
DEFAULT_GRAPHS_DIR = os.path.join(REPO_ROOT, ".anti-legacy", "graphs")
from importlib import resources as _resources  # noqa: E402
SCHEMA_PATH = str(
    _resources.files("antilegacy_core")
    / "schemas" / "requirements-graph.enriched.schema.json"
)

# Mandatory cluster weight mode — see module docstring / wicked_estate.cluster
# KNOWN SHIM LIMITATION. "calls" is the only mode that yields a real capability
# partition today. Hardcoding it is correct, not a shortcut.
CLUSTER_WEIGHT = "calls"

# When a community's behavior-bearing member count exceeds this threshold the
# community is too large to yield a meaningful capability unit (dense modern code
# collapses into one near-fully-connected blob).  Sub-partition by file/package
# prefix so each generated requirement stays tractable.
# Override per project via config.json: {"domain_graph": {"max_community_size": N}}.
DEFAULT_MAX_COMMUNITY_MEMBERS = 500

# Data-asset kinds: a member's outgoing edge to one of these is a data_access.
# Derived from coverage.py's structural-leaf taxonomy (the data side of it), so
# this stays in lock-step with the engine's kind vocabulary. Repo-agnostic.
DATA_ASSET_KINDS = {
    "table",
    "file",
    "table_model",
    "dataset",
    "db2_table",
    "ims_database",
    "ims_segment",
    "copybook",
}

# Numeric-output detection for parity hints. Money / rate / percent / count
# language in a rule statement or a field name flags a numeric output whose
# COMP-3 precision loss is silent. Repo-agnostic keyword heuristic.
#
# Defect-3 hardening: in addition to the domain nouns, EXPLICIT numeric / packed-
# decimal markers (COMP-3, packed decimal, cent, basis-point, dollar/currency,
# remuneration/receivable, score) are treated as numeric so a money/rate output
# that names the precision risk by its mechanism (not its noun) is never missed.
# A standalone numeric marker with no domain noun is classed conservatively as
# `money` (the COMP-3 / packed-decimal precision-loss class), except score (count)
# and explicit rate/percent markers.
_PARITY_PATTERNS = [
    ("rate", re.compile(
        r"\b(rate|apr|apy|yield|factor|basis[\s-]?point|basis[\s-]?points|bps)\b",
        re.I)),
    ("percent", re.compile(r"\b(percent|percentage|pct)\b|%", re.I)),
    ("count", re.compile(
        r"\b(count|number\s+of|num|qty|quantity|tally|score|fico)\b", re.I)),
    ("money", re.compile(
        r"\b(amount|amt|balance|total|subtotal|price|cost|fee|charge|payment|"
        r"credit|debit|limit|due|principal|interest|dollar|dollars|currency|"
        r"cent|cents|remuneration|receivable|receivables|payable|payables|"
        r"disbursement|settlement|comp-?3|packed[\s-]?decimal)\b", re.I)),
]


class DomainGraphError(RuntimeError):
    """Any builder-level failure (front-half coverage gap, schema-invalid output,
    round-trip gap). Callers get one exception type to catch."""


# ---------------------------------------------------------------------------
# Config + front-half coverage precondition
# ---------------------------------------------------------------------------
def load_config(config_path: str) -> dict:
    if not config_path or not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def assert_front_half_coverage(coverage_report_path: str) -> dict:
    """(A) FRONT-HALF precondition: refuse to translate an incompletely-annotated
    graph. The latest coverage-report.json MUST show coverage == 1.0. Surfaces the
    unaccounted SymbolIds when it does not (mirrors coverage.py's gate behavior).

    Returns the parsed report. Missing report is a hard error (extraction must
    have run first).
    """
    if not os.path.exists(coverage_report_path):
        raise DomainGraphError(
            "front-half coverage report not found: %s — run `extraction` + "
            "`coverage` first (§I5 refuses to translate an unannotated graph)."
            % coverage_report_path
        )
    try:
        with open(coverage_report_path, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DomainGraphError(
            "cannot read coverage report %s: %s" % (coverage_report_path, exc)
        )
    cov_val = report.get("coverage")
    try:
        cov_val = float(cov_val)
    except (TypeError, ValueError):
        cov_val = 0.0
    if cov_val < 1.0:
        unaccounted = [
            u.get("symbol_id") for u in report.get("unaccounted_nodes", [])
        ]
        raise DomainGraphError(
            "front-half coverage %.4f < 1.0 — %d behavior-bearing nodes are "
            "neither RESOLVED nor RISK. Annotate them before §I5:\n  %s"
            % (cov_val, len(unaccounted), "\n  ".join(str(u) for u in unaccounted))
        )
    return report


def resolve_app_dbs(config: dict, explicit_db=None, graphs_dir=None):
    """Resolve (app_name, db_path) pairs — the SAME pattern coverage.resolve_app_dbs
    uses, so the §I5 denominator matches the front-half denominator exactly.

    `graphs_dir` is the directory the per-app DBs live in. It MUST be anchored on
    the WORKSPACE (beside the loaded config), not on this script's plugin-install
    location — `build()` derives it from the resolved config dir and threads it
    here. Without it a no-`--db` multi-repo run falls back to coverage.GRAPHS_DIR
    (the `__file__`-anchored plugin tree) and finds nothing — the ISS-23 trap that
    coverage.py already fixed for its own path."""
    if graphs_dir is None:
        graphs_dir = DEFAULT_GRAPHS_DIR
    return cov.resolve_app_dbs(config, explicit_db=explicit_db, graphs_dir=graphs_dir)


# ---------------------------------------------------------------------------
# Per-app gather: nodes (behavior subset) + clusters + overlay + edges
# ---------------------------------------------------------------------------
def _confirmed_terms_by_type(vocab_path: str) -> dict:
    """The CONFIRMED glossary terms bucketed by type — {"entity": [...],
    "action": [...]}. Only confirmed, non-blank-canonical terms are authoritative
    enough to name a capability. Missing/invalid glossary -> empty buckets."""
    out = {"entity": [], "action": []}
    if not vocab_path or not os.path.isfile(vocab_path):
        return out
    try:
        with open(vocab_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return out
    for t in (doc.get("terms") if isinstance(doc, dict) else None) or []:
        if t.get("status") == "confirmed" and t.get("canonical") \
                and t.get("term_type") in out:
            out[t["term_type"]].append(t["canonical"])
    return out


def _confirmed_term_freqs(vocab_path: str) -> dict:
    """Confirmed canonicals bucketed by type WITH their glossary freq —
    {"entity": {canon: freq}, "action": {canon: freq}}. Freq ranks which term
    wins when a name carries more than one confirmed token (e.g. KafkaProducer
    -> KAFKA + PRODUCER; the higher-freq PRODUCER is the head noun). Empty
    buckets on a missing/invalid glossary (clean fallback)."""
    out = {"entity": {}, "action": {}}
    if not vocab_path or not os.path.isfile(vocab_path):
        return out
    try:
        with open(vocab_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return out
    for t in (doc.get("terms") if isinstance(doc, dict) else None) or []:
        if t.get("status") == "confirmed" and t.get("canonical") \
                and t.get("term_type") in out:
            out[t["term_type"]][t["canonical"]] = int(t.get("freq", 0) or 0)
    return out


def _derive_term_index_from_glossary(nodes_list, vocab_path):
    """Glossary-DIRECT term index — {node_name -> {entity, action}} derived by
    matching each node name's tokens against the CONFIRMED glossary, WITHOUT the
    engine `domain_*` annotation round-trip.

    This is the engine-independent path the projected-tags reader (the native
    `annotate`/`nodes --annotated-with` seam) falls back to when the engine
    exposes no annotation store (e.g. wicked-estate releases without the native
    `annotate` subcommand). The glossary is already the term MEANING source
    (Read-able, default-0); deriving the per-node binding here just skips the
    re-projectable engine cache. Each node's:
      * entity = the highest-glossary-freq confirmed-entity token in its name
        (the dominant domain noun — head noun of a compound like KafkaProducer);
      * action = the first confirmed-action token in its name (skipping the
        modern accessor/boilerplate verbs the action miner already excludes).
    Returns {} when the glossary has no confirmed terms (clean fallback to the
    statement-noun namer)."""
    freqs = _confirmed_term_freqs(vocab_path)
    ent_freq, act_freq = freqs["entity"], freqs["action"]
    if not ent_freq and not act_freq:
        return {}
    index = {}
    for n in nodes_list:
        name = n.get("name")
        if not name:
            continue
        toks = vocab._tokens(name)
        slot = {}
        # Boolean/accessor boilerplate (get/set/is/has/new) is language mechanics,
        # never a domain noun OR verb — exclude it from BOTH the entity and the
        # action choice so 'isConnected' does not name an IS capability.
        ent_hits = [(ent_freq[t], t) for t in toks
                    if t in ent_freq and t not in vocab._ACTION_BOILERPLATE]
        if ent_hits:
            # highest freq wins; ties -> alphabetical (deterministic)
            slot["entity"] = max(ent_hits, key=lambda ft: (ft[0], ft[1]))[1]
        for t in toks:
            if t in vocab._ACTION_BOILERPLATE:
                continue
            if t in act_freq:
                slot["action"] = t
                break
        if slot:
            index[name] = slot
    return index


def build_term_index(db_path: str, vocab_path: str) -> dict:
    """Build the {node_name -> {"entity": canonical, "action": canonical}} term
    index that names capabilities (ISS-02), MERGING two signals:

      1. GLOSSARY-DIRECT (dense base): tokenize every node name and match against
         the confirmed glossary. This binds many nodes (every name carrying a
         confirmed term), which is what makes same-capability nodes across apps
         share an entity and COALESCE.
      2. ENGINE-PROJECTED `domain_*` tags (authoritative overlay): the
         `vocabulary project` cache, read via `nodes --annotated-with`. The engine
         binds CONSERVATIVELY (only unambiguous nodes), so on its own it is sparse
         and under-coalesces; used as an OVERLAY it corrects/authorizes the dense
         base where the human-curated projection disagrees.

    Engine tags win on conflict (the curated cache is authoritative); the dense
    base fills every node the conservative projection skipped. Empty when no
    glossary / unreadable DB, so naming falls back cleanly to statement nouns."""
    # 1. Dense base from the glossary (engine-independent).
    index: dict = {}
    freqs = _confirmed_term_freqs(vocab_path)
    if freqs["entity"] or freqs["action"]:
        try:
            index = _derive_term_index_from_glossary(
                we.list_nodes(db_path), vocab_path)
        except we.WickedEstateError:
            index = {}

    # 2. Overlay the authoritative engine-projected tags (engine wins on conflict).
    terms = _confirmed_terms_by_type(vocab_path)
    try:
        for term_type, key in (("entity", "domain_entity"), ("action", "domain_action")):
            for canonical in terms[term_type]:
                # Boolean/accessor boilerplate is language mechanics, never a domain
                # entity OR action — even if a confirmed term, it must not name an
                # `IsCapability`. Filtered on BOTH the base and this overlay.
                if canonical in vocab._ACTION_BOILERPLATE:
                    continue
                for node in we.nodes_annotated_with(db_path, key, canonical):
                    nm = node.get("name")
                    if nm:
                        index.setdefault(nm, {})[term_type] = canonical
    except we.WickedEstateError:
        # Engine can't read the DB (missing / non-engine fixture / no native
        # annotate): the dense base from step 1 stands on its own.
        pass
    return index


def gather_app(app_name: str, db_path: str, settings: dict, overlay_index: dict,
               vocab_path: str = DEFAULT_VOCAB_PATH, language: str = None):
    """Gather everything §I5 needs for one source app, all via the helper.

    Returns a dict:
      nodes        : {symbol_id -> node} for ALL nodes (name/kind/file/...).
      behavior_ids : set of behavior-bearing symbol_ids (the §I5 denominator side).
      communities  : {label -> [symbol_id]}  (cluster weight="calls").
      node_comm    : {symbol_id -> label}.
      annotations  : {symbol_id -> overlay record} (per-node, db_id-tolerant).
      edges_out    : {symbol_id -> [(tgt_symbol_id, kind), ...]}  (calls + data).
      term_index   : {node_name -> {entity, action}} from the engine domain_* tags.
      language     : the source app's declared language (drives the capability
                     partition strategy — mainframe stays on call-affinity,
                     modern uses package structure; see _capability_partition).
    """
    if not os.path.exists(db_path):
        raise DomainGraphError(
            "db not found for app %r: %s — run `survey` (wicked-estate index) first."
            % (app_name, db_path)
        )

    nodes_list = we.list_nodes(db_path)
    nodes = {n["symbol_id"]: n for n in nodes_list}
    behavior_ids = {
        n["symbol_id"] for n in nodes_list if cov.is_behavior_bearing(n, settings)
    }

    clusters = we.cluster(db_path, weight=CLUSTER_WEIGHT)

    # Per-node overlay lookup (db_id-tolerant via the same helper coverage uses).
    annotations = {}
    for sid in nodes:
        rec = cov.annotation_for(overlay_index, sid, app_name=app_name)
        if rec is not None:
            annotations[sid] = rec

    # Outgoing edges (source=dependent, target=dependency). We keep BOTH the
    # call edges (cross-cluster dependencies) and the data edges (data_access).
    edges_out: dict = {}
    for src, tgt, kind, _conf, _f in we._read_edges(db_path):
        edges_out.setdefault(src, []).append((tgt, kind))

    # Term-aware capability naming (ISS-02): read the domain_* tags vocabulary
    # project bound onto this graph. Empty (clean fallback) if nothing projected.
    term_index = build_term_index(db_path, vocab_path)

    return {
        "app": app_name,
        "db": db_path,
        "nodes": nodes,
        "behavior_ids": behavior_ids,
        "communities": clusters["communities"],
        "node_comm": clusters["node_community"],
        "annotations": annotations,
        "edges_out": edges_out,
        "term_index": term_index,
        "language": (language or "").strip().lower(),
    }


# ---------------------------------------------------------------------------
# Classification reuse — a member is a SETTLED requirement only when coverage
# would call it RESOLVED. We reuse cov.classify_node so the §I5 threshold logic
# stays consistent with the front-half (never re-implemented).
# ---------------------------------------------------------------------------
def member_state(annotation, settings):
    """(state, confidence) for one member via the front-half classifier."""
    return cov.classify_node(annotation, settings, native_validated=None)


# ---------------------------------------------------------------------------
# Capability identity + naming (deterministic, capability-oriented, repo-agnostic)
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "for", "with", "by", "on", "in",
    "is", "are", "be", "from", "this", "that", "rule", "rules", "must", "should",
    "when", "if", "then", "value", "field", "record", "data", "program",
}
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")


def _capability_words(statements):
    """Rank salient capability nouns across a set of rule statements (most frequent
    non-stopword first). Deterministic: ties broken alphabetically."""
    freq: dict = {}
    for st in statements:
        for w in _WORD_RE.findall(st or ""):
            lw = w.lower()
            if lw in _STOPWORDS or len(lw) < 3:
                continue
            freq[lw] = freq.get(lw, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]


def _titlecase(token: str) -> str:
    """A copybook/program-id-ish token -> CamelCase capability fragment."""
    parts = re.split(r"[^A-Za-z0-9]+", token)
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p)


def _dominant(tally):
    """Most frequent key (ties broken alphabetically); None for an empty tally."""
    if not tally:
        return None
    return min(tally, key=lambda k: (-tally[k], k))


def _term_aware_name(member_terms):
    """ISS-02: name a capability from the dominant domain TERM among its members
    — the `domain_*` tags `vocabulary project` bound onto the graph. A verb+noun
    (action+entity) yields a true capability name (e.g. POST + TRAN ->
    `PostTranCapability`). Returns None when members carry no domain tags, so the
    caller falls back to statement/program-id naming."""
    ent: dict = {}
    act: dict = {}
    for t in member_terms or []:
        if t.get("entity"):
            ent[t["entity"]] = ent.get(t["entity"], 0) + 1
        if t.get("action"):
            act[t["action"]] = act.get(t["action"], 0) + 1
    e = _dominant(ent)
    a = _dominant(act)
    if a and e:
        return _titlecase(a) + _titlecase(e) + "Capability"
    if e:
        return _titlecase(e) + "Capability"
    if a:
        return _titlecase(a) + "Capability"
    return None


def domain_name_for(statements, member_names, member_terms=None):
    """Synthesize a capability DOMAIN name (capability noun, never Domain_<file>).

    Priority: (1) the dominant confirmed domain TERM among members — the
    term-aware capability name read from the engine's `domain_*` tags (ISS-02);
    (2) the dominant capability noun from the annotated rule statements; (3) the
    lexicographically-first member program identity. Deterministic across runs.
    NOTE: collisions across apps that denote the same capability naturally merge
    into one domain (same name -> same domain key), which is exactly the
    cross-app capability coalescing §I5 wants.
    """
    term_name = _term_aware_name(member_terms)
    if term_name:
        return term_name
    words = _capability_words(statements)
    if words:
        primary = _titlecase(words[0])
        if len(words) > 1:
            return primary + _titlecase(words[1]) + "Capability"
        return primary + "Capability"
    # No rule text (pure-risk capability): name from the central member program.
    if member_names:
        return _titlecase(sorted(member_names)[0]) + "Capability"
    return "UnnamedCapability"


def req_id_for(member_ids):
    """Deterministic, capability-oriented REQ_ID — a stable hash of the sorted
    member symbol_ids (NOT a program name). Stable across runs."""
    import hashlib
    h = hashlib.sha256("|".join(sorted(member_ids)).encode("utf-8")).hexdigest()
    return "REQ_CAP_" + h[:12].upper()


# ---------------------------------------------------------------------------
# data_access + entities (co-located so data_access ⊆ own-domain entities — T2)
# ---------------------------------------------------------------------------
def _entity_object(asset_name: str) -> dict:
    """Canonical entity object — mirrors graph_normalizer._entity_object so the
    co-location T2 invariant holds with the identical shape the rest of the
    pipeline expects."""
    return {
        "description": "Logical entity derived from legacy asset: %s" % asset_name,
        "fields": [
            {"name": "id", "type": "string", "description": "Primary identifier"}
        ],
    }


def member_data_access(app: dict, member_ids):
    """Union of data-asset names the members touch (via outgoing edges to
    data-asset-kind nodes). Repo-agnostic — keyed off node KIND, not file name."""
    nodes = app["nodes"]
    edges_out = app["edges_out"]
    assets = set()
    for sid in member_ids:
        for tgt, _kind in edges_out.get(sid, []):
            tnode = nodes.get(tgt)
            if not tnode:
                continue
            tkind = cov.normalize_kind(tnode.get("kind"))
            if tkind in DATA_ASSET_KINDS:
                name = tnode.get("name")
                if name:
                    assets.add(name)
    return sorted(assets)


# ---------------------------------------------------------------------------
# parity hints (additive; surfaced for the test-strategy contract phase)
# ---------------------------------------------------------------------------
def _parity_hints(rule_records, member_names):
    """Detect numeric outputs (money/rate/percent/count) from rule statements and
    surface a machine-readable hint per detected output. The downstream
    test-strategy phase turns these into contract parity_rules.

    Each hint: {field, kind, precision}. precision is a conservative default
    (2 dp money, 6 dp rate/percent, 0 count) the contract phase can refine.
    """
    hints = []
    seen = set()
    default_precision = {"money": 2, "rate": 6, "percent": 4, "count": 0}
    for rec in rule_records:
        text = rec.get("statement", "") or ""
        for kind, pat in _PARITY_PATTERNS:
            m = pat.search(text)
            if not m:
                continue
            field = m.group(0).strip().lower()
            key = (field, kind)
            if key in seen:
                continue
            seen.add(key)
            hints.append({
                "field": field,
                "kind": kind,
                "precision": default_precision[kind],
            })
    return hints


# ---------------------------------------------------------------------------
# Rule object whitelist (schema rule objects are additionalProperties:false)
# ---------------------------------------------------------------------------
def _clamp_conf(value):
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    if c < 0:
        return 0.0
    if c > 1:
        return 1.0
    return c


# GOTCHA-3 trust-tier discriminator. The enriched schema declares
# provenance.source_kinds as an OPTIONAL enum array; these four are the ONLY
# legal members. A rule is trusted_verified ONLY when grounded in code-body
# and/or data-def; comment/doc-only grounding is untrusted (RISK-eligible). See
# schemas/requirements-graph.enriched.schema.json + tests/test_provenance_trust.py.
_SOURCE_KIND_ENUM = ("code-body", "data-def", "comment", "doc")


def _clean_source_kinds(value):
    """Validate + de-noise the annotation's source_kinds for emission into the
    rule object's provenance.

    Returns a list of legal source-kind strings (input order preserved,
    de-duplicated), or None when there is nothing schema-valid to emit — absent,
    not a list/tuple, or EVERY entry out-of-enum. The slot is OPTIONAL: absence is
    tolerated cleanly (None -> the key is omitted), and an invalid kind is DROPPED
    rather than failing the build, so a stray value never invalidates the
    otherwise-good graph against the enum-constrained schema."""
    if not isinstance(value, (list, tuple)):
        return None
    cleaned = []
    seen = set()
    for k in value:
        if k in _SOURCE_KIND_ENUM and k not in seen:
            seen.add(k)
            cleaned.append(k)
    return cleaned or None


def _rule_object(rule_id, statement, symbol_id, app_name, annotation):
    """Build a schema-legal business_rules item (the whitelisted five keys only:
    id, statement, source_ref, confidence, provenance). The overlay's extra keys
    (raw_confidence, ring_depth, cluster, status, ...) MUST NOT leak in.

    GOTCHA-3: the annotation's `source_kinds` (the grounding kind(s) the extractor
    actually read — code-body | data-def | comment | doc) ride through into
    `provenance.source_kinds` so the trust tier is computable downstream. Validated
    against the schema enum, de-duplicated, and OPTIONAL (omitted cleanly when the
    annotation carries none / only out-of-enum values)."""
    obj = {
        "id": rule_id,
        "statement": statement or "RISK",
        "source_ref": symbol_id,
    }
    conf = _clamp_conf(annotation.get("confidence")) if annotation else None
    if conf is not None:
        obj["confidence"] = round(conf, 6)
    program = None
    if annotation:
        program = annotation.get("name") or annotation.get("program")
    ref = annotation.get("provenance") if annotation else None
    prov = {"source_app": app_name}
    if program:
        prov["program"] = str(program)
    if ref is not None:
        prov["ref"] = str(ref)
    # GOTCHA-3 source-kind passthrough (the trust-tier discriminator). The
    # annotation overlay is lossless (--rule-object passes arbitrary keys), so
    # `source_kinds` arrives here when the extractor recorded it.
    kinds = _clean_source_kinds(annotation.get("source_kinds")) if annotation else None
    if kinds:
        prov["source_kinds"] = kinds
    obj["provenance"] = prov
    return obj


# ---------------------------------------------------------------------------
# Sub-partition an oversized community by file/package prefix.
# ---------------------------------------------------------------------------
def _sub_partition_by_package(member_ids, nodes):
    """Split member_ids into sub-groups keyed by the PARENT DIRECTORY of each
    node's file.  Returns {package_dir: [symbol_ids]}.

    Parent directory is the portion of the file path up to (but not including)
    the filename — e.g. "src/main/java/org/apache/kafka/clients/consumer" for a
    file at "src/main/java/org/apache/kafka/clients/consumer/ConsumerRecord.java".
    This is the right semantic boundary for modern Java/Scala/Python packages and
    produces one requirement per source-package directory.

    Every member lands in exactly one group — members without file info land in the
    "" (root) group so nothing is silently dropped.  When all members live in the
    same directory the result is a single-entry dict (no split; the caller handles
    the still-oversized case via schema-valid adaptive rule IDs).
    """
    groups: dict = {}
    for sid in member_ids:
        file_path = (nodes.get(sid) or {}).get("file") or ""
        if not isinstance(file_path, str):
            file_path = ""  # defensive: a non-str file never crashes the split
        norm = file_path.replace("\\", "/").rstrip("/")
        slash = norm.rfind("/")
        key = norm[:slash] if slash >= 0 else ""
        groups.setdefault(key, []).append(sid)
    return groups


# Mainframe languages whose call graph IS the capability structure (discrete
# programs invoked by CALL/EXEC PGM → community detection recovers capabilities).
# Everything else is treated as modern (OO/package-structured), where dense call
# graphs collapse into blobs or explode into per-method singletons and the
# package/directory is the better capability boundary. Repo-agnostic.
_MAINFRAME_LANGUAGES = {
    "cobol", "jcl", "cics", "ims", "db2", "natural", "pli", "pl/i", "rpg",
    "rpg400", "rpgle", "assembler", "asm", "fortran",
}


def _capability_partition(app, max_members):
    """Return the FINAL capability grouping {label -> [behavior_symbol_ids]} for
    one app, choosing the partition signal by the app's language (Phase 2):

      * MAINFRAME (cobol/jcl/…): the call-affinity communities are the capability
        structure — used as-is (oversized communities are still package-split by
        the caller). This is the historical, correct behavior; untouched.
      * MODERN (java/…): dense OO call graphs do not express capabilities — they
        collapse into one mega-community or explode into per-method singletons
        (interface surfaces). Package/directory structure IS the capability
        boundary, so behaviour nodes are grouped by their parent source package.

    `config.coverage.capability_partition` overrides the auto choice:
      "auto" (default) → language-driven as above; "calls" → always communities;
      "package" → always package-grouped; "hierarchical"/"semantic" → use the
    newer engine's finer Louvain / embedding clustering (graceful fallback to the
    auto behavior when the engine does not provide it). Mainframe estates are never
    silently re-partitioned (auto keeps them on calls), so this is a pure modern win.
    """
    communities = app["communities"]
    behavior_ids = app["behavior_ids"]
    nodes = app["nodes"]
    strategy = app.get("capability_partition", "auto")
    language = (app.get("language") or "").lower()

    def _call_groups():
        # Call-affinity communities (mainframe / explicit "calls"). Drop the pure
        # structural communities that carry no behavior member.
        return {label: [m for m in members if m in behavior_ids]
                for label, members in communities.items()
                if any(m in behavior_ids for m in members)}

    # "community": consume the PERSISTED type:community annotations the engine
    # wrote at survey time (`clusters --annotate`, author=system) — the survey's
    # capability partition, co-located on the nodes, with no re-clustering here.
    # Graceful: empty (no community tags / older engine) → degrade to auto.
    if strategy == "community":
        labels = {}
        if app.get("db"):
            try:
                labels = we.community_labels(app["db"])
            except we.WickedEstateError:
                labels = {}
        if labels:
            groups: dict = {}
            for sid in behavior_ids:
                lab = labels.get(sid)
                if lab is not None:
                    groups.setdefault("comm:%s" % lab, []).append(sid)
            if groups:
                return groups
        # No labels (no DB / older engine / clusters --annotate not run) → degrade.
        strategy = "auto"

    # Newer-engine clustering modes (opt-in). Each tries the engine; if it is
    # unavailable (older engine / no DB / can't read it / no embeddings) it returns
    # None and we fall through to the language-driven default — never a hard fail.
    if strategy in ("hierarchical", "semantic"):
        res = None
        if app.get("db"):
            try:
                res = we.cluster_modes(
                    app["db"],
                    hierarchical=(strategy == "hierarchical"),
                    semantic=(strategy == "semantic"))
            except we.WickedEstateError:
                res = None
        if res and res.get("communities"):
            groups = {label: [m for m in members if m in behavior_ids]
                      for label, members in res["communities"].items()
                      if any(m in behavior_ids for m in members)}
            if groups:
                return groups
        # Engine mode unavailable/empty → degrade to the language-driven default.
        strategy = "auto"

    if strategy == "auto":
        use_package = language and language not in _MAINFRAME_LANGUAGES
    else:
        use_package = strategy == "package"

    if use_package:
        # Flatten all behavior members across communities, de-duplicated and
        # order-stable (dict.fromkeys): a degenerate engine partition with
        # overlapping community membership must not duplicate a symbol into two
        # capabilities (and thus into legacy_components / the round-trip set).
        all_behavior = list(dict.fromkeys(
            m for c in communities.values() for m in c if m in behavior_ids))
        pkg_groups = _sub_partition_by_package(all_behavior, nodes)
        # No real package signal (a flat module: every node in one directory) →
        # package-partition would collapse the app into a single capability. Fall
        # back to call-affinity, which still distinguishes sub-capabilities. Only
        # auto mode falls back; an explicit "package" override is honored as asked.
        if strategy == "auto" and len(pkg_groups) <= 1:
            return _call_groups()
        return {("pkg:%s" % k if k else "pkg:<root>"): v for k, v in pkg_groups.items()}

    return _call_groups()


# ---------------------------------------------------------------------------
# Build one capability requirement from a community's behavior members.
# ---------------------------------------------------------------------------
def _risk_reason_for(ann):
    """The human-facing reason a member is on the HITL queue (risk/unaccounted).
    Prefers an explicit risk_reason, then a below-threshold statement, then a
    generic reason. Never returns empty (the rule statement must be non-empty)."""
    if ann:
        rr = (ann.get("risk_reason") or "").strip()
        if rr:
            return rr
        st = (ann.get("statement") or "").strip()
        if st:
            return st
        status = str(ann.get("status", "")).lower()
        if status == "risk":
            return "risk-flagged member (no reason recorded)"
        return "annotation present but below resolve_threshold (HITL queue)"
    return "no annotation recorded for this behavior-bearing node (HITL queue)"


def build_requirement(app: dict, label, member_ids, settings):
    """Produce (req_id, requirement_dict, domain_name, legacy_rule_edges).

    legacy_rule_edges = the list of {app, symbol_id, rule_id, state} for EVERY
    behavior-bearing member (the round-trip T set this requirement contributes).

    CARDINAL FIX (adversarial Defects 1+2): a rule object is emitted for EVERY
    behavior-bearing member — a RESOLVED member gets its real statement; a RISK or
    UNACCOUNTED member gets a REVIEW-flagged rule carrying its own statement /
    risk_reason and `source_ref` = its symbol_id. Previously a risk member only
    flipped a boolean (`has_risk`), so its behavior text vanished from the graph
    while its symbol still rode inside `legacy_components` — a SILENT erasure that
    round-trip (graded at symbol granularity) reported as covered. Now every member
    is BOTH represented in legacy_components AND carries a traceable rule, and the
    round-trip grades each member edge by its own state.
    """
    nodes = app["nodes"]
    annotations = app["annotations"]
    app_name = app["app"]

    member_ids = sorted(member_ids)
    member_names = [nodes[sid]["name"] for sid in member_ids if sid in nodes]

    resolved_statements = []
    rule_objects = []
    legacy_rule_edges = []  # {app, symbol_id, rule_id, state}
    resolved_seq = 0
    review_seq = 0
    n_resolved = 0
    n_review = 0  # risk + unaccounted behavior members (all forced to review)

    # One rule per behavior member, ids re-numbered RULE-NNN deterministically over
    # the (sorted) members so ids are stable + unique within the requirement.
    # Width: minimum 3 digits; widens automatically for large communities so IDs stay
    # unique and match the schema's ^RULE-[0-9]{3,6}$ pattern.
    rule_id_width = max(3, len(str(len(member_ids))))
    rule_seq = 0
    for sid in member_ids:
        ann = annotations.get(sid)
        state, _conf = member_state(ann, settings)
        rule_seq += 1
        rid = "RULE-%0*d" % (rule_id_width, rule_seq)
        if state == "resolved":
            n_resolved += 1
            statement = (ann.get("statement") or "").strip() or "RESOLVED (no statement text)"
            resolved_statements.append(statement)
            rule_objects.append(_rule_object(rid, statement, sid, app_name, ann))
            legacy_rule_edges.append({
                "app": app_name, "symbol_id": sid,
                "rule_id": (ann.get("rule_id") if ann else None) or rid,
                "state": "resolved",
            })
        else:
            # RISK or UNACCOUNTED: keep the member's behavior text in the graph as a
            # review-flagged rule (never silently erased), traceable via source_ref.
            n_review += 1
            reason = _risk_reason_for(ann)
            rule_objects.append(_rule_object(
                rid, "REVIEW REQUIRED: %s" % reason, sid, app_name, ann))
            legacy_rule_edges.append({
                "app": app_name, "symbol_id": sid,
                "rule_id": (ann.get("rule_id") if ann else None) or rid,
                "state": "review",
            })

    # Defensive: a behavior community must contain >=1 behavior member (the caller
    # guarantees it), so rule_objects is never empty. Guard minItems>=1 anyway.
    if not rule_objects:
        rule_objects.append({
            "id": "RULE-001",
            "statement": "UNRESOLVABLE: capability has no annotated behavior rule",
            "provenance": {"source_app": app_name},
        })

    has_risk = n_review > 0
    data_access = member_data_access(app, member_ids)

    # Term-aware naming (ISS-02): the members' domain_* tags, if projected.
    term_index = app.get("term_index", {})
    member_terms = [term_index.get(nm, {}) for nm in member_names]
    domain_name = domain_name_for(resolved_statements, member_names,
                                  member_terms=member_terms)
    req_id = req_id_for(member_ids)

    if n_resolved >= 1 and not has_risk:
        status = "active"
    elif n_resolved >= 1 and has_risk:
        status = "review"
    elif has_risk:
        status = "review"
    else:
        status = "unresolvable"

    # Description names the merged source programs + app (provenance trail) —
    # capability-intent text, NOT "Migrate <PROG>".
    if resolved_statements:
        intent = resolved_statements[0]
    else:
        intent = "behavior pending human review"
    description = (
        "Target capability covering %s (intent: %s). Derived from %d legacy "
        "component(s) in app %r." % (
            ", ".join(sorted(member_names)) or "(unnamed members)",
            intent[:160],
            len(member_ids),
            app_name,
        )
    )
    title = domain_name.replace("Capability", "").strip() or "Capability"

    requirement = {
        "title": title,
        "description": description,
        "legacy_components": member_ids,            # MANDATORY, non-null
        "data_access": data_access,
        "dependencies": [],                          # filled after all reqs exist
        "business_rules": rule_objects,
        "validations": [],
        "error_paths": [],
        "status": status,
        "merged_programs": sorted(member_names),
        # --- additive §I5 fields (schema-legal: requirement object is not
        #     additionalProperties:false) ---
        "provenance": app_name,
        "disposition": None,            # set by reconcile_dispositions
        "disposition_reason": None,     # set by reconcile_dispositions
    }
    parity = _parity_hints(rule_objects, member_names)
    if parity:
        requirement["parity_hints"] = parity

    return req_id, requirement, domain_name, legacy_rule_edges


# ---------------------------------------------------------------------------
# Disposition assignment (keep | modify | drop | new) — the explicit model.
# ---------------------------------------------------------------------------
def reconcile_dispositions(requirements_by_id):
    """Assign disposition PER requirement from (provenance + overlay state +
    cross-app capability coalescing).

    KEEP   : >=1 resolved rule, single-source (or non-conflicting). status active.
    MODIFY : the SAME capability (same domain+title) was contributed by >1 app —
             a cross-source merge that reshapes; status forced to review.
    DROP   : decided at the manifest level (a legacy edge intentionally not made an
             active requirement). v1 emits no automatic drops — every resolved edge
             is represented — but the mechanism + manifest exist and the round-trip
             check reads them, so a future curator can drop with a reason and the
             invariant still holds.
    NEW    : provenance == "net-new" / legacy_components == [].

    Mutates the requirement dicts in place (sets disposition + disposition_reason).
    """
    # Detect cross-source capability overlap: group by (domain, title).
    cap_apps: dict = {}
    for rid, info in requirements_by_id.items():
        key = (info["domain"], info["requirement"]["title"])
        cap_apps.setdefault(key, set()).add(info["requirement"].get("provenance"))

    for rid, info in requirements_by_id.items():
        req = info["requirement"]
        legacy = req.get("legacy_components") or []
        prov = req.get("provenance")
        key = (info["domain"], req["title"])
        # multi-source = >1 distinct LEGACY source apps for the same capability
        # (net-new is not a legacy source — exclude it so a net-new sharing a
        # domain+title never wrongly flips a legacy keep into modify).
        legacy_sources = [a for a in cap_apps.get(key, set()) if a and a != "net-new"]
        multi_source = len(legacy_sources) > 1

        if prov == "net-new" or not legacy:
            req["disposition"] = "new"
            req["disposition_reason"] = (
                "net-new target capability; no legacy source"
            )
        elif multi_source:
            req["disposition"] = "modify"
            req["disposition_reason"] = (
                "merged divergent rules from multiple source apps for capability "
                "%r; reconciled to a single target requirement" % req["title"]
            )
            req["status"] = "review"
        else:
            req["disposition"] = "keep"
            req["disposition_reason"] = "behavior preserved from %s" % prov


# ---------------------------------------------------------------------------
# Cross-cluster dependencies (member calls a node owned by another capability).
# ---------------------------------------------------------------------------
def wire_dependencies(apps, requirements_by_id):
    """Fill each requirement's `dependencies` with the REQ_IDs of other
    capabilities its members CALL (cross-cluster call edges), per app.

    Single-DB call edges ONLY — and this is CORRECT, not a gap (ISS-08): the
    merge case is INDEPENDENT legacy systems coalesced at the capability level
    (by domain+title), not codebases that call each other. The two real source
    apps (carddemo COBOL, credit-card Java) share 0 call targets and emit 0
    cross-app/unresolved edges, so there is nothing to resolve across DBs. A
    cross-app dependency that DID exist (a shared library one repo calls into the
    other) would surface as an unresolved edge target — handle it via the engine's
    `cross-graph` IF such a config appears. How two MERGED capabilities INTERACT
    in the target is a target-design decision made at blueprint time, not a
    legacy-call edge — so it does not belong here."""
    # symbol_id -> req_id (within its app).
    owner: dict = {}
    for rid, info in requirements_by_id.items():
        for sid in info["requirement"].get("legacy_components") or []:
            owner[(info["app"], sid)] = rid

    for app in apps:
        app_name = app["app"]
        for src, edges in app["edges_out"].items():
            src_req = owner.get((app_name, src))
            if not src_req:
                continue
            for tgt, kind in edges:
                if cov.normalize_kind(kind) not in we._CALL_AFFINITY_EDGE_KINDS:
                    continue
                tgt_req = owner.get((app_name, tgt))
                if tgt_req and tgt_req != src_req:
                    deps = requirements_by_id[src_req]["requirement"]["dependencies"]
                    if tgt_req not in deps:
                        deps.append(tgt_req)
    for info in requirements_by_id.values():
        info["requirement"]["dependencies"].sort()


# ---------------------------------------------------------------------------
# NET-NEW target requirements (Defect-5 fix) — the "add target capability" half.
# ---------------------------------------------------------------------------
def build_net_new_requirement(spec):
    """Build ONE net-new target requirement from a curator-authored spec.

    The product is "merge sources AND add net-new target capability". A net-new
    requirement traces to NO legacy edge: provenance="net-new", legacy_components=[]
    (exempt from the round-trip denominator). It still carries >=1 object-form
    business rule so it is schema-valid and reviewable at GATE_1.

    spec (dict) keys:
      domain        : capability domain name (REQUIRED — net-new has no cluster to
                      derive a name from). A "Capability" suffix is enforced.
      title         : requirement title (default: domain minus "Capability").
      description   : free text (default synthesized).
      business_rules: list of {statement[, id]} — rule TEXT the curator authored
                      (ids re-numbered RULE-NNN; only the whitelisted keys kept).
      data_access   : optional list of entity names co-located into the domain.

    Returns (req_id, requirement_dict, domain_name).
    """
    domain = str(spec.get("domain") or "").strip()
    if not domain:
        raise DomainGraphError(
            "net-new requirement missing required 'domain' (a net-new capability "
            "has no cluster to derive a domain name from)."
        )
    if not domain.endswith("Capability"):
        domain = _titlecase(domain) + "Capability"
    title = str(spec.get("title") or domain.replace("Capability", "")).strip() or "Capability"

    raw_rules = spec.get("business_rules") or []
    rule_objects = []
    rule_statements = []
    for i, r in enumerate(raw_rules, start=1):
        if isinstance(r, str):
            statement = r.strip()
        else:
            statement = str((r or {}).get("statement") or "").strip()
        if not statement:
            continue
        rule_statements.append(statement)
        rule_objects.append({
            "id": "RULE-%03d" % (len(rule_objects) + 1),
            "statement": statement,
            "provenance": {"source_app": "net-new"},
        })
    if not rule_objects:
        rule_objects.append({
            "id": "RULE-001",
            "statement": "NET-NEW: target capability pending rule authoring",
            "provenance": {"source_app": "net-new"},
        })

    data_access = sorted({str(a) for a in (spec.get("data_access") or []) if a})
    description = str(spec.get("description") or "").strip() or (
        "Net-new target capability %r with no legacy origin." % title
    )
    # Deterministic id keyed off the domain+title (net-new has no member symbols).
    import hashlib
    h = hashlib.sha256(("net-new|%s|%s" % (domain, title)).encode("utf-8")).hexdigest()
    req_id = "REQ_NEW_" + h[:12].upper()

    requirement = {
        "title": title,
        "description": description,
        "legacy_components": [],                 # net-new: empty (exempt from L)
        "data_access": data_access,
        "dependencies": [],
        "business_rules": rule_objects,
        "validations": [],
        "error_paths": [],
        "status": "active",
        "merged_programs": [],
        "provenance": "net-new",
        "disposition": "new",
        "disposition_reason": "net-new target capability; no legacy source",
    }
    parity = _parity_hints(rule_objects, [])
    if parity:
        requirement["parity_hints"] = parity
    return req_id, requirement, domain


def load_net_new_specs(config, net_new_path=None):
    """Resolve net-new requirement specs: an explicit --net-new JSON file (a list,
    or {"net_new": [...]}), else config['net_new'] (the in-config curator list).
    Returns a (possibly empty) list of spec dicts."""
    specs = []
    if net_new_path and os.path.exists(net_new_path):
        try:
            with open(net_new_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "net_new" in data:
                data = data["net_new"]
            if isinstance(data, list):
                specs.extend(s for s in data if isinstance(s, dict))
        except (OSError, ValueError) as exc:
            raise DomainGraphError(
                "cannot read net-new specs %s: %s" % (net_new_path, exc))
    cfg_specs = config.get("net_new") if isinstance(config, dict) else None
    if isinstance(cfg_specs, list):
        specs.extend(s for s in cfg_specs if isinstance(s, dict))
    return specs


# ---------------------------------------------------------------------------
# Assemble the requirements graph + entities (co-located — T2).
# ---------------------------------------------------------------------------
def assemble_graph(apps, settings, migration_mode, net_new_specs=None):
    """Build the full requirements graph dict + the per-requirement bookkeeping
    (legacy rule edges, app provenance) and the legacy requirement-edge set L."""
    requirements_by_id: dict = {}   # req_id -> {requirement, domain, app, legacy_edges}
    legacy_L = []                   # [{app, symbol_id, rule_id, state}] per behavior member

    max_members = int(settings.get("max_community_size", DEFAULT_MAX_COMMUNITY_MEMBERS))

    for app in apps:
        # Phase 2: choose the capability partition by language — mainframe stays on
        # call-affinity communities; modern code partitions by source package (a
        # dense OO call graph expresses no capabilities). Returns the final
        # {label -> behavior_member_ids} groups (pure-structural groups already
        # dropped).
        capability_groups = _capability_partition(app, max_members)
        for label, behavior_members in capability_groups.items():
            # A group becomes a capability iff it holds >=1 behavior-bearing node.
            if not behavior_members:
                continue

            # Sub-partition oversized groups so each requirement stays tractable.
            # Dense modern code (Kafka, Pulsar, Spring monoliths) can still leave a
            # single package (or a giant mainframe call-community) above the cap;
            # split by file/package prefix so each sub-group becomes its own
            # requirement.  Mainframe call-clusters are typically well below it.
            if len(behavior_members) > max_members:
                sub_groups = _sub_partition_by_package(behavior_members, app["nodes"])
            else:
                sub_groups = {"": behavior_members}

            for pkg_key, sub_members in sub_groups.items():
                if not sub_members:
                    continue
                sub_label = ("%s:%s" % (label, pkg_key)) if pkg_key else label
                req_id, requirement, domain_name, edges = build_requirement(
                    app, sub_label, sub_members, settings
                )
                # If a (rare) hash collision across apps maps two distinct member sets
                # to the same req_id, suffix the app to keep ids unique.
                if req_id in requirements_by_id:
                    req_id = "%s_%s" % (req_id, app["app"].upper().replace("-", "_")[:8])
                requirements_by_id[req_id] = {
                    "requirement": requirement,
                    "domain": domain_name,
                    "app": app["app"],
                    "legacy_edges": edges,
                }
                legacy_L.extend(edges)

    # NET-NEW target requirements (Defect-5): the add-capability half of the merge.
    for spec in net_new_specs or []:
        req_id, requirement, domain_name = build_net_new_requirement(spec)
        if req_id in requirements_by_id:
            req_id = "%s_N" % req_id
        requirements_by_id[req_id] = {
            "requirement": requirement,
            "domain": domain_name,
            "app": "net-new",
            "legacy_edges": [],
        }

    reconcile_dispositions(requirements_by_id)
    wire_dependencies(apps, requirements_by_id)

    # Materialize domains, co-locating entities for each requirement's data_access
    # INTO the requirement's own domain (T2: data_access ⊆ own-domain entities).
    domains: dict = {}
    for req_id, info in requirements_by_id.items():
        dname = info["domain"]
        req = info["requirement"]
        domain = domains.setdefault(dname, {"requirements": {}, "entities": {}})
        domain["requirements"][req_id] = req
        for asset in req["data_access"]:
            if asset not in domain["entities"]:
                domain["entities"][asset] = _entity_object(asset)

    graph = {
        "metadata": {"migration_mode": migration_mode},
        "domains": domains,
    }
    return graph, requirements_by_id, legacy_L


# ---------------------------------------------------------------------------
# (B) DISPOSITION-AWARE ROUND-TRIP COVERAGE — the §I5 invariant.
# ---------------------------------------------------------------------------
def compute_roundtrip(legacy_L, requirements_by_id, drop_manifest):
    """L ⊆ T. Every behavior-bearing legacy requirement edge must be represented by
    an actual business_rule in an active/review requirement OR explicitly dropped
    (with a reason) in the manifest. Absence from BOTH is a SILENT DROP — the exact
    failure the invariant forbids.

    REPRESENTATION IS GRADED AT RULE GRANULARITY, not symbol granularity (the
    adversarial Defect-1/2 fix). A member's symbol merely appearing in
    `legacy_components` is NOT sufficient: the round-trip credits a member edge ONLY
    when an emitted business_rule carries `source_ref == symbol_id` (resolved OR
    review-flagged). So a member whose behavior rule was dropped from the graph —
    even though its symbol still rides in legacy_components — is correctly flagged
    as uncovered rather than silently passed.

    Returns {legacy_rule_total, represented, dropped, uncovered_symbol_ids,
    roundtrip_coverage, uncovered}.
    """
    # T_represented: (app, symbol_id) for every business_rule.source_ref emitted by
    # an active/review requirement — i.e. the member's behavior actually survived
    # into the graph (NOT merely its symbol in legacy_components).
    represented = set()
    for info in requirements_by_id.values():
        req = info["requirement"]
        if req.get("status") not in ("active", "review"):
            continue
        for br in req.get("business_rules") or []:
            ref = br.get("source_ref")
            if ref:
                represented.add((info["app"], ref))

    # T_dropped: symbol_ids in the drop manifest with a non-empty reason.
    dropped = set()
    for d in drop_manifest.get("dropped", []):
        if (d.get("drop_reason") or "").strip():
            dropped.add((d.get("app"), d.get("symbol_id")))

    total = 0
    covered = 0
    uncovered = []
    # Dedup L by (app, symbol_id) — one node carries one behavior rule edge.
    seen = set()
    for edge in legacy_L:
        key = (edge["app"], edge["symbol_id"])
        if key in seen:
            continue
        seen.add(key)
        total += 1
        if key in represented or key in dropped:
            covered += 1
        else:
            uncovered.append({
                "app": edge["app"],
                "symbol_id": edge["symbol_id"],
                "rule_id": edge.get("rule_id"),
                "state": edge.get("state"),
            })

    roundtrip = round(covered / total, 6) if total else 1.0
    uncovered.sort(key=lambda u: (str(u["app"]), str(u["symbol_id"])))
    return {
        "legacy_rule_total": total,
        "represented": len(represented),
        "dropped": len(dropped),
        "uncovered_symbol_ids": [u["symbol_id"] for u in uncovered],
        "uncovered": uncovered,
        "roundtrip_coverage": roundtrip,
    }


def clustering_diagnostic(apps, graph):
    """Surface whether the capability partition is genuinely call-affinity-derived
    or degenerated into singleton-per-program (the Defect-4 risk on a disconnected
    batch estate). NOT a hard gate — a real estate of independent jobs legitimately
    yields singletons; but the reviewer must SEE it, never have it pass silently as
    'capability domains'.

    Reports, per app, the share of behavior members that ended up in a singleton
    community (no call edge to any sibling), and a global `degenerate` flag when
    EVERY behavior community is a singleton (the pure batch case). Repo-agnostic.
    """
    per_app = []
    total_behavior = 0
    total_singletons = 0
    all_singleton = True
    for app in apps:
        communities = app["communities"]
        behavior_ids = app["behavior_ids"]
        n_behavior = 0
        n_singleton = 0
        for _label, members in communities.items():
            bmem = [m for m in members if m in behavior_ids]
            if not bmem:
                continue
            n_behavior += len(bmem)
            if len(bmem) == 1:
                n_singleton += 1
            else:
                all_singleton = False
        total_behavior += n_behavior
        total_singletons += n_singleton
        per_app.append({
            "app": app["app"],
            "behavior_members": n_behavior,
            "singleton_capabilities": n_singleton,
        })
    per_app.sort(key=lambda a: str(a["app"]))
    return {
        "per_app": per_app,
        "behavior_members": total_behavior,
        "singleton_capabilities": total_singletons,
        # degenerate == every behavior community is a singleton (no call affinity
        # to group on — a disconnected/batch topology). The reviewer should confirm
        # the partition reflects real distinct capabilities, not file/program 1:1.
        "degenerate": bool(total_behavior) and all_singleton,
        "note": (
            "weight='calls' merges only nodes that CALL each other. On a "
            "disconnected/batch estate every node can be a singleton -> one "
            "capability per program. 'degenerate=true' means NO call-affinity "
            "grouping occurred; confirm the domains are real distinct "
            "capabilities (not a laundered file/program 1:1 partition)."
        ),
    }


def build_drop_manifest(requirements_by_id, decided_by="domain_graph"):
    """The explicit DROP manifest. v1 emits NO automatic drops — every resolved
    edge is represented as a requirement — but the manifest is always written so
    the round-trip check is a 2-file read and a future curator can record an
    explicit drop ({symbol_id, app, legacy_rule_id, drop_reason, decided_by}) that
    the round-trip check will honor. An empty `dropped` list is the honest v1
    state, NOT a missing file."""
    return {
        "decided_by": decided_by,
        "dropped": [],
        "_note": (
            "Explicit DROP manifest. A legacy SymbolId listed here with a "
            "drop_reason is intentionally reimagined away (not a coverage gap). "
            "Absence from BOTH this manifest and every requirement's "
            "legacy_components is a SILENT DROP and fails the round-trip check."
        ),
    }


def load_or_init_drop_manifest(path):
    """Read an existing curator-authored dispositions.json (so an explicit DROP
    with a reason is honored end-to-end by the round-trip), else return the empty
    v1 manifest. A malformed file is a hard error (a curator drop that cannot be
    read must NOT silently degrade into 'no drops' — that would re-open the silent-
    drop hole from the read side)."""
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise DomainGraphError(
                "cannot read drop manifest %s: %s — a curator-authored drop that "
                "cannot be parsed must not silently become 'no drops'." % (path, exc))
        if isinstance(data, dict) and isinstance(data.get("dropped"), list):
            data.setdefault("decided_by", "domain_graph")
            data.setdefault("_note", build_drop_manifest({})["_note"])
            return data
    return build_drop_manifest({})


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
def validate_schema(graph, schema_path=SCHEMA_PATH):
    """Validate the graph against the enriched schema (Draft7). Returns a list of
    human-readable error strings (empty == valid). jsonschema is a hard dep for
    the gate — its absence is an error, not a skip."""
    try:
        import jsonschema  # noqa
        from jsonschema import Draft7Validator
    except ImportError as exc:
        raise DomainGraphError(
            "jsonschema is required to gate-validate the requirements graph "
            "(%s). Install it (it is a declared dep)." % exc
        )
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    validator = Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(graph), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append("%s: %s" % (loc, err.message))
    return errors


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def assert_front_half_from_overlay(apps, settings):
    """(A') Re-derive front-half coverage FROM THE SAME OVERLAY the builder reads
    (the Defect-2 binding fix). The scalar in coverage-report.json can drift from
    the live overlay (report built from overlayX, builder reads overlayY); nothing
    bound them. Here we re-classify every behavior-bearing node from the overlay
    the builder is about to use, and refuse if any is UNACCOUNTED. This makes the
    front-half gate honest against the actual input, not a possibly-stale file.
    """
    unaccounted = []
    for app in apps:
        annotations = app["annotations"]
        for sid in sorted(app["behavior_ids"]):
            state, _conf = member_state(annotations.get(sid), settings)
            if state not in ("resolved", "risk"):
                unaccounted.append((app["app"], sid))
    if unaccounted:
        raise DomainGraphError(
            "front-half coverage < 1.0 re-derived from the live overlay — %d "
            "behavior-bearing node(s) are neither RESOLVED nor RISK (the "
            "coverage-report.json scalar disagreed with the overlay the builder "
            "reads). Annotate them before §I5:\n  %s"
            % (len(unaccounted), "\n  ".join("%s :: %s" % u for u in unaccounted))
        )


def build(
    config_path=DEFAULT_CONFIG,
    output_path=DEFAULT_OUTPUT,
    coverage_report_path=DEFAULT_COVERAGE_REPORT,
    overlay_path=None,
    explicit_db=None,
    schema_path=SCHEMA_PATH,
    skip_front_half=False,
    net_new_path=None,
    vocab_path=DEFAULT_VOCAB_PATH,
    graphs_dir=None,
):
    """Full §I5 build. Returns (graph, roundtrip, drop_manifest, schema_errors).
    Raises DomainGraphError on any gate failure (front-half<1.0, schema-invalid,
    roundtrip<1.0). Writes the three artifacts on success.

    `graphs_dir` is the per-app DB directory. When None it is anchored on the
    WORKSPACE — the directory of the resolved `config_path`, i.e. beside the
    config we actually loaded — NOT this script's plugin-install dir. This fixes
    the no-`--db` (multi-repo) path that otherwise searches the plugin tree and
    finds nothing (the same ISS-23 fix coverage.py applied to its own path).
    """
    config = load_config(config_path)
    settings = cov.coverage_settings(config)
    # Inject the community-size cap so assemble_graph can sub-partition dense repos.
    settings["max_community_size"] = (
        int(config.get("domain_graph", {}).get("max_community_size",
                                                DEFAULT_MAX_COMMUNITY_MEMBERS))
        if isinstance(config, dict) else DEFAULT_MAX_COMMUNITY_MEMBERS
    )
    migration_mode = config.get("migration_mode", "functional")
    if migration_mode not in ("structural", "functional"):
        migration_mode = "functional"

    # Per-app graph DBs live under the WORKSPACE .anti-legacy/graphs — beside the
    # config we actually loaded — NOT this script's plugin-install dir (the
    # `__file__`-anchored DEFAULT_GRAPHS_DIR). Anchoring on the resolved config's
    # directory fixes the no-`--db` (multi-repo) path. (ISS-23)
    if graphs_dir is None:
        graphs_dir = os.path.join(
            os.path.dirname(os.path.abspath(config_path)), "graphs"
        )

    # (A) front-half precondition (the report-file scalar gate).
    if not skip_front_half:
        assert_front_half_coverage(coverage_report_path)

    # Overlay: honor explicit arg > ANTI_LEGACY_ANNOTATIONS env > default.
    overlay = we._overlay_path(overlay_path)
    overlay_index = cov.load_annotations(overlay)

    # Per-app language (drives the Phase 2 capability-partition strategy) and the
    # optional config override, both resolved from config.
    lang_by_app = {
        a.get("name"): (a.get("language") or "")
        for a in (config.get("source_apps") or []) if isinstance(a, dict)
    }
    partition_override = ((config.get("coverage") or {}).get("capability_partition")
                          or "auto")

    # Per-app gather (cluster() / list_nodes() are single-db).
    apps = []
    for app_name, db_path in resolve_app_dbs(config, explicit_db, graphs_dir=graphs_dir):
        app = gather_app(app_name, db_path, settings, overlay_index,
                         vocab_path=vocab_path, language=lang_by_app.get(app_name))
        app["capability_partition"] = partition_override
        apps.append(app)

    # (A') front-half RE-DERIVED from the same overlay (Defect-2 binding): the
    # report scalar alone can be stale relative to the live overlay; bind them.
    if not skip_front_half:
        assert_front_half_from_overlay(apps, settings)

    # NET-NEW target requirement specs (Defect-5): the add-capability half.
    net_new_specs = load_net_new_specs(config, net_new_path)

    # Assemble + reconcile + wire.
    graph, requirements_by_id, legacy_L = assemble_graph(
        apps, settings, migration_mode, net_new_specs=net_new_specs
    )

    # Drop manifest: an existing curator-authored dispositions.json next to the
    # output is READ and honored end-to-end (a drop with a reason is not a gap);
    # absent that, the empty v1 manifest is written.
    out_dir = os.path.dirname(output_path) or "."
    drop_manifest = load_or_init_drop_manifest(
        os.path.join(out_dir, "dispositions.json"))
    roundtrip = compute_roundtrip(legacy_L, requirements_by_id, drop_manifest)
    # Clustering diagnostic (Defect-4): flag a singleton-per-program partition so a
    # disconnected/batch estate (steps chained by JCL, not CALL) does not silently
    # masquerade as capability domains. Informational on the evidence — the human
    # reviewer at GATE_1 sees it; it does not auto-fail the build.
    roundtrip["clustering"] = clustering_diagnostic(apps, graph)

    # Schema gate.
    schema_errors = validate_schema(graph, schema_path)

    # Write artifacts (only after computing everything; write even on failure so
    # the human can inspect, but raise after).
    os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, sort_keys=True)
        fh.write("\n")
    disp_path = os.path.join(out_dir, "dispositions.json")
    with open(disp_path, "w", encoding="utf-8") as fh:
        json.dump(drop_manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    rt_path = os.path.join(out_dir, "roundtrip-coverage.json")
    with open(rt_path, "w", encoding="utf-8") as fh:
        json.dump(roundtrip, fh, indent=2, sort_keys=True)
        fh.write("\n")

    # Gate assertions (raise AFTER writing the evidence).
    if schema_errors:
        raise DomainGraphError(
            "requirements graph is schema-INVALID against %s (%d error(s)):\n  %s"
            % (schema_path, len(schema_errors), "\n  ".join(schema_errors[:50]))
        )
    if roundtrip["roundtrip_coverage"] < 1.0:
        raise DomainGraphError(
            "round-trip coverage %.4f < 1.0 — %d RESOLVED legacy rule(s) are "
            "neither represented in any requirement's legacy_components nor "
            "explicitly dropped (SILENT DROPS):\n  %s"
            % (
                roundtrip["roundtrip_coverage"],
                len(roundtrip["uncovered"]),
                "\n  ".join(
                    "%s :: %s (%s)" % (u["app"], u["symbol_id"], u["rule_id"])
                    for u in roundtrip["uncovered"]
                ),
            )
        )

    return graph, roundtrip, drop_manifest, schema_errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the §I5 TARGET-state domain graph from the annotated "
                    "wicked-estate code graph + the annotations overlay. Emits a "
                    "gate-validated requirements_graph.json, a drop manifest, and "
                    "the disposition-aware round-trip coverage evidence. Exits "
                    "non-zero on schema-invalid output, roundtrip<1.0, or "
                    "front-half coverage<1.0 (the gate-predicate discipline)."
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to write requirements_graph.json (default "
             ".anti-legacy/requirements/requirements_graph.json).",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to anti-legacy config.json (default .anti-legacy/config.json).",
    )
    parser.add_argument(
        "--coverage-report", default=None,
        help="Path to the front-half coverage-report.json precondition "
             "(default .anti-legacy/coverage-report.json).",
    )
    parser.add_argument(
        "--annotations", default=None,
        help="Path to the annotations.jsonl overlay (default: "
             "ANTI_LEGACY_ANNOTATIONS env or .anti-legacy/annotations.jsonl).",
    )
    parser.add_argument(
        "--db", default=None,
        help="Single wicked-estate DB to build over (default: per-app DBs from "
             "config.source_apps under .anti-legacy/graphs/).",
    )
    parser.add_argument(
        "--schema", default=None,
        help="Path to the enriched requirements schema (default "
             "schemas/requirements-graph.enriched.schema.json).",
    )
    parser.add_argument(
        "--net-new", default=None,
        help="Path to a JSON list (or {\"net_new\": [...]}) of net-new TARGET "
             "requirement specs ({domain, title, business_rules, data_access}) — "
             "the add-capability half of the merge (provenance=net-new, "
             "legacy_components=[]). Also read from config.net_new.",
    )
    parser.add_argument(
        "--skip-front-half-check", action="store_true",
        help="Skip the front-half coverage==1.0 precondition (TEST/DRY-RUN ONLY — "
             "the gate requires it in production).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the human summary on stdout (still writes artifacts).",
    )
    args = parser.parse_args(argv)

    # CWD-relative defaults so a run from a workspace picks up its own .anti-legacy.
    def _cwd_default(rel):
        cwd_path = os.path.join(os.getcwd(), rel)
        return cwd_path if os.path.exists(os.path.dirname(cwd_path)) else None

    config_path = args.config or _cwd_default(".anti-legacy/config.json") or DEFAULT_CONFIG
    output_path = args.output or os.path.join(
        os.getcwd(), ".anti-legacy", "requirements", "requirements_graph.json"
    )
    coverage_report = (
        args.coverage_report
        or _cwd_default(".anti-legacy/coverage-report.json")
        or DEFAULT_COVERAGE_REPORT
    )
    schema_path = args.schema or SCHEMA_PATH

    try:
        graph, roundtrip, drop_manifest, schema_errors = build(
            config_path=config_path,
            output_path=output_path,
            coverage_report_path=coverage_report,
            overlay_path=args.annotations,
            explicit_db=args.db,
            schema_path=schema_path,
            skip_front_half=args.skip_front_half_check,
            net_new_path=args.net_new,
        )
    except DomainGraphError as exc:
        sys.stderr.write("domain_graph: %s\n" % exc)
        return 1

    if not args.quiet:
        n_domains = len(graph["domains"])
        n_reqs = sum(len(d["requirements"]) for d in graph["domains"].values())
        sys.stdout.write(
            "domains=%d  requirements=%d  roundtrip_coverage=%.4f  "
            "legacy_rule_total=%d  represented=%d  dropped=%d\n" % (
                n_domains, n_reqs, roundtrip["roundtrip_coverage"],
                roundtrip["legacy_rule_total"], roundtrip["represented"],
                roundtrip["dropped"],
            )
        )
        sys.stdout.write("wrote %s\n" % output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
