"""Pytest path setup for the anti-legacy restructure.

Adds the antilegacy_core package parent + every skill's scripts/ dir (where
single-owner leaf scripts now live) to sys.path (in-process imports) and
PYTHONPATH (subprocess CLI tests). Top-level scripts/ is intentionally NOT on the
import path: everything is migrated, and the only files left there are
by-path migration shims, which must not shadow the real modules on import.
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
