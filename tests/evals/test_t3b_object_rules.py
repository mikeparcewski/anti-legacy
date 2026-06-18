r"""
EVAL_T3b_object_rules  --  Theory T3 RESIDUAL closure (string->object rule items).

What this eval pins down (the unclosed residual of T3)
------------------------------------------------------
T3 proved the rich content (business_rules / validations / error_paths) was
off-schema. The base + enriched schema files now model OBJECT-form items, but
the residual is that the producer still emits STRING items, the REAL shipped
graph still fails the enriched profile, the additive forward-compat fields
(confidence / provenance) are not yet whitelisted, and the GATE_1_DESIGN
validator still does a weak truthiness check instead of real jsonschema
validation. These tests are RED against current code and turn GREEN only when:

  1. schemas/requirements-graph.enriched.schema.json (and the object branch of
     the base anyOf) whitelist the additive OPTIONAL fields confidence
     (number 0..1) + provenance {source_app, program, ref} under their
     additionalProperties:false rule objects -- WITHOUT making them required,
     and WITHOUT loosening id-pattern / required {id, statement} enforcement.

  2. The REAL shipped demo graph (tests/evals/fixtures/real_demo_requirements_graph.json)
     validates against the enriched profile with ZERO errors (i.e. the 345 string items
     have been migrated to {id, statement} object form).

  3. scripts/validator_discovery.py's GATE_1_DESIGN performs a real schema
     validation: it REJECTS string-form business_rules (today they are truthy
     so the weak check passes) and it does NOT silently skip when jsonschema is
     absent (it must surface an error instead).

Tiers
-----
  * STRUCTURE tier (no third-party deps): parses the schema JSON and asserts
    the additive confidence/provenance props are declared while required stays
    exactly {id, statement}. RED today: those props are absent, so an
    object-form rule carrying them would be rejected by additionalProperties.
  * VALIDATION tier (needs ``jsonschema``): proves, via Draft7Validator, that
    the enriched profile ACCEPTS object-form items (with optional confidence/
    provenance) and that the REAL graph passes with 0 errors. RED today: the
    real graph has 345 string-form violations; the additive fields are
    rejected.
  * GATE tier (needs scripts/validator_discovery.py): drives the real
    GATE_1_DESIGN against a temp workspace and asserts string-form rules are
    REJECTED and object-form rules are ACCEPTED. RED today: the gate's weak
    `if not req.get("business_rules")` truthiness passes string lists.

Determinism
-----------
Reads only in-repo schema/graph files and writes to a pytest tmp_path; no
network, clock, or randomness. The validation/gate tiers skip cleanly (never
error) when jsonschema is unavailable so the suite stays deterministic and
CI-safe, but the T3 fix declares + installs jsonschema so they RUN.
"""
import json
import os

import pytest

# jsonschema is the dependency the T3 fix declares (requirements.txt). It is
# intentionally optional here so the suite never errors on a bare env.
try:
    import jsonschema  # noqa: F401
    from jsonschema import Draft7Validator, RefResolver
    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover - depends on env
    _HAS_JSONSCHEMA = False

_NEEDS_JSONSCHEMA = pytest.mark.skipif(
    not _HAS_JSONSCHEMA,
    reason="jsonschema not installed (declared by the T3 fix in requirements.txt)",
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


def _real_graph_path(repo_root):
    # The shipped public demo graph (Apache Kafka + Pulsar producer APIs). Relocated out
    # of the (now-gitignored, local-only) .anti-legacy/ workspace into the eval fixtures so
    # this test still validates the real graph on a fresh clone.
    return os.path.join(
        repo_root, "tests", "evals", "fixtures", "real_demo_requirements_graph.json"
    )


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _enriched_defs(repo_root):
    return _load_json(_enriched_schema_path(repo_root)).get("$defs", {})


def _base_object_branch(def_node):
    """Given a base-schema $defs node that is an anyOf of [string, object],
    return the object branch's `properties` dict (or {} if not found)."""
    for member in def_node.get("anyOf", []):
        if isinstance(member, dict) and member.get("type") == "object":
            return member.get("properties", {}), member.get("required", [])
    return {}, []


def _resolve_enriched_for_validation(repo_root):
    """Load the enriched schema and a resolver so any cross-file $ref to the
    base file resolves (belt-and-suspenders; the enriched $defs are self
    contained today)."""
    base = _load_json(_base_schema_path(repo_root))
    enriched = _load_json(_enriched_schema_path(repo_root))
    store = {
        "requirements-graph.schema.json": base,
        base.get("$id", "requirements-graph.schema.json"): base,
    }
    resolver = RefResolver(base_uri="", referrer=enriched, store=store)
    return enriched, resolver


def _wrap_single_req(req):
    """Wrap one requirement object into a full requirements-graph document."""
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


def _object_form_req(with_provenance=True, with_confidence=True):
    """A fully object-form enriched requirement carrying the OPTIONAL additive
    forward-compat fields (confidence + provenance). These additive fields are
    the whole point of the residual: under additionalProperties:false they MUST
    be whitelisted or the schema rejects them."""
    rule = {"id": "RULE-001", "statement": "Accounts need a unique id."}
    val = {
        "id": "VAL-001",
        "statement": "Status in {A,C,S}.",
        "field": "account_status",
        "error_ref": "ERR-001",
    }
    err = {"id": "ERR-001", "statement": "Reject invalid status.", "code": "12"}
    if with_confidence:
        rule["confidence"] = 0.9
        val["confidence"] = 0.8
        err["confidence"] = 0.7
    if with_provenance:
        prov = {"source_app": "demoapp", "program": "PROG_A", "ref": "L42"}
        rule["provenance"] = dict(prov)
        val["provenance"] = dict(prov)
        err["provenance"] = dict(prov)
    return {
        "title": "Manage customer account lifecycle",
        "description": "Create/update/validate customer accounts.",
        "legacy_components": ["demoapp:PROG_A"],
        "data_access": ["CUSTOMER"],
        "dependencies": [],
        "business_rules": [rule],
        "validations": [val],
        "error_paths": [err],
    }


# ===========================================================================
# STRUCTURE TIER  -- additive confidence/provenance must be whitelisted, and
# required must stay exactly {id, statement}. No jsonschema needed.
# These are RED today: confidence/provenance are absent from the property maps,
# so under additionalProperties:false the schema cannot carry them.
# ===========================================================================
def test_t3b_enriched_defs_whitelist_confidence_and_provenance(repo_root):
    """Each enriched $def (rule/validation/errorPath) must declare the OPTIONAL
    additive fields confidence + provenance in its property map, because all
    three set additionalProperties:false."""
    defs = _enriched_defs(repo_root)
    for name in ("rule", "validation", "errorPath"):
        node = defs.get(name, {})
        assert node, f"enriched $defs is missing the '{name}' shape"
        # additionalProperties:false is the constraint that forces whitelisting.
        assert node.get("additionalProperties") is False, (
            f"$defs.{name} is expected to set additionalProperties:false "
            "(that is what makes the additive-field whitelist load-bearing)"
        )
        props = node.get("properties", {})
        assert "confidence" in props, (
            f"$defs.{name} does not whitelist the additive 'confidence' field; "
            "an object-form item carrying confidence would be REJECTED by "
            "additionalProperties:false"
        )
        conf = props["confidence"]
        assert conf.get("type") == "number", (
            f"$defs.{name}.confidence must be a number, got {conf!r}"
        )
        assert conf.get("minimum") == 0 and conf.get("maximum") == 1, (
            f"$defs.{name}.confidence must be bounded to [0,1], got {conf!r}"
        )
        assert "provenance" in props, (
            f"$defs.{name} does not whitelist the additive 'provenance' object; "
            "object-form items carrying provenance would be REJECTED"
        )
        prov = props["provenance"]
        assert prov.get("type") == "object", (
            f"$defs.{name}.provenance must be an object, got {prov!r}"
        )
        prov_props = prov.get("properties", {})
        for sub in ("source_app", "program", "ref"):
            assert sub in prov_props, (
                f"$defs.{name}.provenance must allow '{sub}'"
            )


def test_t3b_additive_fields_stay_optional(repo_root):
    """provenance is ADDITIVE everywhere; confidence is ADDITIVE on
    validation/errorPath. ISS-11 tightens ONLY the `rule` $def to additionally
    REQUIRE a numeric confidence (a confidence-less business_rule is a hard
    GATE_1 failure), so rule.required == {id, statement, confidence}; the other
    two $defs keep required == {id, statement}. provenance must never become
    required on any $def."""
    expected_required = {
        "rule": {"id", "statement", "confidence"},  # ISS-11: confidence required on rules
        "validation": {"id", "statement"},
        "errorPath": {"id", "statement"},
    }
    defs = _enriched_defs(repo_root)
    for name in ("rule", "validation", "errorPath"):
        req = set(defs.get(name, {}).get("required", []))
        assert req == expected_required[name], (
            f"$defs.{name}.required must be exactly {sorted(expected_required[name])}; "
            f"provenance must NOT become required (and confidence stays optional "
            f"on validation/errorPath). Got: {sorted(req)}"
        )


def test_t3b_base_object_branch_mirrors_additive_fields(repo_root):
    """The OBJECT branch of each base-schema anyOf must mirror the same additive
    confidence + provenance whitelist (base + enriched object branches stay
    structurally identical; the base just keeps the string alternative)."""
    base = _load_json(_base_schema_path(repo_root))
    defs = base.get("$defs", {})
    for name in ("rule", "validation", "errorPath"):
        node = defs.get(name, {})
        assert "anyOf" in node, (
            f"base $defs.{name} should keep the string|object anyOf (the base "
            "profile stays transitional)"
        )
        # The string branch must survive (transitional base profile).
        assert any(
            m.get("type") == "string" for m in node["anyOf"]
        ), f"base $defs.{name} dropped its transitional string branch"
        props, required = _base_object_branch(node)
        assert set(required) == {"id", "statement"}, (
            f"base $defs.{name} object branch required must stay "
            f"{{id, statement}}, got {sorted(required)}"
        )
        assert "confidence" in props and "provenance" in props, (
            f"base $defs.{name} object branch does not mirror the additive "
            "confidence/provenance whitelist that the enriched profile adds"
        )


# ===========================================================================
# VALIDATION TIER  -- prove the enriched profile ACCEPTS object-form items with
# the additive fields and REJECTS regressions; prove the REAL graph passes.
# RED today: additive fields rejected + real graph has 345 string violations.
# ===========================================================================
@_NEEDS_JSONSCHEMA
def test_t3b_enriched_accepts_object_form_with_additive_fields(repo_root):
    """An object-form requirement carrying the optional confidence + provenance
    fields must PASS the enriched profile (proves the whitelist additions did
    not break acceptance, and proves additionalProperties:false now allows
    them)."""
    enriched, resolver = _resolve_enriched_for_validation(repo_root)
    validator = Draft7Validator(enriched, resolver=resolver)
    doc = _wrap_single_req(_object_form_req(with_provenance=True, with_confidence=True))
    errors = list(validator.iter_errors(doc))
    assert not errors, (
        "enriched profile rejected a well-formed object-form requirement "
        "carrying optional confidence/provenance (they must be whitelisted "
        "under additionalProperties:false):\n  "
        + "\n  ".join(e.message for e in errors)
    )


@_NEEDS_JSONSCHEMA
def test_t3b_enriched_still_rejects_string_form_rules(repo_root):
    """The enriched (post-migration) profile must NOT accept the transitional
    string form for business_rules -- string items are exactly what the
    residual migrates away from."""
    enriched, resolver = _resolve_enriched_for_validation(repo_root)
    validator = Draft7Validator(enriched, resolver=resolver)
    req = _object_form_req()
    req["business_rules"] = ["RULE-001: a legacy string item"]
    errors = list(validator.iter_errors(_wrap_single_req(req)))
    assert errors, (
        "enriched profile accepted a STRING-form business_rule; the enriched "
        "overlay must be object-only"
    )


@_NEEDS_JSONSCHEMA
def test_t3b_enriched_rejects_out_of_range_confidence(repo_root):
    """The additive confidence field must stay bounded to [0,1] -- a value of
    1.5 must FAIL (proves the additive edit did not weaken validation)."""
    enriched, resolver = _resolve_enriched_for_validation(repo_root)
    validator = Draft7Validator(enriched, resolver=resolver)
    req = _object_form_req()
    req["business_rules"][0]["confidence"] = 1.5  # out of [0,1]
    errors = list(validator.iter_errors(_wrap_single_req(req)))
    assert errors, (
        "enriched profile accepted confidence=1.5 (must be bounded to [0,1])"
    )


@_NEEDS_JSONSCHEMA
def test_t3b_enriched_still_enforces_rule_id_pattern_with_additive_fields(repo_root):
    """Adding confidence/provenance must NOT loosen id enforcement: a bad id
    pattern still fails even when the additive fields are present."""
    enriched, resolver = _resolve_enriched_for_validation(repo_root)
    validator = Draft7Validator(enriched, resolver=resolver)
    req = _object_form_req()
    req["business_rules"][0]["id"] = "RULE-1"  # wrong pattern
    errors = list(validator.iter_errors(_wrap_single_req(req)))
    assert errors, (
        "enriched profile accepted business_rule id 'RULE-1' even though the "
        "additive fields were added; id pattern ^RULE-[0-9]{3,6}$ must hold"
    )


@_NEEDS_JSONSCHEMA
def test_t3b_real_graph_passes_enriched_profile(repo_root):
    """THE true T3 close: the REAL shipped requirements_graph.json must validate
    against the enriched profile with ZERO errors. RED today: the graph carries
    345 string-form items (174 business_rules + 97 validations + 74 error_paths)
    that the enriched object-only profile rejects."""
    real_path = _real_graph_path(repo_root)
    # M7: the absence of the SHIPPED graph is a regression, not an environmental
    # excuse. Hard-fail (RED) instead of skipping, so this contract test cannot
    # be defeated by the graph simply disappearing. (The jsonschema availability
    # gate stays a legitimate skip via the _NEEDS_JSONSCHEMA marker.)
    assert os.path.exists(real_path), (
        f"shipped requirements_graph.json is MISSING ({real_path}); the enriched-"
        "profile contract this test guards cannot pass by the graph disappearing"
    )
    enriched, resolver = _resolve_enriched_for_validation(repo_root)
    rg = _load_json(real_path)
    errors = sorted(
        Draft7Validator(enriched, resolver=resolver).iter_errors(rg),
        key=lambda e: list(e.path),
    )
    sample = "\n  ".join(
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors[:8]
    )
    assert errors == [], (
        f"REAL requirements_graph.json fails the enriched profile with "
        f"{len(errors)} error(s); the string->object migration is incomplete. "
        f"First failures:\n  {sample}"
    )


@_NEEDS_JSONSCHEMA
def test_t3b_real_graph_business_rules_are_objects(repo_root):
    """Content-preservation guard: every business_rules / validations /
    error_paths item in the REAL graph must be an OBJECT with id + non-empty
    statement (no item lost its content during migration)."""
    real_path = _real_graph_path(repo_root)
    # M7: same as above -- a missing shipped graph is RED, not a green skip, so
    # the content-preservation guard cannot be silenced by deleting the graph.
    assert os.path.exists(real_path), (
        f"shipped requirements_graph.json is MISSING ({real_path}); the content-"
        "preservation guard this test enforces cannot pass by the graph "
        "disappearing"
    )
    rg = _load_json(real_path)
    string_items = []
    empty_statements = []
    for dom in rg.get("domains", {}).values():
        for req_id, req in dom.get("requirements", {}).items():
            for field in ("business_rules", "validations", "error_paths"):
                for item in req.get(field, []):
                    if isinstance(item, str):
                        string_items.append(f"{req_id}/{field}: {item[:50]}")
                    elif isinstance(item, dict):
                        if not item.get("statement", "").strip():
                            empty_statements.append(f"{req_id}/{field}/{item.get('id')}")
    assert not string_items, (
        f"{len(string_items)} rule items are still STRING-form (must be objects)."
        f" e.g. {string_items[:3]}"
    )
    assert not empty_statements, (
        f"{len(empty_statements)} migrated items have an empty statement "
        f"(content lost): {empty_statements[:3]}"
    )


# ===========================================================================
# GATE TIER  -- the validator must do REAL validation, not weak truthiness.
# RED today: GATE_1_DESIGN's `if not req.get("business_rules")` passes string
# lists, so string-form rules sail through.
# ===========================================================================
def _write_workspace(tmp_path, business_rules):
    """Build a minimal valid GATE_1_DESIGN workspace whose single requirement
    carries the given `business_rules` value. Returns the workspace path."""
    ws = tmp_path / "ws"
    req_dir = ws / ".anti-legacy" / "requirements"
    req_dir.mkdir(parents=True)
    rg = {
        "metadata": {"migration_mode": "functional"},
        "domains": {
            "Domain_A": {
                "requirements": {
                    "REQ_1": {
                        "title": "T",
                        "description": "D",
                        "legacy_components": ["billing.cbl"],
                        "data_access": [],
                        "dependencies": [],
                        "business_rules": business_rules,
                        "validations": [],
                        "error_paths": [],
                    }
                },
                "entities": {
                    "ACCT": {
                        "description": "x",
                        "fields": [
                            {"name": "id", "type": "string", "description": "pk"}
                        ],
                    }
                },
            }
        },
    }
    (req_dir / "requirements_graph.json").write_text(json.dumps(rg))
    (req_dir / "blueprint.json").write_text(json.dumps({
        "components": {
            "BillingComponent": {
                "fields": [{"name": "salary_amount", "type": "DECIMAL(11,2)"}]
            }
        }
    }))
    (req_dir / "nfrs.md").write_text("# NFRs")
    (ws / ".anti-legacy" / "config.json").write_text(json.dumps({"target_stack": "python"}))
    return str(ws)


@_NEEDS_JSONSCHEMA
def test_t3b_gate1_rejects_string_form_rules(tmp_path):
    """GATE_1_DESIGN must REJECT a requirement whose business_rules is the
    legacy STRING list. RED today: the weak `if not req.get("business_rules")`
    truthiness check passes any non-empty list, so string-form data is accepted.
    """
    from antilegacy_core import validator_discovery  # on sys.path via tests/evals/conftest.py
    ws = _write_workspace(tmp_path, ["RULE-001: a legacy string rule"])
    runner = validator_discovery.ValidatorRunner(
        ws,
        os.path.join(ws, ".anti-legacy", "config.json"),
        os.path.join(ws, ".anti-legacy", "manifest.json"),
    )
    success = runner.run_gate("GATE_1_DESIGN")
    assert success is False, (
        "GATE_1_DESIGN ACCEPTED string-form business_rules; it must run a real "
        "schema validation that rejects the transitional string form"
    )


@_NEEDS_JSONSCHEMA
def test_t3b_gate1_accepts_object_form_rules(tmp_path):
    """The companion: GATE_1_DESIGN must ACCEPT a requirement whose
    business_rules is object form {id, statement}. This guards the gate edit
    from over-rejecting once real validation is wired."""
    from antilegacy_core import validator_discovery
    ws = _write_workspace(
        tmp_path,
        # ISS-11: business_rules now REQUIRE a numeric confidence, so a
        # well-formed object-form rule must carry one for GATE_1 to accept it.
        [{"id": "RULE-001", "statement": "an object-form rule", "confidence": 0.9}],
    )
    runner = validator_discovery.ValidatorRunner(
        ws,
        os.path.join(ws, ".anti-legacy", "config.json"),
        os.path.join(ws, ".anti-legacy", "manifest.json"),
    )
    success = runner.run_gate("GATE_1_DESIGN")
    assert success is True, (
        "GATE_1_DESIGN REJECTED well-formed object-form business_rules; the "
        "real-validation gate must accept the post-migration object form"
    )


def test_t3b_gate1_source_uses_real_validation_not_truthiness(repo_root):
    """Static guard (no deps): validator_discovery.py's GATE_1 must invoke real
    schema validation (Draft7Validator) rather than rely solely on the weak
    `if not req.get("business_rules")` truthiness. RED today: the source still
    contains only the truthiness check and never references Draft7Validator."""
    src_path = os.path.join(repo_root, "skills", "anti-legacy-expert", "scripts",
                            "antilegacy_core", "validator_discovery.py")
    src = open(src_path, "r", encoding="utf-8").read()
    assert "Draft7Validator" in src, (
        "validator_discovery.py never references Draft7Validator -- GATE_1 still "
        "relies on weak truthiness instead of validating against the enriched "
        "schema (the silent-skip / string-form residual is open)"
    )
    # The enriched schema must be the thing it validates against.
    assert "requirements-graph.enriched.schema.json" in src, (
        "validator_discovery.py never loads the enriched schema; GATE_1 cannot "
        "be doing real enriched-profile validation"
    )
