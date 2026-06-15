r"""
EVAL_T3_schema_models_rules  --  Theory T3 (the richest content is off-schema).

Theory
------
business_rules / validations / error_paths -- the rich annotation arrays
present in the produced requirements graph -- are NOT modeled in
schemas/requirements-graph.schema.json. The base requirement object requires
only [title, description, legacy_components, data_access, dependencies] and the
schema has no $defs for the rich item shapes. The rules are therefore
unvalidated: nothing rejects a malformed or missing rule.

What "fixed" means (post-fix behavior these assertions encode)
-------------------------------------------------------------
1. Base schema (requirements-graph.schema.json):
     * gains a top-level "$defs" with item shapes "rule", "validation",
       "errorPath";
     * the requirement object's "properties" includes business_rules,
       validations, error_paths (OPTIONAL here, so raw normalizer output with
       no rules still validates).
2. A NEW enriched overlay schema
   (schemas/requirements-graph.enriched.schema.json) exists and REQUIRES the
   three rich arrays on every requirement, with ID patterns
   ^RULE-\d{3}$ / ^VAL-\d{3}$ / ^ERR-\d{3}$ and business_rules minItems>=1.

Two assertion tiers
-------------------
  * STRUCTURE tier (no third-party deps): parses the JSON schema files and
    checks $defs + property presence + the enriched file's existence and
    required-ness. These FAIL today (no $defs, no rich props, no enriched file)
    -- they are the load-bearing red for T3.
  * VALIDATION tier (needs ``jsonschema``): uses Draft7Validator to prove the
    enriched profile actually REJECTS a missing/ill-formed rule and ACCEPTS a
    well-formed one. jsonschema is not yet installed, so these SKIP with a
    clear message (the dependency is added as part of the T3 fix). The
    structure tier alone already encodes the theory in red.

Determinism
-----------
Reads only the in-repo schema files and validates in-memory dicts; no network,
clock, or randomness.
"""
import json
import os

import pytest

# jsonschema is intentionally optional (added as part of the T3 fix).
try:
    import jsonschema  # noqa: F401
    from jsonschema import Draft7Validator
    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover - depends on env
    _HAS_JSONSCHEMA = False

_NEEDS_JSONSCHEMA = pytest.mark.skipif(
    not _HAS_JSONSCHEMA,
    reason="jsonschema not installed (dependency added as part of the T3 fix)",
)


# ---------------------------------------------------------------------------
# Path / load helpers
# ---------------------------------------------------------------------------
def _base_schema_path(repo_root):
    return os.path.join(repo_root, "schemas", "requirements-graph.schema.json")


def _enriched_schema_path(repo_root):
    return os.path.join(
        repo_root, "schemas", "requirements-graph.enriched.schema.json"
    )


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _requirement_object(schema):
    """Drill schema -> domains -> requirements -> (requirement object)."""
    return (
        schema["properties"]["domains"]["additionalProperties"]["properties"]
        ["requirements"]["additionalProperties"]
    )


def _resolve_enriched_for_validation(repo_root):
    """Load the enriched schema; if it $refs the base file's $defs by relative
    URI, register a resolver so Draft7Validator can follow it."""
    base_path = _base_schema_path(repo_root)
    enriched_path = _enriched_schema_path(repo_root)
    base = _load_json(base_path)
    enriched = _load_json(enriched_path)
    # Map the base file's relative name to its loaded document so any
    # "requirements-graph.schema.json#/$defs/..." reference resolves.
    store = {
        "requirements-graph.schema.json": base,
        base.get("$id", "requirements-graph.schema.json"): base,
    }
    resolver = jsonschema.RefResolver(base_uri="", referrer=enriched, store=store)
    return enriched, resolver


def _wrap_single_req(req):
    """Wrap one requirement object into a full requirements-graph document so a
    whole-graph schema can validate it."""
    return {
        "metadata": {"migration_mode": "functional"},
        "domains": {
            "Domain_customer": {
                "requirements": {"REQ_X": req},
                "entities": {
                    "CUSTOMER": {
                        "description": "x",
                        "fields": [
                            {"name": "id", "type": "string", "description": "pk"}
                        ],
                    }
                },
            }
        },
    }


def _well_formed_enriched_req():
    """A fully-formed enriched requirement (object-form rule items)."""
    return {
        "title": "Manage customer account lifecycle",
        "description": "Create/update/validate customer accounts.",
        "legacy_components": ["demoapp:PROG_A"],
        "data_access": ["CUSTOMER"],
        "dependencies": [],
        "business_rules": [
            {"id": "RULE-001", "statement": "Accounts need a unique id."},
            {"id": "RULE-002", "statement": "Balance updates preserve the ledger."},
        ],
        "validations": [
            {"id": "VAL-001", "statement": "Status in {A,C,S}.",
             "field": "account_status", "error_ref": "ERR-001"}
        ],
        "error_paths": [
            {"id": "ERR-001", "statement": "Reject invalid status, code 12.",
             "code": "12"}
        ],
    }


# ===========================================================================
# STRUCTURE TIER  (no jsonschema; this is the load-bearing red for T3)
# ===========================================================================
def test_t3_base_schema_defines_rule_item_shapes(repo_root):
    """The base schema must declare $defs for rule / validation / errorPath."""
    schema = _load_json(_base_schema_path(repo_root))
    assert "$defs" in schema, (
        "base requirements-graph.schema.json has no top-level $defs -- the "
        "rich item shapes (rule/validation/errorPath) are not modeled"
    )
    defs = schema["$defs"]
    for name in ("rule", "validation", "errorPath"):
        assert name in defs, f"$defs is missing the '{name}' item shape"


def test_t3_base_schema_models_rich_requirement_arrays(repo_root):
    """The requirement object must expose business_rules/validations/error_paths
    as properties (optional in the base profile)."""
    schema = _load_json(_base_schema_path(repo_root))
    props = _requirement_object(schema).get("properties", {})
    for field in ("business_rules", "validations", "error_paths"):
        assert field in props, (
            f"requirement object does not model '{field}' -- the richest "
            "content is off-schema and unvalidated"
        )
        ref = json.dumps(props[field])
        assert "$defs" in ref, (
            f"'{field}' must reference a $defs item shape (a typed array), "
            f"not be loosely typed: {props[field]!r}"
        )


def test_t3_enriched_schema_file_exists(repo_root):
    """The enriched overlay schema file must exist."""
    path = _enriched_schema_path(repo_root)
    assert os.path.exists(path), (
        f"enriched overlay schema is absent: {path} -- there is no profile "
        "that REQUIRES the rich fields after enrichment"
    )


def test_t3_enriched_schema_requires_rich_fields(repo_root):
    """The enriched overlay must REQUIRE business_rules/validations/error_paths
    on every requirement (and require >=1 business_rule)."""
    path = _enriched_schema_path(repo_root)
    if not os.path.exists(path):
        pytest.fail(
            f"enriched overlay schema absent ({path}); cannot require rich "
            "fields"
        )
    enriched = _load_json(path)
    blob = json.dumps(enriched)
    for field in ("business_rules", "validations", "error_paths"):
        assert field in blob, f"enriched schema never mentions '{field}'"

    # The enriched profile must list the three rich arrays as REQUIRED somewhere
    # in its requirement constraint (overlay re-declares 'required').
    def _required_lists(node):
        found = []
        if isinstance(node, dict):
            if isinstance(node.get("required"), list):
                found.append(node["required"])
            for v in node.values():
                found.extend(_required_lists(v))
        elif isinstance(node, list):
            for v in node:
                found.extend(_required_lists(v))
        return found

    required_lists = _required_lists(enriched)
    rich = {"business_rules", "validations", "error_paths"}
    assert any(rich.issubset(set(rl)) for rl in required_lists), (
        "enriched schema does not REQUIRE business_rules + validations + "
        f"error_paths together on a requirement; required lists seen: "
        f"{required_lists}"
    )

    # Object-form ID patterns must be enforced (RULE-/VAL-/ERR- + 3 digits).
    assert "^RULE-[0-9]{3}$" in blob or "^RULE-\\d{3}$" in blob, (
        "enriched schema does not enforce the RULE-NNN id pattern"
    )


# ===========================================================================
# VALIDATION TIER  (needs jsonschema; skips cleanly when absent)
# ===========================================================================
@_NEEDS_JSONSCHEMA
def test_t3_enriched_rejects_requirement_missing_business_rules(repo_root):
    """A requirement with NO business_rules must FAIL the enriched profile."""
    if not os.path.exists(_enriched_schema_path(repo_root)):
        pytest.fail("enriched overlay schema absent; cannot validate")
    enriched, resolver = _resolve_enriched_for_validation(repo_root)

    req = _well_formed_enriched_req()
    del req["business_rules"]  # missing required rich field
    doc = _wrap_single_req(req)

    validator = Draft7Validator(enriched, resolver=resolver)
    errors = list(validator.iter_errors(doc))
    assert errors, (
        "enriched profile accepted a requirement missing business_rules "
        "(it must be rejected)"
    )


@_NEEDS_JSONSCHEMA
def test_t3_enriched_rejects_bad_rule_id_pattern(repo_root):
    """A business_rule item with a malformed id ('RULE-1') or no id must FAIL."""
    if not os.path.exists(_enriched_schema_path(repo_root)):
        pytest.fail("enriched overlay schema absent; cannot validate")
    enriched, resolver = _resolve_enriched_for_validation(repo_root)
    validator = Draft7Validator(enriched, resolver=resolver)

    # (a) wrong id pattern
    req_bad_pattern = _well_formed_enriched_req()
    req_bad_pattern["business_rules"] = [
        {"id": "RULE-1", "statement": "bad id pattern"}
    ]
    assert list(validator.iter_errors(_wrap_single_req(req_bad_pattern))), (
        "enriched profile accepted business_rule id 'RULE-1' (must match "
        "^RULE-[0-9]{3}$)"
    )

    # (b) missing id entirely
    req_no_id = _well_formed_enriched_req()
    req_no_id["business_rules"] = [{"statement": "no id at all"}]
    assert list(validator.iter_errors(_wrap_single_req(req_no_id))), (
        "enriched profile accepted a business_rule item with no id"
    )


@_NEEDS_JSONSCHEMA
def test_t3_enriched_accepts_well_formed_requirement(repo_root):
    """A fully-formed enriched requirement (object-form items) must PASS."""
    if not os.path.exists(_enriched_schema_path(repo_root)):
        pytest.fail("enriched overlay schema absent; cannot validate")
    enriched, resolver = _resolve_enriched_for_validation(repo_root)
    validator = Draft7Validator(enriched, resolver=resolver)

    doc = _wrap_single_req(_well_formed_enriched_req())
    errors = list(validator.iter_errors(doc))
    assert not errors, (
        "enriched profile rejected a well-formed enriched requirement:\n  "
        + "\n  ".join(e.message for e in errors)
    )
