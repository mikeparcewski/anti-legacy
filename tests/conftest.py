"""Pytest path setup for the anti-legacy restructure.

Adds the antilegacy_core package parent + every skill's scripts/ dir (where
single-owner leaf scripts now live) to sys.path (in-process imports) and
PYTHONPATH (subprocess CLI tests). Top-level scripts/ is intentionally NOT on the
import path: everything is migrated, and the only files left there are
by-path migration shims, which must not shadow the real modules on import.

CANONICAL RUNNER — pytest only (ISS-13).
    Run the suite with:  python3 -m pytest tests/ -q      (CI runs `python -m pytest -q`)
This module is a pytest `conftest.py`. `python -m unittest discover` does NOT load
conftest, so under plain unittest the leaf-script paths above are never injected and
the suite reports dozens of spurious import/collection errors (`_FailedTest`) and
subprocess `-m` failures (`No module named antilegacy_core` / ...).
That red is an artifact of the wrong runner, not a real failure — pytest is green.
unittest-discover is unsupported by design; see tests/README.md.
"""
import glob
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATHS = []
for _d in sorted(glob.glob(os.path.join(_ROOT, "skills", "*", "scripts"))):
    if os.path.isdir(_d) and _d not in _PATHS:
        _PATHS.append(_d)
for _p in _PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)
_existing = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = os.pathsep.join(_PATHS + ([_existing] if _existing else []))
