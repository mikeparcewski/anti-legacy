"""CLI entry: `python -m antilegacy_core.preflight`.

Runs the host-agnostic readiness check and prints a fail-fast report. Dispatched
by the workspace seam as `python3 .anti-legacy/run.py preflight`.
"""
import sys

from antilegacy_core import __version__, preflight


def main():
    errors = preflight()
    if errors:
        sys.stderr.write("preflight: NOT READY\n")
        for err in errors:
            sys.stderr.write(f"  ✗ {err}\n")
        sys.exit(1)
    sys.stdout.write(f"preflight: READY (antilegacy_core {__version__})\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
