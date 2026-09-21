# 08 · Handover to a downstream analysis program (v0.2)

This software ends at "spectrum + peak table + processing record". **Statistical inference and
significance judgement belong to your own independent analysis program**, which should read the
products according to the following contract.

## 8.1 Interface

| What to read for your analysis | |
| --- | --- |
| Peak position of each parameter combination (two algorithms) | `study/records/peak_table_parabolic.csv` / `peak_table_gaussian.csv` (long table), or workflow by workflow `study/workflows/<id>/<condition>/peak_table_*.csv` |
| Peak identity | Reference peak table: `reference_peak_id`(R0001...); **The matching of the combined peak table is on your side** (In the combined mode, peaks are selected independently, `reference_peak_id`/`assignment` in the table are left blank, and `H_ppm`/`N_ppm` are used for matching) |
| condition A/B | `condition` / `dataset` column (or group by directory / condition) |
| parameter with automatic parameter actual value | `workflows/<id>/workflow.json` with `runs.json` of `parameters_requested`/`parameters_used`/`parameters_resolved` |
| Availability of peaks | `SNR`, `fit_success`, `boundary_hit`, `fallback` (the combined mode peak table only contains detected peaks; the reference peak table also contains the reserved rows of `detected=false`) |
| Reference | `records/manifest.json` of `references`(script /Spectrum/Two peak table hashes) and `peak_identity` |
| Recalculation and citation | script / spectrum SHA-256, `grid_sha256`, `versions`, complete `log.txt` |

## 8.2 Minimum read example (read only, not calculated)

```python
import csv
from pathlib import Path

records = Path("~/studies/hsqc_params/study/records").expanduser()

def load(method: str) -> list[dict]:
    with (records / f"peak_table_{method}.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

parabolic = load("parabolic")      # columns: workflow_id, condition, peak_id,
gaussian = load("gaussian")        #     reference_peak_id, H_ppm, N_ppm, intensity, ...

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

1. **Peak Alignment**: Peak table in combined mode **None** `reference_peak_id` (independent peak selection) -- Press.
   The `H_ppm`/`N_ppm` tolerance matches the peaks of each combination back to `reference_peak_id` of the reference peak table.
   (or your own designation), and clearly record the matching tolerance, unmatched peaks and number of peaks;
2. **Algorithm selection**: parabolic and gaussian are two sets of independent observations; when comparing the differences between the two.
   The Gaussian side should exclude `fit_success=false` (or treat them as missing).
   And retain `fallback_reason` as an audit trail;
3. **weight/Missing**: When a certain combination does not match a certain reference peak **Do not delete silently** -- Clearly mark it in the analysis.
   (The missing mechanism may be related to the scanned parameter; the `detected=false` row in the reference peak table is also retained);
4. **Recalculable**: Attached to the analysis product are the script/spectral hashes in `records/manifest.json` and.
   `grid_sha256`, and `versions` of the software used;
5. **Do not write back to the research root**: Please place the analysis results in your own directory (the software product is the execution record.
   Analysis should not overwrite them).

## 8.4 Sharding and parallelism (optional)

The upper limit of the number of combinations `max_runs` (default 256); if you want to parallelize, please split according to the **parameter axis** (each machine runs different subsystems).
Grids, each with a study root), and finally press the button on the analysis side to merge the long tables -- the same reference and peak identity are guaranteed to be comparable.
Key points: File `records/manifest.json` together when sharding to facilitate checking whether they have the same reference.
