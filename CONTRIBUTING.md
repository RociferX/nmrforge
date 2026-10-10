# Contributing

Open an issue before a substantial change, or submit a focused pull request against `main`. Describe
the resulting behavior, compatibility impact, and checks performed. Read the
[processing model](docs/processing-model.md) and [QC guide](docs/qc-system.md) when a change touches
processing or quality control.

## Development and validation

Linux is the target runtime. Use Python 3.12 or newer and install the development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q
python -m ruff check .
```

Tests use mocked engine boundaries and do not require NMRPipe or SMILE. For a processing change,
cover automatic and manual routes for 2D and 3D uniform and NUS data with
`python -m pytest tests/test_full_paths.py`. Batch processing currently supports 2D only. Describe
any real-engine verification separately from mocked test results. Run the relevant `unit`,
`integration`, and `regression` test groups when they help isolate a change.

Keep each change focused. Do not disable a failing test to make a check pass; fix the behavior or
document the reason and expected follow-up. Keep provenance accurate: records must contain values
actually applied, and reports must not present requested values as executed results.

## Public API and compatibility

For public API changes, update the implementation, tests, [API references](docs/external-api/README.md),
and compatibility declaration together. Software version 1.0.5 and API version 1.1.1 are tracked
separately. The compatibility declaration records code/data fingerprints, a change level, affected
steps, and golden-vector hashes. The content fingerprint covers all files in `core/`, `backend/`,
`workflow/`, and `nmrforge_api/`, as well as the shipped default configuration and presets. After
changing any of these, refresh the declaration from the CLI:

```bash
python -m nmrforge_api compat --out compat.json
python -m nmrforge_api compat --golden
```

Use `behavior_changed` for numerical behavior changes and `contract_changed` for output fields,
columns, or error-code changes; set `affected` for either level. Update golden hashes when behavior
or recipes change. See [development](docs/development.md) for the fingerprint scope and
[API_CONTRACT.md](docs/API_CONTRACT.md) for the public interface contract.

## Reporting issues

Include the source or binary version, Linux environment, reproduction steps, expected behavior, and
a redacted error log. Do not attach private datasets, credentials, or machine-specific paths. Report
security issues through [SECURITY.md](SECURITY.md), and follow the
[code of conduct](CODE_OF_CONDUCT.md).

## Licensing contributions

Contributions are accepted under the project's [Apache-2.0 licence](LICENSE). Third-party
components retain their own terms. See [licensing and distribution](LICENSE_OPTIONS.md) for the
source and AppImage packaging details.
