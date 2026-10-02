# Development guide

This guide describes current contributor practice for the public source tree. The implementation is
the source of truth for runtime behaviour. A historical benchmark or release note describes only
the revision and inputs recorded on that page; it is not a validation claim for the current tree.

## Environment and dependencies

Linux is the target runtime. Windows may be used to edit the source, but Windows runtime behaviour
is not a project compatibility target.

For an editable source installation:

```bash
git clone https://github.com/RociferX/nmrforge.git
cd nmrforge
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python main.py
```

See [installation](installation.md) for optional dependency groups and packaged resources.
The project configuration and runtime assets are in `pyproject.toml` and `nmrforge_data/`.
Keep machine-local configuration in `nmrforge_data/config/nmrforge.local.yaml`; do not put
developer-specific paths or credentials in tracked defaults or examples.

NMRPipe and SMILE are external tools, not Python package dependencies and not bundled in the source
tree. NMRPipe is needed for conversion and spectral processing; SMILE is needed for NUS
reconstruction. The backend resolves NMRPipe from the configured executable/directory, then
`PATH`, the sourced `csh` environment, and supported common locations. See
[external dependencies](external-dependencies.md) for the exact precedence and capability boundary.
Missing engines should produce an explicit unavailable-capability or actionable error, never a
substitute result.

SMILE memory use depends strongly on direct-dimension size and the indirect reconstruction grid.
The application estimates the requested budget before starting reconstruction. Avoid increasing
zero filling or thread counts without considering available memory. Record the engine versions
alongside real-engine acceptance results.

Source and binary releases are separate deliverables. Editing or syncing source does not update an
existing AppImage. A release must identify the source revision represented by its binary; do not
describe an AppImage as current unless that exact artifact has been built and checked.

## Change workflow

Keep a change focused and describe the behaviour, affected routes, and validation performed.
Before changing processing behaviour, inspect the relevant public contract and tests. Consider the
change across:

- 2D and 3D data;
- uniformly sampled data and supported NUS layouts;
- automatic and manual processing entry points.

A path-specific implementation is acceptable only where the operation is inherently specific to
that path. State that scope and its reason in the user-facing documentation.

Tests and fixtures should be reproducible on another supported Linux environment. Prefer synthetic
data and fake backends where real external engines are not required. Do not make tests depend on
developer-machine absolute paths, installed private datasets, or fixed CPU/memory assumptions.
Keep generated test data, logs, caches, and temporary files outside the source tree.

## Testing and validation

Run the narrowest relevant checks while iterating, then the broader suite on the supported Linux
environment. Test classes are listed in [`tests/categories.py`](../tests/categories.py):

```bash
pytest -m unit
pytest -m integration
pytest -m regression
```

The full-path regression is:

```bash
python -m pytest tests/test_full_paths.py
```

It covers automatic and manual 2D/3D uniform and NUS routes. Batch processing currently supports
2D only; do not claim that batch covers all four dimension/sampling combinations. GUI tests may
run with `QT_QPA_PLATFORM=offscreen` when a display is unavailable.

Prefer mocked/fake-engine checks for logic that does not require NMRPipe or SMILE. Real-engine
acceptance must be performed separately on Linux with the relevant tools installed. Record the
source revision, input description, NMRPipe/SMILE versions, relevant parameters, command or workflow,
and outcome. A mocked test suite passing is not evidence of engine execution or scientific validity.
Do not re-run or alter a historical benchmark merely to make the current documentation appear
validated; label the historical scope and use a new benchmark only when specifically planned.

Before submitting code changes, run relevant tests and `ruff check .`. For changes to processing
routes, include the full-path regression when the supported Linux test environment is available.
Document environmental limitations instead of treating a skipped real-engine check as a pass.

## Processing and data contracts

These are cross-cutting invariants to preserve when a change touches processing or records:

- **Sampling:** use a standard `nuslist` or the file explicitly named by `acqus.NUSLIST`. Do not
  infer a schedule from a bare integer list or nominal NUS percentage. A complete schedule in
  standard order may use the uniform route; a complete but reordered schedule still needs
  schedule-based placement. If explicit NUS metadata has no recoverable schedule or sample
  positions, reject import rather than guessing. Trailing block padding is not an unsampled point.
- **Axis identity:** keep storage order, logical F-axis identity, and nucleus identity distinct.
  Use `FDDIMORDER` to map axes; equal nuclei do not make axes interchangeable. Zero `ORIG` is a
  valid header value; missing values remain missing rather than being replaced by a guessed value.
- **Acquisition metadata:** preserve NMRPipe's real/complex point-count and acquisition-mode
  semantics, including mode-specific keywords and `-N` variants. Validate geometry fields without
  forcing historical CAR conventions. Resolve sweep-width conflicts explicitly and retain the
  adopted value and source in provenance.
- **Peak picking:** do not introduce an unconditional edge band. Automatic axial screening is a
  conservative heuristic requiring compatible experiment/acquisition evidence plus many narrow,
  aligned candidates at original data edges. Retain internal or isolated edge peaks and ambiguous
  cases. A user-specified margin is separate from automatic screening and must be recorded as such.
- **Peak localization:** the supported method is three-point parabolic localization. Do not expose
  removed Gaussian-fitting controls as current options. Keep peak detection, table row counts, and
  per-spectrum peak numbering distinct from optional targeted localization.
- **Background and sign:** threshold, peak height, SNR and reference measurements use the documented
  global-median background convention; this convention does not modify the spectrum. Follow the
  experiment template for automatic sign handling. Phase-sensitive COSY, NOESY, and ROESY retain
  both signs; explicit API sign requests take precedence.
- **Alignment:** reference alignment is a coordinate operation and must not modify spectral data.
  Low-quality or ambiguous matches must not silently remove candidate peaks.
- **Segment offsets:** `segment_shift_hz` is user-specified; segment 1 uses 0 Hz. The software does
  not automatically estimate inter-segment drift or guarantee a residual recheck. Report a value
  as applied only if that requested offset was actually applied, and instruct users to inspect the
  resulting spectrum.

For detailed input, output, and error semantics, update
[`API_CONTRACT.md`](API_CONTRACT.md) and the relevant
[external API reference](external-api/README.md) together with the implementation.

## Public Python API and compatibility

Document public entry points with these five English section labels:

```text
Parameters
Returns
Raises
Side effects
Examples
```

Parameter documentation should give types and physical meaning; examples must use supported
arguments and outputs. Keep exported names, API reference pages, CLI documentation, and examples in
sync. Internal helpers are not automatically public API.

The `nmrforge_api` compatibility manifest classifies externally observable changes. When changing
processing code, public outputs, or shipped defaults, determine whether the change is
`same`, `additive`, `behavior_changed`, or `contract_changed`. Numerical behaviour changes
must state affected areas; changes to fields, columns, or error codes must update the public
contract. Use the compatibility tooling and golden-vector workflow documented by the project; do
not hand-assemble run stamps or compatibility records.

Whole JSON state documents must be written atomically. Append-only logs and audit streams are
separate record types. Preserve serialization and stable identifiers unless the change intentionally
updates the public contract.

## Logging and user-facing errors

Libraries use named loggers and do not configure logging handlers during import. Application entry
points configure logging. Keep machine-readable CLI JSON on stdout and diagnostics on stderr.
Detailed tracebacks belong in debug output or run logs, not in ordinary dialogs.

Known input and environment failures should explain what the user can do next. Preserve useful
exception details for unknown failures without inventing a cause or showing only a bare exception
class name. Never silently convert a failed operation into a successful result.

Records should distinguish requested values, values applied, resolved automatic values, warnings,
and failures. A recommendation is not an executed correction. QC reports describe data quality;
they are not biosafety certifications.

## Documentation and review

Treat the English documentation in this directory as the public user/developer reference. Keep
examples runnable against the current API and use neutral paths and synthetic inputs where
possible. The Chinese documentation is an adapted mirror; update the corresponding page when its
public contract changes.

- [Architecture](architecture.md) explains component boundaries.
- [Processing model](processing-model.md), [QC system](qc-system.md), and
  [peak picking](peak-picking.md) describe user-visible processing behaviour.
- [External API](external-api/README.md) and [CLI reference](cli.md) document callable interfaces.
- [Evidence](evidence/real-data-comparison.md) is a dated historical snapshot, not a current-source
  validation. Preserve its recorded figures unless a separately authorized benchmark is conducted.

Review links and code examples after edits. Do not add private project notes, unpublished sample
names, machine-specific paths, or development-environment details to public documentation.

## Biological safety

NMRForge is for routine NMR processing, quality control, visualization, and analysis. Do not use
it to support enhancement of pathogen pathogenicity, transmissibility, host range, immune escape,
or other high-risk biological capabilities. Assess a task by its intended use and foreseeable
impact; public biological data or a viral protein name alone is not sufficient to infer harmful use.
Stop only the unsafe portion and continue benign software maintenance where appropriate.
