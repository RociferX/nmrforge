# Development guide

This page covers the public source tree. Linux is the target runtime, and the
project requires Python 3.12 or newer. Processing behaviour and supported
arguments are defined by the implementation and the public references.

## Set up a source checkout

    git clone https://github.com/RociferX/nmrforge.git
    cd nmrforge
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install -e ".[dev]"

Start the desktop application with python main.py. The test extra installs
pytest; the dev extra also installs Ruff. See [installation](installation.md)
for packaged installs and runtime resources.

NMRPipe and SMILE are separate tools and are not bundled with the source.
NMRPipe is required for conversion and processing; SMILE is required for NUS
reconstruction. See [external dependencies](external-dependencies.md) for
supported discovery and capability boundaries.

## Validate changes

Run relevant tests first. The configured unit, integration, and regression
groups can be selected independently; then run the full suite and static
checks on Linux:

    python -m pytest -m unit
    python -m pytest -m integration
    python -m pytest -m regression
    python -m pytest -q
    python -m ruff check .

For processing-route changes, also run the full-path regression:

    python -m pytest tests/test_full_paths.py

Tests that require a display may use QT_QPA_PLATFORM=offscreen. Real-engine
checks require the corresponding external tools and should be reported
separately from mocked tests. A passing test suite does not establish
scientific validity.

## Contributor and compatibility references

Keep changes focused and update user-facing documentation when behaviour or
outputs change. Public API changes must update implementation, tests, API
references, and compatibility metadata together.

- [Contributing guide](../CONTRIBUTING.md) describes review expectations.

- [API contract](API_CONTRACT.md#11-public-nmrforge_api-contract) summarizes
  the versioned scripting boundary.

- [External API guide](external-api/README.md) links to function, CLI, input,
  and output references.

- [Architecture](architecture.md) describes current component responsibilities.

Do not publish machine-specific paths, credentials, private datasets, or
internal project notes.
