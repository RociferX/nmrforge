# 01 · Positioning and terminology (API v1.1.1)

## What it is

`nmrforge_api` is NMRForge's Qt-free Python and command-line interface for processing user-defined
parameter combinations. It accepts Bruker NMR data and explicit parameter combinations, then writes
candidate spectra, peak tables, processing scripts, logs, and provenance records. It can run without
a graphical display.

The API uses NMRForge's processing and measurement conventions:

- Processing scripts are generated and run through the NMRPipe backend; NUS processing uses SMILE.
- Peak coordinates use the NMRPipe ppm-axis mapping. `ORIG` is preferred, `CAR` is the fallback,
  and `FDDIMORDER` maps logical dimensions to stored data axes.
- Each workflow records its executed script, peak table, log, requested and resolved parameters,
  and software/tool versions.

## Processing flow

```text
Raw data (A/B...)
    ↓  build a reference spectrum and reference peak table for each condition
Reference workflow
    ↓  user-defined parameter table: one workflow_id per row (W0001, W0002, ...)
Combination workflows
    ↓  reuse the reference FID and apply only the parameters named in each row
Candidate spectra
    ↓  detect peaks independently at the reference-locked threshold, then refine by three-point parabola
Peak tables and provenance records
```

The reference is the baseline for parameter perturbations; it is not a claim that the parameter
combination is globally optimal.

Sampling classification uses the standard `nuslist` name or a schedule explicitly named by
`acqus.NUSLIST`; the importer does not guess from arbitrary integer files. A valid full grid in
standard order may use uniform processing. Full coverage in another order still requires schedule
placement. Explicit NUS without a recoverable schedule is rejected during import. Trailing zero
padding alone does not establish that a dataset is uniformly sampled.

Dataset import uses one Bruker directory by default. To treat acquisition segments as one condition,
pass `segmented=True` and the complete ordered list of at least two directories. Each segment must
match in acquisition parameters, dimensions, nuclei, effective TD, spectral width, sampling mode,
axis layout, SFO frequency, and carrier. Kinetic layouts and NUS inputs without a usable schedule
are rejected. Duplicate paths are rejected, and segment order is part of the source identity.

## Software boundaries

- The API does not perform statistical analysis, significance testing, or scientific interpretation.
- It does not infer peak assignments, deconvolve overlapping peaks, or match peaks across spectra.
  An external peak table can provide reference peak identities.
- Peak-position refinement uses three-point parabolic localization.
- 3D NUS parameter combinations are unsupported. Combination studies support 2D uniform and 2D NUS
  data; see [input boundaries](05-inputs-and-data.md).
- Workflows run serially and can resume completed work. `axes` expands a full-factorial grid for
  convenience; `combos=` executes the supplied rows in order without designing a parameter space.

## Glossary

| Term | Meaning |
| --- | --- |
| Study root | A directory containing the NMRForge project (`project.json`) and `study/` products |
| Condition | One dataset or one ordered list of acquisition segments, labelled A/B/... |
| Dataset (`DatasetRef`) | A Bruker dataset registered to a condition and project entry (`exp_id/data_id`) |
| Reference spectrum | The condition's reference spectrum at `study/reference/<key>/reference.ft2` |
| Reference script | The NMRPipe script actually run to make the reference, stored as `process.com` with SHA-256 |
| Reference peak table | The condition's `reference.list` identities (`R0001`...) and `reference_peak_table_parabolic.csv` |
| `reference_peak_id` | An identity local to a reference peak table. Combination peak tables leave it blank; matching across spectra is downstream work. |
| `workflow_id` | The identifier for one row of the parameter-combination table (`W0001`, `W0002`, ...) |
| `parameters_requested` | The parameter values supplied by the user for that row |
| `parameters_used` | The complete parameter mapping passed to the backend (reference base plus overrides) |
| `parameters_resolved` | Values resolved during processing, including actual phase, SMILE settings, and spectrum noise |
| Candidate spectrum | The spectrum for one workflow and condition, stored under `study/workflows/<id>/<condition>/spectrum.ft2`; it does not replace the active project spectrum |
| Peak table | `peak_table_parabolic.csv`; `peak_id` is local to that spectrum |
| Status | `success`, `success_with_warning`, or `failed` |

## Runtime semantics

1. The API does not import Qt or change GUI state.
2. Combination workflows use the FID created during reference construction. They do not re-import,
   reconvert, or merge data. Missing or damaged FIDs, inconsistent input/conversion evidence, or a
   requested change that requires reconversion raises an error; rebuild the reference with
   `force=True`. Single-file, 3D uniform slice-directory, and merged multi-segment FID inputs are
   supported. See the [FID reuse boundary](09-limitations-and-roadmap.md#99-frozen-fid-reuse).
3. The reference locks the phase baseline. Direct-dimension phase search is skipped and indirect
   dimensions use the reference phase. Use `phase_delta.<axis>.p0|p1` for offsets from the reference
   or `phase.<axis>.p0|p1` for absolute values.
4. Candidate spectra are written under `study/workflows/`; they do not replace the active spectrum
   or change the desktop project's active state.
5. A successful workflow/condition writes `run.json`; matching successful runs are skipped on
   resume.
6. A failed condition is recorded as `failed`, and processing continues with the next combination
   unless the caller requests stop-on-error behavior.
7. All conditions in one workflow use the same `parameters_requested` row and produce their own
   peak tables. This does not assign shared peak identities or establish cross-spectrum matches.
