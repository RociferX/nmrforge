# Contributing

Open an issue for a substantial change, or submit a focused pull request against `main`.
Describe the resulting behavior, compatibility impact, and checks performed.

## Development and checks

Linux is the target runtime. Use Python 3.12 or newer and install the development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q
python -m ruff check --no-cache .
```

The ordinary suite uses mocked engine boundaries and does not require NMRPipe or SMILE.
Processing changes must cover automatic/manual 2D/3D uniform/NUS routes in
`tests/test_full_paths.py`; batch processing currently supports 2D only. Describe real-engine
verification separately from mocked regression results.

For API changes, update the implementation, tests, and [API documentation](docs/external-api/README.md)
together, including the compatibility declaration. See the [development guide](docs/development.md)
for the relevant commands.

## Reporting issues

Include the source or binary version, Linux environment, reproduction steps, expected behavior,
and a redacted error log. Do not attach private datasets, credentials, or machine-specific paths.
Report security issues through [SECURITY.md](SECURITY.md), and follow the
[code of conduct](CODE_OF_CONDUCT.md).

Contributions are accepted under the project's [Apache-2.0 licence](LICENSE).
Third-party components retain their own terms.
