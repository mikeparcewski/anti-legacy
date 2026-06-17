"""
Integration round-trip eval (T1 + T2 + prompt-layer).

This file runs the *real* ``scripts/graph_normalizer.py`` end-to-end (via its
CLI, in a subprocess) on the synthetic ``fixtures/code_graph.json`` and asserts
that the produced requirements graph is **capability-shaped** — the intended
*functional* output of the pipeline:

  * mode is resolved from ``config.json`` (T1),
  * programs are merged into business **capabilities** (T1),
  * every entity a requirement accesses is **co-located** in the requirement's
    own domain (T2),
  * there are **no empty (requirement-less) domains** (T2).

It also encodes the mechanically-checkable slice of the prompt-layer fixes:
the agent-facing ``SKILL.md`` files must point the pipeline at *functional*
mode and must say that build/compile checks do **not** prove ``business_rules``
are implemented.

Every test asserts the *correct, post-fix* behavior, so each one is RED against
the current (unfixed) code/skills — that red is the proof of the theory.

Determinism: no network, no clocks, no randomness. The normalizer is driven on
a tiny hand-authored fixture written to ``tmp_path``; nothing under
``.anti-legacy/`` is read or mutated.
"""
import json
import os
import subprocess
import sys

import pytest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _normalizer_path(scripts_dir):
    return os.path.join(scripts_dir, "graph_normalizer.py")


def _run_normalizer(scripts_dir, input_path, output_path, *, config_path=None,
                    mode=None):
    """Invoke graph_normalizer.py via its CLI in a subprocess.

    Mirrors how the real pipeline (skills/graph-translator) shells out to the
    script. Returns the CompletedProcess so callers can inspect the exit code.
    """
    cmd = [
        sys.executable,
        "-m", "antilegacy_core.graph_normalizer",
        "--input", input_path,
        "--output", output_path,
    ]
    if config_path is not None:
        cmd += ["--config", config_path]
    if mode is not None:
        cmd += ["--mode", mode]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        # Run from a neutral cwd (tmp's parent) so the script cannot pick up a
        # stray real config.json / .anti-legacy from the repo root.
        cwd=os.path.dirname(input_path),
    )


def _iter_requirements(graph):
    """Yield (domain_name, domain, req_id, req) over every requirement."""
    for dname, domain in graph.get("domains", {}).items():
        for rid, req in domain.get("requirements", {}).items():
            yield dname, domain, rid, req


def _read_skill(repo_root, *parts):
    path = os.path.join(repo_root, *parts)
    with open(path, "r", encoding="utf-8") as fh:
        return path, fh.read()


# --------------------------------------------------------------------------- #
# Fixtures local to this eval
# --------------------------------------------------------------------------- #
@pytest.fixture
def code_graph_path(fixtures_dir):
    """Absolute path to the shared synthetic code graph fixture."""
    return os.path.join(fixtures_dir, "code_graph.json")


@pytest.fixture
def functional_run(tmp_path, scripts_dir, code_graph_path):
    """Run the real normalizer CLI in *functional* mode selected via config.json.

    Copies the synthetic code graph into tmp_path, writes a
    ``config.json {"migration_mode": "functional"}`` next to it, runs the CLI
    with ``--config`` but **no** ``--mode``, and returns
    ``(completed_process, output_graph_or_None)``.

    This is the integration the whole file hinges on: with no ``--mode`` flag,
    the *intended* behavior is that mode is read from config -> functional.
    Today the CLI has neither ``--config`` nor a functional default, so this
    run is the T1 red.
    """
    # Stage inputs in an isolated tmp dir.
    with open(code_graph_path, "r", encoding="utf-8") as fh:
        code_graph = json.load(fh)
    input_path = str(tmp_path / "code_graph.json")
    with open(input_path, "w", encoding="utf-8") as fh:
        json.dump(code_graph, fh)

    config_path = str(tmp_path / "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"migration_mode": "functional"}, fh)

    output_path = str(tmp_path / "requirements_graph.json")
    proc = _run_normalizer(
        scripts_dir, input_path, output_path, config_path=config_path
    )

    graph = None
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as fh:
            graph = json.load(fh)
    return proc, graph


@pytest.fixture
def functional_graph(functional_run, code_graph_path):
    """A *functional-mode* requirements graph for the co-location (T2) checks.

    Prefers the real CLI subprocess output (the full integration path). If the
    CLI run produced no graph — which is the case today, because the CLI does
    not yet accept ``--config`` (T1 unfixed) — it falls back to driving the
    normalizer's public ``GraphNormalizer(..., mode="functional")`` API
    in-process on the same fixture.

    This makes the T2 invariant provable *independently* of the T1 CLI wiring:
    the co-location defect is in functional normalization itself, and these
    tests stay RED on the genuine functional output regardless of how it was
    produced. Once T1 lands, the subprocess output is used directly.
    """
    _, graph = functional_run
    if graph is not None:
        return graph

    # Fallback: exercise the in-process functional normalizer so T2 is tested
    # on real functional output even before the T1 CLI fix lands.
    from antilegacy_core import graph_normalizer as gn  # scripts/ is on sys.path via conftest

    with open(code_graph_path, "r", encoding="utf-8") as fh:
        code_graph = json.load(fh)
    normalizer = gn.GraphNormalizer(code_graph, mode="functional")
    normalizer.normalize()
    return normalizer.requirements_graph


# =========================================================================== #
# T1 — mode is resolved from config.json; output is capability-shaped
# =========================================================================== #
def test_functional_mode_resolved_from_config(functional_run):
    """T1: ``--config {migration_mode: functional}`` + no ``--mode`` => functional.

    RED today: ``main()`` has no ``--config`` arg, so passing it makes argparse
    exit non-zero (unrecognized argument) and never produces output. Even if
    the arg existed, ``--mode`` defaults to ``structural`` and config is never
    read. Either way the assertions below cannot be satisfied.
    """
    proc, graph = functional_run

    # The CLI must accept --config and succeed (argparse rejects unknown args
    # with exit code 2 and emits nothing).
    assert proc.returncode == 0, (
        "normalizer CLI did not accept --config / failed to run "
        f"(exit={proc.returncode}). stderr:\n{proc.stderr}"
    )
    assert graph is not None, "normalizer produced no output graph"

    assert graph["metadata"]["migration_mode"] == "functional", (
        "config.json migration_mode=functional was not honored; got "
        f"{graph['metadata'].get('migration_mode')!r} "
        "(mode must resolve as flag > config > default)."
    )


def test_output_is_capability_shaped(functional_run):
    """T1: functional output merges programs into ONE capability, not 1:1 reqs.

    On the synthetic graph PROG_A calls PROG_B (single caller) and both share
    CUSTOMER, so functional mode must leaf-merge them into exactly one CAP_*
    capability node carrying both legacy components — vs the structural
    1-program-per-REQ skeleton.
    """
    proc, graph = functional_run
    assert graph is not None, (
        "normalizer produced no output graph "
        f"(exit={proc.returncode}); stderr:\n{proc.stderr}"
    )

    req_ids = [rid for _, _, rid, _ in _iter_requirements(graph)]

    # Capability ids, not code-equivalent REQ_PROG_* ids.
    assert any(rid.startswith("CAP_") for rid in req_ids), (
        "expected capability-shaped CAP_* requirement ids (functional), "
        f"got {req_ids!r} — looks like the 1:1 structural code skeleton."
    )
    assert not any(rid in ("REQ_PROG_A", "REQ_PROG_B") for rid in req_ids), (
        "found 1:1 structural REQ_PROG_* nodes; functional mode must merge "
        f"PROG_A/PROG_B into a single capability. ids={req_ids!r}"
    )

    # PROG_A + PROG_B must be merged into a single capability node.
    merging_caps = [
        req for _, _, _, req in _iter_requirements(graph)
        if any(c.endswith("PROG_A") for c in req.get("legacy_components", []))
    ]
    assert len(merging_caps) == 1, (
        "PROG_A must live in exactly one capability node, "
        f"found {len(merging_caps)}."
    )
    components = merging_caps[0].get("legacy_components", [])
    assert any(c.endswith("PROG_A") for c in components) and \
        any(c.endswith("PROG_B") for c in components), (
        "the capability owning PROG_A must also own its single-caller leaf "
        f"PROG_B (leaf-merge); legacy_components={components!r}"
    )


# =========================================================================== #
# T2 — entities are co-located with the requirements that access them
# =========================================================================== #
def test_entities_colocated_with_requirements(functional_graph):
    """T2: every name in a req's ``data_access`` is an entity in its OWN domain.

    RED today: functional mode builds entity-only ``Domain_{asset}`` domains per
    asset and routes the merged capability into the *primary* asset's domain,
    so the capability that accesses CUSTOMER+CONFIG+LEDGER lives in
    ``Domain_customer`` whose ``entities`` only contains CUSTOMER. CONFIG and
    LEDGER are stranded elsewhere -> the subset invariant fails.
    """
    graph = functional_graph

    offenders = []
    for dname, domain, rid, req in _iter_requirements(graph):
        accessed = set(req.get("data_access", []))
        in_domain = set(domain.get("entities", {}).keys())
        missing = accessed - in_domain
        if missing:
            offenders.append((dname, rid, sorted(missing), sorted(in_domain)))

    assert not offenders, (
        "co-location invariant violated — these requirements access entities "
        "that are NOT in their own domain:\n"
        + "\n".join(
            f"  domain={d} req={r}: missing {m} (domain entities={e})"
            for d, r, m, e in offenders
        )
    )


def test_no_empty_domains_in_functional_output(functional_graph):
    """T2: functional output has NO requirement-less (entity-only) domains.

    RED today: per-asset ``Domain_config`` / ``Domain_ledger`` are emitted with
    entities but zero requirements (the capability that owns those entities
    landed in ``Domain_customer``). Functional-mode suppression of entity-only
    domains is what makes this green.
    """
    graph = functional_graph

    empty = [
        dname for dname, domain in graph.get("domains", {}).items()
        if not domain.get("requirements")
    ]
    assert not empty, (
        "functional output contains requirement-less (entity-only) domains: "
        f"{sorted(empty)} — these strand entities away from the capability "
        "that uses them. Suppress/fold them in functional mode."
    )


# =========================================================================== #
# Prompt-layer (mechanically checkable) — the agent instructions must point the
# pipeline at functional mode and must not claim the build proves the rules.
# =========================================================================== #
def test_graph_translator_skill_drives_functional_mode(repo_root):
    """Prompt T1: graph-translator must invoke the normalizer in functional mode.

    RED today: the Step 3 normalizer invocation passes NO ``--mode`` (so it
    silently defaults to structural) and the prose calls the output
    "structural clustering". The fix reads mode from config and names
    *functional* as the intended default.
    """
    path, text = _read_skill(repo_root, "skills", "graph-translator", "SKILL.md")
    lowered = text.lower()

    assert "functional" in lowered, (
        f"{path} never mentions 'functional' mode; the graph-translator skill "
        "must tell the agent the requirements graph is a capability plan "
        "(functional), not a 1:1 code skeleton."
    )
    # The normalizer must be wired to honor migration_mode rather than silently
    # defaulting to structural.
    assert ("migration_mode" in lowered) or ("--mode" in text), (
        f"{path} runs scripts/graph_normalizer.py without selecting a mode "
        "from config (no '--mode' / 'migration_mode'); it will default to "
        "structural. Wire the invocation to migration_mode (functional)."
    )


def test_target_review_skill_scopes_build_vs_rules(repo_root):
    """Prompt T4: target-review must say the build does NOT prove the rules.

    RED today: skills/target-review/SKILL.md auto-clears GATE_3_BUILD on a
    successful compile and never mentions ``business_rules`` /
    ``@ImplementsRule`` — i.e. it presents 'compiles' as 'done'. The fix adds a
    scope caveat: GATE_3_BUILD proves classes exist/compile, not that the
    business rules/validations/error paths are implemented.
    """
    path, text = _read_skill(repo_root, "skills", "target-review", "SKILL.md")
    lowered = text.lower()

    mentions_rules = (
        "business_rule" in lowered
        or "@implementsrule" in lowered
        or "rule-coverage" in lowered
        or "rule coverage" in lowered
    )
    assert mentions_rules, (
        f"{path} never references business_rules / @ImplementsRule / "
        "rule-coverage. A successful build does NOT prove the rules are "
        "implemented; the skill must scope GATE_3_BUILD accordingly so it does "
        "not report the round-trip 'done' on a mere compile."
    )
