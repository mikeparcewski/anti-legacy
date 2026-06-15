#!/usr/bin/env python3
import os, sys, subprocess

PLUGIN_ROOT = r"__PLUGIN_ROOT__"  # written by anti-legacy:setup at init time

if len(sys.argv) < 2:
    sys.stderr.write('usage: run.py <script-stem> [args...]\n')
    sys.exit(2)

stem = sys.argv[1]
scripts_dir = os.path.join(PLUGIN_ROOT, 'scripts')

# Confine the stem to scripts/: reject any path separator or parent-dir escape
# so a stem like '../foo' cannot escape the scripts directory. Validate both
# the raw stem (cheap, catches separators/..) and the normalized resolved path
# (defense in depth: the resolved script must live directly under scripts_dir).
script = os.path.join(scripts_dir, stem + '.py')

if (os.sep in stem or (os.altsep and os.altsep in stem) or '..' in stem.split(os.sep)
        or os.path.dirname(os.path.normpath(stem))):
    sys.stderr.write('run.py: illegal script stem\n')
    sys.exit(2)

resolved = os.path.normpath(os.path.abspath(script))
scripts_root = os.path.normpath(os.path.abspath(scripts_dir))
try:
    confined = os.path.commonpath([resolved, scripts_root]) == scripts_root
except ValueError:
    confined = False
if not confined:
    sys.stderr.write('run.py: illegal script stem\n')
    sys.exit(2)

if not os.path.isfile(script):
    sys.stderr.write('run.py: no such script: %s\n' % script)
    sys.exit(2)

sys.exit(subprocess.call([sys.executable, script] + sys.argv[2:]))
