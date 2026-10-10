# Development guide

Linux is the target runtime. Development requires Python 3.12 or newer. Create an editable source
installation and start the desktop application with:

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python main.py
```

The `test` extra installs pytest; `dev` installs pytest and Ruff. PySide6 and pyqtgraph are regular
runtime dependencies. NMRPipe and SMILE are separate tools: NMRPipe is used for conversion and
processing, and SMILE for NUS reconstruction. They are not included with the source or AppImage.
See [installation](installation.md) and [external dependencies](external-dependencies.md) for
resource installation and engine discovery details.

## Tests and checks

Tests are assigned to `unit`, `integration`, and `regression` groups in
[`tests/categories.py`](../tests/categories.py), with markers applied from
`tests/conftest.py`. Run an individual group with `python -m pytest -m <group>`, or run all groups
with `python -m pytest -q`. The full processing-route regression is:

```bash
python -m pytest tests/test_full_paths.py
```

It exercises automatic and manual entry points for 2D and 3D uniform and NUS data. Batch processing
currently covers 2D. The ordinary tests use fake or mocked engine boundaries and do not require
NMRPipe or SMILE; report real-engine checks separately. Set `QT_QPA_PLATFORM=offscreen` when a test
needs Qt without a display. Run `python -m ruff check .` for lint checks.

## Processing and compatibility

When changing processing behaviour, trace the automatic and manual routes across 2D/3D and uniform/
NUS data. Path-specific behaviour should be limited to operations that require it and documented.
Use `tests/test_full_paths.py` to check route coverage. Keep records faithful to the values actually
applied; requested, resolved, and applied values are distinct.

The `nmrforge_api` compatibility manifest records a content `behavior_digest`, a normalized-code
`token_digest`, one of `same`, `additive`, `behavior_changed`, or `contract_changed`, and affected
processing steps. The content fingerprint includes every file under `core/`, `backend/`,
`workflow/`, and `nmrforge_api/`, plus the shipped default configuration and presets. After changing
any of these, inspect the manifest and golden-vector result with:

```bash
python -m nmrforge_api compat --out compat.json
python -m nmrforge_api compat --golden
```

Update the declaration with `scripts/update_compat_declaration.py`, for example:

```bash
python scripts/update_compat_declaration.py --level behavior_changed \
  --affected localization,sweep_detection --note "Describe the numerical change"
```

Supported steps are defined in `nmrforge_api.compat.AFFECTED_STEPS`.
The script validates the level and regenerates golden hashes
unless `--no-golden` is supplied. For a documentation-only update with unchanged executable tokens,
use `--level same --affected "" --no-golden`.

Classify numerical changes as `behavior_changed`; changes to fields, columns, or error codes are
`contract_changed`. Set `affected` for either level. Update the golden-vector hashes when behavior
or recipes change. `same` requires an unchanged token digest. The compatibility regression checks
the declaration against the code and golden vector; do not hand-edit a run stamp or compatibility
record.

## Runtime resources and interface text

Default configuration, presets, and tutorials live in `nmrforge_data/`; `gui/assets/` and
`ui_support/locales/` are also runtime resources. `core/app_paths.py` resolves resources from the
source checkout, an installed package, or the frozen AppImage layout. Keep resource paths in that
shared layout and exclude machine-local `nmrforge.local.yaml` from distributable files.

Interface strings use English literals passed to `tr()` and Chinese translations in
`ui_support/locales/zh.json`. When changing interface text, keep the translation and extraction
metadata current, then run:

```bash
python scripts/i18n_extract_ui.py --write
python scripts/i18n_extract_ui.py --check
```

The check verifies registered strings, converted-file coverage, and the Chinese coverage floor.
`source.json` and `converted.json` are development/CI metadata, not runtime catalogues. The runtime
catalogues are `default.json`, `en.json`, and `zh.json`; packaging lists resource files explicitly.

## Public interfaces and documentation

Software version **1.0.5** and scripting API version **1.1.1** are versioned separately. For API
changes, update the implementation, tests, compatibility declaration, API contract, and relevant
external API pages together. Keep examples aligned with the supported arguments and outputs. See
the [contributing guide](../CONTRIBUTING.md), [API contract](API_CONTRACT.md),
[external API guide](external-api/README.md), and [architecture](architecture.md).

Use neutral paths and synthetic data in examples. Do not publish credentials, machine-specific
paths, private datasets, or internal project records.
