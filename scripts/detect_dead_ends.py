#!/usr/bin/env python3
"""Dead-end / isolated-node detection over the wicked-estate code graph.

REWIRED (WF1 §H/§I): this script no longer reads the deleted
`.anti-legacy/legacy_graph.json` intermediate. The estate IS the graph — all
structure now comes from the `wicked-estate` engine via the
`scripts/wicked_estate.py` helper (CLI-backed: `query` / `blast-radius` /
node-list), one DB per source app under `.anti-legacy/graphs/<app>.db`.

What it still does (unchanged business logic, new data source):
  * COBOL programs with no incoming program-to-program CALL (in-degree-0 over
    the estate's `calls` edges, derived from `blast-radius` dependents) that are
    nonetheless reachable via JCL / CICS-CSD-BMS / MQ are surfaced as
    modernization-decision questions (they are NOT truly dead — they are
    batch/online/async entry points).
  * Java interfaces that are implemented by a class but referenced nowhere else
    ("isolated interfaces") are surfaced as a preserve-or-collapse decision.

Each detected dead-end is emitted in-band on stdout (DEAD_END_QUESTION) for the
survey skill to present; this replaces the retired event_hub.db registration.
"""
import os
import sys
import json

# Make the sibling helper importable whether this script is run from the repo
# root, the workspace, or via the run.py dispatcher (same scripts/ dir).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

CONFIG_PATH = ".anti-legacy/config.json"
GRAPHS_DIR = ".anti-legacy/graphs"

# Track emitted question ids so the same dead-end isn't reported twice in a run.
_emitted_questions = set()

# Node kinds the engine prints for a callable COBOL program / generic module.
_PROGRAM_KINDS = {"module", "program"}
# Node kinds that represent a Java/OO type.
_CLASS_KINDS = {"class", "struct"}
_INTERFACE_KINDS = {"interface"}
# File extensions whose nodes are JCL-domain (not program-to-program callers).
_JCL_EXTS = (".jcl",)


def _load_helper():
    """Import the wicked_estate helper. Kept behind a function so tests can
    inject a fake module via dependency injection (see run_detection)."""
    import wicked_estate as we  # noqa: F401  (resolved at call time)
    return we


def emit_question(q_id, text, q_type="single_choice", options=None):
    """Print a detected modernization-decision question to stdout so survey
    can present it in-band. Replaces the legacy event_hub.db registration."""
    if q_id in _emitted_questions:
        return
    _emitted_questions.add(q_id)

    print(f"DEAD_END_QUESTION: {q_id}")
    print(f"  type: {q_type}")
    print(f"  question: {text}")
    if options:
        print("  options:")
        for opt in options:
            print(f"    - {opt}")


def find_references_in_files(dir_path, query, extensions):
    """Filesystem cross-reference scan: which JCL/CSD/BMS files name `query`.

    The estate graph already resolves COBOL<->JCL EXEC PGM edges, but this scan
    keeps the original behaviour of naming the *files* a program is wired into
    so the generated question can cite them. Unchanged from the JSON era.
    """
    matched_files = []
    query_upper = query.upper()
    if not dir_path or not os.path.exists(dir_path):
        return matched_files

    for root, _dirs, files in os.walk(dir_path):
        for file in files:
            if any(file.lower().endswith(ext) for ext in extensions):
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        if query_upper in f.read().upper():
                            matched_files.append(path)
                except Exception:
                    pass
    return matched_files


def _norm_kind(kind):
    """Normalize a kind label to a bare lowercase token usable in the kind sets.

    The helper hands back kind strings in three encodings depending on the call:
      * CLI display form from `query`/`blast_radius` matches: ``Module``,
        ``File``, ``Interface``, and estate kinds as ``Other("step")``.
      * DB-verbatim form from `list_nodes`: ``"module"`` (literal JSON quotes),
        and estate kinds as ``{"other":"step"}``.
    This reduces all of them to the bare token (``module``, ``step``, ...).
    """
    if not kind:
        return ""
    k = str(kind).strip()
    # Estate object form: {"other":"step"} -> step
    if k.startswith("{") and "other" in k.lower():
        try:
            obj = json.loads(k)
            if isinstance(obj, dict) and "other" in obj:
                return str(obj["other"]).strip().lower()
        except (ValueError, TypeError):
            pass
    k = k.lower()
    # CLI Other("...") wrapper -> inner token
    if k.startswith("other(") and k.endswith(")"):
        return k[6:-1].strip().strip("\"'")
    # DB-verbatim JSON-quoted simple kind: "module" -> module
    return k.strip('"').strip("'")


def _is_jcl_node(node):
    """True if this dependent node lives in a JCL file (a batch reference, not a
    program-to-program COBOL call)."""
    f = (node.get("file") or "").lower()
    return f.endswith(_JCL_EXTS)


def _dependents_of(info):
    """Pull the dependent-node list out of a parsed blast-radius dict, tolerant
    of the helper's exact key naming (`dependents` is the documented key;
    `callers`/`nodes` are accepted as synonyms)."""
    if not isinstance(info, dict):
        return []
    for key in ("dependents", "callers", "nodes"):
        val = info.get(key)
        if isinstance(val, list):
            return val
    return []


def _own_file_of(we, db, name, info=None):
    """Resolve the queried node's own source file.

    The helper's `blast_radius` result does NOT carry the queried node's file
    (only its dependents), so we resolve it via `query` and take the first
    match. A dict `info` carrying a `file`/`source_file` key (older/alternate
    helper shape) is honoured first to avoid the extra round-trip."""
    if isinstance(info, dict):
        f = info.get("file") or info.get("source_file")
        if f:
            return f
    try:
        q = we.query(db, name)
    except Exception:
        return None
    matches = q.get("matches", []) if isinstance(q, dict) else []
    # Prefer a non-File match (the program/interface itself) for the file.
    for m in matches:
        if _norm_kind(m.get("kind")) != "file":
            return m.get("file")
    if matches:
        return matches[0].get("file")
    return None


def _same_node(a_file, a_name, dep):
    """Is dependent `dep` the node itself (its own File/Module self-reference)?"""
    return (dep.get("name") == a_name) and (
        os.path.basename((dep.get("file") or "")).lower()
        == os.path.basename((a_file or "")).lower()
    )


def program_callers(we, db, prog):
    """Return the set of OTHER COBOL program/module nodes that depend on `prog`
    via a real program-to-program relationship.

    Derived from `wicked-estate blast-radius`: its dependent list is every
    symbol that reaches `prog` over any edge kind. We keep only dependents that
    are (a) a program/class/interface kind, (b) not `prog`'s own self-node, and
    (c) not in a JCL file (JCL EXEC PGM refs are handled by the separate
    cross-reference scan, not counted as program callers). The remainder are the
    genuine in-degree-bearing callers — empty set == in-degree-0 == uncalled.
    """
    info = we.blast_radius(db, prog)
    dependents = _dependents_of(info)
    own_file = _own_file_of(we, db, prog, info)

    callers = []
    callable_kinds = _PROGRAM_KINDS | _CLASS_KINDS | _INTERFACE_KINDS
    for dep in dependents:
        kind = _norm_kind(dep.get("kind"))
        if kind not in callable_kinds:
            # File / step / field / import nodes are structural, not callers.
            continue
        if _is_jcl_node(dep):
            continue
        if _same_node(own_file, prog, dep):
            continue
        callers.append(dep)
    return callers


def list_program_nodes(we, db):
    """All callable COBOL-program / module nodes in the estate DB.

    Uses the helper's node enumeration (the same node-list coverage.py consumes
    for its denominator). Filters to program kinds and drops JCL-domain nodes
    (JCL steps/files are not COBOL programs)."""
    nodes = we.list_nodes(db, kinds=sorted(_PROGRAM_KINDS))
    progs = []
    for n in nodes:
        if _norm_kind(n.get("kind")) not in _PROGRAM_KINDS:
            continue
        if _is_jcl_node(n):
            continue
        progs.append(n)
    return progs


def list_interface_nodes(we, db):
    """All interface nodes in the estate DB."""
    nodes = we.list_nodes(db, kinds=sorted(_INTERFACE_KINDS))
    return [n for n in nodes if _norm_kind(n.get("kind")) in _INTERFACE_KINDS]


def interface_external_refs(we, db, iface):
    """Dependents of an interface that are NOT one of its implementing classes.

    The estate follows `implements` edges, so an interface's blast-radius
    dependents are its implementer classes (+ their File nodes). An interface is
    "isolated" when its only non-self, non-File dependents are those implementers
    — i.e. nothing else references the type. We approximate "implementer" as a
    class whose source declares `implements <Interface>`; any other class
    dependent is an external reference that disqualifies isolation.

    Returns (implementer_names, external_ref_names).
    """
    info = we.blast_radius(db, iface)
    dependents = _dependents_of(info)
    own_file = _own_file_of(we, db, iface, info)

    implementers = []
    external_refs = []
    for dep in dependents:
        kind = _norm_kind(dep.get("kind"))
        if kind not in _CLASS_KINDS:
            continue  # File / interface-self nodes don't count.
        if _same_node(own_file, iface, dep):
            continue
        if _class_implements(we, db, dep, iface):
            implementers.append(dep.get("name"))
        else:
            external_refs.append(dep.get("name"))
    return implementers, external_refs


def _class_implements(we, db, class_node, iface_name):
    """Best-effort: does `class_node`'s source declare `implements <iface>`?

    Reads the class source slice via the helper (`wicked-estate source`). If the
    slice is unavailable we conservatively treat the dependent as an implementer
    (the original logic assumed implements-only edges for strategy interfaces),
    so a missing source never turns an implementer into a false external ref.
    """
    try:
        src = we.source(db, class_node.get("name"))
    except Exception:
        return True
    body = ""
    if isinstance(src, dict):
        body = src.get("source") or src.get("body") or ""
    elif isinstance(src, str):
        body = src
    if not body:
        return True
    return f"implements {iface_name}".lower() in body.lower() or (
        ("implements" in body.lower()) and (iface_name.lower() in body.lower())
    )


def _app_db_path(app_name):
    return os.path.join(GRAPHS_DIR, f"{app_name}.db")


def analyze_app(we, app_name, app_path, db_path):
    """Run dead-end detection for a single source app against its estate DB."""
    print(f"Analyzing app '{app_name}' for dead ends...")

    # --- COBOL: programs with no incoming program-to-program call ---
    uncalled_cobol_progs = []
    try:
        programs = list_program_nodes(we, db_path)
    except Exception as e:
        programs = []
        print(f"  (skipping COBOL program scan for '{app_name}': {e})")

    for node in programs:
        name = node.get("name")
        if not name:
            continue
        try:
            callers = program_callers(we, db_path, name)
        except Exception:
            callers = []
        if not callers:
            uncalled_cobol_progs.append(name)

    if uncalled_cobol_progs:
        # 1. JCL references
        jcl_referenced = {}
        for prog in uncalled_cobol_progs:
            matches = find_references_in_files(app_path, prog, [".jcl"])
            if matches:
                jcl_referenced[prog] = [os.path.basename(m) for m in matches]

        if jcl_referenced:
            prog_list = ", ".join(jcl_referenced.keys())
            jcl_list = ", ".join(sorted(set(sum(jcl_referenced.values(), []))))
            q_id = f"DEAD_END_COBOL_JCL_{app_name.upper().replace('-', '_')}"
            text = (
                f"We detected COBOL programs in application '{app_name}' "
                f"({prog_list}) that have no incoming program-to-program calls, "
                f"but are referenced in JCL batch scripts ({jcl_list}). Should "
                f"these JCL steps be modernized into scheduled Spring Batch jobs "
                f"in the target system?"
            )
            options = [
                "Yes, translate JCL steps to Spring Batch jobs (Recommended)",
                "No, these are obsolete and can be discarded",
                "Keep them as standalone executable jar files",
            ]
            emit_question(q_id, text, options=options)

        # 2. CICS CSD / BMS references
        csd_referenced = {}
        for prog in uncalled_cobol_progs:
            matches = find_references_in_files(app_path, prog, [".csd", ".bms"])
            if matches:
                csd_referenced[prog] = [os.path.basename(m) for m in matches]

        if csd_referenced:
            prog_list = ", ".join(csd_referenced.keys())
            csd_list = ", ".join(sorted(set(sum(csd_referenced.values(), []))))
            q_id = f"DEAD_END_COBOL_CICS_{app_name.upper().replace('-', '_')}"
            text = (
                f"The programs ({prog_list}) in application '{app_name}' are "
                f"defined in CICS system definition or screen files ({csd_list}) "
                f"but have no incoming COBOL calls. Should these online "
                f"transactions/screens be modernized as REST API endpoints in the "
                f"target Credit Card Service?"
            )
            options = [
                "Yes, expose as REST APIs with a modern UI (Recommended)",
                "No, these online functions are no longer required",
                "Merge them with the core Card Demo transactions",
            ]
            emit_question(q_id, text, options=options)

        # 3. MQ Series references in source code
        mq_referenced = []
        for prog in uncalled_cobol_progs:
            src_content = _program_source(we, db_path, app_path, prog)
            if src_content and any(
                mq_call in src_content.upper()
                for mq_call in ["MQOPEN", "MQGET", "MQPUT", "MQCONN"]
            ):
                mq_referenced.append(prog)

        if mq_referenced:
            prog_list = ", ".join(mq_referenced)
            q_id = f"DEAD_END_COBOL_MQ_{app_name.upper().replace('-', '_')}"
            text = (
                f"The programs ({prog_list}) in application '{app_name}' handle MQ "
                f"queue messaging but have no incoming COBOL calls. How should this "
                f"asynchronous MQ interface be modernized?"
            )
            options = [
                "Expose them as Spring Boot REST APIs and JMS/ActiveMQ listeners (Recommended)",
                "Expose only as REST APIs",
                "These MQ queue handlers are obsolete",
            ]
            emit_question(q_id, text, options=options)

    # --- Java: isolated interfaces (implemented but referenced nowhere else) ---
    try:
        interfaces = list_interface_nodes(we, db_path)
    except Exception as e:
        interfaces = []
        print(f"  (skipping interface scan for '{app_name}': {e})")

    for node in interfaces:
        iface_name = node.get("name")
        if not iface_name:
            continue
        try:
            implementers, external_refs = interface_external_refs(
                we, db_path, iface_name
            )
        except Exception:
            implementers, external_refs = [], []
        # Isolated == implemented by >=1 class AND referenced by nothing else.
        if implementers and not external_refs:
            simple_name = iface_name.split(".")[-1]
            q_id = f"DEAD_END_JAVA_INTERFACE_{simple_name.upper()}"
            text = (
                f"The Java interface '{iface_name}' is isolated in legacy "
                f"application '{app_name}' (it is implemented by a class but never "
                f"referenced elsewhere). Should we preserve this interface in the "
                f"target Spring Boot codebase?"
            )
            options = [
                "Yes, preserve it for clean design/extensibility (Recommended)",
                "No, simplify and use only concrete class",
                "Skip/Ignore",
            ]
            emit_question(q_id, text, options=options)


def _program_source(we, db, app_path, prog):
    """Source text for a program: prefer the engine's source slice, fall back to
    reading the file from disk (so MQ detection works even if the slice is
    unavailable)."""
    try:
        src = we.source(db, prog)
        if isinstance(src, dict):
            body = src.get("source") or src.get("body")
            if body:
                return body
        elif isinstance(src, str) and src:
            return src
    except Exception:
        pass
    # Disk fallback: locate the program's source file by name under app_path.
    if app_path and os.path.exists(app_path):
        for root, _dirs, files in os.walk(app_path):
            for file in files:
                stem = os.path.splitext(file)[0]
                if stem.upper() == prog.upper():
                    path = os.path.join(root, file)
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            return f.read()
                    except Exception:
                        return None
    return None


def run_detection(config, we=None):
    """Core entry point (testable). Iterates source apps from config and runs
    detection against each app's estate DB via the helper `we`."""
    if we is None:
        we = _load_helper()

    source_apps = config.get("source_apps", [])
    for app in source_apps:
        app_name = app.get("name")
        app_path = app.get("path")
        if not app_name:
            continue
        db_path = _app_db_path(app_name)
        if not os.path.exists(db_path):
            print(
                f"Estate graph for '{app_name}' not found at {db_path}; "
                f"run survey to index it. Skipping."
            )
            continue
        analyze_app(we, app_name, app_path, db_path)

    print("Dead-end analysis complete.")


def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"Required {CONFIG_PATH} not found.")
        return
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    try:
        run_detection(config)
    except ImportError as e:
        print(
            "wicked_estate helper unavailable; the estate graph cannot be "
            f"queried ({e}). Ensure scripts/wicked_estate.py is present."
        )


if __name__ == "__main__":
    main()
