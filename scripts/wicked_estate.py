#!/usr/bin/env python3
"""wicked-estate integration helper (anti-legacy WF1).

This is the ONE seam between anti-legacy and the user's MIT code-graph engine,
`wicked-estate`. It is both importable (the extraction/survey/analyze skills and
scripts/coverage.py call its functions) and a thin CLI (`run.py wicked_estate
<cmd> ...`) so skill recipes can shell into it.

Design contract (BACKLOG §H + the WF1 shared contracts):

  * STRUCTURE comes from the stable CLI: `index`, `stats`, `query`,
    `blast-radius`, `source`, `rank`, `cross-graph`. We parse the human output.
  * ANNOTATION goes through the stable CLI write path: `semantics <symbol_id>
    --requirement ... --description ... --validated ...` and round-trips via
    `by-requirement` / `semantics <id>` show.
  * The `<symbol>` arg to `semantics` is the FULL interned SymbolId STRING, not
    the simple name. Passing a simple name is a SILENT NO-OP (0 rows update).
    Names are NOT unique (carddemo: MAIN-PARA x21). So every write resolves
    name -> SymbolId first via `resolve_symbol_id`.
  * The read-side CLI exposes NO SymbolId and has NO --json. There is no CLI
    way to obtain a SymbolId. The ONE deliberate, documented raw-SQLite
    exception is `resolve_symbol_id`: a read-only id-resolution lookup against
    the ADR-002-locked `symbols(sym)` intern table joined to `nodes`. All graph
    STRUCTURE still comes from the CLI; the WRITE still goes through the CLI.
  * `annotate()` writes BOTH the native `requirement`/`description`/
    `requirement_validated` columns (in-graph evidence projection) AND the
    lossless anti-legacy rule object to `.anti-legacy/annotations.jsonl` (the
    IP-rich sidecar and coverage.py's source of truth). Both atomically.

Binary resolution priority (first hit wins): config `wicked_estate_path` ->
WICKED_ESTATE_PATH env -> PATH -> the wicked-estate release fallback. Raises a
clear error on no-resolve (never silently degrades — the engine's R2 rule).

Cross-platform: pure-Python subprocess + shutil.which; no shell builtins; no
shell=True; text mode; subprocess timeouts on every call.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Paths & constants. All paths are repo-root-relative; callers run with
# cwd == repo root (the dispatcher and skills do).
# ---------------------------------------------------------------------------
CONFIG_PATH = ".anti-legacy/config.json"
ANNOTATIONS_PATH = ".anti-legacy/annotations.jsonl"
DEFAULT_DB = ".anti-legacy/legacy-graph.db"
GRAPHS_DIR = ".anti-legacy/graphs"


def _overlay_path(explicit=None) -> str:
    """Resolve the annotations-overlay path. Precedence: an explicit caller
    argument > the ANTI_LEGACY_ANNOTATIONS env var (lets tests/skills redirect
    the IP sidecar) > the default `.anti-legacy/annotations.jsonl`."""
    if explicit:
        return explicit
    return os.environ.get("ANTI_LEGACY_ANNOTATIONS") or ANNOTATIONS_PATH

# Binary-resolution tier 4: explicit override via env var (no hardcoded paths).
WICKED_ESTATE_FALLBACK = os.environ.get("WICKED_ESTATE_PATH", "")

# F7 timeout pattern: a hung tool must FAIL, never hang the pipeline. Index can
# be slow on a large estate (carddemo ~1.8s, but headroom for bigger repos);
# read commands are fast.
DEFAULT_TIMEOUTS = {
    "index": 600,
    "stats": 60,
    "query": 60,
    "blast_radius": 120,
    "source": 60,
    "rank": 120,
    "cross_graph": 180,
    "semantics": 60,
    "by_requirement": 60,
    "semantic": 60,
    "changed_since": 120,
}

# Edge kinds the data-affinity cluster weight mode coalesces on (data coupling /
# containment) — distinct from BEHAVIOR_EDGE_KINDS (which is the call-affinity /
# behavior-bearing set the coverage denominator uses).
_DATA_AFFINITY_EDGE_KINDS = {"references", "accesses", "uses", "contains", "imports"}
# Edge kinds the call-affinity cluster weight mode (weight=="calls") coalesces on.
_CALL_AFFINITY_EDGE_KINDS = {"calls", "invokes"}

# Edge kinds that carry behavior (a node with one of these as an OUTGOING edge
# "does something" — calls/uses/accesses another node). Mirrors coverage.py's
# BEHAVIOR_EDGE_KINDS so `list_nodes`'s `_has_behavior_out_edge` flag means the
# same thing the denominator predicate expects (a data-only copybook module with
# 0 behavior-out edges is structural, not behavior-bearing).
BEHAVIOR_EDGE_KINDS = {"calls", "uses", "references", "accesses", "invokes"}

# Lines that vary run-to-run / machine-to-machine and MUST be stripped from the
# stats digest so the checksummed evidence is deterministic. Verified empirically
# against the binary: `repo:` (git provenance), `STALENESS:`, and any `db=NN.NMB`
# size suffix are the only volatile bits; the node/edge-count block is byte-stable.
_VOLATILE_STATS_PREFIXES = ("repo:", "staleness:")


class WickedEstateError(RuntimeError):
    """Raised for any helper-level failure (no binary, failed invocation,
    unresolvable symbol, etc.). Callers get one exception type to catch."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _load_config(config_path: str = CONFIG_PATH) -> dict:
    """Best-effort load of .anti-legacy/config.json. Missing/invalid -> {}."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Binary resolution (4-tier, first hit wins; raises on no-resolve)
# ---------------------------------------------------------------------------
def _is_executable(path) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_binary(
    config=None,
    config_path: str = CONFIG_PATH,
    fallback: str | None = None,
    search_path: bool = True,
):
    """Resolve the wicked-estate binary, in priority order (first hit wins):

      1. config `wicked_estate_path` (absolute path), if executable.
      2. WICKED_ESTATE_PATH env var, if executable.
      3. `wicked-estate` on PATH (shutil.which) — unless search_path=False.
      4. the wicked-estate release fallback (the known-good v0.0.1 spike binary).

    `config` may be passed as an already-loaded dict (tests/callers that hold
    the config in memory); when None it is loaded from `config_path`.
    `fallback` overrides the tier-4 fallback path (defaults to WICKED_ESTATE_FALLBACK).
    `search_path=False` disables tier 3 (used to make resolution hermetic).

    Raises WickedEstateError with install instructions if none resolve, mirroring
    the engine's R2 contract: never silently degrade.
    """
    cfg = config if config is not None else _load_config(config_path)

    # 1. config
    cfg_path = (cfg or {}).get("wicked_estate_path")
    if cfg_path and _is_executable(cfg_path):
        return cfg_path

    # 2. env
    env_path = os.environ.get("WICKED_ESTATE_PATH")
    if env_path and _is_executable(env_path):
        return env_path

    # 3. PATH
    if search_path:
        which = shutil.which("wicked-estate")
        if which:
            return which

    # 4. wicked-estate release fallback
    fb = fallback if fallback is not None else WICKED_ESTATE_FALLBACK
    if _is_executable(fb):
        return fb

    raise WickedEstateError(
        "wicked-estate engine is REQUIRED and was not found. Install it with "
        "`cargo install wicked-estate` (needs the Rust toolchain — one-line install at "
        "https://rustup.rs), then make sure `wicked-estate` is on PATH. Alternatively, "
        "set `wicked_estate_path` in .anti-legacy/config.json or export WICKED_ESTATE_PATH "
        "to an existing binary. The pipeline cannot index, extract, or build without it."
    )


# ---------------------------------------------------------------------------
# Subprocess core
# ---------------------------------------------------------------------------
def _run(args, *, timeout: int, cwd: str = ".", binary: str | None = None) -> str:
    """Invoke the binary with `args`, return stdout (text). Raises
    WickedEstateError on non-zero exit or timeout. cwd defaults to repo root.

    `args` is the list of CLI args AFTER the binary (e.g. ["query", name,
    "--db", db]).
    """
    bin_path = binary or resolve_binary()
    cmd = [bin_path] + list(args)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise WickedEstateError(
            f"wicked-estate {args[0] if args else ''} timed out after {e.timeout}s"
        ) from e
    except OSError as e:
        raise WickedEstateError(f"failed to invoke wicked-estate: {e}") from e

    if result.returncode != 0:
        raise WickedEstateError(
            f"wicked-estate {' '.join(str(a) for a in args)} exited "
            f"{result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def _db_args(db: str) -> list:
    """--db is ALWAYS passed explicitly (default DEFAULT_DB)."""
    return ["--db", db or DEFAULT_DB]


# ---------------------------------------------------------------------------
# Parsers for the human CLI output (verified against the v0.0.1 binary)
# ---------------------------------------------------------------------------
def _parse_stats(stdout: str) -> dict:
    """Parse `stats` output, e.g.:

        nodes=84 edges=101 files=1
          edge "calls" = 18
          edge "contains" = 83
        repo:

    Returns {nodes, edges, files, edges_by_kind: {calls: 18, ...}}.
    """
    out = {"nodes": 0, "edges": 0, "files": 0, "edges_by_kind": {}}
    for line in stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.lower().startswith(_VOLATILE_STATS_PREFIXES):
            continue
        if s.startswith("nodes=") and "edges=" in s:
            for tok in s.split():
                if "=" not in tok:
                    continue
                k, _, v = tok.partition("=")
                if k in ("nodes", "edges", "files"):
                    try:
                        out[k] = int(v)
                    except ValueError:
                        pass
        elif s.startswith('edge "'):
            # edge "calls" = 18
            try:
                name = s.split('"', 2)[1]
                count = int(s.rsplit("=", 1)[1].strip())
                out["edges_by_kind"][name] = count
            except (IndexError, ValueError):
                pass
    return out


def _canonicalize_stats(raw: str) -> str:
    """Deterministic canonical stats block from RAW stats text.

    STRIPS the volatile lines (repo:/STALENESS:/db=NN.NMB), normalizes the
    node/edge block and sorts edge kinds by name. This is the byte-stable body
    that survey checksums as the `legacy-graph` evidence. Pure-text entry point
    so it can be unit-tested without a binary.
    """
    parsed = _parse_stats(raw)
    lines = [f"nodes={parsed['nodes']} edges={parsed['edges']} files={parsed['files']}"]
    for kind in sorted(parsed["edges_by_kind"]):
        lines.append(f'edge "{kind}" = {parsed["edges_by_kind"][kind]}')
    return "\n".join(lines) + "\n"


# Public alias for the pure-text digest entry point.
digest_stats_text = _canonicalize_stats


def _parse_index(stdout: str) -> dict:
    """Parse `index` summary, e.g.:

        indexed /tmp/we_src (/tmp/we_probe.db) → 84 nodes, 101 edges, 1 files
          "calls" = 18

    Returns {nodes, edges, files, edges_by_kind}.
    """
    out = {"nodes": 0, "edges": 0, "files": 0, "edges_by_kind": {}}
    for line in stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("indexed "):
            tail = s
            for arrow in ("→", "->"):
                if arrow in tail:
                    tail = tail.split(arrow, 1)[1]
                    break
            for part in tail.split(","):
                toks = part.strip().split()
                if len(toks) >= 2 and toks[1] in ("nodes", "edges", "files"):
                    try:
                        out[toks[1]] = int(toks[0])
                    except ValueError:
                        pass
        elif s.startswith('"') and "=" in s:
            try:
                name = s.split('"', 2)[1]
                count = int(s.rsplit("=", 1)[1].strip())
                out["edges_by_kind"][name] = count
            except (IndexError, ValueError):
                pass
    return out


def _parse_node_line(line: str):
    """Parse a `Kind name (file:line)` node line into a dict, else None.

    e.g. "  Function 0000-ACCTFILE-OPEN (CBACT01C.cbl:317)" ->
         {kind: "Function", name: "0000-ACCTFILE-OPEN",
          file: "CBACT01C.cbl", line: 317}
    """
    s = line.strip()
    if not s or "(" not in s or not s.endswith(")"):
        return None
    head, _, loc = s.rpartition("(")
    loc = loc[:-1]  # strip trailing ')'
    if ":" not in loc:
        return None
    file_part, _, line_part = loc.rpartition(":")
    try:
        line_no = int(line_part)
    except ValueError:
        return None
    head = head.strip()
    if " " not in head:
        return None
    kind, _, name = head.partition(" ")
    return {
        "kind": kind.strip(),
        "name": name.strip(),
        "file": file_part.strip(),
        "line": line_no,
    }


def _parse_matches(stdout: str) -> list:
    """Parse the node lines out of `query`/`source`/`blast-radius` output."""
    nodes = []
    for line in stdout.splitlines():
        node = _parse_node_line(line)
        if node is not None:
            nodes.append(node)
    return nodes


def _parse_rank(stdout: str) -> list:
    """Parse `rank` output, e.g.:

        top 25 symbols by PageRank:
          0.0214  Field TWO-BYTES-BINARY (CBACT01C.cbl:107)

    Returns [{score, kind, name, file, line}, ...] in rank order.
    """
    out = []
    for line in stdout.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("top "):
            continue
        toks = s.split(None, 1)
        if len(toks) != 2:
            continue
        try:
            score = float(toks[0])
        except ValueError:
            continue
        node = _parse_node_line(toks[1])
        if node is not None:
            node["score"] = score
            out.append(node)
    return out


def _parse_semantics_show(stdout: str):
    """Parse `semantics <id>` SHOW output, e.g.:

        symbol: ts-cobol ...
          description: Opens the account master file
          requirement: RULE-001|0.91|...
          validated:   true

    Returns {symbol, description, requirement, validated(bool)} or None when
    "no semantics set ...".
    """
    text = stdout.strip()
    if not text or text.startswith("no semantics set"):
        return None
    out = {"symbol": None, "description": None, "requirement": None, "validated": False}
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("symbol:"):
            out["symbol"] = s.split(":", 1)[1].strip()
        elif s.startswith("description:"):
            out["description"] = s.split(":", 1)[1].strip()
        elif s.startswith("requirement:"):
            out["requirement"] = s.split(":", 1)[1].strip()
        elif s.startswith("validated:"):
            out["validated"] = s.split(":", 1)[1].strip().lower() == "true"
    return out


# ---------------------------------------------------------------------------
# Public read/structure API (all through the stable CLI)
# ---------------------------------------------------------------------------
def index(paths, db: str = DEFAULT_DB, binary: str | None = None,
          embeddings: bool | None = None, fresh: bool = False) -> dict:
    """Run `wicked-estate index <path> --db <db>` for each (name, path) pair
    into the SAME db, returning the parsed stats of the final state.

    `paths` is a list of (name, path) tuples (name is informational; the CLI
    keys off the path) — bare path strings are also accepted. For multi-repo
    survey, callers use one db PER repo and call index() once per db.

    `embeddings`: pass `--embeddings` so the engine generates per-node embeddings
    (local, no API key). Default resolves from config `embeddings` (default True)
    — embeddings power semantic `correspond` / `semantic` (the cross-language
    merge-alignment signal); without them correspond degrades to name-only.

    `fresh`: delete the db (+ -shm/-wal) before indexing so the build is a FULL
    re-parse, never incremental. REQUIRED for an authoritative survey: incremental
    indexing skips unchanged files, so spans/structure computed by an OLDER engine
    binary persist stale after an engine upgrade (e.g. a pre-#6 binary's label-only
    COBOL paragraph spans survive a re-index). Survey should always pass fresh=True.
    """
    if embeddings is None:
        embeddings = bool(_load_config().get("embeddings", True))
    if fresh:
        for suffix in ("", "-shm", "-wal"):
            try:
                os.remove(db + suffix)
            except OSError:
                pass
    extra = ["--embeddings"] if embeddings else []
    last = {"nodes": 0, "edges": 0, "files": 0, "edges_by_kind": {}}
    for entry in paths:
        if isinstance(entry, (list, tuple)):
            path = entry[1]
        else:
            path = entry
        out = _run(
            ["index", path] + extra + _db_args(db),
            timeout=DEFAULT_TIMEOUTS["index"],
            binary=binary,
        )
        last = _parse_index(out)
    return last


def stats(db: str = DEFAULT_DB, binary: str | None = None) -> dict:
    """Run `wicked-estate stats --db <db>`, return parsed counts."""
    out = _run(["stats"] + _db_args(db), timeout=DEFAULT_TIMEOUTS["stats"], binary=binary)
    return _parse_stats(out)


def stats_digest(db_or_text: str = DEFAULT_DB, binary: str | None = None) -> str:
    """Deterministic, checksummable stats digest.

    Polymorphic for ergonomics + testability:
      * If `db_or_text` is RAW stats text (multi-line / contains "nodes="
      and is not an existing file path), canonicalize it directly.
      * Otherwise treat it as a db PATH: run `stats`, then canonicalize.

    STRIPS the volatile lines (repo:/STALENESS:/db=NN.NMB) and sorts edge kinds,
    yielding the stable multi-line string survey writes to
    `.anti-legacy/legacy-graph.digest.txt` and checksums as the `legacy-graph`
    evidence.
    """
    looks_like_text = ("\n" in db_or_text) or (
        "nodes=" in db_or_text and not os.path.exists(db_or_text)
    )
    if looks_like_text:
        return _canonicalize_stats(db_or_text)
    raw = _run(
        ["stats"] + _db_args(db_or_text),
        timeout=DEFAULT_TIMEOUTS["stats"],
        binary=binary,
    )
    return _canonicalize_stats(raw)


def query(db: str, name: str, binary: str | None = None) -> dict:
    """Run `wicked-estate query <name> --db <db>`. Returns
    {name, matches: [node, ...]}."""
    out = _run(
        ["query", name] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["query"],
        binary=binary,
    )
    return {"name": name, "matches": _parse_matches(out)}


def blast_radius(db: str, name: str, binary: str | None = None) -> dict:
    """Run `wicked-estate blast-radius <name> --db <db>`. Returns
    {name, dependents: [node, ...]} (the 1-up dependents set)."""
    out = _run(
        ["blast-radius", name] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["blast_radius"],
        binary=binary,
    )
    return {"name": name, "dependents": _parse_matches(out)}


def source(db: str, name: str, binary: str | None = None) -> dict:
    """Run `wicked-estate source <name> --db <db>`. Returns
    {name, matches: [node, ...], body: <text>} where body is the source slice
    text (everything after the match header lines).
    """
    out = _run(
        ["source", name] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["source"],
        binary=binary,
    )
    matches = _parse_matches(out)
    body_lines = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            body_lines.append(line)
            continue
        if s.endswith(":") and "match(es) for" in s:
            continue
        if s.startswith("[") and "@" in s:
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip("\n")
    return {"name": name, "matches": matches, "body": body}


def _parse_source_match_header(line: str):
    """Parse a `source` per-match header line into {kind, name, file, line}, else
    None.

    The engine emits, per match, a header of the form:

        ``  [Function] MAIN-PARA @ app/cbl/COUSR00C.cbl:98``

    The trailing ``@ <file>:<line>`` is the disambiguator that distinguishes the
    21 carddemo MAIN-PARA collisions — the body BELOW each header belongs to the
    node at that exact file. Returns None for the count banner / body lines.
    """
    s = line.strip()
    if not (s.startswith("[") and "]" in s and " @ " in s):
        return None
    kind_close = s.index("]")
    kind = s[1:kind_close].strip()
    rest = s[kind_close + 1:].strip()
    nm, _, loc = rest.rpartition(" @ ")
    nm = nm.strip()
    loc = loc.strip()
    if ":" not in loc:
        return None
    file_part, _, line_part = loc.rpartition(":")
    try:
        line_no = int(line_part)
    except ValueError:
        return None
    return {
        "kind": kind,
        "name": nm,
        "file": file_part.strip(),
        "line": line_no,
    }


def source_by_match(db: str, name: str, binary: str | None = None) -> list:
    """Split `source <name>` output into ONE record PER match, each carrying its
    OWN body slice — the collision-free source read.

    `source()` (above) concatenates EVERY match's body into a single `body`
    string. For a name that resolves to many nodes (carddemo MAIN-PARA ×21) that
    single blob is shared by all of them — the ISS-20 fingerprint aliasing bug.
    This splits on the per-match `[Kind] name @ file:line` header so each
    distinct node (keyed by its file) gets the exact body BELOW its header.

    Returns ``[{kind, name, file, line, body}, ...]`` in engine order. A name with
    a single match returns a one-element list; an unindexed/empty name returns [].
    """
    out = _run(
        ["source", name] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["source"],
        binary=binary,
    )
    records = []
    cur = None
    cur_lines: list = []
    for line in out.splitlines():
        s = line.strip()
        if s.endswith(":") and "match(es) for" in s:
            continue
        hdr = _parse_source_match_header(line)
        if hdr is not None:
            if cur is not None:
                cur["body"] = "\n".join(cur_lines).strip("\n")
                records.append(cur)
            cur = dict(hdr)
            cur_lines = []
            continue
        if cur is not None:
            cur_lines.append(line)
    if cur is not None:
        cur["body"] = "\n".join(cur_lines).strip("\n")
        records.append(cur)
    return records


def rank(db: str = DEFAULT_DB, binary: str | None = None) -> list:
    """Run `wicked-estate rank --db <db>`. Returns the ranked node list
    (most important first) for the crawl worklist order."""
    out = _run(["rank"] + _db_args(db), timeout=DEFAULT_TIMEOUTS["rank"], binary=binary)
    return _parse_rank(out)


def cross_graph(name: str, dbs, binary: str | None = None) -> dict:
    """Run `wicked-estate cross-graph <name> --db a --db b ...` for federated
    search + blast-radius across multiple repo DBs (the merge case).

    Returns {name, dbs, matches, raw}. cross-graph interleaves per-repo sections;
    callers needing structure use `matches`.
    """
    if not dbs:
        raise WickedEstateError("cross_graph requires at least one --db")
    args = ["cross-graph", name]
    for d in dbs:
        args += ["--db", d]
    out = _run(args, timeout=DEFAULT_TIMEOUTS["cross_graph"], binary=binary)
    return {"name": name, "dbs": list(dbs), "matches": _parse_matches(out), "raw": out}


# ---------------------------------------------------------------------------
# SymbolId resolution — the ONE documented read-only raw-SQLite exception.
# ---------------------------------------------------------------------------
def resolve_symbol_id(db: str, name: str, file: str | None = None, kind: str | None = None) -> list:
    """Resolve a simple node `name` to its full interned SymbolId string(s).

    This is the SOLE deliberate, documented exception to BACKLOG §H's "no raw
    SQLite" rule, justified because (a) it is read-only id-resolution, not graph
    consumption — all STRUCTURE still comes from the CLI; (b) the
    `symbols(sym)` intern table + `nodes(symbol,name)` columns are ADR-002-locked
    stable identity, the most stable surface in the schema; (c) there is no CLI
    alternative and the write API itself requires the id.

    Query: SELECT s.sym, n.name, n.kind, n.file FROM nodes n
           JOIN symbols s ON n.symbol = s.sid WHERE n.name = ?
    Optionally disambiguated by `file` and/or `kind` (names are NOT unique).

    `kind` in the DB is stored JSON-quoted (e.g. '"function"'); we match
    case-insensitively against the de-quoted value, so callers can pass either
    `function` or `Function`. `file` matches the full repo-relative path OR the
    basename.

    Returns a list of full SymbolId strings (empty if no match).
    """
    db_path = db or DEFAULT_DB
    if not os.path.exists(db_path):
        raise WickedEstateError(f"db not found for symbol resolution: {db_path}")
    # Read-only connection (immutable URI) so we never mutate the engine's DB.
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as e:
        raise WickedEstateError(f"could not open db {db_path} for read: {e}") from e
    try:
        cur = conn.execute(
            "SELECT s.sym, n.name, n.kind, n.file "
            "FROM nodes n JOIN symbols s ON n.symbol = s.sid "
            "WHERE n.name = ?",
            (name,),
        )
        rows = cur.fetchall()
    except sqlite3.Error as e:
        raise WickedEstateError(f"symbol-resolution query failed: {e}") from e
    finally:
        conn.close()

    results = []
    for sym, _rname, rkind, rfile in rows:
        dekind = (rkind or "").strip().strip('"').lower()
        if kind is not None and dekind != kind.strip().strip('"').lower():
            continue
        if file is not None:
            rf = rfile or ""
            if rf != file and os.path.basename(rf) != os.path.basename(file):
                continue
        results.append(sym)
    return results


def _dekind(raw) -> str:
    """De-quote the JSON-stored kind ('"function"' -> 'function'). Estate kinds
    are objects ('{"other":"step"}') — return the raw JSON token in that case so
    callers can normalize it themselves."""
    s = (raw or "").strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return s[1:-1].lower()
    return s.lower()


def list_nodes(db: str, kinds=None) -> list:
    """Enumerate the graph's nodes as dicts (the denominator source for
    coverage.py and the program/interface lister for detect_dead_ends.py).

    This is the SAME documented read-only intern-table exception as
    `resolve_symbol_id`: id/structure ENUMERATION the CLI does not expose, never
    graph consumption. It joins `nodes` to the ADR-002-locked `symbols(sym)`
    intern table and pre-computes, per node, whether it has any OUTGOING
    behavior edge (calls/uses/references/accesses/invokes) so consumers can apply
    the behavior-bearing predicate without a second pass.

    Each row: {symbol, symbol_id, name, kind, file, _has_behavior_out_edge}.
    `symbol` and `symbol_id` are both the full interned SymbolId (two keys so
    either consumer convention works). `kind` is returned VERBATIM as stored
    (callers normalize). Optional `kinds` (an iterable of bare lowercase kind
    tokens) filters to those de-quoted simple kinds — estate object-kinds are
    only returned when `kinds` is None.
    """
    db_path = db or DEFAULT_DB
    if not os.path.exists(db_path):
        raise WickedEstateError(f"db not found for node enumeration: {db_path}")
    want = None
    if kinds is not None:
        want = {str(k).strip().strip('"').lower() for k in kinds}
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as e:
        raise WickedEstateError(f"could not open db {db_path} for read: {e}") from e
    try:
        # Which symbols have an outgoing behavior edge.
        behavior_out = set()
        cur = conn.execute(
            "SELECT DISTINCT s.sym, e.kind "
            "FROM edges e JOIN symbols s ON e.source = s.sid"
        )
        for sym, ekind in cur.fetchall():
            if _dekind(ekind) in BEHAVIOR_EDGE_KINDS:
                behavior_out.add(sym)
        cur = conn.execute(
            "SELECT s.sym, n.name, n.kind, n.file "
            "FROM nodes n JOIN symbols s ON n.symbol = s.sid"
        )
        rows = cur.fetchall()
    except sqlite3.Error as e:
        raise WickedEstateError(f"node-enumeration query failed: {e}") from e
    finally:
        conn.close()

    out = []
    for sym, name, kind, file_ in rows:
        if want is not None:
            dek = _dekind(kind)
            # estate object-kinds ('{"other":"step"}') are NOT simple tokens; a
            # `kinds` filter is by simple kind only.
            if dek.startswith("{") or dek not in want:
                continue
        out.append({
            "symbol": sym,
            "symbol_id": sym,
            "name": name,
            "kind": kind,
            "file": file_ or "",
            "_has_behavior_out_edge": sym in behavior_out,
        })
    return out


# ---------------------------------------------------------------------------
# Annotation write + read (through the stable CLI) + lossless JSONL sidecar.
# ---------------------------------------------------------------------------
def _append_annotation_overlay(record: dict, overlay_path: str | None = None) -> None:
    """Append one JSON line to the anti-legacy-owned annotations overlay.

    The overlay is the IP-rich, lossless record and coverage.py's source of
    truth. Each line is keyed by {db_id, symbol_id} and carries the full rule
    object. Written via python json (cross-platform; no echo)."""
    path = _overlay_path(overlay_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def annotate(
    db: str,
    symbol_id: str,
    requirement: str | None = None,
    description: str | None = None,
    validated: bool = False,
    *,
    rule_object: dict | None = None,
    overlay_path: str | None = None,
    binary: str | None = None,
) -> dict:
    """Write an annotation onto a node, atomically across the native field and
    the JSONL sidecar.

    Guards the SILENT-NO-OP trap: `symbol_id` MUST be a non-empty, resolved full
    SymbolId. Passing a bare name (or empty) raises rather than calling
    `semantics` with an unresolvable string that would report success while
    updating 0 rows. Callers obtain `symbol_id` via `resolve_symbol_id` first.

    Writes:
      1. NATIVE field (in-graph evidence projection): shells
         `wicked-estate semantics <symbol_id> [--requirement ...]
         [--description ...] --validated <true|false> --db <db>`.
      2. JSONL OVERLAY (lossless IP sidecar): appends a record keyed by
         {db_id, symbol_id} carrying the full `rule_object` (confidence,
         provenance, resolved_by, risk reason, ring depth, ...) plus the
         projected fields and a timestamp.

    The convention (per the annotation contract) is that `requirement` is the
    compact tagged string "<rule_id>|<confidence>|<provenance>|<statement>" and
    `requirement_validated` = 1 when RESOLVED at/above threshold, 0 when RISK.
    `rule_object` is the full anti-legacy rule object mirrored losslessly.

    Returns the overlay record that was written.
    """
    if not symbol_id or not str(symbol_id).strip():
        raise WickedEstateError(
            "annotate() refused: empty/unresolved symbol_id. The `semantics` "
            "write path is a SILENT NO-OP on an un-interned string — resolve the "
            "name to its full SymbolId via resolve_symbol_id() first."
        )

    # 1. NATIVE field via the stable CLI.
    args = ["semantics", symbol_id]
    if requirement is not None:
        args += ["--requirement", requirement]
    if description is not None:
        args += ["--description", description]
    args += ["--validated", "true" if validated else "false"]
    args += _db_args(db)
    _run(args, timeout=DEFAULT_TIMEOUTS["semantics"], binary=binary)

    # 2. JSONL overlay (lossless IP sidecar), keyed by {db_id, symbol_id}.
    record = {
        "db_id": db or DEFAULT_DB,
        "symbol_id": symbol_id,
        "requirement": requirement,
        "description": description,
        "requirement_validated": 1 if validated else 0,
        "ts": int(time.time()),
    }
    if rule_object:
        for k, v in rule_object.items():
            if k not in ("db_id", "symbol_id"):
                record[k] = v
    _append_annotation_overlay(record, overlay_path=_overlay_path(overlay_path))
    return record


def read_semantics(db: str, symbol_id: str, binary: str | None = None):
    """Read the native annotation for a node via `semantics <symbol_id>` SHOW.

    Returns {symbol, description, requirement, validated} or None when no
    semantics are set. Used by coverage.py to cross-check the JSONL overlay
    against the in-graph field so the two can't silently diverge.
    """
    if not symbol_id or not str(symbol_id).strip():
        raise WickedEstateError("read_semantics() requires a non-empty symbol_id")
    out = _run(
        ["semantics", symbol_id] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["semantics"],
        binary=binary,
    )
    return _parse_semantics_show(out)


def by_requirement(db: str, req: str, binary: str | None = None) -> dict:
    """Reverse lookup via `by-requirement <REQ> --db <db>` (exact-string match).

    Returns {requirement, count, matches: [node, ...]}. Callers key the reverse
    lookup on the rule_id prefix or the full tagged string per the convention.
    """
    out = _run(
        ["by-requirement", req] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["by_requirement"],
        binary=binary,
    )
    count = 0
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("symbols satisfying requirement"):
            try:
                count = int(s.rsplit(":", 1)[1].strip())
            except (IndexError, ValueError):
                count = 0
            break
    return {"requirement": req, "count": count, "matches": _parse_matches(out)}


# ---------------------------------------------------------------------------
# A (adjunct): native multi-key k/v annotation surface.
#
# annotate() above stays the PRIMARY requirement-write path: SymbolId-precise
# (resolved before write — names collide), writing the native semantics
# requirement/description columns AND the lossless .anti-legacy/annotations.jsonl IP
# sidecar that coverage.py + the §2 traceability thread + by_requirement depend on.
# Native `annotate <name> --key K --value V` is a RICHER multi-key store
# (confidence/provenance/author per key) but NAME-based — it tags EVERY search hit
# (the carddemo MAIN-PARA ×21 collision trap). So it is exposed ADJUNCT-ONLY,
# precision-guarded to a single resolved SymbolId; it NEVER becomes the requirement
# write path and NEVER touches the sidecar.
# ---------------------------------------------------------------------------
def _parse_annotations_show(stdout: str) -> list:
    """Parse native `annotations <name>` output into a flat list of
    {kind, name, file, line, key, value, confidence, provenance, author}.

    Block form (per hit):
        [Function] beta (chain.py:4)
          rule=RULE-1 [confidence=0.910 provenance="extract" author="claude"]
          (no annotations)
    """
    out = []
    cur_node = None
    for line in stdout.splitlines():
        s = line.strip()
        if not s or s.startswith("no symbols found"):
            cur_node = None
            continue
        if s.startswith("[") and "]" in s:
            # "[Function] beta (chain.py:4)"  -> strip the [Kind] bracket to a node line
            kind_close = s.index("]")
            kind = s[1:kind_close].strip()
            rest = s[kind_close + 1:].strip()
            node = _parse_node_line(rest)
            if node is not None:
                node["kind"] = kind or node.get("kind", "")
                cur_node = node
            else:
                cur_node = {"kind": kind, "name": rest, "file": "", "line": 0}
            continue
        if s == "(no annotations)":
            continue
        if "=" in s and cur_node is not None:
            # "key=value [confidence=0.910 provenance=".." author=".."]"
            meta = {"confidence": None, "provenance": None, "author": None}
            kv_part = s
            if "[" in s and s.endswith("]"):
                kv_part = s[: s.index("[")].strip()
                bracket = s[s.index("[") + 1 : -1]
                for tok in ("confidence", "provenance", "author"):
                    marker = tok + "="
                    if marker in bracket:
                        val = bracket.split(marker, 1)[1].strip()
                        # value is up to the next " <field>=" or end
                        for nxt in ("confidence=", "provenance=", "author="):
                            if nxt != marker and nxt in val:
                                val = val.split(nxt, 1)[0].strip()
                        meta[tok] = val.strip().strip('"')
            key, _, value = kv_part.partition("=")
            rec = {
                "kind": cur_node.get("kind", ""),
                "name": cur_node.get("name", ""),
                "file": cur_node.get("file", ""),
                "line": cur_node.get("line", 0),
                "key": key.strip(),
                "value": value.strip(),
            }
            rec.update(meta)
            out.append(rec)
    return out


def annotate_kv(
    db: str,
    symbol_id: str,
    key: str,
    value: str,
    *,
    confidence: float | None = None,
    provenance: str | None = None,
    author: str | None = None,
    binary: str | None = None,
) -> dict:
    """ADJUNCT (richer surface): write ONE native multi-key annotation
    (key/value [+ confidence/provenance/author]) via native `annotate`.

    PRECISION-GUARDED: `symbol_id` MUST be a resolved full SymbolId. We confirm it
    resolves to EXACTLY ONE node (by querying the node's name back) and only then call
    native `annotate <name>` — refusing when the name is ambiguous, so native's
    annotate-ALL-hits fan-out cannot smear the tag across collisions. This NEVER writes
    the requirement column and NEVER touches the JSONL sidecar — it is an optional
    metadata surface layered on top of the precise annotate() path.

    Returns {symbol_id, name, key, value, confidence, provenance, author, annotated}.
    Raises if native `annotate` is absent or the name resolves to >1 symbol.
    """
    if not symbol_id or not str(symbol_id).strip():
        raise WickedEstateError("annotate_kv() requires a non-empty resolved symbol_id")
    if not _probe_native_subcommand("annotate", binary=binary):
        raise WickedEstateError(
            "annotate_kv: native `annotate` subcommand not available on this engine; "
            "the requirement write path is the SymbolId-precise annotate()."
        )
    # Map the SymbolId back to its name (read-only intern table) and verify the name
    # is unambiguous so native's name-fan-out cannot tag a collision.
    name = _name_for_symbol_id(db, symbol_id)
    if name is None:
        raise WickedEstateError(
            f"annotate_kv: symbol_id {symbol_id!r} not found in {db}"
        )
    sids = resolve_symbol_id(db, name)
    if len(sids) != 1:
        raise WickedEstateError(
            f"annotate_kv: name {name!r} resolves to {len(sids)} symbols — native "
            "annotate is name-based and would tag ALL of them; refuse to corrupt "
            "precision. Use the SymbolId-precise annotate() for requirements."
        )
    args = ["annotate", name, "--key", key, "--value", value]
    if confidence is not None:
        args += ["--confidence", str(confidence)]
    if provenance is not None:
        args += ["--provenance", provenance]
    if author is not None:
        args += ["--author", author]
    args += _db_args(db)
    _run(args, timeout=DEFAULT_TIMEOUTS["semantics"], binary=binary)
    return {
        "symbol_id": symbol_id,
        "name": name,
        "key": key,
        "value": value,
        "confidence": confidence,
        "provenance": provenance,
        "author": author,
        "annotated": True,
    }


def read_kv(db: str, name: str, binary: str | None = None) -> list:
    """ADJUNCT (read-only): the parsed native `annotations <name>` k/v list —
    [{kind,name,file,line,key,value,confidence,provenance,author}, ...].

    Reads the multi-key store annotate_kv() writes. Returns [] when native
    `annotations` is absent or the name has none. This is the richer-surface read
    companion to annotate_kv(); coverage.py's source of truth remains the JSONL sidecar.
    """
    if not _probe_native_subcommand("annotations", binary=binary):
        return []
    out = _run(
        ["annotations", name] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["semantics"],
        binary=binary,
    )
    return _parse_annotations_show(out)


def nodes_annotated_with(db: str, key: str, value: str | None = None,
                         binary: str | None = None) -> list:
    """BULK READ: the nodes carrying a native annotation `key` (optionally
    `key=value`), via `nodes --annotated-with KEY[=VALUE] --json`. Returns a list
    of node dicts ({name, kind, file, line, signature?}); [] when the native
    `nodes` filter is absent or nothing matches.

    This is the term-aware-naming read seam (ISS-02): one call per confirmed term
    value yields its whole bound node set, so a consumer (domain_graph) can name
    capabilities from the domain_* tags `vocabulary project` wrote WITHOUT a
    per-node round-trip."""
    if not _probe_native_subcommand("nodes", binary=binary):
        return []
    spec = key if value is None else "%s=%s" % (key, value)
    out = _run(
        ["nodes", "--annotated-with", spec, "--json"] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["query"],
        binary=binary,
    )
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):
        data = data.get("nodes", [])
    return data if isinstance(data, list) else []


def _name_for_symbol_id(db: str, symbol_id: str):
    """Reverse the interned SymbolId -> node name (read-only intern-table exception,
    the same `file:{abspath}?mode=ro` URI list_nodes/resolve_symbol_id use). Returns
    the name or None. Used by annotate_kv() to drive native's name-based annotate
    while keeping the SymbolId the precision anchor."""
    db_path = db or DEFAULT_DB
    if not os.path.exists(db_path):
        raise WickedEstateError(f"db not found for symbol-name lookup: {db_path}")
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as e:
        raise WickedEstateError(f"could not open db {db_path} for read: {e}") from e
    try:
        cur = conn.execute(
            "SELECT n.name FROM nodes n JOIN symbols s ON n.symbol = s.sid "
            "WHERE s.sym = ?",
            (symbol_id,),
        )
        row = cur.fetchone()
    except sqlite3.Error as e:
        raise WickedEstateError(f"symbol-name lookup failed: {e}") from e
    finally:
        conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# A–E primitives (cluster / context / fingerprint / changed / correspond).
#
# WF4-VERIFIED (2026-06-14, binary v0.1.3) then RE-VERIFIED (2026-06-15, binary
# v0.1.5 at the wicked-estate release path): the engine ships NATIVE A–E ingredients
# under CORRECTED command names (verified empirically + against the `match cmd`
# dispatch in crates/wicked-estate/src/main.rs). v0.1.5 closed issues
# #2/#3/#4/#5/#6, so correspond/changed-since/fingerprint --content are now native
# and several shims below FLIP to native-first (shim kept only as the fallback):
#
#   B cluster      -> native `clusters [<min_size>] [--json]` (main.rs:742).
#                     REAL community detection (wicked_estate_rank::detect_communities,
#                     union-find over Calls|Imports edges ONLY; file/structural nodes
#                     EXCLUDED — no hub degeneracy). JSON = [[symbol_id,...],...].
#                     ADOPTED native-first (transform to {communities,node_community,
#                     num_communities}; singleton-assign the engine-omitted nodes so
#                     node_community is TOTAL). Shim is the FALLBACK (older engines /
#                     when native can't read the DB, e.g. a hand-built fixture).
#   C context      -> native `context <name> --budget <chars> [--json]` (main.rs:780).
#                     budget_context: PageRank-reachable neighbor NAMES only — NO source
#                     slices, NO ring, NO real-char budget (the `--budget` unit is the
#                     proxy name.len()+file.len()+100). It CANNOT drive the extraction
#                     fan-out (which reads slices + ring depth). Shim STAYS PRIMARY; the
#                     native list is exposed adjunct-only via context_native().
#   D fingerprint  -> native `fingerprint <name>` (main.rs:890). 16-hex IDENTITY hash
#                     over id+name+kind+file+SIGNATURE (NOT body text) — a body edit does
#                     NOT move it. The CI drift gate wants BODY drift, so the sha256
#                     body-hash shim STAYS PRIMARY; native identity-hash is exposed
#                     adjunct-only via fingerprint_native() (+ an additive
#                     `identity_fingerprint` field on single-node fingerprint()).
#   D changed      -> native `changed-since <git-sha> [--json]` (main.rs:913). GIT-diff
#                     of FILE paths since a SHA (file-granular, needs a git repo) — a
#                     DIFFERENT mechanism than the shim's per-symbol fingerprint-baseline
#                     diff. changed() STAYS the fingerprint-baseline diff; the git path is
#                     a SEPARATE adjunct changed_since().
#   A annotate     -> native `annotate <name> --key K --value V [...]` + `annotations
#                     <name>` (main.rs:828/861). REAL multi-key k/v store, but NAME-based
#                     (annotates EVERY search hit — the collision trap). annotate() KEEPS
#                     the SymbolId-precise semantics-field write + the lossless JSONL IP
#                     sidecar; native k/v is exposed adjunct-only via annotate_kv()/
#                     read_kv(), precision-guarded to a single resolved SymbolId.
#   E correspond   -> NATIVE `correspond --db-a A --db-b B [--kind K] [--min-score F]
#                     [--json]` EXISTS in v0.1.5 (main.rs correspond arm). JSON = a list
#                     of {a,b,a_name,b_name,basis,score}; is_correspond_kind HARD-EXCLUDES
#                     File|Import|Variable|Parameter|Field|Constant|TypeAlias|Synthetic.
#                     correspond() is NATIVE-FIRST ONLY WHEN a `kinds` code filter is given
#                     (the merge use case — native default is dominated by README/markdown
#                     nodes; File nodes can never pair). With NO kinds filter the shim STAYS
#                     primary (it alone pairs File/structural nodes). Shim is also the
#                     fallback on any native failure (non-engine DB, parse error).
#
# All shims remain (stdlib-only, built on the existing CLI wrappers + the SAME
# documented read-only intern-table exception `file:{abspath}?mode=ro`, uri=True
# that list_nodes()/resolve_symbol_id() use) as the FALLBACK for older engines and
# native-absent paths. Native `drift` EXISTS but is ESTATE/IaC drift (origin=iac vs
# origin=live resource identity) — it never content-hashes a code node, so
# fingerprint/changed do NOT wrap it.
#
# Native-or-shim dispatch: each function probes the binary ONCE (cached) for the
# CORRECTED native name. cluster() and correspond() (the latter only when a `kinds`
# filter is supplied) use native when present, falling back to the shim if the native
# call errors (e.g. a fixture DB the engine store can't read). changed_since() is the
# native git-diff adjunct (now emits `[]` cleanly on no-delta). context() and the
# body-drift fingerprint()/changed() run the shim primary (their consumers and surviving
# tests depend on shim semantics native does not provide); the native --content body hash
# is exposed adjunct-only via fingerprint_content_native().
# ---------------------------------------------------------------------------
import difflib  # noqa: E402  (kept local to the shim block, stdlib only)
import hashlib  # noqa: E402

# Memo of subcommand availability per resolved binary path: {(bin, sub): bool}.
_SUBCMD_NATIVE_CACHE: dict = {}


def _probe_native_subcommand(sub: str, binary: str | None = None) -> bool:
    """Return True iff `sub` is a NATIVE wicked-estate subcommand (not the usage
    banner fall-through). Cached per (binary, sub). An unrecognized subcommand
    prints the "wicked-estate <ver> — usage:" banner (exit 0); a native one does
    not (it either does work or errors on missing args). We detect the banner.

    This is the runtime native-or-shim selector: if a future engine release adds
    one of these as a real subcommand, the helper transparently switches to it.

    When NO engine binary is resolvable (none configured / on PATH — e.g. CI, or
    any host without the engine), native is simply unavailable: return False so
    the caller falls back to the deterministic stdlib shim, rather than letting
    resolve_binary() raise and crash an otherwise-shim-capable call.
    """
    if binary:
        bin_path = binary
    else:
        try:
            bin_path = resolve_binary()
        except WickedEstateError:
            _SUBCMD_NATIVE_CACHE[(None, sub)] = False
            return False
    key = (bin_path, sub)
    if key in _SUBCMD_NATIVE_CACHE:
        return _SUBCMD_NATIVE_CACHE[key]
    native = False
    try:
        result = subprocess.run(
            [bin_path, sub, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        # The usage banner is the unrecognized-subcommand signature.
        first = combined.strip().splitlines()[0] if combined.strip() else ""
        if "— usage:" not in first and "-- usage:" not in first:
            native = True
    except (subprocess.TimeoutExpired, OSError):
        native = False
    _SUBCMD_NATIVE_CACHE[key] = native
    return native


def _native_json(args, *, timeout: int, binary: str | None = None):
    """Run a native subcommand that emits `--json` and return the parsed JSON.

    Raises WickedEstateError on a non-zero exit (which is how the engine signals a
    DB it cannot open — e.g. a hand-built fixture store) or on unparseable output,
    so native-first callers can catch it and fall back to the shim.
    """
    out = _run(args, timeout=timeout, binary=binary)
    try:
        return json.loads(out)
    except (ValueError, TypeError) as e:
        raise WickedEstateError(
            f"native {args[0] if args else ''} returned unparseable JSON: {e}"
        ) from e


def _read_edges(db: str):
    """Read the graph's edges as (src_symbol_id, tgt_symbol_id, kind, confidence,
    file) tuples via the documented read-only intern-table exception (the SAME
    `file:{abspath}?mode=ro` URI list_nodes/resolve_symbol_id use — never a
    writable handle). `kind` is returned de-quoted (via _dekind). Edge direction
    is source=dependent, target=dependency (FEATURES §1).
    """
    db_path = db or DEFAULT_DB
    if not os.path.exists(db_path):
        raise WickedEstateError(f"db not found for edge enumeration: {db_path}")
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as e:
        raise WickedEstateError(f"could not open db {db_path} for read: {e}") from e
    try:
        cur = conn.execute(
            "SELECT ss.sym, st.sym, e.kind, e.confidence, e.file "
            "FROM edges e "
            "JOIN symbols ss ON e.source = ss.sid "
            "JOIN symbols st ON e.target = st.sid"
        )
        rows = cur.fetchall()
    except sqlite3.Error as e:
        raise WickedEstateError(f"edge-enumeration query failed: {e}") from e
    finally:
        conn.close()
    out = []
    for src, tgt, kind, conf, file_ in rows:
        try:
            conff = float(conf) if conf is not None else 1.0
        except (TypeError, ValueError):
            conff = 1.0
        out.append((src, tgt, _dekind(kind), conff, file_ or ""))
    return out


# ---- B: cluster --------------------------------------------------------------
def _native_clusters_readonly(db_path: str, binary: str | None = None):
    """Run native `clusters 1 --json` against a TEMP COPY of `db_path`, returning the
    parsed list-of-lists, or None when native cannot read the DB (e.g. a hand-built
    fixture store that is not the engine's serialization).

    The native command opens the store read-WRITE; copying first keeps the caller's
    DB byte-identical (the read-side no-mutation contract `test_does_not_mutate_db`
    locks) regardless of what the engine does to its working copy. Any native failure
    (non-engine DB, deserialization error, missing native command) returns None so the
    caller falls back to the shim — native is strictly additive, never a regression.
    """
    import tempfile  # noqa: E402  (stdlib, local to the native copy path)

    tmpd = tempfile.mkdtemp(prefix="we-clusters-ro-")
    try:
        copy_db = os.path.join(tmpd, "g.db")
        try:
            shutil.copyfile(db_path, copy_db)
            # Copy WAL/SHM sidecars too so the engine sees a consistent snapshot.
            for suffix in ("-wal", "-shm"):
                side = db_path + suffix
                if os.path.exists(side):
                    shutil.copyfile(side, copy_db + suffix)
        except OSError:
            return None
        try:
            raw = _native_json(
                ["clusters", "1", "--json", "--db", copy_db],
                timeout=DEFAULT_TIMEOUTS["rank"],
                binary=binary,
            )
        except WickedEstateError:
            return None
        return raw
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _cluster_from_native(db: str, raw, weight: str, min_confidence: float) -> dict:
    """Transform native `clusters --json` ([[symbol_id,...],...]) into the stable
    consumer dict shape {db, weight, min_confidence, communities, node_community,
    num_communities}.

    The engine returns ONLY the detected communities (union-find over Calls|Imports
    edges), EXCLUDING edgeless/structural nodes. To keep the contract the extraction
    loop + §I5 depend on (node_community is TOTAL — every node carries a community),
    we enumerate list_nodes(db) and SINGLETON-assign every node the engine omitted
    (its own symbol_id as its label). Each detected community is labelled by its
    min(sorted member symbol_id) — stable, collision-free (ids are interned).
    `weight`/`min_confidence` are echoed back for API stability only (native has
    neither; see cluster()'s docstring).
    """
    if not isinstance(raw, list):
        raise WickedEstateError(
            f"native clusters returned a non-list payload: {type(raw).__name__}"
        )
    communities: dict = {}
    node_community: dict = {}
    for comm in raw:
        members = sorted(str(s) for s in comm)
        if not members:
            continue
        label = members[0]  # deterministic: min sorted member symbol_id
        communities[label] = members
        for sid in members:
            node_community[sid] = label

    # Singleton-assign every node the engine left out (edgeless/structural/File
    # nodes) so node_community is TOTAL over list_nodes' id set.
    for n in list_nodes(db):
        sid = n["symbol_id"]
        if sid in node_community:
            continue
        communities[sid] = [sid]
        node_community[sid] = sid

    # Stable member order within each community.
    for lab in communities:
        communities[lab].sort()

    return {
        "db": db or DEFAULT_DB,
        "weight": weight,
        "min_confidence": min_confidence,
        "communities": communities,
        "node_community": node_community,
        "num_communities": len(communities),
    }


def cluster(
    db: str,
    weight: str = "calls",
    *,
    min_confidence: float = 0.0,
    prefer: str = "native",
    binary: str | None = None,
) -> dict:
    """Partition the graph into capability communities.

    NATIVE-FIRST (v0.1.5+): when the engine exposes native `clusters` and can read
    the DB, this runs `clusters 1 --json` — REAL community detection
    (wicked_estate_rank::detect_communities: union-find over Calls|Imports edges
    ONLY; file/structural nodes EXCLUDED). The native list-of-lists is transformed
    into the SAME return shape the consumers expect, with engine-omitted nodes
    (edgeless singletons like a standalone function, plus excluded structural/File
    nodes) assigned SINGLETON labels so node_community is TOTAL (every node carries
    a community — §I5 and the extraction loop require it). On the native path `weight`
    / `min_confidence` are SHIM-ONLY knobs (native has neither weighting nor a
    confidence filter): the caller's `weight` is echoed back for API stability and
    `min_confidence` is recorded but NOT applied.

    SHIM FALLBACK (older engines, or when native can't read the DB): deterministic,
    stdlib-only synchronous label propagation over the UNDIRECTED weighted adjacency
    read directly from the `edges` table (read-only intern-table exception).

    `prefer` (default "native") selects which path is authoritative when BOTH are
    available:
      * "native" — native-first; native's real community detection is used whenever
        the engine exposes `clusters` and can read the DB (the `weight` knob is then
        echoed but NOT applied — native has no weighting). This is the right default
        for capability grouping (§I5, and the extraction loop's "calls" mode, which
        is congruent with native's union-find over Calls|Imports).
      * "shim"   — FORCE the deterministic weighted shim, ignoring native even when
        present. Use this when the caller's downstream logic is TUNED to a specific
        `weight` mode's distinct semantics (e.g. the §I3 god-program / cross-cutting
        detector measures cohesion against the SHIM's "data-affinity" file-coupling
        signal — a signal native deliberately drops by excluding structural/file
        nodes). Routing such a consumer through native would silently invert its
        verdict, so it must demand the shim. Keeping BOTH behaviors reachable (rather
        than silently swapping semantics under native-first) is the WF4 reconciliation
        rule: native-first by default, shim-on-demand where weight is load-bearing.

    `weight` modes (SHIM ONLY — native ignores them):
      * "calls"          — only {calls,invokes} edges, weight 1.0 (call-affinity).
      * "confidence"     — every edge weighted by its `confidence` float (collapse
                           the engine's resolution certainty into community
                           strength; heuristic call edges ~0.65 naturally
                           down-weight relative to parsed/contains 1.0).
      * "data-affinity"  — {references,accesses,uses,contains,imports} edges PLUS
                           a small same-file bonus so data-coupled nodes coalesce.

    Edges with confidence < `min_confidence` are dropped (SHIM path only). Isolated
    nodes form singleton communities (kept, never dropped) on BOTH paths.

    KNOWN SHIM-ONLY LIMITATION (fixed on the native path): in the SHIM fallback, on a
    REAL engine index the file/module node emits a `contains` edge to EVERY function
    it holds (a hub), so `weight="confidence"` and `weight="data-affinity"` collapse
    each file into one community ("group by file") — NOT true capability communities;
    only `weight="calls"` yields a real call-affinity partition under the shim. The
    NATIVE path has NO such degeneracy — detect_communities excludes file/structural
    nodes and uses real union-find, so capability grouping is correct regardless of
    `weight`. The extraction loop and §I5 default to "calls" for shim safety; on a
    native-capable engine they transparently get the better partition.

    Returns {db, weight, min_confidence, communities: {label -> [symbol_id,...]},
    node_community: {symbol_id -> label}, num_communities}.
    """
    cfg = _load_config().get("cluster", {}) or {}
    same_file_bonus = float(cfg.get("data_affinity_same_file_bonus", 0.25))
    max_passes = int(cfg.get("max_passes", 20))
    if weight not in ("calls", "confidence", "data-affinity"):
        raise WickedEstateError(
            f"cluster: unknown weight mode {weight!r} "
            "(expected calls|confidence|data-affinity)"
        )
    if prefer not in ("native", "shim"):
        raise WickedEstateError(
            f"cluster: unknown prefer mode {prefer!r} (expected native|shim)"
        )

    # A missing DB must raise WickedEstateError (never a bare sqlite/OSError, and
    # never silently create a DB via a native write-side open). Guard up front so the
    # contract holds on BOTH the native and shim paths.
    db_path = db or DEFAULT_DB
    if not os.path.exists(db_path):
        raise WickedEstateError(f"db not found for clustering: {db_path}")

    # NATIVE-FIRST: probe the CORRECTED name `clusters` (the old code probed
    # `cluster` -> always False). detect_communities filters communities to
    # len >= min_size, so we ask for min_size=1 to surface every real community;
    # the engine still EXCLUDES edgeless/structural nodes, which we re-add as
    # singletons below so node_community stays TOTAL. The native command opens the
    # store READ-WRITE (it can migrate/touch the file), so we run it against a TEMP
    # COPY — leaving the caller's DB byte-identical (the read-side no-mutation
    # contract) and letting a non-engine fixture DB error there, harmlessly, before
    # we fall back to the shim.
    if prefer == "native" and _probe_native_subcommand("clusters", binary=binary):
        raw = _native_clusters_readonly(db_path, binary=binary)
        if raw is not None:
            return _cluster_from_native(db, raw, weight, min_confidence)

    nodes = list_nodes(db)  # symbol_id/name/kind/file
    node_ids = [n["symbol_id"] for n in nodes]
    file_of = {n["symbol_id"]: n["file"] for n in nodes}
    node_set = set(node_ids)

    # Build undirected weighted adjacency keyed by symbol_id.
    adj: dict = {nid: {} for nid in node_ids}

    def _add(a, b, w):
        if a not in node_set or b not in node_set or a == b or w <= 0:
            return
        adj[a][b] = adj[a].get(b, 0.0) + w
        adj[b][a] = adj[b].get(a, 0.0) + w

    for src, tgt, kind, conf, _efile in _read_edges(db):
        if conf < min_confidence:
            continue
        if weight == "calls":
            if kind in _CALL_AFFINITY_EDGE_KINDS:
                _add(src, tgt, 1.0)
        elif weight == "confidence":
            _add(src, tgt, conf)
        else:  # data-affinity
            if kind in _DATA_AFFINITY_EDGE_KINDS:
                _add(src, tgt, 1.0)

    if weight == "data-affinity" and same_file_bonus > 0:
        # Synthetic same-file bonus: nodes sharing a (non-empty) file get a small
        # constant edge so data-coupled nodes coalesce even without an explicit
        # data edge. Grouped by file, deterministic over sorted ids.
        by_file: dict = {}
        for nid in sorted(node_ids):
            f = file_of.get(nid) or ""
            if f:
                by_file.setdefault(f, []).append(nid)
        for f, members in by_file.items():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    _add(members[i], members[j], same_file_bonus)

    # Label propagation: init label = own id; iterate over nodes in STABLE sorted
    # order; each node adopts the highest-summed-weight neighbor label, ties
    # broken by lexicographically-smallest label (fully deterministic — no random
    # tiebreak). Stop when a full pass changes nothing.
    label = {nid: nid for nid in node_ids}
    ordered = sorted(node_ids)
    for _ in range(max(1, max_passes)):
        changed = False
        for nid in ordered:
            neighbors = adj[nid]
            if not neighbors:
                continue
            sums: dict = {}
            for nbr, w in neighbors.items():
                lab = label[nbr]
                sums[lab] = sums.get(lab, 0.0) + w
            # max weight, tie -> smallest label
            best = min(sums.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if best != label[nid]:
                label[nid] = best
                changed = True
        if not changed:
            break

    communities: dict = {}
    for nid in ordered:
        communities.setdefault(label[nid], []).append(nid)
    # Stable: sort members within each community.
    for lab in communities:
        communities[lab].sort()
    return {
        "db": db or DEFAULT_DB,
        "weight": weight,
        "min_confidence": min_confidence,
        "communities": communities,
        "node_community": {nid: label[nid] for nid in node_ids},
        "num_communities": len(communities),
    }


# ---- C: context --------------------------------------------------------------
def context(
    db: str,
    node: str,
    *,
    budget: int | None = None,
    max_hops: int | None = None,
    file: str | None = None,
    kind: str | None = None,
    binary: str | None = None,
) -> dict:
    """Build the bounded, ranked, minimal-sufficient neighborhood around `node`.

    SHIM-PRIMARY (a native `context` exists in v0.1.5 but does NOT serve this
    consumer — see below + the block header). This is the per-node fan-out the
    extraction loop crawls, honoring the SAME crawl.* config the extraction phase
    uses. Defaults: budget = crawl.context_budget_chars (18000), max_hops =
    crawl.max_rings (3).

    Why the shim stays primary even though native `context` exists: native
    budget_context returns ONLY neighbor metadata [{name,kind,file,line}] — NO source
    slices (the extraction loop reads ctx['slices'] to extract the business rule), NO
    ring depth (extract.py keys off ranked_nodes[].ring), and its `--budget` is a
    proxy (name.len()+file.len()+100), NOT real source chars. Native cannot drive the
    extraction fan-out and would break the surviving hard-char-cap-over-real-slices
    contract. Native's bidirectional neighbor NAMES (it includes CALLEES, which this
    blast_radius-DEPENDENTS-only shim omits — see the limitation note) are exposed
    adjunct-only via context_native() for callers that want to enrich the frontier.

    Algorithm:
      1. Seed ring0 = the start node (optionally disambiguated by file/kind).
      2. Expand each ring's frontier via blast_radius() (the transitive-dependents
         fan-out the stable CLI exposes), deduping ring members by (name, file).
      3. Order the accumulated neighborhood by rank() PageRank importance
         (unranked nodes -> score 0.0, sorted stably after ranked ones).
      4. Walk that ranked order pulling a bounded source() slice per node,
         appending until the cumulative char count would exceed `budget`;
         truncate the last slice to fit and stop (HARD char cap — never overshoot).

    Returns {node, budget, max_hops, ring_of: {name->ring}, ranked_nodes,
    slices, chars_used, truncated}.

    KNOWN SHIM LIMITATION: the frontier expands ONLY via blast_radius() (transitive
    DEPENDENTS / callers), so a node's CALLEES (what it itself calls) do not enter the
    neighborhood. For a leaf or downstream node the upward fan-out still reaches its
    chain, but a mid-chain node's own dependencies are omitted. The char-cap / ranking
    is exact. Native `context` (budget_context) DOES traverse bidirectionally
    (callees + callers), so its neighbor NAMES — surfaced via context_native() — can
    seed this shim's frontier to close the missing-callee gap WITHOUT changing this
    function's slice-bearing return shape.
    """
    crawl_cfg = _load_config().get("crawl", {}) or {}
    if budget is None:
        budget = int(crawl_cfg.get("context_budget_chars", 18000))
    if max_hops is None:
        max_hops = int(crawl_cfg.get("max_rings", 3))

    # NOTE: native `context` EXISTS in v0.1.5 (probe -> True) but is intentionally
    # NOT routed through here — it returns neighbor names only (no slices/ring/real
    # budget) and cannot serve the extraction loop. The shim is the live path
    # unconditionally; native is reachable via context_native() as an adjunct.

    # Resolve / seed the start node. If the caller disambiguates by file/kind we
    # confirm it resolves (raises on no-match), but the text CLI is name-keyed so
    # the crawl proceeds by name + (name,file) dedup.
    seed = query(db, node, binary=binary)
    if file is not None or kind is not None:
        sids = resolve_symbol_id(db, node, file=file, kind=kind)
        if not sids:
            raise WickedEstateError(
                f"context: node {node!r} not found "
                f"(file={file!r} kind={kind!r})"
            )

    # Pick the seed member matching the disambiguators if given, else first match.
    seed_member = None
    for m in seed["matches"]:
        if file is not None and os.path.basename(m["file"]) != os.path.basename(file):
            continue
        if kind is not None and m["kind"].lower() != kind.strip().strip('"').lower():
            continue
        seed_member = m
        break
    if seed_member is None:
        if not seed["matches"]:
            raise WickedEstateError(f"context: node {node!r} not found in {db}")
        seed_member = seed["matches"][0]

    # Bounded ring crawl. Dedup by (name, file). ring_of records first-seen ring.
    def _key(m):
        return (m["name"], os.path.basename(m["file"] or ""))

    ring_of: dict = {}
    members: dict = {}  # key -> node dict
    frontier = [seed_member]
    seed_key = _key(seed_member)
    ring_of[seed_key] = 0
    members[seed_key] = seed_member

    for hop in range(1, max_hops + 1):
        next_frontier = []
        for fm in frontier:
            br = blast_radius(db, fm["name"], binary=binary)
            for dep in br["dependents"]:
                k = _key(dep)
                if k in members:
                    continue
                members[k] = dep
                ring_of[k] = hop
                next_frontier.append(dep)
        if not next_frontier:
            break
        frontier = next_frontier

    # Rank the whole graph once; join scores onto the crawled set by (name, file).
    ranked = rank(db, binary=binary)
    score_by_key = {}
    for r in ranked:
        score_by_key[(r["name"], os.path.basename(r["file"] or ""))] = r.get("score", 0.0)

    ranked_nodes = []
    for k, m in members.items():
        ranked_nodes.append({
            "kind": m["kind"],
            "name": m["name"],
            "file": m["file"],
            "line": m["line"],
            "score": score_by_key.get(k, 0.0),
            "ring": ring_of[k],
        })
    # Importance order: score desc, then stable by (ring, name, file).
    ranked_nodes.sort(key=lambda n: (-n["score"], n["ring"], n["name"], n["file"]))

    # Walk ranked order pulling bounded source slices under the HARD char cap.
    slices = []
    chars_used = 0
    truncated = False
    for rn in ranked_nodes:
        if chars_used >= budget:
            truncated = True
            break
        try:
            body = source(db, rn["name"], binary=binary).get("body", "") or ""
        except WickedEstateError:
            body = ""
        if not body.strip():
            continue  # skip empties (source not stored) — don't count their chars
        remaining = budget - chars_used
        if len(body) > remaining:
            body = body[:remaining]
            truncated = True
            slices.append({"name": rn["name"], "file": rn["file"], "body": body})
            chars_used += len(body)
            break
        slices.append({"name": rn["name"], "file": rn["file"], "body": body})
        chars_used += len(body)

    return {
        "node": node,
        "budget": budget,
        "max_hops": max_hops,
        "ring_of": {f"{name}|{file_}": ring for (name, file_), ring in ring_of.items()},
        "ranked_nodes": ranked_nodes,
        "slices": slices,
        "chars_used": chars_used,
        "truncated": truncated,
    }


def context_native(db: str, name: str, budget: int = 4096, binary: str | None = None) -> list:
    """ADJUNCT (read-only): the NATIVE `context <name> --budget <chars> --json`
    neighbor list — [{name, kind, file, line}, ...] of the PageRank-reachable
    bidirectional neighbors (callees + callers + FTS name-hits) that fit native's
    proxy char budget, seed EXCLUDED.

    This is NOT routed through context() (which keeps its slice-bearing shim shape).
    It exists so callers can harvest native's CALLEE names — the one direction the
    blast_radius-DEPENDENTS-only shim omits — e.g. to enrich the shim's frontier seed.
    Returns [] when native `context` is absent (older engine) so callers degrade
    gracefully. The `budget` unit is native's proxy (name.len()+file.len()+100), NOT
    real source chars.
    """
    if not _probe_native_subcommand("context", binary=binary):
        return []
    raw = _native_json(
        ["context", name, "--budget", str(int(budget)), "--json"] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["query"],
        binary=binary,
    )
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append({
            "name": item.get("name"),
            "kind": item.get("kind"),
            "file": item.get("file"),
            "line": item.get("line"),
        })
    return out


# ---- D: fingerprint + changed ------------------------------------------------
_UNHASHABLE_MARKERS = ("source not stored", "(source not stored)")


def _normalize_body(body: str) -> str:
    """Normalize a source slice for stable hashing: normalize line endings to
    '\\n' and strip trailing whitespace per line, so cross-platform / whitespace-
    only churn doesn't false-trigger drift."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(ln.rstrip() for ln in text.split("\n"))


def _body_is_unhashable(body: str) -> bool:
    b = (body or "").strip().lower()
    return (not b) or any(m in b for m in _UNHASHABLE_MARKERS)


def _hash_body(body: str) -> str:
    return hashlib.sha256(_normalize_body(body).encode("utf-8")).hexdigest()


def _parse_native_fingerprint_line(line: str):
    """Parse one native `fingerprint` output line:

        `afbcf9fbe321a05e  Function beta (chain.py:4)`

    Returns {fingerprint, kind, name, file, line} or None. '(not indexed)  <name>'
    and 'no symbol found ...' lines return None (no usable hash). The 16-hex token
    is whitespace-separated from the trailing `Kind name (file:line)`.
    """
    s = line.strip()
    if not s or s.startswith("(not indexed)") or s.startswith("no symbol found"):
        return None
    parts = s.split(None, 1)
    if len(parts) != 2:
        return None
    fp, rest = parts[0], parts[1]
    # A native identity hash is lowercase hex; guard against banner/other lines.
    if not fp or any(c not in "0123456789abcdef" for c in fp.lower()):
        return None
    node = _parse_node_line(rest)
    if node is None:
        return None
    node["fingerprint"] = fp
    return node


def fingerprint_native(
    db: str,
    node: str,
    *,
    file: str | None = None,
    kind: str | None = None,
    binary: str | None = None,
) -> dict:
    """ADJUNCT (read-only): the NATIVE `fingerprint <name>` IDENTITY hash for a
    SINGLE resolved symbol.

    Returns {symbol_id, name, file, kind, fingerprint:<16hex>, kind_of:'identity'}.
    Resolves name -> SymbolId first (refusing ambiguous names like the shim) so the
    native name-fan-out (one line per search hit) cannot smear the result across
    collisions. The 16-hex hash covers id+name+kind+file+SIGNATURE (NOT body) — use it
    for "is this the same symbol slot" (rename/move/signature tracking), NOT body drift
    (that is fingerprint()'s sha256 body-hash). Raises if native `fingerprint` is absent.
    """
    if not _probe_native_subcommand("fingerprint", binary=binary):
        raise WickedEstateError(
            "fingerprint_native: native `fingerprint` subcommand not available on "
            "this engine; use the body-hash fingerprint() instead."
        )
    sids = resolve_symbol_id(db, node, file=file, kind=kind)
    if not sids:
        raise WickedEstateError(
            f"fingerprint_native: node {node!r} not found (file={file!r} kind={kind!r})"
        )
    if len(sids) > 1 and not (file or kind):
        raise WickedEstateError(
            f"fingerprint_native: node {node!r} resolves to {len(sids)} symbols; "
            "disambiguate with --file/--kind."
        )
    symbol_id = sids[0]
    out = _run(
        ["fingerprint", node] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["source"],
        binary=binary,
    )
    # Native prints one line per search hit; pick the line whose (file,kind) match
    # the resolved node (when disambiguators are given), else the first hashable line.
    chosen = None
    for line in out.splitlines():
        parsed = _parse_native_fingerprint_line(line)
        if parsed is None:
            continue
        if file is not None and os.path.basename(parsed["file"] or "") != os.path.basename(file):
            continue
        if kind is not None and parsed["kind"].lower() != kind.strip().strip('"').lower():
            continue
        chosen = parsed
        break
    if chosen is None:
        raise WickedEstateError(
            f"fingerprint_native: no hashable native fingerprint line for {node!r}"
        )
    return {
        "symbol_id": symbol_id,
        "name": chosen["name"],
        "file": chosen["file"],
        "kind": chosen["kind"],
        "fingerprint": chosen["fingerprint"],
        "kind_of": "identity",
    }


def fingerprint_content_native(
    db: str,
    node: str,
    *,
    file: str | None = None,
    kind: str | None = None,
    binary: str | None = None,
) -> dict:
    """ADJUNCT (read-only): the NATIVE `fingerprint <name> --content` 16-hex BODY-drift
    hash for a SINGLE resolved symbol (the #4 fix — CWD-safe: the engine resolves the
    span against the stored `indexed_root` meta, so the same body yields the same hash
    from any working directory).

    Unlike the identity hash (fingerprint_native), this MOVES on a body edit — it is the
    native counterpart to the sha256 body-hash in fingerprint(). Returns {symbol_id, name,
    file, kind, fingerprint:<16hex>, kind_of:'content'}. Resolves name -> SymbolId first
    (refusing ambiguous names) so the native name-fan-out cannot smear the result across
    collisions. Raises if native `fingerprint` is absent or no hashable line is produced
    (e.g. a `(cannot read ...)` source).
    """
    if not _probe_native_subcommand("fingerprint", binary=binary):
        raise WickedEstateError(
            "fingerprint_content_native: native `fingerprint` subcommand not available "
            "on this engine; use the body-hash fingerprint() instead."
        )
    sids = resolve_symbol_id(db, node, file=file, kind=kind)
    if not sids:
        raise WickedEstateError(
            f"fingerprint_content_native: node {node!r} not found "
            f"(file={file!r} kind={kind!r})"
        )
    if len(sids) > 1 and not (file or kind):
        raise WickedEstateError(
            f"fingerprint_content_native: node {node!r} resolves to {len(sids)} "
            "symbols; disambiguate with --file/--kind."
        )
    symbol_id = sids[0]
    out = _run(
        ["fingerprint", node, "--content"] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["source"],
        binary=binary,
    )
    # Native prints one line per search hit; reuse the identity-line parser (same
    # `<hex>  Kind name (file:line)` format) and pick the (file,kind)-matching line.
    chosen = None
    for line in out.splitlines():
        parsed = _parse_native_fingerprint_line(line)
        if parsed is None:
            continue
        if file is not None and os.path.basename(parsed["file"] or "") != os.path.basename(file):
            continue
        if kind is not None and parsed["kind"].lower() != kind.strip().strip('"').lower():
            continue
        chosen = parsed
        break
    if chosen is None:
        raise WickedEstateError(
            f"fingerprint_content_native: no hashable native content line for {node!r}"
        )
    return {
        "symbol_id": symbol_id,
        "name": chosen["name"],
        "file": chosen["file"],
        "kind": chosen["kind"],
        "fingerprint": chosen["fingerprint"],
        "kind_of": "content",
    }


def fingerprint(
    db: str,
    node: str | None = None,
    *,
    file: str | None = None,
    kind: str | None = None,
    binary: str | None = None,
) -> dict:
    """Stable content fingerprint(s) of code node(s) — the CODE-drift primitive.

    SHIM-PRIMARY for the BODY-drift gate (a native `fingerprint` exists in v0.1.5 but
    is an IDENTITY hash that does NOT move on a body edit — see below). The fingerprint
    is the sha256 of the NORMALIZED source SLICE TEXT (NOT the byte-span offsets, which
    shift on reformatting). Keyed by the interned symbol_id (stable identity, ADR-002)
    — never by name (collides) nor line (moves).

    Single-node mode (`node` given): returns {db, symbol_id, name, file, kind,
    fingerprint, unhashable}. When native `fingerprint` is present an ADDITIVE
    `identity_fingerprint` (the native 16-hex identity hash) is included alongside the
    body `fingerprint` — never replacing it. Disambiguate by file/kind when needed.

    Baseline mode (`node` is None): iterates list_nodes(db) (optionally filtered
    by `kind`) and returns {db, fingerprints: {symbol_id -> sha256}, count}. The
    CI drift gate persists this map to JSON (default config fingerprint
    .baseline_path) and later diffs it with changed().

    Why the shim body-hash stays the drift primitive: native `fingerprint <name>` is a
    16-hex IDENTITY hash over id+name+kind+file+SIGNATURE (NOT body text) — empirically
    a body edit (42 -> 777, signature unchanged) leaves the native hash UNCHANGED. The
    drift gate's whole purpose is body-drift detection, which only the sha256 body-hash
    captures. Native's value is COMPLEMENTARY ("is this the same symbol slot" across
    rename/move/signature change) and is exposed via fingerprint_native() + the additive
    identity_fingerprint field.

    NAME-COLLISION SAFETY (ISS-20, FIXED): baseline mode no longer keys the body cache by
    NAME. The prior shim fetched source(db, name) — which CONCATENATES every match into one
    blob — so the 21 carddemo MAIN-PARA nodes shared one fingerprint and a single edit moved
    ALL of them in changed() (over-report on collision-heavy estates). Baseline mode now
    splits source() into PER-MATCH bodies via source_by_match() and attributes each node's
    own file slice to its interned symbol_id, so every collision-name node gets a DISTINCT
    fingerprint. The SINGLE-node shim path was already exact (resolve_symbol_id + refuse on
    ambiguity). The NATIVE identity-fingerprint (fingerprint_native()) remains collision-free
    by construction; the body-drift baseline is now collision-free too.
    """
    # NOTE: native `fingerprint` EXISTS in v0.1.5 (probe -> True) but is an IDENTITY
    # hash that does NOT move on a body edit, so it cannot be the drift primitive. The
    # shim body-hash runs unconditionally; native is exposed via fingerprint_native()
    # and the additive identity_fingerprint field below.
    if node is not None:
        # Resolve to a single symbol_id (disambiguate if asked).
        sids = resolve_symbol_id(db, node, file=file, kind=kind)
        if not sids:
            raise WickedEstateError(
                f"fingerprint: node {node!r} not found "
                f"(file={file!r} kind={kind!r})"
            )
        if len(sids) > 1 and not (file or kind):
            raise WickedEstateError(
                f"fingerprint: node {node!r} resolves to {len(sids)} symbols; "
                "disambiguate with --file/--kind."
            )
        symbol_id = sids[0]
        # Per-match split so a file-disambiguated single node hashes ONLY ITS OWN
        # slice, never the concatenation of every collision (ISS-20). source() alone
        # returns ALL matches' bodies joined — using that would alias collisions.
        recs = source_by_match(db, node, binary=binary)
        # Pick the record whose file/kind matches the resolved node.
        meta = None
        for r in recs:
            if file is not None and os.path.basename(r["file"]) != os.path.basename(file):
                continue
            if kind is not None and r["kind"].lower() != kind.strip().strip('"').lower():
                continue
            meta = r
            break
        if meta is None and recs:
            meta = recs[0]
        body = (meta or {}).get("body", "")
        unhashable = _body_is_unhashable(body)
        result = {
            "db": db or DEFAULT_DB,
            "symbol_id": symbol_id,
            "name": (meta or {}).get("name", node),
            "file": (meta or {}).get("file", file or ""),
            "kind": (meta or {}).get("kind", kind or ""),
            "fingerprint": "" if unhashable else _hash_body(body),
            "unhashable": unhashable,
        }
        # ADDITIVE: when native `fingerprint` is present, attach the identity hash
        # alongside (never replacing) the body hash. Best-effort: a native miss
        # leaves the key absent so the body-drift contract is never weakened.
        if _probe_native_subcommand("fingerprint", binary=binary):
            try:
                nat = fingerprint_native(
                    db, node, file=file, kind=kind, binary=binary
                )
                result["identity_fingerprint"] = nat.get("fingerprint")
            except WickedEstateError:
                pass
        return result

    # Baseline mode: full map over all (optionally kind-filtered) nodes, keyed by
    # the interned symbol_id (ISS-20: collision-free per node).
    #
    # The PRIOR shim keyed the body cache by NAME and used source(db, name), which
    # CONCATENATES every match's body into one blob — so all 21 carddemo MAIN-PARA
    # nodes shared one fingerprint and a single edit moved ALL of them (over-report).
    # The fix: split source() into per-match bodies (source_by_match) keyed by the
    # node's FILE, then attribute the right body to each symbol_id by its own file.
    kinds = [kind] if kind else None
    nodes = list_nodes(db, kinds=kinds)
    fingerprints: dict = {}
    # Per name: a {file_basename -> body} map from the split per-match output, plus
    # an ordered fallback list so a single-match (no header) name still resolves.
    by_name_cache: dict = {}
    for n in nodes:
        name = n["name"]
        nfile = os.path.basename(n.get("file") or "")
        if name not in by_name_cache:
            per_file: dict = {}
            ordered: list = []
            try:
                for rec in source_by_match(db, name, binary=binary):
                    rbody = rec.get("body", "")
                    ordered.append(rbody)
                    per_file.setdefault(os.path.basename(rec.get("file") or ""), rbody)
            except WickedEstateError:
                pass
            by_name_cache[name] = (per_file, ordered)
        per_file, ordered = by_name_cache[name]
        # Resolve THIS node's body: prefer its exact file slice (collision-safe);
        # fall back to the lone match when the name has exactly one body (no
        # header / file mismatch on a singleton name).
        if nfile and nfile in per_file:
            body = per_file[nfile]
        elif len(ordered) == 1:
            body = ordered[0]
        elif nfile in per_file:
            body = per_file[nfile]
        else:
            body = ""
        if _body_is_unhashable(body):
            fingerprints[n["symbol_id"]] = ""
        else:
            fingerprints[n["symbol_id"]] = _hash_body(body)
    return {
        "db": db or DEFAULT_DB,
        "fingerprints": fingerprints,
        "count": len(fingerprints),
    }


def changed(
    db: str,
    since: str,
    *,
    kind: str | None = None,
    binary: str | None = None,
) -> dict:
    """Diff the current code-node fingerprint map against a persisted baseline.

    Companion to fingerprint() — powers the CI drift gate over CODE nodes
    (orthogonal to the estate `drift` IaC axis). `since` is a PATH to a baseline
    JSON written by a prior fingerprint() baseline run; it is either the raw
    {symbol_id -> sha256} map OR the full fingerprint() baseline dict (we accept
    both shapes). The shim does NOT own storage — it diffs two maps.

    This is DELIBERATELY the fingerprint-baseline diff, NOT native `changed-since`:
    native `changed-since <git-sha>` is a GIT-diff of FILE paths (file-granular,
    over-reports every symbol in a touched file, needs a git repo + a SHA, NO content
    comparison) — a different mechanism answering a different question. The CI body-drift
    gate wants this per-SYMBOL content diff, so changed() is NOT routed to native; the
    git path is available as the SEPARATE adjunct changed_since(db, sha).

    Returns {since, added, removed, moved, unchanged_count}:
      * added   — symbol_ids present now but not in the baseline.
      * removed — symbol_ids in the baseline but gone now.
      * moved   — [{symbol_id, name, old, new}] where the fingerprint differs for
                  a symbol_id present in both ("moved" = content changed).
      * unchanged_count — symbol_ids whose fingerprint is identical.
    """
    if not os.path.exists(since):
        raise WickedEstateError(f"changed: baseline not found: {since}")
    try:
        with open(since, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise WickedEstateError(f"changed: could not load baseline {since}: {e}") from e
    if isinstance(raw, dict) and "fingerprints" in raw and isinstance(raw["fingerprints"], dict):
        baseline = raw["fingerprints"]
    elif isinstance(raw, dict):
        baseline = raw
    else:
        raise WickedEstateError(
            f"changed: baseline {since} is not a fingerprint map / dict"
        )

    current = fingerprint(db, node=None, kind=kind, binary=binary)["fingerprints"]
    name_of: dict = {n["symbol_id"]: n["name"] for n in list_nodes(db)}

    base_keys = set(baseline)
    cur_keys = set(current)
    added = sorted(cur_keys - base_keys)
    removed = sorted(base_keys - cur_keys)
    moved = []
    unchanged = 0
    for sid in sorted(base_keys & cur_keys):
        if baseline[sid] != current[sid]:
            moved.append({
                "symbol_id": sid,
                "name": name_of.get(sid, ""),
                "old": baseline[sid],
                "new": current[sid],
            })
        else:
            unchanged += 1
    return {
        "since": since,
        "added": added,
        "removed": removed,
        "moved": moved,
        "unchanged_count": unchanged,
    }


def changed_since(db: str, sha: str, binary: str | None = None) -> dict:
    """ADJUNCT (read-only): the NATIVE `changed-since <git-sha> --json` symbol list.

    Returns {since_sha, symbols: [{name,kind,file,line}, ...], count} — every symbol in
    any FILE that `git diff --name-only <sha>..HEAD` reports changed (file-granular).
    This is a SEPARATE function from changed() (git SHA input + git mechanism, NOT a
    fingerprint-baseline diff); it does NOT replace it. Requires a git repo and the
    native `changed-since` subcommand; raises when absent. v0.1.5: with `--json` a
    no-delta run now emits `[]` natively (the old human "no files changed" sentinel is
    gone), so the no-delta case returns a clean empty list — it never raises on parse.
    """
    if not _probe_native_subcommand("changed-since", binary=binary):
        raise WickedEstateError(
            "changed_since: native `changed-since` subcommand not available on this "
            "engine; use the fingerprint-baseline changed() instead."
        )
    raw = _native_json(
        ["changed-since", sha, "--json"] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS.get("changed_since", 120),
        binary=binary,
    )
    symbols = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            symbols.append({
                "name": item.get("name"),
                "kind": item.get("kind"),
                "file": item.get("file"),
                "line": item.get("line"),
            })
    return {"since_sha": sha, "symbols": symbols, "count": len(symbols)}


# ---- D: drift (legacy-graph digest drift gate, ISS-12) ----------------------
def _digest_checksum(digest_text: str) -> str:
    """SHA-256 of a canonical digest body — the same checksum the manifest stores
    for the `legacy-graph` artifact (which is `sha256(legacy-graph.digest.txt)`).

    The digest is already canonicalized (volatile lines stripped, edge kinds
    sorted) so this hash is deterministic across machines/re-runs."""
    return hashlib.sha256(digest_text.encode("utf-8")).hexdigest()


def _digest_lines(digest_text: str) -> list:
    """Non-empty stripped lines of a canonical digest, for line-level diffing.

    A canonical digest is::

        nodes=84 edges=101 files=1
        edge "calls" = 18
        edge "contains" = 83

    so each line is either the node/edge/file count header or one ``edge "k" = N``
    count — exactly the granular facts a drift diff reports as changed.
    """
    return [ln.strip() for ln in (digest_text or "").splitlines() if ln.strip()]


def _resolve_baseline_digest(against: str) -> str:
    """Resolve the `against` baseline argument to canonical digest TEXT.

    Accepts (first applicable wins):
      * a PATH to a digest file (e.g. `.anti-legacy/legacy-graph.digest.txt`) —
        read, then canonicalize (idempotent if already canonical);
      * RAW digest text (contains `nodes=`) — canonicalize directly;
      * a bare 64-hex SHA-256 checksum — returned as-is (compare-by-checksum only;
        line-level `changed` detail is unavailable for a bare hash).

    Returns the canonical digest text, OR the bare checksum string when `against`
    is a checksum (the caller detects this via the 64-hex shape). Raises if a
    given file path does not exist.
    """
    a = (against or "").strip()
    if not a:
        raise WickedEstateError("drift: --against baseline is empty")
    # Bare SHA-256 checksum (64 lowercase hex) — checksum-only comparison.
    if len(a) == 64 and all(c in "0123456789abcdef" for c in a.lower()):
        return a.lower()
    # A path to a digest file.
    if os.path.exists(a) and os.path.isfile(a):
        try:
            with open(a, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            raise WickedEstateError(f"drift: could not read baseline digest {a}: {e}") from e
        return _canonicalize_stats(raw)
    # Raw digest text passed inline.
    if "nodes=" in a:
        return _canonicalize_stats(a)
    raise WickedEstateError(
        f"drift: --against {a!r} is neither an existing digest file, raw digest "
        "text (must contain 'nodes='), nor a 64-hex SHA-256 checksum"
    )


def drift(db: str, against: str, binary: str | None = None) -> dict:
    """Detect drift between the legacy CODE graph and its registered digest seam.

    THE §I6 KEYSTONE (ISS-12): rule annotations are written against a graph whose
    deterministic stats digest (`.anti-legacy/legacy-graph.digest.txt`) was
    checksummed and registered as the `legacy-graph` evidence at survey time. If
    the underlying code changes and the graph is re-indexed WITHOUT re-running
    extraction, the annotations are stale — and that staleness is checksum-
    detectable: the current digest no longer matches the registered one.

    This recomputes the CURRENT canonical digest from `db` (stats_digest, which
    strips volatile git/staleness/size lines and sorts edge kinds) and compares
    it — both by SHA-256 checksum AND line-by-line — against the `against`
    baseline (a digest-file path, raw digest text, or a bare 64-hex checksum;
    see _resolve_baseline_digest). It COMPOSES the existing digest primitive — it
    does NOT re-implement or fake the comparison.

    Returns::

        {
          "db": ...,
          "against": <the baseline arg>,
          "baseline_kind": "digest" | "checksum",
          "drift": <bool>,                 # True when the checksums differ
          "current_checksum": <sha256>,
          "baseline_checksum": <sha256>,
          "changed": [ {"baseline": <line|None>, "current": <line|None>}, ... ],
        }

    `changed` is the symmetric line diff of the two canonical digests (added,
    removed, or count-changed facts). When the baseline is a bare checksum the
    per-line detail is unavailable, so `changed` is `[]` and the verdict rests on
    the checksum alone. A True `drift` means: re-run extraction (re-annotate the
    changed nodes) before the annotations can be trusted — the gate BLOCKS.
    """
    current_digest = stats_digest(db, binary=binary)
    current_checksum = _digest_checksum(current_digest)

    resolved = _resolve_baseline_digest(against)
    is_checksum = (
        len(resolved) == 64 and all(c in "0123456789abcdef" for c in resolved)
    )
    if is_checksum:
        baseline_checksum = resolved
        baseline_kind = "checksum"
        changed: list = []
    else:
        baseline_checksum = _digest_checksum(resolved)
        baseline_kind = "digest"
        base_lines = _digest_lines(resolved)
        cur_lines = _digest_lines(current_digest)
        changed = _diff_digest_lines(base_lines, cur_lines)

    return {
        "db": db or DEFAULT_DB,
        "against": against,
        "baseline_kind": baseline_kind,
        "drift": current_checksum != baseline_checksum,
        "current_checksum": current_checksum,
        "baseline_checksum": baseline_checksum,
        "changed": changed,
    }


def _diff_digest_lines(base_lines: list, cur_lines: list) -> list:
    """Symmetric line diff of two canonical digests -> a list of
    {baseline, current} change records.

    A canonical digest line is a single fact (the `nodes=.. edges=.. files=..`
    header, or one `edge "k" = N`). We key each fact by its STEM:
      * the count header keys on the literal "nodes/edges/files" tag,
      * an edge line keys on its kind (the quoted name),
    so a count CHANGE pairs (baseline vs current) on the same stem rather than
    showing as an unrelated add+remove. Facts only in the baseline -> removed
    (current=None); only in current -> added (baseline=None). Deterministically
    ordered by stem.
    """
    def _stem(line: str) -> str:
        s = line.strip()
        if s.startswith("nodes=") and "edges=" in s:
            return "\x00counts"  # the single node/edge/file header line
        if s.startswith('edge "') and '"' in s[6:]:
            return 'edge:' + s.split('"', 2)[1]
        return s  # fallback: the whole line is its own stem

    base_by = {_stem(l): l for l in base_lines}
    cur_by = {_stem(l): l for l in cur_lines}
    out = []
    for stem in sorted(set(base_by) | set(cur_by)):
        b = base_by.get(stem)
        c = cur_by.get(stem)
        if b != c:
            out.append({"baseline": b, "current": c})
    return out


# ---- E: correspond (+ thin semantic wrapper) --------------------------------
def semantic(db: str, query_text: str, binary: str | None = None) -> dict:
    """Thin wrapper of the NATIVE `semantic <query>` free-text search.

    Returns {query, matches: [{similarity, kind, name, file, line}, ...]}. Only
    yields results when the DB was indexed with `--embeddings` (empirically 0
    matches otherwise — "embeddings may not have been populated yet"). It is a
    free-text-query-keyed search, NOT symbol-to-symbol, so correspond() treats it
    as a weak best-effort tie-breaker only (default use_semantic=False).
    """
    out = _run(
        ["semantic", query_text] + _db_args(db),
        timeout=DEFAULT_TIMEOUTS["semantic"],
        binary=binary,
    )
    matches = []
    for line in out.splitlines():
        s = line.strip()
        # Format: "[0.83] Function name (file:line)"
        if not s.startswith("["):
            continue
        try:
            sim = float(s[1:s.index("]")])
        except (ValueError, IndexError):
            continue
        rest = s[s.index("]") + 1:].strip()
        node = _parse_node_line(rest)
        if node is not None:
            node["similarity"] = sim
            matches.append(node)
    return {"query": query_text, "matches": matches}


def _out_degree_map(db: str) -> dict:
    """Per-symbol_id out-degree (count of outgoing edges) from the read-only
    edges table — a coarse structural arity signal for correspond()."""
    deg: dict = {}
    for src, _tgt, _kind, _conf, _file in _read_edges(db):
        deg[src] = deg.get(src, 0) + 1
    return deg


def _correspond_native(db_a, db_b, *, kinds, min_score, binary):
    """NATIVE arm of correspond(): invoke `correspond --db-a A --db-b B --kind K
    --min-score F --json` and map the engine's list of
    {a,b,a_name,b_name,basis,score} onto the public pair shape.

    Native takes a SINGLE `--kind` value; `kinds` is the public iterable, single-element
    for the merge use case (multi-kind callers fall back to the shim). The engine already
    does greedy dedup + sort + min-score filtering, so we pass min_score straight through
    and do not re-filter. Flags are TOP-LEVEL (parsed before the subcommand) and use
    --db-a/--db-b NOT --db, so we do NOT append _db_args(db). Raises WickedEstateError on
    any native failure (so correspond() falls through to the shim).
    """
    kind = next(iter(kinds))
    raw = _native_json(
        [
            "correspond",
            "--db-a", db_a or DEFAULT_DB,
            "--db-b", db_b or DEFAULT_DB,
            "--kind", str(kind),
            "--min-score", str(min_score),
            "--json",
        ],
        timeout=DEFAULT_TIMEOUTS["cross_graph"],
        binary=binary,
    )
    if not isinstance(raw, list):
        raise WickedEstateError(
            f"native correspond returned non-list JSON: {type(raw).__name__}"
        )
    pairs = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pairs.append({
            "a_symbol_id": item.get("a"),
            "a_name": item.get("a_name"),
            "b_symbol_id": item.get("b"),
            "b_name": item.get("b_name"),
            # native omits kind; it equals the requested filter.
            "kind": _dekind(str(kind)),
            "score": round(float(item.get("score", 0.0)), 6),
            # preserve the public signals block; native gives `basis`, not the shim's
            # 3 numeric signals — keep the keys present (shape) and carry basis through.
            "signals": {
                "name_ratio": None,
                "kind_match": 1.0,
                "degree_sim": None,
                "basis": item.get("basis", ""),
            },
        })
    return {
        "db_a": db_a or DEFAULT_DB,
        "db_b": db_b or DEFAULT_DB,
        "pairs": pairs,
        "count": len(pairs),
    }


def correspond(
    db_a: str,
    db_b: str,
    *,
    kinds=None,
    min_score: float = 0.0,
    use_semantic: bool = False,
    binary: str | None = None,
) -> dict:
    """Enumerate likely cross-repo node correspondences (the merge-alignment unit
    for multi-repo semantic-join).

    NATIVE-FIRST when a `kinds` code filter is given (v0.1.5 ships native `correspond
    --db-a A --db-b B --kind K --min-score F --json`); the SHIM is the fallback when
    `kinds` is None (only the shim pairs File/structural nodes — native's
    is_correspond_kind hard-excludes File|Import|Variable|Parameter|Field|Constant|
    TypeAlias|Synthetic) and on any native failure (non-engine DB / parse error).

    Shim path — structural candidate generation, optional semantic re-rank, stdlib +
    existing helpers:
      1. Enumerate both sides via list_nodes (optional `kinds` filter passes
         straight through).
      2. Candidate pairs: same de-quoted kind AND (exact case-insensitive name
         equality [primary] OR difflib name ratio >= name_ratio_threshold
         [secondary fuzzy tier]).
      3. Score each pair structurally: name_ratio (difflib) blended with
         kind_match (1.0/0.0) and a degree-similarity signal (out-degree from the
         read-only edges table — within-repo, since cross-repo edges are not
         resolved by the engine).
      4. If use_semantic, refine with a best-effort native `semantic <name>`
         tie-breaker (gated — embeddings are absent by default).
      5. Keep pairs >= min_score, greedily assign 1:1 by descending score
         (deterministic: sort by (-score, a_symbol_id, b_symbol_id)).

    Returns {db_a, db_b, pairs: [{a_symbol_id, a_name, b_symbol_id, b_name, kind,
    score, signals:{name_ratio, kind_match, degree_sim, semantic?}}], count}. The
    native path preserves this shape — it carries the engine's `basis` in
    signals.basis and synthesizes the numeric signals (name_ratio/degree_sim None,
    kind_match 1.0) the engine does not return.
    """
    cfg = _load_config().get("correspond", {}) or {}
    name_ratio_threshold = float(cfg.get("name_ratio_threshold", 0.85))
    if min_score == 0.0:
        min_score = float(cfg.get("min_score", 0.0))
    if not use_semantic:
        use_semantic = bool(cfg.get("use_semantic", False))

    # NATIVE-FIRST, kinds-gated: native `correspond` (v0.1.5) pairs CODE nodes only
    # (is_correspond_kind excludes File/structural), so it serves the merge use case
    # ONLY when a code `kinds` filter is supplied. With no filter the shim is the sole
    # path (it alone pairs File/structural nodes). Any native failure falls through to
    # the shim (non-engine fixture DB, parse error) — never a raise.
    if kinds and _probe_native_subcommand("correspond", binary=binary):
        try:
            return _correspond_native(
                db_a, db_b, kinds=kinds, min_score=min_score, binary=binary
            )
        except WickedEstateError:
            pass

    nodes_a = list_nodes(db_a, kinds=kinds)
    nodes_b = list_nodes(db_b, kinds=kinds)
    deg_a = _out_degree_map(db_a)
    deg_b = _out_degree_map(db_b)

    # Index b-side by de-quoted kind for candidate pruning.
    by_kind_b: dict = {}
    for nb in nodes_b:
        by_kind_b.setdefault(_dekind(nb["kind"]), []).append(nb)

    candidates = []
    for na in nodes_a:
        ka = _dekind(na["kind"])
        a_name_l = na["name"].lower()
        for nb in by_kind_b.get(ka, ()):
            b_name_l = nb["name"].lower()
            if a_name_l == b_name_l:
                name_ratio = 1.0
            else:
                name_ratio = difflib.SequenceMatcher(None, a_name_l, b_name_l).ratio()
                if name_ratio < name_ratio_threshold:
                    continue
            kind_match = 1.0  # same de-quoted kind by construction
            da = deg_a.get(na["symbol_id"], 0)
            db_ = deg_b.get(nb["symbol_id"], 0)
            if da == 0 and db_ == 0:
                degree_sim = 1.0
            else:
                degree_sim = 1.0 - abs(da - db_) / float(max(da, db_, 1))
            score = 0.6 * name_ratio + 0.25 * kind_match + 0.15 * degree_sim
            signals = {
                "name_ratio": round(name_ratio, 6),
                "kind_match": kind_match,
                "degree_sim": round(degree_sim, 6),
            }
            candidates.append({
                "a_symbol_id": na["symbol_id"],
                "a_name": na["name"],
                "b_symbol_id": nb["symbol_id"],
                "b_name": nb["name"],
                "kind": ka,
                "score": score,
                "signals": signals,
            })

    # Optional semantic tie-breaker (best-effort; embeddings may be absent).
    if use_semantic and candidates:
        sem_cache: dict = {}
        for c in candidates:
            qname = c["a_name"]
            if qname not in sem_cache:
                try:
                    sem_cache[qname] = semantic(db_b, qname, binary=binary)["matches"]
                except WickedEstateError:
                    sem_cache[qname] = []
            sem_sim = 0.0
            for m in sem_cache[qname]:
                if m["name"].lower() == c["b_name"].lower():
                    sem_sim = m.get("similarity", 0.0)
                    break
            c["signals"]["semantic"] = round(sem_sim, 6)
            # Blend a small semantic nudge without overpowering structure.
            c["score"] = 0.85 * c["score"] + 0.15 * sem_sim

    # Greedy deterministic 1:1 assignment by descending score.
    candidates.sort(key=lambda c: (-c["score"], c["a_symbol_id"], c["b_symbol_id"]))
    used_a = set()
    used_b = set()
    pairs = []
    for c in candidates:
        if c["score"] < min_score:
            continue
        if c["a_symbol_id"] in used_a or c["b_symbol_id"] in used_b:
            continue
        used_a.add(c["a_symbol_id"])
        used_b.add(c["b_symbol_id"])
        c = dict(c)
        c["score"] = round(c["score"], 6)
        pairs.append(c)

    return {
        "db_a": db_a or DEFAULT_DB,
        "db_b": db_b or DEFAULT_DB,
        "pairs": pairs,
        "count": len(pairs),
    }


# ---------------------------------------------------------------------------
# Thin CLI for skill recipes: `run.py wicked_estate <cmd> ...`
# ---------------------------------------------------------------------------
def _print_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="wicked_estate",
        description="anti-legacy / wicked-estate integration helper.",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help="graph db path (default %(default)s)")
    # `--db` is accepted EITHER before the subcommand (top-level, above) OR after
    # it (the skills / sibling integration call `index <path> --db <db>`). The
    # parent below adds the post-subcommand form to every subparser; its
    # SUPPRESS default means it only overrides `args.db` when actually supplied,
    # so the top-level default DEFAULT_DB still applies when neither is given.
    db_parent = argparse.ArgumentParser(add_help=False)
    db_parent.add_argument("--db", default=argparse.SUPPRESS, help="graph db path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("resolve-binary", help="print the resolved binary path", parents=[db_parent])

    p = sub.add_parser("index", help="index one or more source paths into --db", parents=[db_parent])
    p.add_argument("paths", nargs="+")
    p.add_argument("--no-embeddings", action="store_true",
                   help="skip the embeddings pass (default: on per config.embeddings)")
    p.add_argument("--fresh", action="store_true",
                   help="delete the db first for a full (non-incremental) re-parse; use for an authoritative survey")

    sub.add_parser("stats", help="print parsed stats JSON", parents=[db_parent])
    sub.add_parser("stats-digest", help="print the deterministic checksummable digest", parents=[db_parent])

    p = sub.add_parser("query", help="query a node by name", parents=[db_parent])
    p.add_argument("name")

    p = sub.add_parser("blast-radius", help="dependents of a node", parents=[db_parent])
    p.add_argument("name")

    p = sub.add_parser("source", help="source slice for a node", parents=[db_parent])
    p.add_argument("name")

    sub.add_parser("rank", help="PageRank worklist order", parents=[db_parent])

    p = sub.add_parser("cross-graph", help="federated search/blast across repos", parents=[db_parent])
    p.add_argument("name")
    p.add_argument("--dbs", nargs="+", required=True, help="list of db paths")

    p = sub.add_parser("resolve-symbol-id", help="name -> full SymbolId(s)", parents=[db_parent])
    p.add_argument("name")
    p.add_argument("--file", default=None)
    p.add_argument("--kind", default=None)

    p = sub.add_parser("list-nodes", help="enumerate graph nodes (denominator source)", parents=[db_parent])
    p.add_argument("--kinds", nargs="+", default=None, help="filter to these simple kinds")

    p = sub.add_parser("annotate", help="write annotation onto a node (by SymbolId)", parents=[db_parent])
    p.add_argument("symbol_id")
    p.add_argument("--requirement", default=None)
    p.add_argument("--description", default=None)
    p.add_argument("--validated", choices=["true", "false"], default="false")
    p.add_argument("--rule-object", default=None,
                   help="JSON object merged losslessly into the overlay row "
                        "(status, confidence, verification, statement, provenance, "
                        "parity, legacy_components, risk_reason). Coverage/§I5 read these.")

    p = sub.add_parser("annotate-kv", help="adjunct: native multi-key k/v annotation (precision-guarded)", parents=[db_parent])
    p.add_argument("symbol_id")
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--confidence", type=float, default=None)
    p.add_argument("--provenance", default=None)
    p.add_argument("--author", default=None)

    p = sub.add_parser("read-kv", help="adjunct: read native k/v annotations for a name", parents=[db_parent])
    p.add_argument("name")

    p = sub.add_parser("read-semantics", help="show native annotation for a SymbolId", parents=[db_parent])
    p.add_argument("symbol_id")

    p = sub.add_parser("by-requirement", help="reverse lookup nodes by requirement string", parents=[db_parent])
    p.add_argument("req")

    p = sub.add_parser("cluster", help="partition the graph into capability communities (native-first, shim fallback)", parents=[db_parent])
    p.add_argument("--weight", choices=["calls", "confidence", "data-affinity"], default="calls")
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--prefer", choices=["native", "shim"], default="native",
                   help="native-first (default) or force the weighted shim when weight is load-bearing")

    p = sub.add_parser("context", help="bounded ranked neighborhood around a node (shim, slice-bearing)", parents=[db_parent])
    p.add_argument("node")
    p.add_argument("--budget", type=int, default=None, help="hard char cap (default crawl.context_budget_chars)")
    p.add_argument("--max-hops", type=int, default=None, help="ring ceiling (default crawl.max_rings)")
    p.add_argument("--file", default=None)
    p.add_argument("--kind", default=None)

    p = sub.add_parser("context-native", help="adjunct: native bidirectional neighbor names (no slices)", parents=[db_parent])
    p.add_argument("name")
    p.add_argument("--budget", type=int, default=4096, help="native proxy char budget")

    p = sub.add_parser("fingerprint", help="stable sha256 of node source slice(s) — body-drift primitive", parents=[db_parent])
    p.add_argument("node", nargs="?", default=None, help="omit for the full baseline map")
    p.add_argument("--file", default=None)
    p.add_argument("--kind", default=None)

    p = sub.add_parser("fingerprint-native", help="adjunct: native 16-hex fingerprint for one symbol (identity, or --content body-drift)", parents=[db_parent])
    p.add_argument("node")
    p.add_argument("--file", default=None)
    p.add_argument("--kind", default=None)
    p.add_argument("--content", action="store_true",
                   help="native body-drift hash (CWD-safe, #4) instead of the identity hash")

    p = sub.add_parser("changed", help="diff current fingerprints vs a baseline json (body-drift)", parents=[db_parent])
    p.add_argument("since", help="path to the baseline fingerprint json")
    p.add_argument("--kind", default=None)

    p = sub.add_parser("changed-since", help="adjunct: native git-diff symbols since a SHA", parents=[db_parent])
    p.add_argument("sha", help="git SHA to diff HEAD against")

    p = sub.add_parser("drift", help="detect legacy-graph digest drift vs a registered baseline (exit 2 on drift)", parents=[db_parent])
    p.add_argument("--against", required=True,
                   help="baseline: a digest-file path (e.g. .anti-legacy/legacy-graph.digest.txt), raw digest text, or a 64-hex sha256 checksum")

    p = sub.add_parser("correspond", help="enumerate cross-repo node correspondences (native-first when --kinds given, shim fallback)", parents=[db_parent])
    p.add_argument("db_a")
    p.add_argument("db_b")
    p.add_argument("--kinds", nargs="+", default=None)
    p.add_argument("--min-score", type=float, default=0.0)
    p.add_argument("--use-semantic", action="store_true")

    p = sub.add_parser("semantic", help="native free-text embedding search", parents=[db_parent])
    p.add_argument("query")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "resolve-binary":
            sys.stdout.write(resolve_binary() + "\n")
        elif args.cmd == "index":
            _print_json(index([(p, p) for p in args.paths], db=args.db,
                              embeddings=(False if args.no_embeddings else None),
                              fresh=args.fresh))
        elif args.cmd == "stats":
            _print_json(stats(args.db))
        elif args.cmd == "stats-digest":
            sys.stdout.write(stats_digest(args.db))
        elif args.cmd == "query":
            _print_json(query(args.db, args.name))
        elif args.cmd == "blast-radius":
            _print_json(blast_radius(args.db, args.name))
        elif args.cmd == "source":
            _print_json(source(args.db, args.name))
        elif args.cmd == "rank":
            _print_json(rank(args.db))
        elif args.cmd == "cross-graph":
            _print_json(cross_graph(args.name, args.dbs))
        elif args.cmd == "resolve-symbol-id":
            _print_json(resolve_symbol_id(args.db, args.name, file=args.file, kind=args.kind))
        elif args.cmd == "list-nodes":
            _print_json(list_nodes(args.db, kinds=args.kinds))
        elif args.cmd == "annotate":
            _rule_obj = json.loads(args.rule_object) if args.rule_object else None
            _print_json(
                annotate(
                    args.db,
                    args.symbol_id,
                    requirement=args.requirement,
                    description=args.description,
                    validated=(args.validated == "true"),
                    rule_object=_rule_obj,
                )
            )
        elif args.cmd == "annotate-kv":
            _print_json(
                annotate_kv(
                    args.db,
                    args.symbol_id,
                    args.key,
                    args.value,
                    confidence=args.confidence,
                    provenance=args.provenance,
                    author=args.author,
                )
            )
        elif args.cmd == "read-kv":
            _print_json(read_kv(args.db, args.name))
        elif args.cmd == "read-semantics":
            _print_json(read_semantics(args.db, args.symbol_id))
        elif args.cmd == "by-requirement":
            _print_json(by_requirement(args.db, args.req))
        elif args.cmd == "cluster":
            _print_json(cluster(args.db, weight=args.weight, min_confidence=args.min_confidence, prefer=args.prefer))
        elif args.cmd == "context":
            _print_json(
                context(
                    args.db,
                    args.node,
                    budget=args.budget,
                    max_hops=args.max_hops,
                    file=args.file,
                    kind=args.kind,
                )
            )
        elif args.cmd == "context-native":
            _print_json(context_native(args.db, args.name, budget=args.budget))
        elif args.cmd == "fingerprint":
            _print_json(fingerprint(args.db, args.node, file=args.file, kind=args.kind))
        elif args.cmd == "fingerprint-native":
            _fp_native = fingerprint_content_native if args.content else fingerprint_native
            _print_json(_fp_native(args.db, args.node, file=args.file, kind=args.kind))
        elif args.cmd == "changed":
            _print_json(changed(args.db, args.since, kind=args.kind))
        elif args.cmd == "changed-since":
            _print_json(changed_since(args.db, args.sha))
        elif args.cmd == "drift":
            _verdict = drift(args.db, args.against)
            _print_json(_verdict)
            # Exit code 2 on drift so CI / the gatekeeper check BLOCKS without
            # parsing JSON (0 = no drift, 2 = drift, 1 = helper error above).
            return 2 if _verdict["drift"] else 0
        elif args.cmd == "correspond":
            _print_json(
                correspond(
                    args.db_a,
                    args.db_b,
                    kinds=args.kinds,
                    min_score=args.min_score,
                    use_semantic=args.use_semantic,
                )
            )
        elif args.cmd == "semantic":
            _print_json(semantic(args.db, args.query))
        else:  # pragma: no cover - argparse enforces choices
            parser.error(f"unknown command {args.cmd!r}")
    except WickedEstateError as e:
        sys.stderr.write(f"wicked_estate: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
