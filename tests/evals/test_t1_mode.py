"""
EVAL_T1_mode_from_config  --  Theory T1 (migration mode is wired from config.json).

Theory
------
The pipeline runs in migration_mode="structural" by default (graph_normalizer.py
argparse default 'structural'); config.json carries no migration_mode and main()
never reads one. Structural mode is the OPPOSITE of the intended functional
"capability plan": it emits one REQ_ node per legacy program (a 1:1 code
skeleton) instead of merging programs into business capabilities.

What "fixed" means (the assertions below encode post-fix behavior)
-----------------------------------------------------------------
main() must resolve the mode with a 3-tier precedence:

    mode = args.mode or cfg.get('migration_mode') or 'structural'

via a safe (try/except) load of a new ``--config`` argument
(default .anti-legacy/config.json). Therefore:

  * Case A -- no ``--mode``, ``--config`` -> {"migration_mode":"functional"}:
        output metadata.migration_mode == "functional", PROG_A + PROG_B
        leaf-merge into exactly ONE CAP_ capability node.
  * Case B -- no ``--mode``, ``--config`` -> {"migration_mode":"structural"}:
        output metadata.migration_mode == "structural", two REQ_ nodes.
  * Case C -- explicit ``--mode structural`` over a functional config:
        the flag WINS -> structural metadata, two REQ_ nodes.

Red today
---------
``--config`` is an unrecognized argument (argparse errors), and even if it
parsed, main() ignores config and the default is 'structural'. So Case A's
functional/merged-capability assertions FAIL.

Determinism
-----------
Pure tmp files + the in-repo synthetic code graph fixture; no network, clock,
or randomness. The normalizer is invoked through its CLI (the stable public
interface) via subprocess.
"""
import json
import os
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalizer_path(repo_root):
    return os.path.join(repo_root, "scripts", "graph_normalizer.py")


def _run_normalizer(repo_root, input_path, output_path, *, config_path=None,
                    mode=None):
    """Invoke graph_normalizer.py via its CLI; return (CompletedProcess)."""
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
        # cwd is a throwaway tmp dir so a stray .anti-legacy/config.json in the
        # repo can never leak into the test (the default config path is
        # relative); each case passes an explicit --config anyway.
        cwd=os.path.dirname(output_path),
    )


def _load_graph(output_path):
    with open(output_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _all_requirements(graph):
    """Flatten domains -> requirements into a list of (req_id, req) tuples."""
    out = []
    for domain in graph.get("domains", {}).values():
        for rid, req in domain.get("requirements", {}).items():
            out.append((rid, req))
    return out


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


# ---------------------------------------------------------------------------
# Case A -- functional config, no --mode flag  (the core T1 red)
# ---------------------------------------------------------------------------
def test_t1_functional_config_yields_functional_capability(
    repo_root, fixtures_dir, tmp_path
):
    """No --mode + config {"migration_mode":"functional"} must produce a
    FUNCTIONAL graph (capability plan), not the structural default."""
    code_graph = os.path.join(fixtures_dir, "code_graph.json")
    config_path = str(tmp_path / "config.json")
    output_path = str(tmp_path / "out_functional.json")
    _write(config_path, {"migration_mode": "functional"})

    proc = _run_normalizer(
        repo_root, code_graph, output_path, config_path=config_path
    )

    # The CLI must accept --config and succeed (today: argparse rejects it).
    assert proc.returncode == 0, (
        "normalizer CLI failed (likely '--config' is unrecognized -- the T1 "
        f"wiring is absent).\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert os.path.exists(output_path), (
        "no output graph was written -- the run did not complete.\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )

    graph = _load_graph(output_path)

    # (1) The mode actually used must be functional, sourced from config.
    assert graph["metadata"]["migration_mode"] == "functional", (
        "config.json's migration_mode was ignored; main() still defaulted to "
        f"'structural'. metadata={graph.get('metadata')!r}"
    )

    reqs = _all_requirements(graph)

    # (2) Functional output is a capability plan: every requirement node is a
    #     CAP_ capability, never a 1:1 REQ_ code-skeleton node.
    rid_set = {rid for rid, _ in reqs}
    assert rid_set, "no requirements were produced"
    assert all(rid.startswith("CAP_") for rid, _ in reqs), (
        "functional mode must emit CAP_ capability nodes, not 1:1 REQ_ nodes; "
        f"got requirement ids {sorted(rid_set)}"
    )

    # (3) PROG_A (caller) + PROG_B (single-caller leaf) must leaf-merge into
    #     EXACTLY ONE capability node carrying both programs.
    merged_caps = [
        (rid, req)
        for rid, req in reqs
        if {"PROG_A", "PROG_B"}.issubset(set(req.get("merged_programs", [])))
    ]
    assert len(merged_caps) == 1, (
        "expected exactly ONE capability merging PROG_A + PROG_B (leaf merge); "
        f"found {len(merged_caps)}: {[rid for rid, _ in merged_caps]}"
    )


# ---------------------------------------------------------------------------
# Case B -- structural config, no --mode flag  (config is honored both ways)
# ---------------------------------------------------------------------------
def test_t1_structural_config_yields_structural_reqs(
    repo_root, fixtures_dir, tmp_path
):
    """config {"migration_mode":"structural"} must produce the structural
    1:1 code skeleton (REQ_ nodes), proving config drives the choice (not just
    a hardcoded functional)."""
    code_graph = os.path.join(fixtures_dir, "code_graph.json")
    config_path = str(tmp_path / "config.json")
    output_path = str(tmp_path / "out_structural.json")
    _write(config_path, {"migration_mode": "structural"})

    proc = _run_normalizer(
        repo_root, code_graph, output_path, config_path=config_path
    )

    assert proc.returncode == 0, (
        "normalizer CLI failed (likely '--config' is unrecognized).\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert os.path.exists(output_path), (
        f"no output written.\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )

    graph = _load_graph(output_path)
    assert graph["metadata"]["migration_mode"] == "structural"

    reqs = _all_requirements(graph)
    rids = {rid for rid, _ in reqs}
    # Structural = 1 program per requirement (PROG_A, PROG_B, PROG_C -> 3 REQ_).
    assert all(rid.startswith("REQ_") for rid in rids), (
        f"structural mode must emit REQ_ nodes; got {sorted(rids)}"
    )
    assert len(reqs) == 3, (
        "structural mode must keep 1 requirement per program "
        f"(PROG_A/B/C => 3); got {len(reqs)}: {sorted(rids)}"
    )


# ---------------------------------------------------------------------------
# Case C -- explicit --mode flag beats config  (precedence: flag > config)
# ---------------------------------------------------------------------------
def test_t1_explicit_flag_overrides_config(repo_root, fixtures_dir, tmp_path):
    """An explicit --mode structural must WIN over a functional config
    (precedence flag > config > default)."""
    code_graph = os.path.join(fixtures_dir, "code_graph.json")
    config_path = str(tmp_path / "config.json")
    output_path = str(tmp_path / "out_flagwins.json")
    _write(config_path, {"migration_mode": "functional"})

    proc = _run_normalizer(
        repo_root, code_graph, output_path,
        config_path=config_path, mode="structural",
    )

    assert proc.returncode == 0, (
        "normalizer CLI failed (likely '--config' is unrecognized).\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert os.path.exists(output_path), (
        f"no output written.\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )

    graph = _load_graph(output_path)
    assert graph["metadata"]["migration_mode"] == "structural", (
        "explicit --mode structural must override the functional config; "
        f"metadata={graph.get('metadata')!r}"
    )
    reqs = _all_requirements(graph)
    assert all(rid.startswith("REQ_") for rid, _ in reqs) and len(reqs) == 3, (
        "flag-forced structural must produce 3 REQ_ nodes; got "
        f"{sorted(rid for rid, _ in reqs)}"
    )
