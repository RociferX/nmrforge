# nmrforge_api/

The scriptable surface: parameter studies built around a frozen reference spectrum, plus
per-condition workflows. It is designed to be imported by downstream analysis code without
pulling in Qt or touching GUI state.

## Modules

| Module | Role |
| --- | --- |
| `__init__.py` | the public exports and `API_VERSION` |
| `session.py` | session and dataset definitions |
| `study.py` | `run_parameter_study()` - the one-call convenience entry point |
| `reference.py` | reference mode: the reference spectrum and script, plus the frozen reference peak tables |
| `sweep.py` | combination mode: parameter combinations to `workflow_id`s, with resumable runs |
| `peaks.py` | peak picking and sub-grid localisation entry points |
| `records.py` | run records, the manifest and the long-form peak table |
| `peak_tables.py` | the unified peak-table schema shared by both localisation methods |
| `direct_range.py` | parsing the direct-dimension range |
| `cli.py`, `__main__.py` | `python -m nmrforge_api ...` |
| `errors.py` | the exceptions the public API raises |
| `uncertainty.py` | the sigma/delta-delta helper, kept for **testing and detection only** - it is not part of the processing contract |

Guides and the contract: [`docs/external-api/`](../docs/external-api/README.md),
[`docs/API_CONTRACT.md`](../docs/API_CONTRACT.md) section 11. The boundary is covered by
[`tests/test_nmrforge_api.py`](../tests/test_nmrforge_api.py), including
`test_statistics_helper_is_not_in_processing_contract`.
