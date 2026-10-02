# Peak picking and localisation

Peak picking detects local maxima on the final processed spectrum, refines their coordinates and
writes a peak table. It does not alter the spectrum or assign resonances. This page describes
current behaviour; existing peak tables are not rewritten when detection rules change.

## Detection

`workflow/pick_peaks.py::pick_peaks` writes a POKY-style `.list` table, a per-peak localisation
sidecar and a run record.

| Parameter | Meaning |
| --- | --- |
| `sigma_multiplier` | Threshold in multiples of the noise estimate (default 35 sigma). If you supply a value, the value actually used is recorded together with its source. |
| `edge_margin_ppm` | Optional manual edge exclusion margin in ppm. There is no unconditional edge band. |
| `edge_margin_points` | Optional manual point margin; zero filling changes point spacing, so this is not comparable across resolutions. |
| `localization_method` | Only `parabolic` is supported. Removed methods raise an explicit error. |
| `ref_peaks`, `ref_nuclei`, `tolerance_ppm` | Restrict or match peaks against a reference peak table (see below). |

Noise is estimated robustly. Threshold, peak height and S/N use the spectrum's global median
background; this is a measurement convention, not a spatial baseline correction. Non-finite
spectrum values are rejected. Compact equal-height maxima caused by half-grid ties are merged;
long ridges and broad plateaus are not treated as local peaks.

### Axial-peak screening

Automatic screening first checks experiment type, nucleus combination and acquisition parameters.
Only when the prior information supports screening does it inspect the original spectrum edges.
Exclusion also requires evidence from many narrow, dispersed and aligned edge candidates. An
isolated edge peak, an internal carrier peak, an unknown experiment, conflicting parameters or an
uncertain cropped boundary is retained. This heuristic is not proof that a peak is an artefact;
the rule, exclusion count and reason for skipping are recorded. GUI picking, SMILE evaluation and
API combinations share the same rule. An explicit margin is a manual override and is recorded
separately.

### Peak sign

Automatic sign selection follows experiment templates. Phase-sensitive COSY, NOESY and ROESY
templates retain positive and negative candidates. Strong evidence of both signs can be a fallback
for unknown or low-confidence experiment types. Explicit API requests (`positive`, `negative`,
`both` or `dominant`) take precedence and their source is recorded. Here same-sign/mixed describes
peak polarity, not uniform/NUS sampling.

### Reference-constrained picking

Reference alignment estimates a global chemical-shift offset for tolerance-based matching. It
does not move the spectrum or change processing parameters. Shared axes are matched by nucleus
and logical F-axis identity; axes with the same nucleus are not interchangeable. When a 2D
reference constrains a 3D spectrum, the third dimension is free, so one reference peak can retain
multiple 3D candidates. If axes cannot be shared, identity is ambiguous or alignment quality is
below the current 60% threshold, automatic filtering is skipped and candidates are retained with
a reason. With fewer than five reference peaks, coordinates are matched directly without global
alignment. Invalid or non-finite coordinates fail explicitly. Export-after-alignment shifts the
exported peak table only, never the source spectrum.

## Localisation

Detection finds the largest sampled point. Localisation estimates its sub-grid position.

| Method | How | Dimensionality |
| --- | --- | --- |
| `parabolic` | Independent three-point parabolic refinement per axis | Any (only method) |

The three-point parabola is a deterministic closed-form calculation, not an iterative fit.
`boundary_hit` marks a vertex offset at ±0.5 points. `fit_success` means a finite equivalent
linewidth could be calculated; it differs from a localisation record's `success` (which means
localisation ran). A boundary or non-concave template may have no valid linewidth. The equivalent
linewidth estimates local curvature and is not a full line-shape fit. `fallback` and
`fallback_reason` remain compatibility fields and do not imply an algorithm switch.

Every localisation writes an attachment next to the peak table,
`<peak table>.localization.json`, with requested and actual methods plus per-peak records. Legacy
`gaussian` and `both` requests raise an error; they are never silently switched to parabolic.

## Peak table format

The default interchange format is a POKY-style `.list`:

```text
label    F2_ppm    F1_ppm    height    ...
```

Import and export are handled by `core/peaks/peak_table.py`, which also writes the axis units and
the nucleus labels derived from the spectrum header (`core/peaks/axis_units.py`). Nucleus
assignment is taken from the NMRPipe header slots rather than guessed from ordering.

## Using it

GUI: run Peak Picking after generating a spectrum. Select a table row to locate it in the viewer.

Python:

```python
from workflow.pick_peaks import pick_peaks

result = pick_peaks(
    manager,
    "exp_001",
    "data_001",
)
print(result["peak_count"], result["peak_path"])
```

Scripting API localization uses `parabolic`, the only supported method; see the
[API reference](external-api/03-api-reference.md).

## Boundaries

- Peak picking needs a processed spectrum, so it needs NMRPipe. Parsing and exporting existing
  peak tables does not.
- The default threshold is a convention, not a measurement. Set it explicitly when a specific
  threshold matters for a publication so the chosen value is recorded.
- The default threshold is a convention, not a measurement. If you rely on a specific threshold
  for a publication, set it explicitly so the recorded value is the one you mean.
