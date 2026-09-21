# config/ (shipped data: nmrforge_data/config)

Default configuration for a local nmrForge installation. It ships with the package: the position is the same in a checkout, an installed wheel and the AppImage - see `nmrforge_data/__init__.py` and docs/packaging.md.

| File | Role |
| --- | --- |
| `nmrforge.yaml` | the shipped defaults - processing linewidths, `points_per_line`, SMILE threads, NMRPipe path |
| `nmrforge.local.yaml` | optional machine-local overrides; this file is git-ignored and never part of the repository |

Precedence is: explicit argument > local override > shipped default > built-in fallback. An
invalid value falls back to the built-in default instead of raising, so a typo in a local file
cannot make processing fail.

Per-machine safety limits stay in code rather than in configuration. The SMILE thread count, for
example, is clamped to `cores - 2` no matter what the file requests
(`backend/config.py::smile_thread_limit`).

Behaviour is covered by [`tests/test_config_defaults.py`](../../tests/test_config_defaults.py).
