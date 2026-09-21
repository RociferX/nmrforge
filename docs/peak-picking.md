# Peak picking and localisation

Peak picking runs on a processed spectrum and produces a peak table. Detection and localisation
are separate stages, and both record what they did.

## Detection

`workflow/pick_peaks.py::pick_peaks` detects peaks in the processed spectrum and writes a
POKY-style `.list` table, registering a `WorkflowRun`.

| Parameter | Meaning |
| --- | --- |
| `sigma_multiplier` | Threshold in multiples of the noise estimate (default 35 sigma). If you supply a value, the value actually used is recorded together with its source. |
| `edge_margin_ppm` | Exclusion margin at the spectrum edges, expressed as a **physical width in ppm** (default: three times the line width of that nucleus). |
| `edge_margin_points` | Escape hatch in points instead of ppm. Not recommended: it is not comparable across different digital resolutions. |
| `localization_method` | `"parabolic"` (default) or `"gaussian"` (2D only). |
| `gaussian_roi_f1_ppm`, `gaussian_roi_f2_ppm` | Gaussian fitting ROI radii as physical widths (indirect / direct), defaulting to `peaks.localization` in the configuration. |
| `ref_peaks`, `ref_nuclei`, `tolerance_ppm` | Restrict or match peaks against a reference peak table (see below). |

Why the edge margin is defined in ppm and converted to points at run time: zero filling changes
the point spacing but not the physical width, so a ppm-defined margin covers the same spectral
region before and after zero filling. A point-defined margin would silently shrink.

### Reference-constrained picking

When a reference peak table is supplied, only peaks matching it by nucleus are kept. For 2D, all
nuclei must match. When picking a 3D spectrum against a 2D reference, the third dimension is free,
so one reference peak may map onto several detected peaks. This is what makes "follow the same
peak through a parameter sweep" meaningful instead of anecdotal.

## Localisation

Detection finds the largest sampled point. Localisation estimates where the peak really is
between samples.

| Method | How | Dimensionality |
| --- | --- | --- |
| `parabolic` | Independent three-point parabolic refinement per axis | Any (default) |
| `gaussian` | 2D Gaussian least-squares fit, initialised from the parabolic result | **2D only** |

Behaviour worth relying on:

- **Asking for a Gaussian fit on a 1D or 3D spectrum is an error, not a silent fallback.** The
  requested and the actual method are both recorded, so a substitution can never look like the
  requested analysis.
- **A failed fit falls back to parabolic**, and the fallback reason is recorded.
- **The fit is bounded on purpose.** The fitting window radius per axis can be capped
  (`peaks.localization.gaussian_roi_max_points`), and the maximum number of function evaluations
  per fit is capped (`gaussian_max_nfev`). This keeps a fine zero-filled grid from turning peak
  picking into an unbounded optimisation.
- **Both methods run on the same detected candidate**, so their results are directly comparable
  when `localization: both` is used.

Every localisation writes an attachment next to the peak table,
`<peak table>.localization.json`, with `requested_method`, `actual_method`, `fallback_reason` and
per-peak records. The run record also carries the same information under
`params['localization']`.

## Peak table format

The default interchange format is a POKY-style `.list`:

```text
label    F2_ppm    F1_ppm    height    ...
```

Import and export are handled by `core/peaks/peak_table.py`, which also writes the axis units and
the nucleus labels derived from the spectrum header (`core/peaks/axis_units.py`). Nucleus
assignment is taken from the NMRPipe header slots rather than guessed from ordering.

## Using it

GUI: the `peak picking` (peak picking) step in the pipeline, after a spectrum exists. Peaks appear in the.
table on the right; clicking a row moves the viewer to that peak.

Python:

```python
from workflow.pick_peaks import pick_peaks

result = pick_peaks(
    manager,
    "exp_001",
    "data_001",
    localization_method="gaussian",
)
print(result["peak_count"], result["peak_path"], result["localization"])
```

Scripting API: peak localisation is selected per combination with
`localization="parabolic" | "gaussian" | "both"` - see
[external-api/03-api-reference.md](external-api/03-api-reference.md).

## Configuration

```yaml
peaks:
  localization:
    method: parabolic            # parabolic (default) | gaussian (2D only)
    gaussian_roi_f1_ppm: 1.5     # indirect-dimension ROI radius
    gaussian_roi_f2_ppm: 0.25    # direct-dimension ROI radius
    gaussian_roi_max_points: 0   # 0 = no cap; a positive value caps the ROI half-width
    gaussian_max_nfev: 200       # hard limit on function evaluations per fit
```

## Boundaries

- Peak picking needs a processed spectrum, so it needs NMRPipe. Parsing and exporting existing
  peak tables does not.
- Gaussian localisation is 2D-only by construction, not by omission: the current fit is a
  two-dimensional model.
- The default threshold is a convention, not a measurement. If you rely on a specific threshold
  for a publication, set it explicitly so the recorded value is the one you mean.