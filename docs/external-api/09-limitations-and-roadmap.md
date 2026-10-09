# 09 - Limits and extension paths (v1.1)

## 9.1 Support matrix

| Item | v1.1 | Notes |
| --- | --- | --- |
| uniform 1D/2D/3D data | yes, combinations run | goes through NMRPipe `process()`; studies are mostly 2D |
| NUS **2D** data | yes, combinations run | goes through `reconstruct_nus()` (SMILE); candidates are isolated; SMILE parameters can be swept |
| NUS 3D data | reference spectrum only | running combinations raises `SweepError` (see 9.2) |
| reference workflow (script + parabolic peak table) | yes | the reference is a baseline, not a claimed optimum |
| batch execution by `workflow_id` | yes | `W0001...`; each condition uses its own reference defaults and independently detects peaks |
| peak localisation | yes | three-point parabolic method only |
| multiple conditions (A/B) | yes | independent reference and peak table per condition; common parameters may be overridden per condition; no cross-condition peak matching |
| full sampling labelled as NUS | schedule-dependent | a valid full grid in standard order may use uniform processing; full coverage in a different order still requires schedule-based placement |
| peak overlap / deconvolution | no | localisation uses a detected extremum and three-point parabola only |
| Lorentzian / Voigt / multi-peak fitting | no | on the roadmap |
| parallel or cluster scheduling | no | serial with resume; shard along a parameter axis (see 8.4) |
| validation of parameter-axis keys | partial | locked keys raise, deterministic and unknown keys warn; key validity is mostly reported in `notes` |
| Statistical inference and scientific conclusions | no **(not this software)** | computed downstream from the unified peak table |

## 9.2 What NUS support covers

> Sampling is not inferred from a nominal percentage or data length alone. Only a standard
> `nuslist` or the file explicitly named by `acqus.NUSLIST` is used. A valid complete schedule in
> standard order may use uniform processing; a complete but reordered schedule still needs
> schedule-based placement. Explicit NUS without a recoverable schedule is rejected at import.
> Trailing zero padding is not treated as a missing sample.

**Supported: 2D NUS.** Both the reference and the workflows call `reconstruct_nus()`; the only
difference is that batch execution isolates the candidate output per combination:

- phase locking: `phases` (indirect dimensions) plus a flat `direct_phase` (direct dimension);
- candidate output at `study/workflows/<id>/<condition>/spectrum.ft2` (the backend
  `out_file`/`script_name` semantics); the final spectrum in the working directory is never
  overwritten;
- sweepable parameters: `nsigma` (alias `nSigma`), `thresh`, `nthread`, `smile_scaling` and
  others; when the value is chosen automatically the actual one is written to
  `parameters_resolved.smile`.

**Not yet supported: 3D NUS** in combination mode (the slice stream buckets by plane directory
and finalises with independent names).

## 9.3 Splitting long batches

- the number of combinations defaults to a maximum of 256 (`max_runs`); beyond that, split into
  several study roots or run in batches;
- a study root can be re-run; workflow x condition pairs that already succeeded are skipped
  (resume);
- for several machines, shard along a **parameter axis** (each machine takes a sub-grid and its
  own study root) and merge the long table afterwards;
- record `grid_sha256` (present in the plan and the manifest) before changing a parameter table,
  so you can check that two runs used the same design.

## 9.4 Roadmap

| Priority | Item | Deliverable shape |
| --- | --- | --- |
| high | 3D NUS in combination mode | slice stream keyed by `workflow_id` plus isolated finalise output |
| medium | extend peak fitting to Lorentzian/Voigt/multi-peak | current sub-grid localisation is a three-point parabola |
| medium | schema validation of parameter keys | raise instead of only warning about unknown keys |
| medium | progress file | update `records/progress.json` per combination for external monitoring |
| medium | explicit 3D plane selection | measure on a named plane when the peak table fixes the dimension values |

When filing a request, attach `records/manifest.json` and the `status` output so it can be
reproduced.

## 9.5 Validation and evidence

The ordinary pytest suite checks processing orchestration and records through mocked engine
boundaries. It does not run NMRPipe or SMILE. Real-engine verification separately records the
input, software revision, engine versions, parameters, and resulting spectra/QC.

[Four-route real-data evidence](../evidence/real-data-comparison.md) compares 2D/3D uniform/NUS
results with identified references. The artificial 2D example is controlled downsampling of
uniform data (75% requested, 68/90 retained), while the 3D NUS example was acquired at 25%.
The main signals agree well in these cases. Candidate coverage is a detection-and-matching
measure, not assigned-peak recovery or a guarantee for every experiment.

Record the software/API version, compatibility manifest, and processing parameters when citing
results. Biological interpretations and experiment-specific scientific acceptance remain the
responsibility of downstream analysis.

## 9.6 Relationship to the desktop application: shared processing, separate entry-point validation

- The API reuses the NMRPipe/SMILE processing chain, spectrum-axis mapping and peak components;
  it does not implement a second processing engine.
- Parameter merging, reference freezing, independent detection, table serialization and resume
  remain API-specific orchestration and require their own validation. Shared components do not
  prove that entry-point parameters are applied or that exported axes and QC are correct.
- The main application's current real-data evidence is in the
  [real-data comparisons](../evidence/real-data-comparison.md). Two-dimensional positions cannot
  validate a three-dimensional carbon axis; projections cannot cover all three-dimensional
  overlap or artifacts. Projection candidate-match fractions are not true-peak recovery rates.

## 9.7 Interpreting run records

Review the run status, warnings, resolved parameters, and resulting spectrum together.
A compatibility manifest identifies software behavior; an accepted QC result does not establish
peak identity or an experiment-specific scientific conclusion.

## 9.8 Input and output boundaries

- Each condition builds an independent reference spectrum and peak table; an external identity
  table applies only to the main condition. IDs are local to each table, not cross-spectrum links.
- The unified 38-column table retains F1/F2/F3 coordinates, nuclei and equivalent linewidths.
  H/N aliases are blank for ambiguous repeated nuclei. Use complete logical-axis identity.
- Targeted localisation refines only selected peaks; other detected peaks retain integer-grid
  positions, actual method `none` and uncomputed localisation QC (`NaN`).
- Reference-only positive finite `sweep_width_hz` and explicit `carrier_ppm` are audited by axis.
  Combinations cannot change conversion calibration. `params_by_condition`/`--condition-params`
  can overlay common reference parameters independently for each condition.
- Boolean FT-neg values and flip aliases may be combination candidates; phase routes and FT-alt
  remain locked. Reference construction applies FT flags before phase optimisation. FT-neg
  changes in combinations do not automatically reoptimise phase.
- CLI rejects different `--study` and `--reference` roots before writing. Within one root,
  combinations write workflow/results records; they are not an entirely read-only API call.
- Conversion provenance records raw digital-filter parameters, script hashes and conversion
  commands; reference audit separates requested/resolved/actual FT commands and stage timing.
  An unverified correction method is `unknown`, not inferred as executed from metadata.

Legacy shared-identity or incomplete-axis references must be rebuilt. The API generates independent
spectra/tables and processing records only; matching, missingness policies and statistical analysis
are downstream responsibilities. Historical uncertainty helpers are not part of this two-stage
processing interface, and local peak IDs cannot be treated as corresponding peaks without matching.

## 9.9 FID reuse boundary

Reference mode imports sources when needed, converts each segment and merges them, then freezes the
FID, conversion evidence and sampling schedule for combination runs. Combination mode processes only
these existing reference artefacts and performs independent peak selection. It supports a single-file
FID, a 3D uniform slice directory and a merged multi-segment FID. It must not automatically reconvert
or re-merge, delete raw sources, modify the reference FID or rewrite the sampling schedule.

If the reference FID is missing or damaged, the source or conversion evidence differs from the
reference, or the requested parameters would require conversion or merging, combination mode raises
an error and asks the user to rebuild the reference with `force=True` (CLI: `reference --force`).
`force` rebuilds at the reference stage; it does not cause combination mode to convert on demand.
Conversion-time settings such as an explicit segment shift must be set when rebuilding the reference.
The GUI's default conversion behavior is unchanged by this API boundary.

Strict reuse for single-file FIDs, 3D uniform slices and merged segments passed Linux engineering
regression. Older references without a frozen-FID record require one explicit rebuild with
`force=True`. Fingerprints use content SHA-256 up to 8 MiB and `size + mtime_ns` for larger files;
they are not content-level authentication. This does not establish real NMRPipe/SMILE engine
validation; engineering regression is not a substitute for real-engine acceptance.
