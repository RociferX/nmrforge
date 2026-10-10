# 09 · Support and execution boundaries (v1.1.1)

## 9.1 Support matrix

| Item | Support | Implementation boundary |
| --- | --- | --- |
| Uniform 1D/2D/3D | Reference and combinations | Backend `process()` through NMRPipe |
| 2D NUS | Reference and combinations | SMILE `reconstruct_nus()`, isolated candidate products |
| 3D NUS | Reference only | Combination execution raises `SweepError` |
| Multiple conditions | Independent reference/table per condition | Common parameters can be overlaid per condition |
| Peak localisation | Three-point parabolic | Optional target subset; independent IDs per spectrum |
| Full schedule labelled NUS | Depends on order | Standard full grid may use uniform; reordered coverage needs placement |
| Overlap/deconvolution and Lorentzian/Voigt/multi-peak fitting | Not provided | Detection/localisation acts on extrema |
| Parallel/cluster scheduling | Not provided | Serial execution with resume; callers can shard separate study roots |
| Parameter-key validation | Partial | Locked keys raise; deterministic/unknown keys produce warnings/notes |
| Cross-spectrum matching, assignment and statistical inference | Not provided | Implemented by downstream analysis |

## 9.2 NUS execution

Only standard `nuslist` or a file explicitly named by `acqus.NUSLIST` supplies sample positions.
A nominal sampling percentage or raw-data length alone is not a schedule. Full coverage in another
order still needs schedule-based placement. Explicit NUS without recoverable positions is rejected
at import; trailing zero padding is not a missing sample.

Both 2D reference and combination execution call `reconstruct_nus()` with indirect `phases` and
direct `direct_phase`. A combination writes isolated products under
`study/workflows/<workflow_id>/<condition>/spectrum.ft2`, preserving the active/reference spectrum.
SMILE parameters include `nsigma` (`nSigma` alias), `thresh`, `nthread` and `smile_scaling`;
automatically selected values are recorded in `parameters_resolved.smile`.
The 3D NUS reference route supports reconstruction/finalisation; API combinations do not execute it.

## 9.3 Batches and resume

The default combination limit is 256 (`max_runs`). Larger grids can be split across study roots
or submitted in batches. Re-running a study skips workflow/condition pairs whose successful result
matches the current request/reference evidence. For multiple machines, assign a sub-grid and a
separate root to each worker and combine output tables downstream. `grid_sha256` in plan/manifest
identifies the combination design; source segment order is part of the condition input.

## 9.4 Parameter validation

Conversion calibration and source binding are reference-stage operations. A combination cannot
change them. Phase routes and FT-alt are locked; boolean FT-neg and flip aliases can be candidates.
FT-neg candidates do not trigger phase reoptimisation. Unknown or deterministic keys may produce
warnings rather than an invalid-key exception, so inspect `notes`, warnings, resolved parameters
and executed scripts when checking a parameter effect.

## 9.5 Checks and evidence

The ordinary pytest suite checks orchestration and records with mocked engine boundaries.
Real-engine comparisons separately record input, tool versions, script/parameter provenance and
resulting spectra. [Four-route evidence](../evidence/real-data-comparison.md) includes controlled
2D downsampling at 68/90 increments and acquired 3D NUS at 25%, alongside uniform author references.
Candidate coverage measures detected/matched candidates, not assigned-peak recovery. Three-dimensional
projection counts do not establish independent peak identities or resolve every overlap.

## 9.6 Shared processing components

The API reuses the desktop application's NMRPipe/SMILE chain, axis mapping and peak components.
API-specific orchestration handles parameter merging, reference freezing, independent detection,
serialization and resume. Check these entry-point records as well as the processed spectrum.
The API operates without Qt and does not write GUI presentation state.

## 9.7 Interpreting records

Read status, warnings, requested/resolved parameters and spectrum products together. Compatibility
metadata identifies processing code/contracts; localisation QC describes the numerical measurement,
not peak assignment. The unified table retains complete F1/F2/F3 coordinates, nuclei and equivalent
linewidths. H/N aliases are empty where a repeated nucleus makes them ambiguous.

Targeted localisation refines selected peaks while retaining detection, row count and IDs.
Non-targeted detected peaks keep integer-grid positions, `localization_method="none"` and uncomputed
QC as `NaN`. Their normal skip has an empty `failure_reason`; detection/numerical failure has its
own reason, separate from actual fallback.

## 9.8 Source, calibration and output ownership

Each condition has an independent reference and peak table. An external identity table applies
only to the main condition. `reference_peak_id` and `assignment` are empty in combination tables;
local peak IDs do not create cross-spectrum links.

Positive finite reference `sweep_width_hz` and explicit `carrier_ppm` are applied/audited per axis.
`params_by_condition`/`--condition-params` overlay common reference parameters. Segment shifts are
conversion-time settings. Conversion provenance retains digital-filter inputs, script hashes and
commands; requested/resolved/actual FT commands and stage timing are distinguished. An unverified
applied correction is recorded as `unknown`, rather than inferred from input metadata.

CLI `--study` and `--reference` must resolve to the same root before any writing. Within that root,
combination execution writes workflow/result records and candidate products; only its frozen
reference FID/schedule are read-only.

## 9.9 Frozen FID reuse

Reference construction imports sources as needed, converts each segment, merges and freezes the FID,
conversion evidence and sampling schedule. Combination execution processes those artefacts and
independently selects peaks. Supported inputs include a single FID, 3D uniform slices and a merged
multi-segment FID. It does not automatically reconvert/re-merge, delete sources, alter the reference
FID or rewrite its schedule.

Missing/damaged FID, changed source/conversion evidence, absent frozen evidence or parameters that
require conversion/merge raise an error requiring `force=True` at reference construction
(`reference --force` on the CLI). `force` does not enable conversion on demand during combinations.
Fingerprints use content SHA-256 up to 8 MiB and `size + mtime_ns` for larger files; the latter
detects metadata changes rather than authenticating full file content.
