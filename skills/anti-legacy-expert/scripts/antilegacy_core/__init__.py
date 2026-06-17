"""antilegacy_core — shared library for the anti-legacy modernization pipeline.

Hosted inside the anti-legacy-expert skill so it ships with the portable bundle
(the skills standard delivers a skill's whole directory subtree). Resolved at
runtime by the workspace `.anti-legacy/run.py`, which puts this package's parent
dir on PYTHONPATH and dispatches modules via `python -m antilegacy_core.<stem>`.

Design rule (the ISS-23 trap): workspace state — config.json, annotations.jsonl,
graphs, requirements — is anchored on the CURRENT WORKING DIRECTORY (the user's
project), never on this package's __file__ (which points at wherever the bundle
was installed). __file__ locates code; cwd locates the workspace.
"""
import json
import os
import re
import shutil
import subprocess

__version__ = "0.1.0"

# Minimum wicked-estate engine version the pipeline depends on (annotate --replace
# landed in engine 0.5.1; clustering in 0.4.0; typed annotations in 0.5.0). See
# RESTRUCTURE_SPEC.md §16 — confirm sufficiency before treating as final.
MIN_ENGINE_VERSION = (0, 5, 1)


def workspace_root():
    """The workspace is the current working directory (the user's project)."""
    return os.getcwd()


def resolve_engine():
    """Resolve the wicked-estate binary: config.json -> env -> PATH. Path or None."""
    cfg = os.path.join(workspace_root(), ".anti-legacy", "config.json")
    if os.path.isfile(cfg):
        try:
            with open(cfg) as fh:
                p = json.load(fh).get("wicked_estate_path")
            if p and os.path.isfile(p):
                return p
        except (OSError, ValueError):
            pass
    env = os.environ.get("WICKED_ESTATE_PATH")
    if env and os.path.isfile(env):
        return env
    return shutil.which("wicked-estate")


def _engine_version(binary):
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", (out.stdout or "") + (out.stderr or ""))
        if m:
            return tuple(int(x) for x in m.groups())
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def preflight():
    """Return a list of human-readable error strings. Empty list == ready.

    Host-agnostic: no CLI-specific paths, no daemon. Workspace state is
    cwd-anchored. Safe to call even before a workspace is initialized.
    """
    errors = []

    binary = resolve_engine()
    if not binary:
        errors.append(
            "wicked-estate engine not found. Install it: `cargo install wicked-estate` "
            "(or set WICKED_ESTATE_PATH, or config.json wicked_estate_path)."
        )
    else:
        ver = _engine_version(binary)
        if ver and ver < MIN_ENGINE_VERSION:
            need = ".".join(map(str, MIN_ENGINE_VERSION))
            have = ".".join(map(str, ver))
            errors.append(
                f"wicked-estate {have} is too old; need >= {need}. "
                "Run `cargo install wicked-estate`."
            )

    try:
        import jsonschema  # noqa: F401
    except ImportError:
        errors.append(
            "Python package `jsonschema` missing (gate validation needs it). "
            "Run `pip install jsonschema`."
        )

    return errors
