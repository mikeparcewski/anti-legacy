#!/usr/bin/env python3
"""vocabulary.py — the anti-legacy domain-vocabulary BOOTSTRAP MINER + PROJECTOR.

Two dispatched forms:

  * BOOTSTRAP (mine): `run.py vocabulary --db <app>.db [--config ...]
    [--out .anti-legacy/vocabulary.json] [--min-freq N]`.
  * PROJECT (bind):   `run.py vocabulary project --db <app>.db [--vocab ...]`
    — write each CONFIRMED glossary term back onto the code graph as a native
    `domain_*` k/v annotation so the ENGINE'S domain resolution is term-aware
    (see below). The glossary stays the MEANING source; the engine gets the
    term->node BINDINGS.

THE GLOSSARY IS SMALL ON PURPOSE — READ IT DIRECTLY
---------------------------------------------------
vocabulary.json is the human-governable GLOSSARY (term -> meaning/aliases/
slang/lifecycle), NOT a where-used index. It does NOT duplicate the engine:
by default each record carries only `freq` (the true recurrence count) and
`mined_from` (which miner proposed it) — NO per-term SymbolId provenance list.
That keeps the whole file comfortably under the agent Read cap, so agents and
humans just Read it; there is no sidecar query/lookup seam to learn (an earlier
substring-`lookup` over the file was retired — querying a parallel copy of the
file is exactly the parallel-engine smell this codebase avoids).

PROVENANCE + CATEGORIZATION + DOMAIN RESOLUTION ARE OWNED BY wicked-estate
-------------------------------------------------------------------------
Anything beyond "what does this term MEAN" is a code-graph question — ask the
engine, do not store a parallel copy here:
  * where-used / evidence -> `run.py wicked_estate query <token>` (or `rank`)
  * categorization / domain grouping -> `run.py wicked_estate cluster ...`
  * domain resolution of a node/rule -> the `domain_*` annotations `project`
    writes, read back via `wicked_estate read-kv` / `by-requirement`.
The bootstrap mined SymbolIds only to PROPOSE terms; it does not persist them.
`project` then binds the CONFIRMED terms onto their grounding nodes as native
annotations (`domain_entity` / `domain_action` / `domain_abbrev` = <canonical>),
so the engine's own `cluster` and `by-requirement` become term-aware — domains
resolve through the graph, not a sidecar. (Escape hatch: set
config.vocabulary.max_sources_per_term to a positive N to inline up to N
representative SymbolIds per term for a self-contained glossary — default 0 =
none, engine owns it.)

WHAT IT IS (and is NOT)
-----------------------
This is a FREQUENCY MINER over CONFIG-SELECTED graph kinds. It reads the
REAL wicked-estate code graph and emits the *candidate* domain vocabulary it
finds there. It NEVER coins a definition and NEVER classifies by name shape:
there is no `name.startswith('CB')` heuristic anywhere in this file (that
approach is explicitly banned — it lost all data when tried). Every term it
proposes is grounded in graph node SymbolIds; the MEANING (the `definition`)
is authored later by the agent against the vocabulary skill.

Three miners, each driven by a config-selected kind set (config.coverage.*):

  * ENTITIES  — domain nouns. From `estate_behavior_kinds` (db2_table,
    cics_program, step: each estate object names a domain entity verbatim)
    PLUS the leading token-cluster of `field`-kind names (copybook/record
    field roots: ACCT, CARD, CUST, TRAN, ...). term_type=entity.
  * ACTIONS   — domain verbs. From `behavior_kinds` (function/module): the
    leading token of each paragraph/program name AFTER dropping pure-numeric
    COBOL sequence prefixes (2000-, 0000-). term_type=action.
  * ABBREVS   — short recurring tokens (<= MAX_ABBREV_LEN chars) across
    field/variable names, frequency-ranked. The EXPANSION is left BLANK
    (guardrail c: propose, don't coin). term_type=abbreviation.

The structural `field`/`variable` kinds used by the entity/abbrev miners are
read from config.coverage.structural_kinds (intersected with what actually
names domain nouns); no kind is hardcoded as a domain signal — the config is
the single source of which kinds mean what.

CRITICAL HELPER GOTCHA (honored here)
-------------------------------------
`wicked_estate.list_nodes(db, --kinds=...)` filters SIMPLE kinds only
(function/module/field/variable). The estate object-kinds — db2_table,
cics_program, step — come back ONLY when kinds=None and the caller normalizes
each kind with `_dekind` itself. So this miner ALWAYS calls
`list_nodes(db, kinds=None)` once and buckets by normalized kind in Python.
A caller who passes `--kinds db2_table` to the helper gets [].

OUTPUT (.anti-legacy/vocabulary.json)
-------------------------------------
`{ "terms": [ <record> ], "meta": {...} }`. Every bootstrap-emitted record:
status=proposed, verification=unverified, blank definition, `freq` = the true
recurrence count, `mined_from` = the miner that proposed it, `sources[]` empty
(engine owns where-used) unless config.vocabulary.max_sources_per_term opts in.
The run-level metadata (db/ts/min_freq) lives ONCE in the doc `meta`, not per
record. Validates against schemas/vocabulary.schema.json. See that schema + the
vocabulary skill (skills/vocabulary/SKILL.md) for the two orthogonal axes
(status = is the term real; verification = is the meaning proven).

IDEMPOTENT / RE-RUNNABLE
------------------------
Merges into an existing vocabulary.json by `canonical`. It refreshes the
mined `freq`/`mined_from` of records it owns, but NEVER downgrades a
human-touched record: a record whose status is `confirmed` or whose
verification is above `unverified`, or that carries a non-blank definition,
aliases, or pseudonyms_slang, keeps every authored field. Any human-attached
`sources` (e.g. a doc citation) are preserved verbatim; authored content wins.

Cross-platform: pure Python, json via the json module (no echo/printf),
imports the existing scripts/wicked_estate.py helper (no raw SQLite here, no
re-implementation of kind parsing).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

# Import the ONE graph seam. scripts/ is on sys.path when dispatched via
# run.py (it execs `python <plugin>/scripts/vocabulary.py`); when imported by
# the hermetic test the test inserts scripts/ on the path. Add our own dir
# defensively so a direct `python scripts/vocabulary.py` also resolves it.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
from antilegacy_core import wicked_estate as we  # noqa: E402  (helper seam — list_nodes/_dekind/_load_config)


# ---------------------------------------------------------------------------
# Constants. NONE of these encode a domain meaning — they are tokenizer knobs.
# ---------------------------------------------------------------------------
DEFAULT_OUT = ".anti-legacy/vocabulary.json"
DEFAULT_MIN_FREQ = 3
VOCAB_VERSION = "1.0"
# Default cap on inline provenance SymbolIds stored per term (config-overridable
# via config.vocabulary.max_sources_per_term). DEFAULT 0 = store NONE: the
# glossary is a meaning dictionary, and where-used belongs to wicked-estate
# (`query`/`rank`), so we do not persist a parallel index. The `freq` field
# still carries the TRUE count regardless. Set a POSITIVE N to opt into inlining
# up to N representative SymbolIds per term (a self-contained glossary at the
# cost of size). A missing / non-int value falls back to this default.
DEFAULT_MAX_SOURCES_PER_TERM = 0

# Token splitter: COBOL/copybook names are hyphen/underscore delimited
# (DALYTRAN-RECORD, ACCT_ID, WS-FLG). EXACTLY the original delimiter set — the
# camelCase pass below inserts its boundaries as a private sentinel, not a space,
# so this stays byte-identical to the pre-modern splitter on mainframe names
# (literal spaces/dots in a name are NOT delimiters: 'OEM.DB2.SDSNLOAD' and a
# 'Table of Contents' doc node tokenize exactly as before). Pure language-mechanical.
_SPLIT_RE = re.compile(r"[-_]+")
# Private boundary marker the camelCase pass inserts; never appears in source.
_CAMEL_SEP = "\x00"
# camelCase / PascalCase sub-splitter for MODERN identifiers (getProducerName,
# KafkaProducer, parseXMLToJSON). After the hyphen/underscore split, a separator
# is INSERTED at each case-transition boundary, then the chunk is split on it.
# Two boundary kinds:
#   * camelCase word boundary  — a lowercase letter immediately before an
#     uppercase one:  get|Producer, Kafka|Producer, message|Id
#   * acronym→word boundary    — an uppercase that begins a new Capitalized word
#     at the tail of an acronym run:  XML|To, HTTP|Response
# CRITICAL: boundaries are between LETTERS only — never letter↔digit. Mainframe
# tokens that embed digits (DB2, STAT1, ACCTNO1A, PIC clause 9V2) therefore pass
# through UNCHANGED, exactly as the old hyphen/underscore-only splitter left them.
# That preserves mainframe domain-term resolution (DB2 stays one token) while
# still splitting modern names. Pure language-mechanical, not domain.
_CAMEL_ACRONYM = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_WORD = re.compile(r"(?<=[a-z])(?=[A-Z])")
# A pure-numeric COBOL paragraph sequence prefix (2000-, 0000-, 2700-).
_NUMERIC_RE = re.compile(r"^[0-9]+$")
# Abbreviation candidate length ceiling (a tokenizer knob, not a domain rule).
MAX_ABBREV_LEN = 6
# Filler tokens the COBOL toolchain emits that are never domain vocabulary.
# This is a STOPWORD list (language mechanics), NOT a domain classifier — it
# names COBOL/structural noise, never business meaning.
_STOPWORDS = {
    "FILLER", "X", "N", "S", "V", "PIC", "VALUE", "VALUES", "OF", "TO",
    "FD", "WS", "LS", "LK", "FILE", "REDEFINES", "OCCURS", "COMP", "COMP3",
    # connective tokens that surface from camelCase modern names (sendOffsetsTo
    # Transaction -> ...TO...) — language mechanics, never a domain noun/verb.
    "AND", "OR", "FROM", "WITH", "FOR", "THE", "BY", "ON", "IN", "AT", "AS",
}
# MODERN accessor/boilerplate leading verbs. These are language mechanics, not
# domain actions: a getter/setter names no business behavior, so the action
# miner skips them and the capability is named by its entity (e.g. PRODUCER)
# instead of `GetProducerCapability`. A real verb (send/commit/flush/route)
# is unaffected.
_ACTION_BOILERPLATE = {"GET", "SET", "IS", "HAS", "NEW"}
# MODERN OO type-name mechanics that are not domain entities. The dominant
# domain noun (PRODUCER, MESSAGE) still surfaces by frequency; this just keeps
# pure scaffolding tokens out of the entity tally.
_TYPE_NOISE = {"BUILDER", "IMPL", "FACTORY", "ABSTRACT", "BASE", "EXCEPTION", "DEFAULT"}
# The graph kinds whose NAME is a domain type/entity in modern languages. Covers
# OO classes/interfaces AND the type-declaring kinds other languages use instead:
# Go/Rust/C/C++ structs, Rust traits, enums, and records — without these, entity
# mining (and the capability naming + cross-app coalescing it drives) is blind to
# Go/Rust/C domain types, which declare no `class`. Mainframe estates emit none of
# these kinds, so the wider set is a no-op there.
_DEFAULT_TYPE_KINDS = ["class", "interface", "struct", "trait", "enum", "record"]


# ---------------------------------------------------------------------------
# Config resolution — which kinds each miner is allowed to look at. Driven
# entirely by config.coverage.*; falls back to the documented estate defaults
# only when the config is missing the key (so a bare workspace still mines).
# ---------------------------------------------------------------------------
_DEFAULT_BEHAVIOR_KINDS = ["module", "function", "method"]
_DEFAULT_ESTATE_BEHAVIOR_KINDS = ["db2_table", "cics_program", "step"]
# The structural kinds that NAME domain nouns / carry abbreviations. The full
# structural_kinds set is broad (file/import/constant/...); the noun-bearing
# subset is field + variable. We intersect config's structural_kinds with this
# so the config still gates, but we never mine 'file'/'import' as nouns.
_NOUN_STRUCTURAL_KINDS = {"field", "variable"}


def _norm_kind_list(values) -> list:
    """Lowercase + de-quote a config kind list; drop empties. The estate
    object-kinds in config are bare tokens ('db2_table'), matching what we
    extract from `_dekind('{"other":"db2_table"}')` below."""
    out = []
    for v in values or []:
        s = str(v).strip().strip('"').lower()
        if s:
            out.append(s)
    return out


def _normalize_node_kind(raw) -> str:
    """Normalize a node's verbatim `kind` to a bare lowercase token.

    `_dekind` returns simple kinds de-quoted ('function') but leaves estate
    object-kinds as their raw JSON token ('{"other":"step"}'). Unwrap the
    `{"other":"X"}` form to `X` so a single normalized token flows through
    every miner — THIS is the bucketing the helper gotcha demands the caller
    do itself.
    """
    dek = we._dekind(raw)
    if dek.startswith("{"):
        try:
            obj = json.loads(dek)
            if isinstance(obj, dict) and "other" in obj:
                return str(obj["other"]).strip().lower()
        except (ValueError, TypeError):
            pass
        # Unparseable object form — return as-is (won't match any config kind).
        return dek
    return dek


def coverage_kinds(config: dict) -> dict:
    """Resolve the three config-driven kind sets the miners use. Pure; takes
    an already-loaded config dict so callers/tests can inject one."""
    cov = (config or {}).get("coverage", {}) or {}
    behavior = _norm_kind_list(cov.get("behavior_kinds")) or list(_DEFAULT_BEHAVIOR_KINDS)
    estate = _norm_kind_list(cov.get("estate_behavior_kinds")) or list(_DEFAULT_ESTATE_BEHAVIOR_KINDS)
    structural = _norm_kind_list(cov.get("structural_kinds"))
    # The noun-bearing subset of structural kinds the config actually declares;
    # if config omits structural_kinds entirely, fall back to the default nouns.
    if structural:
        nouns = [k for k in structural if k in _NOUN_STRUCTURAL_KINDS]
    else:
        nouns = sorted(_NOUN_STRUCTURAL_KINDS)
    types = _norm_kind_list(cov.get("type_kinds")) or list(_DEFAULT_TYPE_KINDS)
    return {
        "behavior_kinds": behavior,
        "estate_behavior_kinds": estate,
        "noun_structural_kinds": nouns or sorted(_NOUN_STRUCTURAL_KINDS),
        "type_kinds": types,
    }


def max_sources_per_term(config: dict) -> int:
    """Resolve config.vocabulary.max_sources_per_term — how many inline
    representative SymbolIds the bootstrap stores per term. Pure; takes an
    already-loaded config dict. Default 0 (missing/non-int/non-positive) = store
    NONE: the glossary stays a Read-able meaning dictionary and where-used is the
    engine's job. A positive value opts into a self-contained glossary."""
    vocab = (config or {}).get("vocabulary", {}) or {}
    raw = vocab.get("max_sources_per_term", DEFAULT_MAX_SOURCES_PER_TERM)
    try:
        cap = int(raw)
    except (ValueError, TypeError):
        return DEFAULT_MAX_SOURCES_PER_TERM
    return cap if cap > 0 else DEFAULT_MAX_SOURCES_PER_TERM


# ---------------------------------------------------------------------------
# Tokenizers (language-mechanical only).
# ---------------------------------------------------------------------------
def _tokens(name: str) -> list:
    """Split a graph node name into upper-cased domain tokens.

    Two-stage, language-agnostic: first split on hyphen/underscore/space/dot
    (COBOL/copybook + dotted modern names), then split each chunk on
    camelCase/PascalCase boundaries (modern identifiers). Mainframe all-caps
    hyphen names are unaffected (each chunk is a single all-caps run):
      'DALYTRAN-RECORD'         -> ['DALYTRAN', 'RECORD']
      '2000-POST-TRANSACTION'   -> ['2000', 'POST', 'TRANSACTION']
    Modern camelCase/PascalCase now yields real tokens instead of one blob:
      'getProducerName'         -> ['GET', 'PRODUCER', 'NAME']
      'KafkaProducer'           -> ['KAFKA', 'PRODUCER']
      'parseXMLToJSON'          -> ['PARSE', 'XML', 'TO', 'JSON']
    """
    if not name:
        return []
    # Insert a private sentinel at each camelCase / acronym→word boundary, split on
    # the original hyphen/underscore delimiters, then split each piece on the
    # sentinel. Digits never trigger a boundary and literal spaces/dots are NOT
    # delimiters, so mainframe names tokenize byte-identically to the original.
    marked = _CAMEL_ACRONYM.sub(_CAMEL_SEP, name)
    marked = _CAMEL_WORD.sub(_CAMEL_SEP, marked)
    out = []
    for chunk in _SPLIT_RE.split(marked):
        for piece in chunk.split(_CAMEL_SEP):
            if piece:
                out.append(piece.upper())
    return out


def _action_tokens(name: str) -> list:
    """Tokens for the action miner: drop pure-numeric COBOL sequence prefixes
    so '2000-POST-TRANSACTION' yields ['POST','TRANSACTION'] and the leading
    verb is POST, not 2000."""
    return [t for t in _tokens(name) if not _NUMERIC_RE.match(t)]


# ---------------------------------------------------------------------------
# The miners. Each returns {canonical: candidate} where candidate carries the
# mined sources + freq. NONE of them read names for meaning — they count.
# ---------------------------------------------------------------------------
def _new_candidate(canonical: str, term_type: str, mined_from: str) -> dict:
    return {
        "canonical": canonical,
        "term_type": term_type,
        "_freq": 0,
        "_mined_from": mined_from,
        "_sources": [],  # accumulates {ref, node_kind, file} dicts (deduped)
    }


def _add_source(cand: dict, node: dict, node_kind: str,
                cap: int = DEFAULT_MAX_SOURCES_PER_TERM) -> None:
    cand["_freq"] += 1
    # Cap stored sources to keep the artifact bounded; the `freq` field carries
    # the true count regardless. `cap` = config.vocabulary.max_sources_per_term
    # (DEFAULT 0 = store NONE — where-used is wicked-estate's job, the glossary
    # stays a meaning dictionary). A positive N inlines up to N distinct
    # SymbolIds per term so even a high-frequency token never bloats the file.
    if len(cand["_sources"]) < cap:
        ref = node.get("symbol_id") or node.get("symbol") or node.get("name")
        cand["_sources"].append({
            "ref": ref,
            "node_kind": node_kind,
            "file": node.get("file", "") or "",
        })


def mine_estate_entities(nodes: list, kinds: dict,
                         cap: int = DEFAULT_MAX_SOURCES_PER_TERM) -> dict:
    """HIGH-SIGNAL entity candidates: estate object-kinds (db2_table /
    cics_program / step) where the WHOLE node name is a verbatim domain noun.
    Kept separate from the field-stem entities so the bootstrap can give these
    canonical priority over an incidental field token of the same spelling."""
    estate = set(kinds["estate_behavior_kinds"])
    out: dict = {}
    for n in nodes:
        nk = _normalize_node_kind(n["kind"])
        if nk not in estate:
            continue
        canon = (n["name"] or "").strip().upper()
        if not canon or canon in _STOPWORDS:
            continue
        cand = out.setdefault(canon, _new_candidate(canon, "entity", "estate_behavior_kinds"))
        _add_source(cand, n, nk, cap)
    return out


def mine_field_entities(nodes: list, kinds: dict,
                        cap: int = DEFAULT_MAX_SOURCES_PER_TERM) -> dict:
    """LOWER-SIGNAL entity candidates: the leading token cluster (record/
    copybook field root) of field-kind names. A spelling that is ALSO a genuine
    behavior verb (READ/POST/UPDATE) is intentionally claimed by the action
    miner first in the bootstrap union — this miner only keeps the stems no
    higher-signal miner took."""
    nouns = set(kinds["noun_structural_kinds"])
    out: dict = {}
    for n in nodes:
        nk = _normalize_node_kind(n["kind"])
        if nk != "field" or nk not in nouns:
            continue
        toks = _tokens(n["name"])
        if not toks:
            continue
        stem = toks[0]
        # Modern boolean/accessor fields (isRetry, hasInflight, newTopics) tokenize
        # to a boilerplate STEM (IS/HAS/NEW) that is language mechanics, not a domain
        # noun — exclude it here too (mine_type_entities/mine_actions already do), or
        # it leaks into the entity space and names an `IsCapability`.
        if stem in _STOPWORDS or stem in _ACTION_BOILERPLATE or _NUMERIC_RE.match(stem):
            continue
        cand = out.setdefault(stem, _new_candidate(stem, "entity", "field-token"))
        _add_source(cand, n, nk, cap)
    return out


def mine_type_entities(nodes: list, kinds: dict,
                       cap: int = DEFAULT_MAX_SOURCES_PER_TERM) -> dict:
    """MODERN entity candidates: the significant tokens of class/interface names
    (`KafkaProducer` -> PRODUCER + KAFKA, `MessageRouter` -> MESSAGE + ROUTER).
    The dominant domain noun (PRODUCER, MESSAGE) surfaces by frequency across the
    type surface, which is exactly what names a capability and lets the same
    capability coalesce across source apps. Pure OO scaffolding tokens
    (Builder/Impl/Factory/Exception) are dropped via `_TYPE_NOISE`. This is the
    modern analogue of mine_estate_entities (mainframe object-kind nouns)."""
    types = set(kinds.get("type_kinds", _DEFAULT_TYPE_KINDS))
    out: dict = {}
    for n in nodes:
        nk = _normalize_node_kind(n["kind"])
        if nk not in types:
            continue
        for tok in _tokens(n["name"]):
            if (tok in _STOPWORDS or tok in _TYPE_NOISE or tok in _ACTION_BOILERPLATE
                    or _NUMERIC_RE.match(tok) or len(tok) < 3):
                continue
            cand = out.setdefault(tok, _new_candidate(tok, "entity", "type-token"))
            _add_source(cand, n, nk, cap)
    return out


def mine_actions(nodes: list, kinds: dict,
                 cap: int = DEFAULT_MAX_SOURCES_PER_TERM) -> dict:
    """ACTION candidates: leading verb token of behavior-kind (function/module/
    method) names, after dropping pure-numeric COBOL sequence prefixes. MODERN
    accessor/boilerplate leading verbs (get/set/is/has/new) are skipped so a
    getter contributes no spurious action and the capability is named by its
    entity instead — a real verb (send/commit/flush/route) is unaffected."""
    behavior = set(kinds["behavior_kinds"])
    out: dict = {}
    for n in nodes:
        nk = _normalize_node_kind(n["kind"])
        if nk not in behavior:
            continue
        toks = _action_tokens(n["name"])
        if not toks:
            continue
        verb = toks[0]
        if verb in _STOPWORDS or verb in _ACTION_BOILERPLATE:
            continue
        cand = out.setdefault(verb, _new_candidate(verb, "action", "behavior_kinds"))
        _add_source(cand, n, nk, cap)
    return out


def mine_abbreviations(nodes: list, kinds: dict,
                       cap: int = DEFAULT_MAX_SOURCES_PER_TERM) -> dict:
    """ABBREVIATION candidates: short (<= MAX_ABBREV_LEN) recurring tokens
    across the noun-bearing structural kinds (field/variable), freq-ranked.
    The expansion is left BLANK (propose, don't coin)."""
    nouns = set(kinds["noun_structural_kinds"])
    out: dict = {}
    for n in nodes:
        nk = _normalize_node_kind(n["kind"])
        if nk not in nouns:
            continue
        for tok in _tokens(n["name"]):
            if len(tok) > MAX_ABBREV_LEN or tok in _STOPWORDS or _NUMERIC_RE.match(tok):
                continue
            cand = out.setdefault(tok, _new_candidate(tok, "abbreviation", "field-token"))
            _add_source(cand, n, nk, cap)
    return out


# ---------------------------------------------------------------------------
# Record assembly + merge.
# ---------------------------------------------------------------------------
def _candidate_to_record(cand: dict) -> dict:
    """Materialize a fresh bootstrap record (status proposed / unverified /
    blank definition) from a mined candidate. `freq` is the TRUE recurrence
    count; `mined_from` names the miner that proposed it. `sources` is empty
    unless config opted into inline provenance (where-used otherwise belongs to
    wicked-estate); run-level metadata lives once in the doc `meta`, not here."""
    sources = [
        {"kind": "graph_node", "ref": s["ref"], "node_kind": s["node_kind"], "file": s["file"]}
        for s in cand["_sources"]
    ]
    return {
        "canonical": cand["canonical"],
        "term_type": cand["term_type"],
        "definition": "",                # BLANK — authored later, never coined
        "aliases": [],
        "pseudonyms_slang": [],
        "status": "proposed",
        "verification": "unverified",
        "freq": cand["_freq"],
        "mined_from": cand["_mined_from"],
        "sources": sources,
    }


def _is_human_touched(record: dict) -> bool:
    """A record carries authored content if a human/agent has moved it past the
    bootstrap default: confirmed status, any verification above unverified, or
    any non-empty authored field (definition/aliases/pseudonyms_slang)."""
    if record.get("status") == "confirmed":
        return True
    if record.get("verification") not in (None, "", "unverified"):
        return True
    if (record.get("definition") or "").strip():
        return True
    if record.get("aliases"):
        return True
    if record.get("pseudonyms_slang"):
        return True
    return False


def _merge_sources(existing: list, mined: list) -> list:
    """Union existing + mined sources by (kind, ref), keeping non-graph_node
    (doc/human) sources untouched. With inline provenance off (the default)
    `mined` is empty, so any human-attached sources are simply preserved."""
    by_ref = {}
    order = []
    for s in (existing or []) + (mined or []):
        key = (s.get("kind"), s.get("ref"))
        if key not in by_ref:
            order.append(key)
            by_ref[key] = dict(s)
    return [by_ref[k] for k in order]


def merge_records(existing: list, mined_records: list) -> list:
    """Idempotent merge by canonical. Human-touched records keep ALL authored
    fields; only their mined `freq`/`mined_from`/`sources` are refreshed.
    Untouched (pure-bootstrap) records are replaced by the fresh mined record.
    Records that exist only in `existing` (e.g. human-added terms with no graph
    mining hit) are preserved verbatim."""
    by_canon = {r.get("canonical"): dict(r) for r in existing or []}
    mined_by_canon = {r["canonical"]: r for r in mined_records}

    for canon, mined in mined_by_canon.items():
        prior = by_canon.get(canon)
        if prior is None:
            by_canon[canon] = mined
            continue
        if _is_human_touched(prior):
            # Preserve authored content; refresh mined evidence only.
            prior["sources"] = _merge_sources(prior.get("sources", []), mined.get("sources", []))
            prior["freq"] = mined.get("freq", prior.get("freq"))
            # keep prior mined_from if set, else adopt mined
            prior.setdefault("mined_from", mined.get("mined_from"))
            by_canon[canon] = prior
        else:
            # Pure-bootstrap record — safe to replace with fresh mining.
            by_canon[canon] = mined

    return list(by_canon.values())


def _load_existing(out_path: str) -> list:
    if not os.path.isfile(out_path):
        return []
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    terms = data.get("terms") if isinstance(data, dict) else None
    return terms if isinstance(terms, list) else []


def _build_meta(records: list, config_ref: str, run_meta: dict) -> dict:
    confirmed = sum(1 for r in records if r.get("status") == "confirmed")
    trusted = sum(1 for r in records if r.get("verification") == "trusted_verified")
    return {
        "vocabulary_version": VOCAB_VERSION,
        "config_ref": config_ref,
        "generated_by": "anti-legacy:vocabulary",
        "bootstrap_run": dict(run_meta),   # run-level metadata lives ONCE here
        "term_count": len(records),
        "confirmed_count": confirmed,
        "trusted_count": trusted,
    }


# ---------------------------------------------------------------------------
# Top-level bootstrap (the importable seam the test drives).
# ---------------------------------------------------------------------------
def bootstrap(db, config=None, out_path=DEFAULT_OUT, min_freq=DEFAULT_MIN_FREQ,
              config_ref=we.CONFIG_PATH, nodes=None, now=None):
    """Mine `db`, merge into `out_path`, write, and return the full document.

    `config` — already-loaded config dict (the test injects one; the CLI loads
    from config_ref). `nodes` — optional pre-enumerated node list (the hermetic
    test injects synthetic nodes; the CLI calls list_nodes(db, kinds=None)).
    """
    if config is None:
        config = we._load_config(config_ref)
    kinds = coverage_kinds(config)
    cap = max_sources_per_term(config)

    if nodes is None:
        # THE GOTCHA: kinds=None so estate object-kinds come back; we bucket
        # by normalized kind ourselves inside each miner.
        nodes = we.list_nodes(db, kinds=None)

    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_meta = {"db": os.path.basename(db) if db else "", "ts": ts, "min_freq": min_freq}

    # Run the miners and union their candidate maps. Canonical is unique across
    # the term space, so when the SAME spelling surfaces from more than one
    # miner the FIRST to claim it wins. Order = highest-signal first:
    #   1. estate-object entities (db2_table/cics_program/step — verbatim nouns)
    #   2. action verbs (genuine behavior-kind leading verbs: READ/POST/UPDATE)
    #   3. field-stem entities (incidental record/field roots)
    #   4. abbreviations (short recurring tokens)
    # This keeps READ/POST/UPDATE classified as actions rather than being
    # captured first as an incidental field token.
    candidates: dict = {}
    for miner in (mine_estate_entities, mine_type_entities, mine_actions, mine_field_entities, mine_abbreviations):
        for canon, cand in miner(nodes, kinds, cap).items():
            candidates.setdefault(canon, cand)

    # Apply the min_freq floor (keeps one-off filler out of the proposed set).
    mined_records = [
        _candidate_to_record(cand)
        for canon, cand in candidates.items()
        if cand["_freq"] >= min_freq
    ]

    existing = _load_existing(out_path)
    merged = merge_records(existing, mined_records)
    # Deterministic ordering: by term_type then canonical.
    merged.sort(key=lambda r: (r.get("term_type", ""), r.get("canonical", "")))

    doc = {"terms": merged, "meta": _build_meta(merged, config_ref, run_meta)}

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")
    return doc


# ---------------------------------------------------------------------------
# Project (the BIND seam): write CONFIRMED terms onto the code graph as native
# domain_* annotations so wicked-estate's OWN resolution (cluster /
# by-requirement / read-kv) is term-aware. The glossary defines the term; the
# engine APPLIES it to the graph. This is "put the terms in wicked-estate" —
# the engine becomes the domain-resolution surface, not a sidecar.
# ---------------------------------------------------------------------------
# term_type -> the native k/v key the term's canonical is written under.
_DOMAIN_KEY = {
    "entity": "domain_entity",
    "action": "domain_action",
    "abbreviation": "domain_abbrev",
    "domain_concept": "domain_concept",
}
# Re-mine uncapped when projecting: the bind needs EVERY grounding SymbolId,
# regardless of the (file-size) source cap the bootstrap persists.
_PROJECT_ALL_SOURCES = 10 ** 9


def _confirmed_terms(vocab_path: str) -> list:
    """The terms a human has CONFIRMED (status=confirmed, non-blank canonical) —
    only these are authoritative enough to bind onto the graph as domain tags."""
    return [t for t in _load_existing(vocab_path)
            if t.get("status") == "confirmed" and t.get("canonical")]


# Per-run bindings artifact (ISS-04). Gitignored, regenerated every `project`:
# it is NOT a system of record (SymbolIds re-intern, so a committed binding goes
# stale) — it exists ONLY to make determinism drift detectable. It records node
# NAMES (stable across reindex), so its content_hash changes iff the node SET a
# confirmed term binds to actually changes (a miner/config drift), NOT merely
# because the graph was rebuilt.
DEFAULT_BINDINGS_OUT = ".anti-legacy/vocabulary-bindings.json"


def _stable_hash(obj) -> str:
    """sha256 over a canonical JSON encoding — order-independent for our sorted
    inputs, stable across processes/platforms."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def project_terms_to_graph(db, vocab_path=DEFAULT_OUT, *, config=None,
                           config_ref=we.CONFIG_PATH, nodes=None, binary=None,
                           author="anti-legacy:vocabulary"):
    """Project the CONFIRMED glossary terms back onto the code graph as native
    k/v annotations, so wicked-estate's OWN domain resolution (cluster /
    by-requirement / read-kv) is term-aware.

    This is a DISPOSABLE re-projectable cache, NOT the system of record: the
    graph DB is gitignored and fresh-deleted on every survey, and annotate_kv
    never mirrors to a sidecar, so the tags are wiped on the next re-index. The
    committed glossary (vocabulary.json) is the durable source of term MEANING;
    these tags are the source of term->node RESOLUTION, re-derived here. Hence
    re-run `project` after EVERY fresh survey AND after any term-confirming step.

    For each confirmed term, re-derive its grounding SymbolIds with the SAME
    miners the bootstrap uses (deterministic — no provenance need be persisted
    in the file), then write `<domain_key> = <canonical>` onto each UNAMBIGUOUS
    node via wicked_estate.annotate_kv (precision-guarded: it refuses a name
    that resolves to >1 symbol rather than smear the tag, so an ambiguous node
    is SKIPPED and counted, never mis-bound). trusted_verified terms carry
    confidence 1.0.

    Because terms (unlike rules) have NO committed per-binding overlay, a bind
    that silently fails would leave no trace — so the summary names the gaps:
      * `unbound` — confirmed terms that mined NO grounding on the CURRENT graph
        (the term is not present here; a real coverage gap).
      * `all_skipped` — confirmed terms that DID ground but every node was
        ambiguous and refused (e.g. carddemo MAIN-PARA×21); grounded yet 0 bound.
    Returns a summary dict; binds nothing (and says so) when no confirmed terms."""
    if config is None:
        config = we._load_config(config_ref)
    kinds = coverage_kinds(config)
    if nodes is None:
        nodes = we.list_nodes(db, kinds=None)

    confirmed = {t["canonical"]: t for t in _confirmed_terms(vocab_path)}
    glossary_hash = _stable_hash(
        sorted((c, confirmed[c].get("term_type")) for c in confirmed))
    if not confirmed:
        return {"projected": 0, "terms": 0, "skipped": 0, "confirmed_available": 0,
                "unbound": [], "all_skipped": [], "bindings": [],
                "glossary_hash": glossary_hash, "content_hash": _stable_hash([]),
                "reason": "no confirmed terms to project"}

    sid2name = {n.get("symbol_id"): n.get("name") for n in nodes}

    # Re-mine uncapped so every grounding SymbolId is available to bind.
    candidates: dict = {}
    for miner in (mine_estate_entities, mine_type_entities, mine_actions, mine_field_entities, mine_abbreviations):
        for canon, cand in miner(nodes, kinds, _PROJECT_ALL_SOURCES).items():
            candidates.setdefault(canon, cand)

    projected = skipped = bound_terms = 0
    unbound = []       # confirmed but mined no grounding on this graph (real gap)
    all_skipped = []   # grounded but every node ambiguous -> 0 bound (ambiguity gap)
    bindings = []      # per-term node-NAME bind set (reindex-stable drift record)
    for canon, term in confirmed.items():
        cand = candidates.get(canon)
        if not cand or not cand.get("_sources"):
            unbound.append(canon)
            continue
        key = _DOMAIN_KEY.get(term.get("term_type"), "domain_term")
        conf = 1.0 if term.get("verification") == "trusted_verified" else None
        bound_terms += 1
        term_bound = 0
        for s in cand["_sources"]:
            sid = s.get("ref")
            try:
                # replace=True: the domain_* tags are a re-projectable CACHE, so a
                # re-project (after a term change, without a full re-survey) must
                # UPSERT by (type,key) — not append a duplicate (wicked-estate
                # >= 0.5.1; feature-detected, degrades to append on older engines).
                we.annotate_kv(db, sid, key, canon, confidence=conf,
                               provenance="vocabulary:confirmed", author=author,
                               replace=True, binary=binary)
                projected += 1
                term_bound += 1
            except we.WickedEstateError:
                skipped += 1  # ambiguous name / native annotate absent — never smear
        if term_bound == 0:
            all_skipped.append(canon)
        # Record the MINED grounding by node NAME (the set that shifts under a
        # miner/config change — the thing ISS-04 wants to make auditable).
        node_names = sorted({sid2name.get(s.get("ref")) for s in cand["_sources"]} - {None})
        bindings.append({"canonical": canon, "term_type": term.get("term_type"),
                         "domain_key": key, "node_names": node_names,
                         "node_count": len(node_names)})
    content_hash = _stable_hash([[b["canonical"], b["node_names"]] for b in bindings])
    return {"projected": projected, "terms": bound_terms, "skipped": skipped,
            "confirmed_available": len(confirmed),
            "unbound": sorted(unbound), "all_skipped": sorted(all_skipped),
            "bindings": bindings, "glossary_hash": glossary_hash,
            "content_hash": content_hash}


def check_projection(db, *, config=None, config_ref=we.CONFIG_PATH,
                     vocab_path=DEFAULT_OUT, nodes=None, binary=None):
    """Gate predicate (ISS-03): is the engine's domain_* projection PRESENT?

    A fresh survey deletes the graph DB and wipes every domain_* tag, so if a
    later phase forgets to re-run `project`, the engine silently degrades to
    name-only domain resolution. This check FAILS (not ok) exactly when CONFIRMED
    terms DO ground on the current graph yet the graph carries ZERO domain_* tags
    — the unmistakable signature of a skipped reprojection.

    It deliberately does NOT fail when there are no confirmed terms (nothing to
    enforce) or when confirmed terms simply aren't present in this graph (a
    coverage gap `project` already reports as `unbound`, not a reprojection miss).
    Returns {ok, confirmed, bindable, tagged, reason}."""
    if config is None:
        config = we._load_config(config_ref)
    kinds = coverage_kinds(config)
    if nodes is None:
        nodes = we.list_nodes(db, kinds=None)

    confirmed = {t["canonical"]: t for t in _confirmed_terms(vocab_path)}
    if not confirmed:
        return {"ok": True, "confirmed": 0, "bindable": 0, "tagged": 0,
                "reason": "no confirmed terms — nothing to enforce"}

    # Presence-only re-mine (cap=1): which confirmed terms ground on THIS graph.
    candidates: dict = {}
    for miner in (mine_estate_entities, mine_type_entities, mine_actions, mine_field_entities, mine_abbreviations):
        for canon, cand in miner(nodes, kinds, 1).items():
            candidates.setdefault(canon, cand)
    bindable = [c for c in confirmed
                if candidates.get(c) and candidates[c].get("_sources")]
    if not bindable:
        return {"ok": True, "confirmed": len(confirmed), "bindable": 0, "tagged": 0,
                "reason": "confirmed terms not present in this graph "
                          "(coverage gap, not a reprojection miss)"}

    tagged = sum(len(we.nodes_annotated_with(db, key, binary=binary))
                 for key in ("domain_entity", "domain_action", "domain_abbrev"))
    ok = tagged > 0
    return {
        "ok": ok, "confirmed": len(confirmed), "bindable": len(bindable),
        "tagged": tagged,
        "reason": "" if ok else (
            "%d confirmed term(s) ground on this graph but it carries 0 domain_* "
            "tags — run `vocabulary project` (reprojection was skipped after a "
            "rebuild)" % len(bindable)),
    }


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="vocabulary",
        description="Mine the domain vocabulary from a wicked-estate code graph "
                    "(config-driven kinds; proposes terms, never coins meaning).",
    )
    p.add_argument("--db", required=True, help="path to the <app>.db code graph")
    p.add_argument("--config", default=we.CONFIG_PATH, help="path to config.json")
    p.add_argument("--out", default=DEFAULT_OUT, help="vocabulary.json output path")
    p.add_argument("--min-freq", type=int, default=DEFAULT_MIN_FREQ,
                   help="minimum token frequency to propose a term (default 3)")
    return p.parse_args(argv)


def _parse_project_args(argv):
    p = argparse.ArgumentParser(
        prog="vocabulary project",
        description="Bind CONFIRMED glossary terms onto the code graph as native "
                    "domain_* k/v annotations, so wicked-estate's own cluster / "
                    "by-requirement / read-kv become term-aware (domain resolution "
                    "lives in the engine, not a sidecar).",
    )
    p.add_argument("--db", required=True, help="path to the <app>.db code graph")
    p.add_argument("--config", default=we.CONFIG_PATH, help="path to config.json")
    p.add_argument("--vocab", default=DEFAULT_OUT, help="vocabulary.json path")
    p.add_argument("--bindings-out", default=DEFAULT_BINDINGS_OUT,
                   help="per-run bindings artifact path (gitignored; drift seam)")
    return p.parse_args(argv)


def _write_bindings_and_check_drift(path, summary, db, now=None) -> bool:
    """Write the per-run bindings artifact and detect determinism drift: a prior
    artifact with the SAME glossary_hash but a DIFFERENT content_hash means the
    confirmed terms are unchanged yet the node sets they bind to moved — a
    miner/config drift that leaves NO glossary git-diff. Returns the drift bool."""
    prior = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                prior = json.load(f)
        except (OSError, ValueError):
            prior = None
    drift = bool(prior
                 and prior.get("glossary_hash") == summary.get("glossary_hash")
                 and prior.get("content_hash") != summary.get("content_hash"))
    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "generated_for_db": os.path.basename(db) if db else "",
        "ts": ts,
        "glossary_hash": summary.get("glossary_hash"),
        "content_hash": summary.get("content_hash"),
        "drift_from_prior": drift,
        "bindings": summary.get("bindings", []),
        "summary": {k: summary.get(k) for k in
                    ("projected", "terms", "skipped", "confirmed_available",
                     "unbound", "all_skipped")},
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return drift


def _run_project(argv) -> int:
    """CLI for `vocabulary project --db <db>`. Writes the confirmed terms onto
    the graph and prints a one-line summary. Exit 0 even when nothing is
    confirmed yet (a valid, expected pre-confirmation state)."""
    args = _parse_project_args(argv)
    if not os.path.exists(args.db):
        sys.stderr.write("vocabulary: db not found: %s\n" % args.db)
        return 2
    config = we._load_config(args.config)
    summary = project_terms_to_graph(
        args.db, vocab_path=args.vocab, config=config, config_ref=args.config,
    )
    sys.stdout.write(
        "vocabulary project: bound %d node-annotation(s) for %d confirmed term(s) "
        "-> %s [skipped_ambiguous=%d, confirmed_available=%d]\n"
        % (summary.get("projected", 0), summary.get("terms", 0), args.db,
           summary.get("skipped", 0), summary.get("confirmed_available", 0))
    )
    # Surface the gaps a silent 0-bind would otherwise hide (terms have no
    # committed per-binding overlay). These are coverage signals, not errors.
    unbound = summary.get("unbound") or []
    all_skipped = summary.get("all_skipped") or []
    if unbound:
        sys.stdout.write(
            "  GAP: %d confirmed term(s) mined NO grounding on this graph "
            "(not present here): %s\n" % (len(unbound), ", ".join(unbound)))
    if all_skipped:
        sys.stdout.write(
            "  GAP: %d confirmed term(s) grounded but every node was ambiguous "
            "(0 bound, name-collision): %s\n" % (len(all_skipped), ", ".join(all_skipped)))
    # Persist the per-run bindings artifact (ISS-04) + surface determinism drift.
    drift = _write_bindings_and_check_drift(args.bindings_out, summary, args.db)
    if drift:
        sys.stdout.write(
            "  DRIFT: confirmed glossary unchanged but the node sets terms bind to "
            "CHANGED since the last projection (miner/config drift) — re-review %s\n"
            % args.bindings_out)
    return 0


def _parse_check_args(argv):
    p = argparse.ArgumentParser(
        prog="vocabulary check-projection",
        description="Gate predicate: exit non-zero when confirmed terms ground on "
                    "the graph but it carries 0 domain_* tags (reprojection was "
                    "skipped after a rebuild). Exit 0 when nothing to enforce.",
    )
    p.add_argument("--db", required=True, help="path to the <app>.db code graph")
    p.add_argument("--config", default=we.CONFIG_PATH, help="path to config.json")
    p.add_argument("--vocab", default=DEFAULT_OUT, help="vocabulary.json path")
    return p.parse_args(argv)


def _run_check_projection(argv) -> int:
    """CLI for `vocabulary check-projection --db <db>`. Exit 1 (BLOCKED) when the
    domain_* projection is missing for grounded confirmed terms; else exit 0."""
    args = _parse_check_args(argv)
    if not os.path.exists(args.db):
        sys.stderr.write("vocabulary: db not found: %s\n" % args.db)
        return 2
    config = we._load_config(args.config)
    r = check_projection(args.db, config=config, config_ref=args.config,
                         vocab_path=args.vocab)
    if r["ok"]:
        sys.stdout.write(
            "vocabulary check-projection: OK [confirmed=%d bindable=%d tagged=%d] %s\n"
            % (r["confirmed"], r["bindable"], r["tagged"], r.get("reason", "")))
        return 0
    sys.stderr.write("vocabulary check-projection: BLOCKED — %s\n" % r["reason"])
    return 1


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    # Subcommand dispatch: `project` BINDS confirmed terms onto the graph;
    # `check-projection` is the gate predicate; the bare `--db ...` form mines.
    if argv and argv[0] == "project":
        return _run_project(argv[1:])
    if argv and argv[0] == "check-projection":
        return _run_check_projection(argv[1:])

    args = _parse_args(argv)
    if not os.path.exists(args.db):
        sys.stderr.write("vocabulary: db not found: %s\n" % args.db)
        return 2
    config = we._load_config(args.config)
    doc = bootstrap(
        args.db, config=config, out_path=args.out,
        min_freq=args.min_freq, config_ref=args.config,
    )
    m = doc["meta"]
    by_type: dict = {}
    for r in doc["terms"]:
        by_type[r["term_type"]] = by_type.get(r["term_type"], 0) + 1
    breakdown = ", ".join("%s=%d" % (k, by_type[k]) for k in sorted(by_type))
    sys.stdout.write(
        "vocabulary: mined %d terms (%s) -> %s [min_freq=%d, confirmed=%d, trusted=%d]\n"
        % (m["term_count"], breakdown or "none", args.out, args.min_freq,
           m["confirmed_count"], m["trusted_count"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
