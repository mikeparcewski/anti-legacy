#!/usr/bin/env python3
"""
anti-legacy:coverage — resolved-or-flagged coverage over the wicked-estate code graph.

WF1 §I2. The provable terminal of the extraction model: every behavior-bearing node
of the wicked-estate code graph ends either RESOLVED (a rule annotation at/above the
confidence threshold) or RISK-flagged (on the HITL research queue). Coverage is the
fraction of behavior-bearing nodes that reached one of those two terminal states:

    coverage = (resolved + risk_flagged) / behavior_bearing_total

DoD / provable terminal: coverage == 1.0 (UNACCOUNTED == 0). This script doubles as a
gate predicate — it exits non-zero (and prints the unaccounted SymbolIds) when
coverage < 1.0, so it can be wired as a CI check later (§I6).

DENOMINATOR (behavior-bearing nodes), config-driven via `coverage.behavior_kinds`:
  INCLUDED kinds  : module, function, method, class, struct, interface, plus estate
                    behavior nodes (cics_program, jcl step, db2_table, ...).
  EXCLUDED        : structural / leaf kinds (file, import, field, constant, variable,
                    parameter, type_alias, enum, macro) AND pure copybook/data-only
                    `module`s with 0 outgoing calls/uses edges (they carry no standalone
                    business rule; annotating them would inflate the denominator with
                    un-rule-bearing leaves and make coverage un-provable).

PER-NODE STATE is read from the anti-legacy `.anti-legacy/annotations.jsonl` overlay
(the IP-rich, lossless record), cross-checked against the in-graph wicked-estate
`requirement` field via the helper so the two can't silently diverge:
  RESOLVED    : status="resolved" AND confidence >= resolve_threshold (default 0.75)
                (in-graph requirement_validated must agree when cross-checked).
  RISK        : status="risk" (a risk_reason + provenance — on the HITL queue).
  UNACCOUNTED : behavior-bearing node with neither (incl. a below-threshold
                "resolved" row) -> the coverage hole.

OUTPUT: `.anti-legacy/coverage-report.json` and `.anti-legacy/coverage-report.md`.
Deterministic: nodes sorted by SymbolId, floats rounded to 4 dp.

NODE SOURCE: in production the denominator node-list is pulled from the wicked-estate
graph via the sibling `wicked_estate` helper (or, absent the helper, a NARROW READ-ONLY
intern-table lookup against the ADR-002-locked `symbols`+`nodes` columns — the ONE
documented raw-SQLite exception, id-resolution only, never graph consumption). A
`--nodes <file.json>` seam injects a node-list directly for hermetic test / dry runs.
"""
import argparse
import json
import os
import sqlite3
import sys

# --- repo-root / path conventions (match the rest of scripts/) ---------------
REPO_ROOT = os.getcwd()  # workspace == cwd, not the package __file__ (ISS-23)
CONFIG_PATH = os.path.join(REPO_ROOT, ".anti-legacy", "config.json")
ANNOTATIONS_PATH = os.path.join(REPO_ROOT, ".anti-legacy", "annotations.jsonl")
GRAPHS_DIR = os.path.join(REPO_ROOT, ".anti-legacy", "graphs")
REPORT_JSON = os.path.join(REPO_ROOT, ".anti-legacy", "coverage-report.json")
REPORT_MD = os.path.join(REPO_ROOT, ".anti-legacy", "coverage-report.md")
DEFAULT_DB = os.path.join(REPO_ROOT, ".anti-legacy", "legacy-graph.db")

# --- coverage contract defaults (overridable via config.coverage) ------------
DEFAULT_BEHAVIOR_KINDS = [
    "module",
    "function",
    "method",
    "class",
    "struct",
    "interface",
]
# Estate behavior origins (serialized as {"other":"<x>"} in the wicked-estate schema)
# that CARRY behavior and belong in the denominator alongside the language kinds above.
DEFAULT_ESTATE_BEHAVIOR_KINDS = [
    "cics_program",
    "step",        # jcl step
    "db2_table",
]
# Structural / leaf kinds that carry no standalone business rule -> never counted.
DEFAULT_STRUCTURAL_KINDS = [
    "file",
    "import",
    "field",
    "constant",
    "variable",
    "parameter",
    "type_alias",
    "enum",
    "macro",
    # estate structural leaves
    "dataset",
    "cics_map",
    "ims_database",
    "ims_segment",
    "parent",
]
DEFAULT_RESOLVE_THRESHOLD = 0.75

# Edge kinds that mean a `module` actually drives behavior (not a pure data copybook).
# Stored serialized in the DB (e.g. "calls", {"other":"uses"}); normalized before match.
BEHAVIOR_EDGE_KINDS = {"calls", "uses", "references", "accesses", "invokes"}

ROUND_DP = 4


# --- kind normalization ------------------------------------------------------
def normalize_kind(raw):
    """Normalize a wicked-estate kind to a bare lowercase token.

    The DB stores NodeKind JSON-serialized: simple kinds keep their surrounding
    quotes literally (`"module"`), estate kinds are objects (`{"other":"step"}`).
    The CLI prints capitalized display names (`Module`, `Cics_program`). The
    helper / test node-list passes bare kinds (`module`). Accept every form.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                for key in ("other", "kind", "type"):
                    if key in obj and isinstance(obj[key], str):
                        return obj[key].strip().lower()
        except (ValueError, TypeError):
            pass
        return s.lower()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        try:
            inner = json.loads(s)
            if isinstance(inner, str):
                return inner.strip().lower()
        except (ValueError, TypeError):
            pass
        return s[1:-1].strip().lower()
    return s.lower()


def _normalize_kind_set(kinds):
    return {normalize_kind(k) for k in kinds}


# --- config ------------------------------------------------------------------
def load_config(config_path=CONFIG_PATH):
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as fh:
        return json.load(fh)


def coverage_settings(config):
    """Resolve the (config-driven, defaulted) coverage knobs into normalized sets."""
    cov = config.get("coverage", {}) if isinstance(config, dict) else {}
    behavior = cov.get("behavior_kinds", DEFAULT_BEHAVIOR_KINDS)
    estate = cov.get("estate_behavior_kinds", DEFAULT_ESTATE_BEHAVIOR_KINDS)
    structural = cov.get("structural_kinds", DEFAULT_STRUCTURAL_KINDS)
    threshold = cov.get("resolve_threshold", DEFAULT_RESOLVE_THRESHOLD)
    return {
        "behavior_kinds": _normalize_kind_set(behavior),
        "estate_behavior_kinds": _normalize_kind_set(estate),
        "structural_kinds": _normalize_kind_set(structural),
        "resolve_threshold": float(threshold),
    }


def resolve_app_dbs(config, explicit_db=None, graphs_dir=None):
    """Return a list of (app_name, db_path) to compute coverage over.

    Per the survey rewire each source repo is indexed to its own per-app DB at
    .anti-legacy/graphs/<app>.db. When --db is given that single DB is used.

    `graphs_dir` is the directory the per-app DBs live in. It MUST be anchored on
    the WORKSPACE (beside the loaded config), not on this script's plugin-install
    location — `main()` passes the workspace dir. It defaults to the module-level
    GRAPHS_DIR only for direct/legacy callers.
    """
    if explicit_db:
        name = os.path.splitext(os.path.basename(explicit_db))[0]
        return [(name, explicit_db)]
    if graphs_dir is None:
        graphs_dir = GRAPHS_DIR
    apps = []
    for app in config.get("source_apps", []) if isinstance(config, dict) else []:
        name = app.get("name")
        if not name:
            continue
        apps.append((name, os.path.join(graphs_dir, "%s.db" % name)))
    if not apps:
        apps.append((os.path.splitext(os.path.basename(DEFAULT_DB))[0], DEFAULT_DB))
    return apps


# --- behavior-bearing predicate (the denominator membership) -----------------
def _has_behavior_out_edge(node):
    """Does this node have an outgoing calls/uses/references edge?

    Accepts the helper / DB-derived flag `_has_behavior_out_edge`, or the
    node-list `out_edges` count (the signal that distinguishes a behaving
    `module` from a copybook/data-only leaf module).
    """
    if "_has_behavior_out_edge" in node:
        return bool(node["_has_behavior_out_edge"])
    if "out_edges" in node:
        try:
            return int(node["out_edges"]) > 0
        except (TypeError, ValueError):
            return False
    # Unknown -> conservatively treat as behaving (don't drop a real module).
    return True


def is_behavior_bearing(node, settings):
    """Per-node behavior-bearing predicate (the denominator membership test).

    `settings` is a coverage_settings() dict carrying the normalized
    behavior_kinds / estate_behavior_kinds / structural_kinds sets. A node is
    behavior-bearing iff its (normalized) kind is in the behavior or estate set,
    EXCEPT a `module` with no outgoing calls/uses edge (a pure copybook/data-only
    module, which carries no standalone rule). Structural/leaf kinds are out.
    """
    kind = normalize_kind(node.get("kind"))
    if not kind:
        return False
    if kind in settings["structural_kinds"]:
        return False
    if kind in settings["estate_behavior_kinds"]:
        return True
    if kind in settings["behavior_kinds"]:
        if kind == "module" and not _has_behavior_out_edge(node):
            return False
        return True
    return False


def behavior_bearing_nodes(nodes, settings):
    """Filter a node-list down to the behavior-bearing subset (the denominator)."""
    return [n for n in nodes if is_behavior_bearing(n, settings)]


# --- helper bridge (sibling wicked_estate.py, used when available) -----------
def _load_helper():
    """Import the sibling wicked_estate helper if present, else None.

    Used to (a) enumerate nodes when it exposes a node-list, and (b) cross-check
    the in-graph `requirement` field against the JSONL overlay. coverage.py runs
    standalone without it (read-only intern-table fallback + overlay alone).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from antilegacy_core import wicked_estate  # type: ignore
        return wicked_estate
    except Exception:
        return None


# --- denominator: node enumeration from the graph ----------------------------
def enumerate_nodes(db_path, helper=None):
    """Return the graph's nodes as dicts {symbol_id, name, kind, file, out_edges}.

    Prefers a helper-provided node-list (`list_nodes`); else a narrow READ-ONLY
    intern-table lookup against the stable wicked-estate schema.
    """
    if helper is not None:
        lister = getattr(helper, "list_nodes", None)
        if callable(lister):
            try:
                rows = lister(db_path)
                out = []
                for r in rows:
                    out.append({
                        "symbol_id": r.get("symbol") or r.get("symbol_id"),
                        "name": r.get("name"),
                        "kind": r.get("kind"),
                        "file": r.get("file", ""),
                        "out_edges": r.get("out_edges"),
                        "_has_behavior_out_edge": r.get("_has_behavior_out_edge"),
                    })
                return out
            except Exception:
                pass
    return _enumerate_nodes_readonly(db_path)


def _enumerate_nodes_readonly(db_path):
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            "wicked-estate DB not found: %s — run `survey` (wicked-estate index) first."
            % db_path
        )
    uri = "file:%s?mode=ro" % db_path
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT s.sym, n.name, n.kind, n.file "
            "FROM nodes n JOIN symbols s ON n.symbol = s.sid"
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT DISTINCT s.sym, e.kind "
            "FROM edges e JOIN symbols s ON e.source = s.sid"
        )
        behavior_out = set()
        for sym, ekind in cur.fetchall():
            if normalize_kind(ekind) in BEHAVIOR_EDGE_KINDS:
                behavior_out.add(sym)
        nodes = []
        for (sym, name, kind, file_) in rows:
            nodes.append({
                "symbol_id": sym,
                "name": name,
                "kind": kind,
                "file": file_ or "",
                "_has_behavior_out_edge": sym in behavior_out,
            })
        return nodes
    finally:
        conn.close()


def load_nodes_file(path):
    """Load an injected node-list fixture (the --nodes / hermetic seam)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "nodes" in data:
        data = data["nodes"]
    if not isinstance(data, list):
        raise ValueError("--nodes file must be a JSON list of node dicts")
    return data


# --- per-node state: the annotation overlay ----------------------------------
def load_annotations(annotations_path=None):
    """Read annotations.jsonl into {(db_id, symbol_id): record}.

    Append-only overlay; the LAST record for a key wins (idempotent re-crawl).
    Malformed lines are skipped. `annotations_path=None` uses ANNOTATIONS_PATH.
    """
    if annotations_path is None:
        annotations_path = ANNOTATIONS_PATH
    by_key = {}
    if not os.path.exists(annotations_path):
        return by_key
    with open(annotations_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            symbol_id = rec.get("symbol_id")
            if symbol_id is None:
                continue
            by_key[(rec.get("db_id"), symbol_id)] = rec
    return by_key


def annotation_for(record_index, symbol_id, app_name=None):
    """Look up an annotation by symbol_id, tolerating db_id variants.

    db_id may be stored as the app name, a path, or a basename across writers;
    match on any, then fall back to a unique symbol_id match.
    """
    candidates = []
    if app_name is not None:
        candidates += [
            app_name,
            "%s.db" % app_name,
            os.path.join(GRAPHS_DIR, "%s.db" % app_name),
            os.path.join("graphs", "%s.db" % app_name),
            "graphs/%s.db" % app_name,
        ]
    for db_id in candidates:
        rec = record_index.get((db_id, symbol_id))
        if rec is not None:
            return rec
    matches = [rec for (k_db, k_sym), rec in record_index.items() if k_sym == symbol_id]
    if len(matches) == 1:
        return matches[0]
    if matches:
        # ambiguous across db_ids: prefer the last-written deterministically
        return matches[-1]
    return None


def classify_node(annotation, settings, native_validated=None):
    """Map an annotation record (+ optional in-graph cross-check) to a state.

    Returns (state, confidence), state in {resolved, risk, unaccounted}.
      - no annotation (bare node)                       -> unaccounted
      - status="risk"                                   -> risk
      - status="resolved" & conf >= resolve_threshold &
        (no native disagreement)                        -> resolved
      - status="resolved" but BELOW threshold           -> risk (on the HITL queue;
        the crawl was required to RISK-flag, never under-resolve)
      - status="resolved" but native requirement_validated disagrees (0)
                                                        -> risk (overlay/graph drift)
    """
    if annotation is None:
        return "unaccounted", None

    threshold = settings["resolve_threshold"]
    status = str(annotation.get("status", "")).lower()
    confidence = annotation.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None

    if status == "risk":
        return "risk", confidence

    if status == "resolved":
        if confidence is None or confidence < threshold:
            # below the floor: a settled-but-unresolved node belongs on the queue
            return "risk", confidence
        if native_validated is not None and native_validated != 1:
            # overlay says resolved, in-graph requirement_validated disagrees
            return "risk", confidence
        return "resolved", confidence

    # Fallback for a MINIMAL overlay row written by the `run.py wicked_estate
    # annotate` CLI (which carries no top-level `status`/`confidence` — only the
    # packed `requirement` = "rule_id|confidence|provenance|statement" and
    # `requirement_validated`). Without this a CLI-only crawl is SILENTLY
    # unaccounted (0% coverage). Derive the classification from those fields.
    rv = annotation.get("requirement_validated")
    req = annotation.get("requirement")
    if not status and rv is not None and req:
        if confidence is None:
            parts = str(req).split("|")
            if len(parts) >= 2:
                try:
                    confidence = float(parts[1])
                except (TypeError, ValueError):
                    confidence = None
        resolved = (
            int(rv) == 1
            and confidence is not None
            and confidence >= threshold
            and (native_validated is None or native_validated == 1)
        )
        return ("resolved" if resolved else "risk"), confidence

    # No recognized status -> treat as bare/unaccounted (forces the crawl to act).
    return "unaccounted", confidence


# --- cross-check the in-graph requirement field ------------------------------
def native_validated_for(helper, db_path, symbol_id):
    """Read requirement_validated from the graph via the helper (or None)."""
    if helper is None or symbol_id is None or not db_path:
        return None
    reader = getattr(helper, "read_semantics", None)
    if not callable(reader):
        return None
    try:
        sem = reader(db_path, symbol_id)
    except Exception:
        return None
    if not isinstance(sem, dict):
        return None
    validated = sem.get("requirement_validated")
    if validated is None:
        return None
    try:
        return int(validated)
    except (TypeError, ValueError):
        return None


# --- the computation ---------------------------------------------------------
def compute_coverage(nodes=None, config=None, explicit_db=None,
                     annotations_path=None, cross_check=True, graphs_dir=None):
    """Compute the coverage report dict.

    PRODUCTION (the default): the denominator node-list is enumerated from the
    configured per-app wicked-estate DBs (helper / read-only intern lookup) and
    per-node state from the `.anti-legacy/annotations.jsonl` overlay (loaded via
    `load_annotations`, a patchable seam). When `cross_check` is on and the helper
    is available the in-graph requirement_validated field is consulted.

    A `nodes` list may be injected (dry-run / test) to bypass DB enumeration; then
    `per_app` collapses to a single synthetic 'graph' app.

    Returns the report dict (also the structure written to coverage-report.json).
    """
    if config is None:
        config = load_config()
    settings = coverage_settings(config)
    helper = _load_helper() if (cross_check and nodes is None) else None

    if annotations_path is not None:
        record_index = load_annotations(annotations_path)
    else:
        record_index = load_annotations()

    per_app = []
    all_unaccounted = []
    total_nodes = 0
    behavior_total = 0
    resolved = 0
    risk_flagged = 0
    confidences = []

    if nodes is not None:
        app_units = [("graph", None, list(nodes))]
    else:
        app_units = []
        for app_name, db_path in resolve_app_dbs(config, explicit_db, graphs_dir=graphs_dir):
            app_units.append((app_name, db_path, enumerate_nodes(db_path, helper=helper)))

    for app_name, db_path, node_list in app_units:
        node_list = sorted(node_list, key=lambda n: str(n.get("symbol_id") or ""))
        app_total = len(node_list)
        app_behavior = 0
        app_resolved = 0
        app_risk = 0
        app_unaccounted = []

        for node in node_list:
            total_nodes += 1
            if not is_behavior_bearing(node, settings):
                continue
            behavior_total += 1
            app_behavior += 1
            symbol_id = node.get("symbol_id") or node.get("symbol")
            annotation = annotation_for(record_index, symbol_id, app_name=app_name)
            native_validated = None
            if cross_check and helper is not None:
                native_validated = native_validated_for(helper, db_path, symbol_id)
            state, conf = classify_node(annotation, settings, native_validated)
            if state == "resolved":
                resolved += 1
                app_resolved += 1
                if conf is not None:
                    confidences.append(conf)
            elif state == "risk":
                risk_flagged += 1
                app_risk += 1
            else:
                app_unaccounted.append({
                    "symbol_id": symbol_id,
                    "name": node.get("name"),
                    "kind": normalize_kind(node.get("kind")),
                    "file": node.get("file", ""),
                    "app": app_name,
                })

        app_unaccounted.sort(key=lambda u: str(u.get("symbol_id") or ""))
        all_unaccounted.extend(app_unaccounted)
        per_app.append({
            "app": app_name,
            "db": _rel(db_path) if db_path else None,
            "total": app_total,
            "behavior_bearing": app_behavior,
            "resolved": app_resolved,
            "risk_flagged": app_risk,
            "unaccounted": len(app_unaccounted),
            "coverage": _ratio(app_resolved + app_risk, app_behavior),
        })

    all_unaccounted.sort(key=lambda u: str(u.get("symbol_id") or ""))
    per_app.sort(key=lambda a: str(a["app"]))
    unaccounted = len(all_unaccounted)
    coverage = _ratio(resolved + risk_flagged, behavior_total)
    settled = resolved + risk_flagged
    resolved_rate = round(resolved / settled, ROUND_DP) if settled else 0.0
    mean_conf = (
        round(sum(confidences) / len(confidences), ROUND_DP) if confidences else 0.0
    )

    return {
        "total": total_nodes,
        "behavior_bearing": behavior_total,
        "resolved": resolved,
        "risk_flagged": risk_flagged,
        "unaccounted": unaccounted,
        "coverage": coverage,
        "resolved_rate": resolved_rate,
        "mean_confidence": mean_conf,
        "resolve_threshold": round(settings["resolve_threshold"], ROUND_DP),
        "per_app": per_app,
        "unaccounted_nodes": all_unaccounted,
    }


def _ratio(num, denom):
    if not denom:
        # No behavior-bearing nodes -> vacuously fully covered (nothing to cover).
        return 1.0
    return round(num / denom, ROUND_DP)


def _rel(path):
    if not path:
        return path
    try:
        return os.path.relpath(path, REPO_ROOT)
    except ValueError:
        return path


# --- rendering ---------------------------------------------------------------
def render_markdown(report):
    lines = []
    lines.append("# Coverage report — resolved-or-flagged (§I2)")
    lines.append("")
    cov = report["coverage"]
    status = "PASS (provable terminal)" if cov >= 1.0 else "INCOMPLETE"
    lines.append("**Coverage: %s** — %s" % (_pct(cov), status))
    lines.append("")
    lines.append("| metric | value |")
    lines.append("| --- | --- |")
    lines.append("| behavior-bearing nodes | %d |" % report["behavior_bearing"])
    lines.append("| resolved | %d |" % report["resolved"])
    lines.append("| risk-flagged (HITL queue) | %d |" % report["risk_flagged"])
    lines.append("| unaccounted | %d |" % report["unaccounted"])
    lines.append("| coverage (resolved+risk)/total | %s |" % _pct(cov))
    lines.append("| resolved_rate resolved/(resolved+risk) | %s |"
                 % _pct(report["resolved_rate"]))
    lines.append("| mean_confidence (resolved) | %.4f |" % report["mean_confidence"])
    lines.append("| resolve_threshold | %.4f |" % report["resolve_threshold"])
    lines.append("| total nodes (all kinds) | %d |" % report["total"])
    lines.append("")
    lines.append("## Per-application")
    lines.append("")
    lines.append("| app | behavior | resolved | risk | unaccounted | coverage |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for a in report["per_app"]:
        lines.append("| %s | %d | %d | %d | %d | %s |" % (
            a["app"], a["behavior_bearing"], a["resolved"],
            a["risk_flagged"], a["unaccounted"], _pct(a["coverage"]),
        ))
    lines.append("")
    if report["unaccounted_nodes"]:
        lines.append("## Unaccounted nodes (the coverage hole)")
        lines.append("")
        lines.append("These behavior-bearing nodes are neither RESOLVED nor RISK-flagged.")
        lines.append("Coverage cannot reach 1.0 until each lands in a terminal state.")
        lines.append("")
        lines.append("| SymbolId | kind | name | file | app |")
        lines.append("| --- | --- | --- | --- | --- |")
        for u in report["unaccounted_nodes"]:
            lines.append("| `%s` | %s | %s | %s | %s |" % (
                u["symbol_id"], u["kind"], u.get("name", ""),
                u.get("file", ""), u.get("app", ""),
            ))
        lines.append("")
    else:
        lines.append("## Unaccounted nodes")
        lines.append("")
        lines.append("None — every behavior-bearing node reached a terminal state. "
                     "Coverage == 1.0 (the provable terminal of §I2).")
        lines.append("")
    return "\n".join(lines) + "\n"


def _pct(x):
    return "%.2f%%" % (round(x, ROUND_DP) * 100)


def write_reports(report, json_path=REPORT_JSON, md_path=REPORT_MD):
    json_dir = os.path.dirname(json_path)
    if json_dir:
        os.makedirs(json_dir, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    md_dir = os.path.dirname(md_path)
    if md_dir:
        os.makedirs(md_dir, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))


def _md_companion_path(json_path):
    """The .md companion sits beside the JSON report (same dir/stem)."""
    base, _ = os.path.splitext(json_path)
    return base + ".md"


# --- CLI ---------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolved-or-flagged coverage over the wicked-estate code graph "
                    "(§I2). Exits non-zero with the unaccounted SymbolId list when "
                    "coverage < 1.0 so it doubles as a gate predicate."
    )
    parser.add_argument(
        "--db", default=None,
        help="Single wicked-estate DB to score (default: per-app DBs from "
             "config.source_apps under .anti-legacy/graphs/).",
    )
    parser.add_argument(
        "--nodes", default=None,
        help="Inject a node-list JSON fixture (hermetic / dry-run seam; bypasses "
             "DB enumeration).",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to anti-legacy config.json (default: .anti-legacy/config.json "
             "relative to CWD, else the repo default).",
    )
    parser.add_argument(
        "--json", dest="json_path", default=None,
        help="Path to write the JSON coverage report. Default: "
             ".anti-legacy/coverage-report.json.",
    )
    parser.add_argument(
        "--md", dest="md_path", default=None,
        help="Path to write the Markdown report (default: beside --json).",
    )
    parser.add_argument(
        "--no-cross-check", action="store_true",
        help="Skip the in-graph requirement-field cross-check via the helper.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the human summary on stdout (still writes the reports).",
    )
    args = parser.parse_args(argv)

    # Config: explicit --config, else CWD-relative .anti-legacy/config.json, else default.
    config_path = args.config
    if config_path is None:
        cwd_cfg = os.path.join(os.getcwd(), ".anti-legacy", "config.json")
        config_path = cwd_cfg if os.path.exists(cwd_cfg) else CONFIG_PATH
    try:
        config = load_config(config_path)
    except (OSError, ValueError) as exc:
        sys.stderr.write("coverage: cannot read config %s: %s\n" % (config_path, exc))
        return 2

    # Output paths: default beside the CWD .anti-legacy when running there.
    json_path = args.json_path
    if json_path is None:
        cwd_al = os.path.join(os.getcwd(), ".anti-legacy")
        json_path = (os.path.join(cwd_al, "coverage-report.json")
                     if os.path.isdir(cwd_al) else REPORT_JSON)
    md_path = args.md_path or _md_companion_path(json_path)

    # Per-app graph DBs live under the WORKSPACE .anti-legacy/graphs — beside the
    # config we actually loaded — NOT this script's plugin-install dir (the
    # `__file__`-anchored GRAPHS_DIR). Anchoring on the resolved config's
    # directory fixes the no-`--db` (multi-repo) path that otherwise searches the
    # plugin install tree and finds nothing. (ISS-23)
    graphs_dir = os.path.join(os.path.dirname(os.path.abspath(config_path)), "graphs")

    # Node source.
    nodes = None
    if args.nodes is not None:
        try:
            nodes = load_nodes_file(args.nodes)
        except (OSError, ValueError) as exc:
            sys.stderr.write("coverage: cannot read --nodes %s: %s\n"
                             % (args.nodes, exc))
            return 2

    # Annotation overlay: prefer a CWD-relative .anti-legacy/annotations.jsonl when
    # present (lets a run from a target dir pick up its own overlay). Passed to
    # compute_coverage as the load path; load_annotations remains a patchable seam.
    cwd_overlay = os.path.join(os.getcwd(), ".anti-legacy", "annotations.jsonl")
    overlay_path = cwd_overlay if os.path.exists(cwd_overlay) else None

    try:
        report = compute_coverage(
            nodes=nodes,
            config=config,
            explicit_db=args.db,
            annotations_path=overlay_path,
            cross_check=not args.no_cross_check,
            graphs_dir=graphs_dir,
        )
    except FileNotFoundError as exc:
        sys.stderr.write("coverage: %s\n" % exc)
        return 2

    write_reports(report, json_path=json_path, md_path=md_path)

    if not args.quiet:
        sys.stdout.write(
            "coverage=%s  behavior=%d  resolved=%d  risk=%d  unaccounted=%d  "
            "resolved_rate=%s  mean_conf=%.4f\n" % (
                _pct(report["coverage"]), report["behavior_bearing"],
                report["resolved"], report["risk_flagged"], report["unaccounted"],
                _pct(report["resolved_rate"]), report["mean_confidence"],
            )
        )
        sys.stdout.write("wrote %s and %s\n" % (json_path, md_path))

    if report["coverage"] < 1.0:
        sys.stderr.write(
            "coverage < 1.0 (%d unaccounted behavior-bearing nodes):\n"
            % report["unaccounted"]
        )
        for u in report["unaccounted_nodes"]:
            sys.stderr.write("  %s\n" % u["symbol_id"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
