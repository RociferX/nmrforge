# .github/

Repository automation and contribution templates.

## Workflows

[`workflows/ci.yml`](workflows/ci.yml) is the CI gate. It runs on every push to `main` and on
every pull request:

| Job | What it does |
| --- | --- |
| `static checks` | `ruff check .` |
| `tests (Python 3.12)` / `tests (Python 3.13)` | `pip install -e ".[test]"` plus the full pytest suite with `QT_QPA_PLATFORM=offscreen` |
| `release readiness` | the packaging, version and documentation-link contract (`tests/test_release_readiness.py`) |

A fifth job, `external-engine`, runs the full suite on a self-hosted runner that has NMRPipe
installed. It only activates when the repository variable `NMRFORGE_SELF_HOSTED_CI` is `true`.
The hosted jobs deliberately mock the engine boundary, so gating a pull request never requires an
NMRPipe licence or download.

## Templates

- [`ISSUE_TEMPLATE/`](ISSUE_TEMPLATE) - bug report, feature request and data-processing-problem
  forms, plus the chooser configuration;
- [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md).
