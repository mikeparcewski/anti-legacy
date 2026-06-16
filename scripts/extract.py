#!/usr/bin/env python3
"""
anti-legacy:extraction — the cluster-aware extraction loop (§I3).

This is anti-legacy's MEANING layer over the wicked-estate code graph. The graph
gives STRUCTURE (programs / paragraphs / methods / JCL steps / CICS programs /
DB2 tables, with cross-language edges); this loop writes the BUSINESS RULE on top
of each behavior-bearing node and drives every such node to a provable terminal:

    RESOLVED   (confidence >= coverage.resolve_threshold, with provenance)
    RISK       (below threshold / ambiguous — placed on the HITL research queue)

"No silent maybe-correct": a behavior-bearing node is never silently asserted.
A low-confidence rule MUST flag, never assert. coverage.py then proves the
resolved-or-flagged terminal (coverage == 1.0).

CONTROL FLOW (per node, the falsifiable spine under test):

    list_nodes + rank  ── rank-ordered behavior worklist (denominator = coverage's)
    cluster() ONCE     ── every node gets a community/capability label (§I5 seed)
        │
        ▼  per worklist node (skip if already settled in the overlay — resumable)
    context()          ── bounded fan-out (crawl.context_budget_chars / max_rings)
        │
        ▼  FRAME with the node's cluster (capability context, not line-by-line)
    extract_rule()     ── INJECTED rule extractor returns {statement, confidence, ...}
        │
        ▼  cluster-as-confidence signal applied (sprawl penalty — see below)
    annotate()         ── native requirement field + IP sidecar, ONE call
        confidence >= threshold ─► RESOLVED (validated=true)
        else                     ─► RISK-FLAG (validated=false)   ← never assert

CLUSTER-AS-CONFIDENCE SIGNAL
    The fan-out SHAPE is evidence about how well-bounded the rule is. The loop
    measures `cluster_cohesion` = (# context neighbors in the node's OWN cluster)
    / (# context neighbors with a known cluster). A node whose context stays
    inside its cluster is a clean capability → confidence is trusted as-is. A node
    whose context SPRAWLS across many clusters (a god-program / cross-cutting
    seam) is penalized: the raw extractor confidence is multiplied by a cohesion-
    derived factor, so a sprawling node is pushed BELOW the resolve threshold and
    therefore RISK-flagged for human attention rather than asserted. This is the
    structural prior the spec calls out: in-cluster context ⇒ higher confidence;
    cross-cluster sprawl ⇒ RISK.

The per-node rule extraction (the LLM step in production) is INJECTED via the
`extract_rule` callable so this control flow is deterministically testable and so
no real model is called from the loop itself. The default extractor raises — a
real caller (the skill / an orchestration step) supplies the LLM-backed one.

Repo-agnostic (no source-repo hardcoding — everything is config + DB driven) and
resumable (already-annotated SymbolIds are skipped, so a `--limit`-capped or
interrupted session resumes cleanly and pairs with `subscribe --since`).

Invoked through the dispatcher: `python3 .anti-legacy/run.py extract [args]`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make the sibling helper + coverage importable whether run from the repo root,
# the workspace, or via the run.py dispatcher (same scripts/ dir).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import wicked_estate as we  # noqa: E402  (sibling helper — the engine seam)
import coverage as cov  # noqa: E402  (denominator predicate + overlay reader)

CONFIG_PATH = ".anti-legacy/config.json"
GRAPHS_DIR = ".anti-legacy/graphs"


class ExtractionError(RuntimeError):
    """Raised for loop-level failures (no behavior worklist, bad extractor, ...)."""


# ---------------------------------------------------------------------------
# The injected rule-extractor seam.
#
# In production the skill supplies an LLM-backed callable; the loop never calls a
# model itself. The signature is the contract:
#
#   extract_rule(node: dict, framed_context: dict) -> dict
#
#   node           — {symbol_id, name, kind, file, rank_score, cluster, ...}
#   framed_context — {"context": <context() result>, "cluster": <label>,
#                     "cluster_members": [symbol_id,...], "cohesion": float}
#
# Returns a rule dict carrying at least:
#   {"statement": <language-agnostic rule | "" for can't-state>,
#    "confidence": <0.0-1.0>,
#    "rule_id": <optional stable id>,
#    "resolved_by": <optional>, "risk_reason": <optional>}
# ---------------------------------------------------------------------------
def _default_extract_rule(node, framed_context):  # pragma: no cover - guard only
    raise ExtractionError(
        "no rule extractor injected. extract.run() requires an `extract_rule` "
        "callable (the LLM step). The loop never calls a model itself — the "
        "skill / orchestration step supplies it."
    )


# ---------------------------------------------------------------------------
# Cluster-as-confidence: the fan-out-shape prior.
# ---------------------------------------------------------------------------
def cluster_cohesion(own_or_key, context_result, name_cluster_index, *, structural_kinds=None):
    """Fraction of the node's context neighbors that live in the node's OWN
    cluster. 1.0 == perfectly in-cluster (well-bounded capability); lower ==
    the context sprawls across foreign clusters (god-program / cross-cutting).

    `own_or_key` identifies the node's OWN cluster: it is either the cluster
    LABEL directly, OR a `name|basename(file)` key into `name_cluster_index` from
    which the label is looked up (the loop passes the seed node's name|file key,
    so a symbol-id-keyed map is never required here — this avoids the trap where a
    symbol_id was passed against a name-keyed projection and `own` silently
    resolved to None, making cohesion always 0).

    `context_result` is a context() dict; we read its `ranked_nodes` (the crawled
    neighborhood) and resolve each neighbor's cluster via `name_cluster_index`
    (name|file -> label, with a bare-name fallback). Neighbors are scored as:
      * the SEED (ring 0, the node itself) is excluded — it can't sprawl into
        itself;
      * STRUCTURAL neighbors (File / field / import / … kinds) are noise and are
        EXCLUDED from the denominator — they each form their own singleton cluster
        and would otherwise drag every node's cohesion to ~0 regardless of how
        well-bounded its real behavior neighbors are;
      * a behavior neighbor with no known cluster is ignored (neither for nor
        against cohesion).
    A node with no classifiable behavior neighbors is perfectly cohesive (1.0) —
    nothing to sprawl into, so the prior is neutral and the raw confidence stands.

    Returns (cohesion: float in [0,1], neighbors_in_cluster: int,
    neighbors_classified: int).
    """
    structural = structural_kinds or _DEFAULT_STRUCTURAL_NEIGHBOR_KINDS
    # Resolve the OWN cluster label (accept a key or a literal label).
    own = name_cluster_index.get(own_or_key, own_or_key)

    in_cluster = 0
    classified = 0
    seen_seed = False
    ranked = context_result.get("ranked_nodes", []) if context_result else []
    for rn in ranked:
        if rn.get("ring") == 0 and not seen_seed:
            seen_seed = True  # the seed node itself — never counts
            continue
        kind = (rn.get("kind") or "").strip().strip('"').lower()
        if kind in structural:
            continue  # File / field / import neighbors are cluster noise
        label = _neighbor_cluster(rn, name_cluster_index)
        if label is None:
            continue
        classified += 1
        if own is not None and label == own:
            in_cluster += 1
    if classified == 0:
        return 1.0, 0, 0
    return in_cluster / classified, in_cluster, classified


# Neighbor kinds that are structural noise for the cohesion prior (each forms its
# own singleton cluster; counting them would mask real cross-capability sprawl).
_DEFAULT_STRUCTURAL_NEIGHBOR_KINDS = {
    "file", "field", "import", "constant", "variable", "parameter",
    "type_alias", "enum", "macro",
}


def _neighbor_cluster(ranked_node, node_community):
    """Resolve a context ranked_node's cluster label. `node_community` may be a
    symbol_id->label map OR a (name|file)->label projection; we try both keys."""
    # (name|file) projection form (what build_name_cluster_index produces).
    name = ranked_node.get("name")
    file_ = ranked_node.get("file") or ""
    key = "%s|%s" % (name, os.path.basename(file_))
    if key in node_community:
        return node_community[key]
    # Bare-name fallback (collisions share a label; acceptable for the prior).
    if name in node_community:
        return node_community[name]
    return None


def build_name_cluster_index(cluster_result, nodes):
    """Project cluster()'s symbol_id->label map onto a (name|basename(file))->label
    map so cohesion can be computed from context()'s name-keyed ranked_nodes.

    Name collisions (MAIN-PARA x21) map many symbol_ids to the same (name,file)
    key; when they disagree on cluster the FIRST (sorted) wins deterministically —
    the prior is coarse by design (it only needs to detect SPRAWL, not pinpoint).
    Also returns a bare-name fallback projection."""
    node_community = cluster_result.get("node_community", {})
    by_symbol_meta = {n["symbol_id"]: n for n in nodes}
    proj = {}
    for sid in sorted(node_community):
        meta = by_symbol_meta.get(sid)
        if not meta:
            continue
        label = node_community[sid]
        key = "%s|%s" % (meta.get("name"), os.path.basename(meta.get("file") or ""))
        proj.setdefault(key, label)
        proj.setdefault(meta.get("name"), label)
    return proj


def apply_cluster_signal(raw_confidence, cohesion, *, floor=0.5):
    """Fold the cluster-sprawl prior into the extractor's raw confidence.

    A perfectly-cohesive node (cohesion == 1.0) keeps its raw confidence. A
    sprawling node is multiplied by a factor that scales from `floor` (at
    cohesion 0.0 — maximal cross-cluster sprawl) to 1.0 (at cohesion 1.0). So a
    god-program's confidence is dragged down — typically below the resolve
    threshold — and it is RISK-flagged for a human rather than silently asserted.

    factor = floor + (1 - floor) * cohesion        (linear, monotonic)
    adjusted = raw_confidence * factor
    """
    raw = max(0.0, min(1.0, float(raw_confidence)))
    coh = max(0.0, min(1.0, float(cohesion)))
    factor = floor + (1.0 - floor) * coh
    return round(raw * factor, 6)


# ---------------------------------------------------------------------------
# Worklist: rank-ordered behavior-bearing nodes, minus already-settled ones.
# ---------------------------------------------------------------------------
def settled_symbol_ids(overlay_path):
    """SymbolIds already RESOLVED or RISK in the overlay (idempotency / resume).

    A node whose last overlay record has a recognized terminal status is skipped
    on re-run. Reuses coverage.load_annotations (last-record-wins) so the skip set
    matches exactly what coverage.py considers settled."""
    records = cov.load_annotations(overlay_path)
    settled = set()
    for (_db_id, symbol_id), rec in records.items():
        status = str(rec.get("status", "")).lower()
        if status in ("resolved", "risk"):
            settled.add(symbol_id)
    return settled


def build_worklist(db, config, *, binary=None, overlay_path=None, limit=None):
    """Rank-ordered worklist of UNSETTLED behavior-bearing SymbolIds for `db`.

    Denominator predicate is coverage.is_behavior_bearing (same denominator the
    coverage metric uses — no drift between what the loop crawls and what coverage
    measures). Order is rank() PageRank descending so high-leverage programs /
    entry points resolve first; unranked nodes sort last in stable name order.
    Already-settled nodes (overlay) are dropped (resumable). `limit` caps the
    session.

    Returns a list of node dicts: {symbol_id, name, kind, file, rank_score}.
    """
    settings = cov.coverage_settings(config)
    nodes = we.list_nodes(db)
    behavior = [n for n in nodes if cov.is_behavior_bearing(n, settings)]

    # Rank scores by (name, basename(file)) — rank() is name-keyed text.
    score_by_key = {}
    try:
        for r in we.rank(db, binary=binary):
            k = (r.get("name"), os.path.basename(r.get("file") or ""))
            score_by_key[k] = r.get("score", 0.0)
    except we.WickedEstateError:
        score_by_key = {}

    settled = settled_symbol_ids(overlay_path)

    worklist = []
    for n in behavior:
        if n["symbol_id"] in settled:
            continue
        key = (n.get("name"), os.path.basename(n.get("file") or ""))
        worklist.append({
            "symbol_id": n["symbol_id"],
            "name": n.get("name"),
            "kind": n.get("kind"),
            "file": n.get("file", ""),
            "rank_score": score_by_key.get(key, 0.0),
        })

    # Most-important-first; stable tie-break by (name, file) then symbol_id.
    worklist.sort(key=lambda w: (-w["rank_score"], w["name"] or "", w["file"], w["symbol_id"]))
    if limit is not None:
        worklist = worklist[: int(limit)]
    return worklist


# ---------------------------------------------------------------------------
# The loop.
# ---------------------------------------------------------------------------
def run(
    db,
    *,
    config=None,
    extract_rule=None,
    cluster_weight="calls",
    binary=None,
    overlay_path=None,
    limit=None,
    cohesion_floor=0.5,
    on_node=None,
):
    """Run the cluster-aware extraction loop over one DB.

    Steps:
      1. cluster() the graph ONCE (capability communities — §I5 seed).
      2. Build the rank-ordered, resumable behavior worklist (build_worklist).
      3. Per node:
           a. context() — bounded fan-out (crawl.* config honored by the helper).
           b. cohesion = cluster_cohesion(...) — the fan-out-shape prior.
           c. rules = extract_rule(node, framed_context)  (INJECTED). May return
              ONE rule dict, a LIST of rule dicts, or {"primary", "splits":[...]}
              — a node that DECOMPOSES (a rule + its ERR- twin) returns all of
              them and they are all materialized in THIS pass (atomic multi-emit).
           d. adjusted = apply_cluster_signal(rule.confidence, cohesion) per rule.
           e. RESOLVE iff adjusted >= resolve_threshold, else RISK-FLAG, per rule.
           f. annotate() — native field + IP sidecar, ONE call PER RULE, carrying
              the cluster label on the overlay so §I5 can consume it.

    ATOMIC MULTI-EMIT (the §6 SPLIT cardinality, no downstream cleanup pass): when
    a node decomposes into several requirements the extractor returns them all and
    EACH is written here — one overlay row + one native field per rule. There is no
    "emit the primary, name the sibling, materialize it later" gap: a rule that
    DECLARES a sibling/decomposition rule_id which is NOT emitted in the same pass
    raises (ExtractionError) rather than silently proceeding — the dropped-ERR-twin
    silent failure is made impossible, not merely discouraged.

    `extract_rule` is the injected per-node rule extractor (the LLM step). It must
    be supplied; the default raises (the loop never calls a model itself).
    `overlay_path` redirects the IP sidecar (tests MUST redirect to stay hermetic).

    Returns a summary dict:
      {db, weight, num_communities, processed (NODES), rule_emits (RULE rows —
       >= processed when nodes split), resolved, risk_flagged,
       results: [{symbol_id, name, status, confidence, adjusted, cohesion,
                  cluster, ring_depth, rules_emitted, emitted_rules:[...]}]}.
    `resolved`/`risk_flagged` count RULE emits (a split node contributes >1).
    """
    if config is None:
        config = cov.load_config(_config_path())
    if extract_rule is None:
        extract_rule = _default_extract_rule

    settings = cov.coverage_settings(config)
    threshold = settings["resolve_threshold"]
    crawl_cfg = (config.get("crawl") if isinstance(config, dict) else {}) or {}
    budget = int(crawl_cfg.get("context_budget_chars", 18000))
    max_hops = int(crawl_cfg.get("max_rings", 3))

    # 1. Cluster once. node_community: symbol_id -> capability label.
    #    The cohesion prior (§I3 god-program / cross-cutting detector) is tuned to a
    #    specific weight mode's semantics. Native `clusters` ignores `weight` (real
    #    community detection over Calls|Imports, structural/file nodes excluded), so
    #    for the default "calls" capability mode native-first is congruent and gives a
    #    strictly better partition; but for "data-affinity"/"confidence" the file-
    #    coupling / confidence-weighting signal native drops IS the signal this loop's
    #    sprawl verdict depends on — demand the SHIM there so weight stays load-bearing.
    cluster_prefer = "native" if cluster_weight == "calls" else "shim"
    cluster_result = we.cluster(
        db, weight=cluster_weight, prefer=cluster_prefer, binary=binary
    )
    node_community = cluster_result.get("node_community", {})
    communities = cluster_result.get("communities", {})

    # Project clusters onto a (name|file) index so cohesion can read context()'s
    # name-keyed ranked_nodes. Uses the full node list (not just the worklist) so
    # neighbors outside the worklist still resolve to a cluster.
    all_nodes = we.list_nodes(db)
    name_cluster_index = build_name_cluster_index(cluster_result, all_nodes)

    # 2. Worklist (rank-ordered, resumable).
    worklist = build_worklist(
        db, config, binary=binary, overlay_path=overlay_path, limit=limit
    )

    resolved = 0
    risk_flagged = 0
    rule_emits = 0
    results = []

    # Per-FILE source prefetch (one `source_bundle` call per file vs one `source`
    # call per node): when the engine ships the bulk bundle, the loop fetches each
    # file's full bodies ONCE and serves every node's own-body from the cache —
    # so framed_context carries the node's COMPLETE body (`own_source`), not just
    # what fit in the ring budget. Feature-detected: on an engine without the
    # bundle, source_bundle returns None and own_source stays None (no behavior
    # change, the ring `slices` remain the body source). Keyed by file.
    _body_cache: dict = {}

    def _own_source(n):
        f = n.get("file")
        sid = n.get("symbol_id")
        if not f or not sid:
            return None
        if f not in _body_cache:
            cache = {}
            try:
                bundle = we.source_bundle(db, file=f, binary=binary)
            except we.WickedEstateError:
                bundle = None
            if bundle:
                for bn in bundle.get("nodes", []):
                    if bn.get("symbol_id") and bn.get("source"):
                        cache[bn["symbol_id"]] = bn["source"]
            _body_cache[f] = cache
        return _body_cache[f].get(sid)

    for node in worklist:
        node = dict(node)
        label = node_community.get(node["symbol_id"])
        node["cluster"] = label

        # 3a. Bounded fan-out around the node.
        try:
            ctx = we.context(
                db,
                node["name"],
                budget=budget,
                max_hops=max_hops,
                file=node.get("file") or None,
                kind=node.get("kind") or None,
                binary=binary,
            )
        except we.WickedEstateError as exc:
            # Could not even crawl the node — that is genuine ambiguity, flag it.
            ctx = {"ranked_nodes": [], "slices": [], "chars_used": 0,
                   "max_hops": max_hops, "_error": str(exc)}

        # 3b. Cluster-as-confidence: measure fan-out cohesion. Identify the node's
        #     OWN cluster by its name|file key (the projection's keying) so `own`
        #     resolves correctly even though clusters are interned by symbol_id.
        seed_key = "%s|%s" % (node.get("name"), os.path.basename(node.get("file") or ""))
        cohesion, in_cluster, classified = cluster_cohesion(
            seed_key, ctx, name_cluster_index
        )

        # FRAME the context with the node's capability community so the extractor
        # produces a BUSINESS-LEVEL rule (not a line-by-line restatement).
        framed_context = {
            "context": ctx,
            "cluster": label,
            "cluster_members": communities.get(label, []),
            "cohesion": cohesion,
            "neighbors_in_cluster": in_cluster,
            "neighbors_classified": classified,
            # The node's COMPLETE own-body from the per-file bundle prefetch (None
            # when the engine has no bulk bundle — the ring `slices` still carry it).
            "own_source": _own_source(node),
        }

        # 3c. INJECTED rule extraction (the LLM step in production). A node may
        #     DECOMPOSE: the extractor may return ONE rule dict (back-compat), a
        #     LIST of rule dicts, or a {"primary": {...}, "splits": [...]} shape.
        #     EVERY returned rule is emitted ATOMICALLY in this one pass — one
        #     overlay row + one native requirement field per rule — so a SPLIT
        #     node (a RULE + its ERR- twin) never leaves a sibling for a fragile
        #     downstream cleanup pass to materialize. That dropped-twin path is the
        #     CARDINAL silent failure this loop is built to make impossible.
        raw = extract_rule(node, framed_context)
        rules = _normalize_rules(raw)

        # GUARD (no silent maybe-correct, extended to decomposition): if any rule
        # DECLARES a sibling/decomposition rule_id that is NOT itself emitted in
        # this same pass, that is the silent-drop bug — refuse to proceed. The
        # whole point of atomic multi-emit is that a declared split is materialized
        # HERE, not flagged for a later register that may never run.
        _assert_declared_siblings_emitted(node, rules)

        ring_depth = _ring_depth(ctx)
        node_signal = {
            "cohesion": cohesion,
            "in_cluster": in_cluster,
            "classified": classified,
            "ring_depth": ring_depth,
            "cluster": label,
            "ctx": ctx,
        }

        node_resolved = 0
        node_risk = 0
        emitted = []
        # Emit each rule object atomically: native field + IP overlay, one call
        # per rule. The overlay is {db_id, symbol_id}-keyed and last-record-wins,
        # so the PRIMARY rule is emitted LAST — the node's settled coverage state
        # reflects the primary outcome while every split sibling is still persisted
        # as its own row (distinct rule_id) for §I5 / by_requirement to consume.
        for rule in _emit_order(rules):
            emit = _emit_rule(
                db, node, rule, node_signal,
                threshold=threshold, cohesion_floor=cohesion_floor,
                overlay_path=overlay_path, binary=binary,
            )
            emitted.append(emit)
            if emit["status"] == "resolved":
                node_resolved += 1
                resolved += 1
            else:
                node_risk += 1
                risk_flagged += 1

        # The node-level record reflects the PRIMARY rule (first in the returned
        # order) — that is the node's headline outcome — but carries the full list
        # of emitted rules so a caller can see every requirement a split produced.
        primary = emitted[0]
        record = {
            "symbol_id": node["symbol_id"],
            "name": node["name"],
            "kind": node.get("kind"),
            "file": node.get("file", ""),
            "status": primary["status"],
            "confidence": primary["confidence"],
            "raw_confidence": primary["raw_confidence"],
            "adjusted": primary["adjusted"],
            "cohesion": round(cohesion, 6),
            "cluster": label,
            "ring_depth": ring_depth,
            "rules_emitted": len(emitted),
            "emitted_rules": [
                {"rule_id": e["rule_id"], "status": e["status"],
                 "confidence": e["confidence"]}
                for e in emitted
            ],
        }
        results.append(record)
        rule_emits += len(emitted)
        if on_node is not None:
            on_node(record)

    return {
        "db": db,
        "weight": cluster_weight,
        "num_communities": cluster_result.get("num_communities", 0),
        "processed": len(results),
        "rule_emits": rule_emits,
        "resolved": resolved,
        "risk_flagged": risk_flagged,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def _config_path():
    cwd_cfg = os.path.join(os.getcwd(), ".anti-legacy", "config.json")
    return cwd_cfg if os.path.exists(cwd_cfg) else CONFIG_PATH


def _ring_depth(ctx):
    """Deepest ring actually reached in the context crawl (0 if seed-only)."""
    depth = 0
    for rn in ctx.get("ranked_nodes", []) if ctx else []:
        r = rn.get("ring")
        if isinstance(r, int) and r > depth:
            depth = r
    return depth


def _default_rule_id(node):
    """Stable, deterministic rule id from the node identity (no randomness)."""
    base = (node.get("name") or "NODE").upper().replace(" ", "-")
    return "RULE-%s" % base


def _description(node):
    kind = (node.get("kind") or "").strip().strip('"')
    return "%s %s (%s)" % (kind or "node", node.get("name"), node.get("file", ""))


def _provenance(ctx, in_cluster, classified):
    """Compact provenance string: rings crawled + cohesion fraction."""
    n_neighbors = len(ctx.get("ranked_nodes", [])) if ctx else 0
    max_ring = _ring_depth(ctx)
    return "rings=%d neighbors=%d in-cluster=%d/%d" % (
        max_ring, n_neighbors, in_cluster, classified,
    )


# ---------------------------------------------------------------------------
# Atomic multi-emit: a node may decompose into several requirements (a RULE +
# its ERR- twin). Normalize whatever shape the extractor returns into an ordered
# list, emit EACH atomically, and refuse to silently drop a declared sibling.
# ---------------------------------------------------------------------------
def _normalize_rules(raw):
    """Flatten the extractor's return into an ordered, non-empty list of rule
    dicts (PRIMARY first). Accepts three shapes so single-rule callers stay
    back-compatible while a SPLIT node can return all of its requirements:

      * a single dict                     -> [dict]            (the common case)
      * a list/tuple of dicts             -> [...]             (PRIMARY = [0])
      * {"primary": {...}, "splits":[...]} -> [primary, *splits]
      * None / empty / falsy              -> [{}]              (1 empty rule that
        will RISK-flag — a node always settles to a terminal, never vanishes)

    A {"primary": ...} mapping WITHOUT the marker keys is treated as a plain rule
    dict (it is just a rule that happens to have those fields), so an ordinary
    single-rule return is never mis-split."""
    if raw is None:
        return [{}]
    if isinstance(raw, dict):
        # The {"primary": ..., "splits": [...]} envelope (splits optional).
        if "primary" in raw and ("splits" in raw or "siblings" in raw):
            primary = raw.get("primary") or {}
            splits = raw.get("splits") or raw.get("siblings") or []
            rules = [primary] + [s for s in splits if s is not None]
            return [r for r in rules if isinstance(r, dict)] or [{}]
        return [raw]
    if isinstance(raw, (list, tuple)):
        rules = [r for r in raw if isinstance(r, dict)]
        return rules or [{}]
    # Unknown shape — treat as a single empty rule (will RISK-flag, never silent).
    return [{}]


def _declared_sibling_ids(rule):
    """Rule_ids a rule DECLARES as its decomposition siblings (the twins it says
    must exist). The CARDINAL silent-failure is declaring an `ERR-` twin and never
    materializing it; we collect every form a declaration can take so the guard
    can prove each one was actually emitted. Recognized fields:

      * `decomposition`        — str id, or list of ids, or list of {rule_id:..}
      * `sibling_rule_ids`     — list of ids
      * `splits` / `siblings`  — list of ids or {rule_id:..} dicts

    Self-references (a rule naming its own rule_id) are dropped — only OTHER
    requirements count as siblings that must be independently emitted."""
    own = rule.get("rule_id")
    out = []

    def _add(v):
        if v is None:
            return
        if isinstance(v, str):
            s = v.strip()
            if s:
                out.append(s)
        elif isinstance(v, dict):
            rid = v.get("rule_id") or v.get("id")
            if isinstance(rid, str) and rid.strip():
                out.append(rid.strip())
        elif isinstance(v, (list, tuple)):
            for item in v:
                _add(item)

    for field in ("decomposition", "sibling_rule_ids", "splits", "siblings"):
        if field in rule:
            _add(rule[field])
    # Drop self-references and de-dup while preserving order.
    seen = set()
    siblings = []
    for rid in out:
        if rid == own or rid in seen:
            continue
        seen.add(rid)
        siblings.append(rid)
    return siblings


def _assert_declared_siblings_emitted(node, rules):
    """Guard against the silent-drop: if any rule DECLARES a sibling rule_id that
    is not itself present in this node's emit batch, raise. Atomic multi-emit means
    a declared split is materialized in THIS pass — never deferred to a downstream
    register that may never run (the 1000-DALYTRAN-GET-NEXT / 2700-UPDATE-TCATBAL
    coverage gap the real run exposed)."""
    present = set()
    for rule in rules:
        rid = rule.get("rule_id") or _default_rule_id(node)
        present.add(rid)
    missing = {}
    for rule in rules:
        declarer = rule.get("rule_id") or _default_rule_id(node)
        for sib in _declared_sibling_ids(rule):
            if sib not in present:
                missing.setdefault(sib, declarer)
    if missing:
        detail = ", ".join(
            "%s (declared by %s)" % (sib, by) for sib, by in sorted(missing.items())
        )
        raise ExtractionError(
            "node %s declares decomposition sibling(s) that were NOT emitted in "
            "the same pass: %s. Atomic multi-emit forbids the silent-drop — the "
            "extractor must RETURN every declared split rule (so it is written as "
            "its own overlay row + native field here), not name it for a later "
            "register that may never run." % (node.get("name") or node.get("symbol_id"), detail)
        )


def _emit_order(rules):
    """Order rules for writing so the PRIMARY (rules[0]) is annotated LAST.

    The overlay is {db_id, symbol_id}-keyed and last-record-wins (coverage reads
    the last record), so writing the primary last makes the node's settled COVERAGE
    state reflect the primary outcome — while every split sibling is still persisted
    as its own row (distinct rule_id) for §I5 / by_requirement. For a single-rule
    node this is a no-op."""
    if len(rules) <= 1:
        return list(rules)
    return list(rules[1:]) + [rules[0]]


def _emit_rule(db, node, rule, node_signal, *, threshold, cohesion_floor,
               overlay_path, binary):
    """Resolve-or-RISK a SINGLE rule and write it atomically (native field + IP
    overlay row). Returns the emit summary {rule_id, status, confidence, ...}.

    The cluster-sprawl prior is a NODE-level structural signal (the fan-out shape),
    so the same cohesion/in-cluster numbers apply to every rule the node emits;
    only the extractor's raw confidence + statement vary per rule."""
    rule = rule or {}
    cohesion = node_signal["cohesion"]
    in_cluster = node_signal["in_cluster"]
    classified = node_signal["classified"]
    ring_depth = node_signal["ring_depth"]
    label = node_signal["cluster"]
    ctx = node_signal["ctx"]

    raw_conf = float(rule.get("confidence", 0.0) or 0.0)
    statement = (rule.get("statement") or "").strip()
    adjusted = apply_cluster_signal(raw_conf, cohesion, floor=cohesion_floor)

    if statement and adjusted >= threshold:
        status = "resolved"
        validated = True
        confidence_out = adjusted
    else:
        status = "risk"
        validated = False
        confidence_out = adjusted
        if not statement:
            rule.setdefault("risk_reason", "extractor could not state a rule")
        elif raw_conf < threshold:
            rule.setdefault("risk_reason", "confidence below resolve threshold")
        else:
            rule.setdefault(
                "risk_reason",
                "cluster sprawl: context crosses %d/%d foreign-cluster neighbors"
                % (classified - in_cluster, classified),
            )
        statement = statement or "RISK"

    rule_id = rule.get("rule_id") or _default_rule_id(node)
    provenance = rule.get("provenance") or _provenance(ctx, in_cluster, classified)

    requirement = "%s|%s|%s|%s" % (
        rule_id, confidence_out, provenance,
        statement if status == "resolved" else "RISK",
    )
    rule_object = {
        "rule_id": rule_id,
        "statement": statement,
        "confidence": confidence_out,
        "raw_confidence": round(raw_conf, 6),
        "provenance": provenance,
        "status": status,
        "ring_depth": ring_depth,
        "cluster": label,
        "cluster_cohesion": round(cohesion, 6),
        "neighbors_in_cluster": in_cluster,
        "neighbors_classified": classified,
    }
    # Carry the decomposition declaration through to the overlay so the trace
    # records which siblings this rule belongs with (auditable after the fact).
    siblings = _declared_sibling_ids(rule)
    if siblings:
        rule_object["decomposition_siblings"] = siblings
    if status == "resolved":
        rule_object["resolved_by"] = rule.get("resolved_by", "extraction-loop")
    else:
        rule_object["risk_reason"] = rule.get("risk_reason", "below threshold")

    we.annotate(
        db,
        node["symbol_id"],
        requirement=requirement,
        description=rule.get("description") or _description(node),
        validated=validated,
        rule_object=rule_object,
        overlay_path=overlay_path,
        binary=binary,
    )

    return {
        "rule_id": rule_id,
        "status": status,
        "confidence": confidence_out,
        "raw_confidence": round(raw_conf, 6),
        "adjusted": adjusted,
    }


def resolve_dbs(config, explicit_db=None):
    """The DB list to crawl: an explicit --db, else every per-app DB from config."""
    if explicit_db:
        return [explicit_db]
    dbs = []
    for app in (config.get("source_apps", []) if isinstance(config, dict) else []):
        name = app.get("name")
        if name:
            dbs.append(os.path.join(GRAPHS_DIR, "%s.db" % name))
    return dbs or [we.DEFAULT_DB]


# ---------------------------------------------------------------------------
# CLI — `run.py extract ...`. Production runs supply the LLM extractor in-process
# (the skill imports run()); the CLI is a thin driver + a --dry-run that uses a
# deterministic stub extractor so the loop's plumbing can be smoke-tested.
# ---------------------------------------------------------------------------
def _stub_extractor(node, framed_context):
    """A deterministic, NON-LLM stub for --dry-run smoke tests / wiring checks.

    It states a trivial rule at a fixed confidence derived from the node's rank so
    the loop's RESOLVE/RISK split and cluster signal are exercisable without a
    model. NEVER used for real extraction (the real extractor is injected)."""
    score = float(node.get("rank_score", 0.0) or 0.0)
    conf = 0.80 if score > 0 else 0.60
    return {
        "statement": "%s performs its named behavior within its capability." % node.get("name"),
        "confidence": conf,
        "resolved_by": "dry-run-stub",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cluster-aware extraction loop (§I3): crawl each behavior-"
                    "bearing node, frame it with its capability cluster, and "
                    "RESOLVE or RISK-flag its business rule. Production callers "
                    "inject the LLM extractor by importing run(); the CLI offers "
                    "a --dry-run stub for wiring smoke tests only.",
    )
    parser.add_argument("--db", default=None,
                        help="Single DB to crawl (default: per-app DBs from config).")
    parser.add_argument("--config", default=None, help="Path to config.json.")
    parser.add_argument("--weight", default="calls",
                        choices=["calls", "confidence", "data-affinity"],
                        help="cluster() weight mode (default calls).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap behavior nodes crawled this session (resumable).")
    parser.add_argument("--cohesion-floor", type=float, default=0.5,
                        help="Min sprawl-penalty factor (cohesion 0.0 -> this).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use the deterministic stub extractor (NO LLM) — "
                             "wiring smoke test only, not real extraction.")
    args = parser.parse_args(argv)

    config_path = args.config or _config_path()
    try:
        config = cov.load_config(config_path)
    except (OSError, ValueError) as exc:
        sys.stderr.write("extract: cannot read config %s: %s\n" % (config_path, exc))
        return 2

    if not args.dry_run:
        sys.stderr.write(
            "extract: refusing to run without an extractor. The CLI cannot call "
            "an LLM; either pass --dry-run (stub, smoke test only) or drive "
            "extract.run(db, extract_rule=<llm callable>) from the skill.\n"
        )
        return 2

    extractor = _stub_extractor
    summaries = []
    rc = 0
    for db in resolve_dbs(config, args.db):
        if not os.path.exists(db):
            sys.stderr.write("extract: db not found (run survey first): %s\n" % db)
            rc = 2
            continue
        try:
            summary = run(
                db,
                config=config,
                extract_rule=extractor,
                cluster_weight=args.weight,
                limit=args.limit,
                cohesion_floor=args.cohesion_floor,
            )
        except (we.WickedEstateError, ExtractionError) as exc:
            sys.stderr.write("extract: %s\n" % exc)
            rc = 1
            continue
        summaries.append(summary)

    sys.stdout.write(json.dumps({"runs": summaries}, indent=2, sort_keys=True) + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
