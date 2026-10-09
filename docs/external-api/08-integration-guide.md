# 08 · Handover to a downstream analysis program (v1.1.1)

This software ends at "spectrum + peak table + processing record". **Statistical inference and
significance judgement belong to your own independent analysis program**, which should read the
products according to the following contract.

## 8.1 Interface

| What to read for your analysis | |
| --- | --- |
| Peak position of each parameter combination | `study/records/peak_table_parabolic.csv` (long table), or workflow by workflow `study/workflows/<id>/<condition>/peak_table_parabolic.csv` |
| Peak identity | Reference peak table: `reference_peak_id`(R0001...); **The matching of the combined peak table is on your side** (In the combined mode, peaks are selected independently, `reference_peak_id`/`assignment` in the table are left blank, and `H_ppm`/`N_ppm` are used for matching) |
| condition A/B | `condition` / `dataset` column (or group by directory / condition) |
| parameter with automatic parameter actual value | `workflows/<id>/workflow.json` with `runs.json` of `parameters_requested`/`parameters_used`/`parameters_resolved` |
| Availability of peaks | `SNR`, `fit_success`, `boundary_hit`, `fallback` (the combined mode peak table only contains detected peaks; the reference peak table also contains the reserved rows of `detected=false`) |
| Reference | `records/manifest.json` of `references` (script/spectrum/peak-table hashes) and `peak_identity` |
| Recalculation and citation | script / spectrum SHA-256, `grid_sha256`, `versions`, complete `log.txt` |

## 8.2 Minimum read example (read only, not calculated)

```python
import csv
from pathlib import Path

records = Path("~/studies/hsqc_params/study/records").expanduser()

with (records / "peak_table_parabolic.csv").open(encoding="utf-8") as fh:
    parabolic = list(csv.DictReader(fh))  # workflow_id, condition, peak_id, H_ppm, N_ppm, ...

# example: take every combination's peak positions for condition A (combination mode: the peak table only holds detected peaks)
values = [
    (row["workflow_id"], float(row["N_ppm"]))
    for row in parabolic
    if row["condition"] == "A"
]
print(len(values), values[:3])
```

> The above only does **reading and filtering**. statistical testing should be included in your analysis code
> Implement it according to your own statistical assumptions (sample size, distribution, and handling of missing peaks must be stated). If you just want to be fast
> Self-test, Reusable tests/Detection aid `nmrforge_api.uncertainty` (the processing chain does not call it
> It does not appear in records either)

## 8.3 Recommended processing conventions

1. **Peak correspondence:** combination tables leave `reference_peak_id` blank and `peak_id`
   is local to each spectrum. Downstream analysis must establish correspondence and record its
   evidence, tolerances and ambiguity. Use complete F-axis coordinates; H/N aliases alone may
   lose information in 3D or repeated-nucleus data. Coordinates do not establish identity by themselves.
2. **Localisation:** successful refinement records `localization_method=parabolic`; undetected
   identities or targeted-skipped peaks record `none`. Read `detected`, `localization_requested`,
   `failure_reason` and QC together. Positive and negative peaks use symmetric QC. FWHM is an
   equivalent curvature linewidth, not a multi-peak lineshape fit. Keep legacy Gaussian outputs
   separate from current records.
3. **Missing peaks:** do not silently discard unmatched peaks. Record missingness and the analysis
   policy; it may depend on the processing parameters. Reference rows with `detected=false`
   are also retained.
4. **Reproducibility:** retain the script/spectrum hashes from `records/manifest.json`,
   `grid_sha256`, and software/tool versions alongside downstream products.
5. **No writeback:** save analysis results outside the study root; do not overwrite execution records.

## 8.4 Sharding and parallelism (optional)

`max_runs` defaults to 256. For external parallel execution, split the parameter grid into separate
study roots; do not concurrently write the same root. Build and validate each shard's reference
explicitly. Combinations cannot repair or regenerate invalid FID inputs.

Before joining result tables downstream, compare source and reference evidence, parameter definitions
and behaviour fingerprints, and establish peak correspondence explicitly. Identical parameters or
reference records do not guarantee shared peak identities. Preserve each shard's
`records/manifest.json`. See [input and output boundaries](09-limitations-and-roadmap.md#98-input-and-output-boundaries)
and the [FID reuse boundary](09-limitations-and-roadmap.md#99-fid-reuse-boundary).
