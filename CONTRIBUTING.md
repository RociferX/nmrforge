# Contributing

Thanks for considering a contribution. This page says how to get a development environment
running, what is expected of a change, and how to report problems.

## Before you start

- Read [README.md](README.md) for what the project is and is not.
- Read [docs/processing-model.md](docs/processing-model.md) and
  [docs/qc-system.md](docs/qc-system.md) if your change touches processing or quality control.
  Most rejected contributions are rejected because they make processing guess where the project
  deliberately reports.
- For a large change, open an issue first. It is cheaper than discovering a design disagreement
  after the work is done.

## Development environment

```bash
git clone <this repository>
cd nmrForge
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Requirements: Python 3.12+, and an NMRPipe installation if you want to exercise real processing
(the test suite does not need one - see below).

## Running the checks

```bash
python -m pytest -q                 # full suite
python -m ruff check .              # lint (must be clean)
python -m ruff format --check .     # formatter (advisory for existing files)
```

GUI tests need a display or offscreen Qt:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q      # Windows: set QT_QPA_PLATFORM=offscreen
```

If pytest cannot create its scratch directory (`PermissionError ... pytest-of-<user>`), pass an
explicit location:

```bash
python -m pytest --basetemp=/tmp/nf_pytest -q              # Linux
python -m pytest --basetemp=$env:TEMP\nf_pytest -q          # PowerShell
```

### The suite does not require NMRPipe

The engine boundary is mocked (`FakeBackend`/`MockBackend`), so `pytest` passes on a machine with
no NMRPipe at all. Please keep it that way: a contribution that makes plain CI require a
proprietary external program would break the release pipeline. If your change genuinely needs a
real engine, describe the manual verification you performed in the pull request instead.

### Release-readiness tests

`tests/test_release_readiness.py` locks down the properties that keep the repository publishable:
single-source versioning, resolvable documentation links, no developer-specific absolute paths in
shipped code, valid CI configuration, present templates, compilable examples, explicit licence
state, and no raw NMR data committed as a fixture. If you add or rename a public document, update
that test's lists as well.

## What a change should look like

1. **One logical change per commit.** Do not bundle an unrelated refactor with a fix.
2. **Describe behaviour, not mechanics.** "Sampling classification no longer accepts a full
   `nuslist` as NUS" is useful; "fixed bug" is not.
3. **State backward compatibility.** If a change alters the meaning of a parameter, a study
   record, a peak table or the scripting API, say what breaks and how a user migrates. Breaking
   changes to the `nmrforge_api` surface must be declared through the behaviour manifest
   (`nmrforge_api.compat_manifest()`, level `behavior_changed`/`contract_changed` plus the
   affected steps) and called out in the pull request. The user-facing summary of each version
   is on the GitHub releases page.
4. **Do not disable a failing test to make the suite green.** Fix the behaviour, or open an issue
   and mark the test with a documented reason.
5. **Keep provenance honest.** If you add a value to a run record, it must be the value actually
   used, not the value requested. Never fabricate a version number or a measurement.

### Processing changes: the four-path rule

A change to processing behaviour is expected to apply to **all four paths** - 2D uniform,
2D NUS, 3D uniform, 3D NUS - for both the automatic and the manual entry points. Differences are
allowed, but only where the physics or the engine requires them, and the exception must be stated
in the commit message and in
[docs/development.md](docs/development.md). `tests/test_full_paths.py` covers those paths and must
be run for any change that touches processing.

### Commit messages

The existing history uses English conventional prefixes with Chinese descriptions
(`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:` - followed by a short description). Use
whichever language you write best; the prefix convention is what matters.

## Reporting bugs

Use the issue templates: "Bug report" for code problems, "Data-processing problem" for a wrong
spectrum or a failed conversion. Include the reported experiment type and sampling mode, the
dimensionality, the exact command or GUI path, the quality report, and the versions. **Never
attach raw or unpublished data, sample names, or laboratory paths** - see
[SECURITY.md](SECURITY.md).

## Requesting features

Use the "Feature request" template. A request that includes what is verifiable from the data is
much more likely to be implemented than one that asks for a specific heuristic, because the
project avoids behaviour that cannot be justified or audited.

## Licensing of contributions

nmrforge is released under the **Apache License 2.0** (see [LICENSE](LICENSE) and
[LICENSE_OPTIONS.md](LICENSE_OPTIONS.md)). By opening a pull request you agree that your
contribution may be distributed under those terms (Apache-2.0 section 5: contributions are
inbound under the same licence, with the patent grant it carries). Please open an issue before
starting substantial work, so the change can be scoped against the project's "no unauditable
behaviour" rule.

## The laboratory workflow

The project also has an internal workflow for its own maintainers (an NMRPipe validation machine,
ownership checks, and a single long-lived development branch). That is documented in
[docs/development.md](docs/development.md), and it
is not required of outside contributors.