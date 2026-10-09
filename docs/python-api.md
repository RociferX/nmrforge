# Python API

The public scripting API version is `API_VERSION = "1.1.1"`; this source release uses software
version 1.0.5. This patch changes the API version identifier only; parameters and the 38-column
peak-table contract are unchanged. The 1.0.5 release provides the source and one Linux AppImage; see
the [release page](https://github.com/RociferX/nmrforge/releases) for artifact availability and
validation status. Version 1.0.2 is retained as a historical release. Install from source containing
this version and check `compat_manifest()` before reusing results.

| Surface | Import | Stability |
| --- | --- | --- |
| Processing layer | `core`, `backend`, `workflow` | Internal; also used by the GUI. |
| Scripting API | `nmrforge_api` | Public, versioned and Qt-free. |

## Two-stage workflow

```python
import csv
from pathlib import Path
from nmrforge_api import run_combination_study, run_reference_study

root = "~/studies/hsqc_params"

# Optimize and freeze each condition's reference spectrum and peak table.
run_reference_study(
    root,
    datasets={"A": "~/data/bmr12345/1"},
    sigma_multiplier=25,
)

# Generate new spectra and independently detected peak tables for downstream analysis.
result = run_combination_study(
    root,
    combos=[{"zero_fill": 1}, {"zero_fill": 2}],
)
print(result.summary["status_counts"])

peak_table = Path(root).expanduser() / "study/records/peak_table_parabolic.csv"
with peak_table.open(encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
print(rows[:3])
```

The API hands off reference and candidate spectra/tables. It does not establish peak
correspondences, compare conditions or calculate chemical-shift perturbations. Candidate
`peak_id` values are local to each spectrum; `reference_peak_id` is empty. Downstream code
is responsible for matching. Each combination retains its script, candidate spectrum,
peak coordinates, warnings and records; it does not replace the active GUI spectrum.

The unified `peak_table_parabolic.csv` has 38 columns. It preserves logical F1/F2/F3
coordinates, nucleus labels and equivalent FWHM. H/N columns are compatibility aliases
only when that nucleus uniquely identifies an axis. `localization_requested` describes
the request; `localization_method` reports the actual `none`/`parabolic` outcome.
`failure_reason` is separate from fallback provenance. Unrepresentable exclusive-cell
geometry is NaN, not an ordinary physical-window bound.

Reference reuse requires a matching complete normalized input fingerprint. Changed inputs
or legacy references lacking that fingerprint require explicit `force=True` rebuilding.
See [Outputs and migration](external-api/06-outputs-and-records.md) before updating frozen
v1.0/v0.2 integrations.

## Reference CAR

Without an override, the actual AUTO conversion CAR is retained; the uniform fallback
uses the parsed Bruker carrier. Specify ppm values by logical axis:

```python
run_reference_study(
    root,
    datasets={"A": "~/data/bmr12345/1"},
    carrier_ppm={"F1": 118.0, "F2": 4.7},
    force=True,
)
```

For 2D, F2 is direct and F1 indirect; for 3D, F3 is direct, with F2 and F1 indirect.
Overrides are applied to actual conversion commands, recorded in provenance and
bound to the reference input fingerprint. See [API reference](external-api/03-api-reference.md)
for per-condition overrides and the full default-resolution rules.

## Explicit multi-segment input

Single-directory import remains the default (`segmented=False`). To register multiple source
segments as one condition, pass the complete ordered list and set `segmented=True`:

```python
run_reference_study(
    root,
    dataset=["~/data/segment1", "~/data/segment2"],
    segmented=True,
)
```

For multiple conditions, use a mapping whose values are each condition's ordered segment list, such
as `datasets={"A": ["~/data/a1", "~/data/a2"], "B": ["~/data/b1", "~/data/b2"]}`.
A top-level `datasets=[s1, s2]` represents one condition. Segments must be complete Bruker raw
directories; there must be at least two, with no duplicate paths. The API checks their acquisition
parameters, dimensions, nuclei, effective TD, spectral width, sampling mode, axis layout, SFO frequency
and carrier. Kinetic layouts and NUS data without a usable schedule are rejected. Segments may be in
different parent directories, and their order is part of input identity.

Reference mode performs import, conversion and segment merging. Combination mode processes only the
existing reference FID, including a single-file FID, a 3D uniform slice directory or a merged
multi-segment FID. If the FID is missing/damaged, source or conversion evidence does not match, or the
requested parameters require conversion/merging, the combination call fails and requires rebuilding
the reference with `force=True`. It never reconverts automatically, deletes source data, or modifies
the reference FID or sampling schedule. Older references without a frozen-FID record require one
explicit rebuild with `force=True`. The GUI's default conversion behavior is unchanged. Strict reuse
passed Linux engineering regression, but has not been verified against a real NMRPipe/SMILE engine.

## Qt-free import and errors

```python
import nmrforge_api  # Does not import a Qt binding.
from nmrforge_api import SweepError, run_parameter_study

try:
    run_parameter_study(root, "~/data/bmr12345/1", axes={"window.F1.off": [0.35]})
except SweepError as exc:
    print(f"Invalid sweep request: {exc}")
```

The GUI uses PySide6 through `qtcompat`, but the scripting API can run on headless
Linux nodes. NMRPipe and, for reconstruction, SMILE must be installed separately.
Lower-level `core`, `backend` and `workflow` modules are implementation details, not
stable cross-version interfaces.

## Documentation

- [Overview](external-api/README.md)
- [Quickstart](external-api/02-quickstart.md)
- [API reference](external-api/03-api-reference.md)
- [Inputs and parameter axes](external-api/05-inputs-and-data.md)
- [Limitations](external-api/09-limitations-and-roadmap.md)
