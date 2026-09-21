# tests/

The pytest suite. It runs without an NMRPipe installation, because the engine boundary is mocked;
that is what lets a pull request be gated on a hosted runner.

## Running it

```bash
python -m pytest -q                              # everything
python -m pytest -m unit -q                      # the fast subset used before committing
python -m pytest -m integration -q
python -m pytest -m regression -q
QT_QPA_PLATFORM=offscreen python -m pytest -q    # required where there is no display
```

## Categories

[`categories.py`](categories.py) is the single source of truth for which file belongs to `unit`,
`integration` or `regression`; [`conftest.py`](conftest.py) applies the markers by filename. A new
test file that is not registered there makes
[`test_test_categories.py`](test_test_categories.py) fail.

## Layout

- `conftest.py` - the shared fixtures, including the synthetic NMRPipe FID template used by the
  direct-dimension diagnostics tests;
- `fixtures/bruker/` - deliberately tiny Bruker header fixtures; text headers only, no raw binary
  data;
- [`test_full_paths.py`](test_full_paths.py) - the end-to-end matrix: 2D/3D x uniform/NUS,
  automatic/manual/batch;
- [`test_release_readiness.py`](test_release_readiness.py) - the packaging, version and
  documentation-link contract;
- [`test_ownership.py`](test_ownership.py) - the directory ownership split.

Keep fixtures small: [`test_release_readiness.py`](test_release_readiness.py) fails if raw
datasets are committed.
