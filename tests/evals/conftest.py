"""
Pytest configuration for the theory-eval harness (tests/evals/).

Purpose
-------
The eval suite exercises the four lead-engineer theories (T1..T4) directly
against the project's scripts (graph_normalizer.py, compare_graphs.py, ...).
Those scripts live in <repo>/scripts and import each other by bare module
name (e.g. `import planner_utils`), so they must be importable without a
package prefix.

This conftest puts <repo>/scripts on sys.path so the eval test files can do:

    from antilegacy_core import graph_normalizer
    from antilegacy_core import compare_graphs

and so subprocess invocations that the evals spawn (which set their own path)
match the in-process import path used here.

It also exposes a couple of small, read-only path fixtures so every eval
resolves the repo root and the fixtures/ directory the same way, regardless
of the cwd pytest happens to be launched from.

Cross-platform note: paths are built with os.path/ pathlib only (no shell),
so this works identically on macOS, Linux, and Windows.
"""
import os
import sys

import pytest

# --- resolve key directories relative to THIS file (cwd-independent) --------
# tests/evals/conftest.py  ->  tests/evals  ->  tests  ->  <repo root>
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, os.pardir, os.pardir))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
_FIXTURES_DIR = os.path.join(_THIS_DIR, "fixtures")

# --- make the migrated modules importable -----------------------------------
# Core lives in antilegacy_core (skills/anti-legacy-expert/scripts); single-owner
# leaf scripts live in their owning skill's scripts/ dir. Add every skill scripts
# dir — NOT the legacy top-level scripts/, which now holds only by-path migration
# shims that must never shadow the real modules on import.
import glob as _glob
for _d in sorted(_glob.glob(os.path.join(_REPO_ROOT, "skills", "*", "scripts"))):
    if _d not in sys.path:
        sys.path.insert(0, _d)


# --- shared, read-only path fixtures ---------------------------------------
@pytest.fixture(scope="session")
def repo_root():
    """Absolute path to the repository root."""
    return _REPO_ROOT


@pytest.fixture(scope="session")
def scripts_dir():
    """Absolute path to <repo>/scripts (already on sys.path)."""
    return _SCRIPTS_DIR


@pytest.fixture(scope="session")
def fixtures_dir():
    """Absolute path to tests/evals/fixtures (the synthetic JSON inputs)."""
    return _FIXTURES_DIR
