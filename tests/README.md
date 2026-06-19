# Tests — how to run

**pytest is the canonical (and only supported) runner.** CI runs `python -m pytest -q`
(see `.github/workflows/ci.yml`).

```bash
# Hermetic suite (engine-gated tests skip gracefully when wicked-estate is absent):
python3 -m pytest tests/ -q

# Full suite including the engine-gated tests (resolves wicked-estate on PATH):
PATH="$HOME/.cargo/bin:$PATH" python3 -m pytest tests/ -q
```

Engine-off you'll see `N passed, 69 skipped`; engine-on the 69 skips run and you get
`N passed, 0 skipped`. The skips are the tests that need the `wicked-estate` binary —
they `@unittest.skipUnless(...)` it so the hermetic suite still gates in CI.

## Do NOT use `python -m unittest discover`

It is **unsupported by design** and will report dozens of spurious errors. The reason:
the suite's import bootstrap (adding every `skills/*/scripts` dir to `sys.path` and
`PYTHONPATH`) lives in `tests/conftest.py`, which **only pytest loads**. Under plain
`unittest discover`:

- modules that import a leaf script (`from test_runner import TestRunner`,
  `from completeness_scanner import ...`) fail to import → `_FailedTest` collection
  errors, and whole modules are silently dropped from the run;
- subprocess CLI tests that spawn `python -m antilegacy_core.<stem>`
  / `-m packet_generator` fail with `ModuleNotFoundError` because the child never
  inherits the conftest-exported `PYTHONPATH`.

None of these are real defects — they vanish under pytest. If you see that wall of
red, you ran the wrong runner. Use `pytest` (tracked in ISS-13).
