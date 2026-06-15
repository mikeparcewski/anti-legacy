"""
EVAL_T2_entity_colocation  --  Theory T2 (a requirement cannot see its own
entities because domains are derived from data files, not capabilities).

Theory
------
Domains are built from shared data assets, so each program is placed in ONE
asset's domain (structural uses last-write-wins; functional uses the
"primary asset" domain), while the OTHER assets it accesses become standalone
entity-only domains. The result: a requirement's ``data_access`` lists assets
that live in DIFFERENT domains -- a requirement cannot see the entities it
operates on in its own domain. (Measured on real data: 41/60 req-asset pairs
are cross-domain.)

The co-location invariant (post-fix behavior these assertions encode)
---------------------------------------------------------------------
For EVERY requirement, every name in ``req['data_access']`` must resolve to an
entity present in the SAME domain as that requirement:

    set(req['data_access'])  is a subset of  set(domain['entities'])

Additionally:
  * ``data_access`` must contain no duplicates (sorted(set(...))).
  * In FUNCTIONAL mode no domain may be entity-only (entities but zero
    requirements) -- those data-file mirror domains must be folded away.
    (The no-entity-only assertion is checked on FUNCTIONAL output only, to stay
    clear of the structural Domain_cust_file invariant that test_integration
    pins -- see the design risk note.)

Red today
---------
On the synthetic graph, PROG_A accesses CUSTOMER, CONFIG, LEDGER but its
requirement lands in only ONE of their domains (Domain_ledger structurally,
Domain_customer functionally) whose ``entities`` holds a single asset -> the
subset assertion FAILS. Functional mode also leaves Domain_config / Domain_ledger
as entity-only domains -> the no-entity-only assertion FAILS.

Determinism
-----------
In-process construction of GraphNormalizer over the in-repo synthetic fixture;
no network, clock, or randomness.
"""
import json
import os

import pytest

import graph_normalizer  # on sys.path via conftest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build(fixtures_dir, mode):
    with open(os.path.join(fixtures_dir, "code_graph.json"), "r",
              encoding="utf-8") as f:
        code_graph = json.load(f)
    n = graph_normalizer.GraphNormalizer(code_graph, mode=mode)
    n.normalize()
    return n.requirements_graph


def _iter_reqs(graph):
    """Yield (domain_name, domain, req_id, req)."""
    for dname, domain in graph.get("domains", {}).items():
        for rid, req in domain.get("requirements", {}).items():
            yield dname, domain, rid, req


# ---------------------------------------------------------------------------
# Co-location invariant: data_access subset of OWN-domain entities
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["structural", "functional"])
def test_t2_data_access_resolves_in_own_domain(fixtures_dir, mode):
    """Every asset a requirement accesses must be an entity in the SAME
    domain as that requirement."""
    graph = _build(fixtures_dir, mode)

    # Sanity: the fixture's tri-asset requirement actually got produced.
    reqs = list(_iter_reqs(graph))
    assert reqs, f"{mode}: no requirements produced from the synthetic graph"

    violations = []
    for dname, domain, rid, req in reqs:
        accessed = set(req.get("data_access", []))
        domain_entities = set(domain.get("entities", {}).keys())
        missing = accessed - domain_entities
        if missing:
            violations.append(
                f"[{mode}] {dname}/{rid}: data_access {sorted(accessed)} "
                f"not co-located -- {sorted(missing)} live outside this "
                f"domain (domain entities: {sorted(domain_entities)})"
            )

    assert not violations, (
        "co-location invariant violated -- requirements cannot see their own "
        "entities:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize("mode", ["structural", "functional"])
def test_t2_data_access_has_no_duplicates(fixtures_dir, mode):
    """data_access must be de-duplicated (sorted(set(...)))."""
    graph = _build(fixtures_dir, mode)
    dupes = []
    for dname, _domain, rid, req in _iter_reqs(graph):
        da = req.get("data_access", [])
        if len(da) != len(set(da)):
            dupes.append(f"[{mode}] {dname}/{rid}: data_access has duplicates: {da}")
    assert not dupes, "data_access not de-duplicated:\n  " + "\n  ".join(dupes)


# ---------------------------------------------------------------------------
# Functional mode: no entity-only (data-file mirror) domains
# ---------------------------------------------------------------------------
def test_t2_functional_has_no_entity_only_domains(fixtures_dir):
    """In FUNCTIONAL mode, a domain that holds entities but ZERO requirements
    is a data-file mirror that must be folded into the capability that owns the
    entity. (Checked on functional output only, by design.)"""
    graph = _build(fixtures_dir, "functional")

    entity_only = []
    for dname, domain in graph.get("domains", {}).items():
        has_entities = bool(domain.get("entities"))
        has_reqs = bool(domain.get("requirements"))
        if has_entities and not has_reqs:
            entity_only.append(
                f"{dname} (entities={sorted(domain['entities'].keys())}, 0 reqs)"
            )

    assert not entity_only, (
        "functional mode left entity-only (data-file mirror) domains that "
        "strand entities away from the capability that uses them:\n  "
        + "\n  ".join(entity_only)
    )
