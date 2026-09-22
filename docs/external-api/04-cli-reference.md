# 04 · Command line reference (v1.0)

Entry:`python -m nmrforge_api <Order> --study <Research roots>`.
Public parameter:`--study`(required), `--name`(new research name), `--condition <A|B|…>`.
(Default = all conditions).

## Init -- Create the study and import the dataset

```bash
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/apo --condition A
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/holo --condition B
python -m nmrforge_api init --study ~/studies/s1            # only lists the registered conditions
```

Output: study root + condition + dataset summary (ndim/nuclear/sampling/source/raw_dir/number of files).
The same condition label cannot be bound to two pieces of data (error reported, no overwriting).

## Reference -- reference workflow (one copy for each condition)

```bash
python -m nmrforge_api reference --study ~/studies/s1
python -m nmrforge_api reference --study ~/studies/s1 --condition A --force
python -m nmrforge_api reference --study ~/studies/s1 --params auto.yaml
```

`--params` is the input override of the automatic process (YAML/JSON); `--phase-route` explicitly specifies the
phase route; `--force` rebuilds; `--direct-range HIGH_PPM LOW_PPM` sets the **direct dimension range**
(`ext_lo` high end / `ext_hi` low end), and the reference spectrum is rebuilt when it disagrees with the established
reference. The source is recorded in `reference.json.direct_range.source` (`explicit` / `params` / `default`).
Output: the frozen spectrum and the reference script path with SHA-256, the phase source, the sampling method, and
whether this condition supports parameter combinations.

`--rebuild-peak-tables` recomputes only the two reference peak tables from the existing frozen
spectrum and `reference.list` (the spectrum and the peak identities are untouched and their
SHA-256 values are re-checked), and it **refreshes `software_version` / `software_commit` in the
record plus the study-level aggregate `records/reference.json`** (fixed 2026-09-19: an upgraded
study root used to keep claiming the old version with an empty commit, and the aggregate kept a
stale peak-table SHA).

## Peaks -- reference peak table (identity + two unified peak tables)

```bash
python -m nmrforge_api peaks --study ~/studies/s1
python -m nmrforge_api peaks --study ~/studies/s1 --sigma 25 --max-peaks 60
python -m nmrforge_api peaks --study ~/studies/s1 --peak-table external.list
python -m nmrforge_api peaks --study ~/studies/s1 --localization gaussian \
    --gaussian-roi-f1-ppm 1.5 --gaussian-roi-f2-ppm 0.25
```

The main condition automatically selects peaks (or registers an external peak table) to create `reference.list`; other conditions share the same peak identity;
Then write two reference peak tables. Output peak table path/Hash/source/Number of peaks + summary and positioning of the two tables QC.

`--sigma N` is the **peak selection threshold** (noise σ multiple, default 35σ): when the reference peak table** has not been generated**.
Specify = Select a threshold when generating a reference; specifying a different threshold after the reference has been frozen will be rejected (exit code 2.
The error message states "the threshold has been locked at the reference"), because subsequent parameter perturbations can only use the reference threshold.

## Sweep(= workflows) -- combination mode: batch execution according to parameter combination table

**`--reference` Required** (combination mode must explicitly specify the reference):

```bash
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1 --combos design.csv
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1#B --grid grid.yaml
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1/study/reference/exp_001_d_001/reference.json \
    --combos design.csv --localization both --no-resume
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1 --combos design.csv \
    --localization gaussian --localize-peaks truth_peaks.csv
```

Reference mode = `reference`(reference spectrum/script) + `peaks`(two reference peak tables) two commands; when the reference does not exist, `sweep` will report an error and prompt to run these two commands first.

| Option | Meaning |
| --- | --- |
| `--reference` | **Required**: Reference writing method (`<Research roots>` / `<Research roots>#<condition>` / `reference.json`) |
| `--combos` | ** user parameter combination table** (CSV/TSV/YAML/JSON, one combination per row, executed in order as is) |
| `--grid` | Candidate value of each axis (YAML/JSON of `axes:`, interface expansion full factor) |
| `--direct-range` | Direct dimension range `HIGH_PPM LOW_PPM` (overrides the workflow base value of this batch; it raises when it disagrees with the frozen reference range, see the next row) |
| `--allow-ext-override` | Allow this batch's `--direct-range` to disagree with the frozen reference range (every run then carries the `direct_range_override` warning code); without it a disagreement raises instead of changing the window silently |
| `--max-runs` | Maximum number of combinations (default 256) |
| `--localize-peaks` | **Targeted localization**: refine only the peaks in a CSV (at least a `peak_id` column); detection, row count and `peak_id` numbering are unchanged and unlisted peaks stay (position from the detection-stage parabola); default = the whole spectrum. The CSV may carry a `condition` column (**multi-condition studies**: each condition reads only its own rows, a missing row is an error; one file can serve A and B) |
| `--localize-peaks-gaussian` / `--localize-peaks-parabolic` | **Per-method** targeting (overrides `--localize-peaks`): typical use is parabolic for the whole spectrum plus Gaussian only on the truth peaks; the `condition` column works here too |
| `--localization` | Peak position refinement method: `parabolic` (default)/ `gaussian` (only 2D)/ `both` (both tables appear) |
| `--edge-margin-ppm` | Peak selection excludes the physical width of the edge axis peak (ppm; default 3 x nuclide line width of this axis) |
| `--gaussian-roi-f1-ppm` / `--gaussian-roi-f2-ppm` | Gaussian ROI Physical Radius (ppm) |
| `--no-resume` | Do not skip completed workflow |

`--combos` and `--grid` must and can only be given one. Each combination = one `workflow_id`.
(`W0001`…); Run processing on all conditions, and then use the reference lock threshold to independently select peaks on the own spectrum of the combination.
Press `--localization` to output the peak table (parabolic default; `both` to output two) + complete log + parameter three layers.
+ Version.Threshold key(`sigma_multiplier`/`min_snr`/`threshold_sigma`/.
`detection.sigma_multiplier`) will directly report an error when written into the combination table (the threshold is locked at the reference).
A `--direct-range` that disagrees with the frozen reference range **raises** (exit code 2) by default: the
reference spectrum is not rebuilt while peak positions and the peak set follow the window. Pass
`--allow-ext-override` to confirm the override; every run then carries `direct_range_override` in `warnings`
(the reference spectrum is still not rebuilt).

Output: workflow number, condition list, status count, records path.

## Report -- Recalculate the summary using existing records (no re-run processing)

```bash
python -m nmrforge_api report --study ~/studies/s1
```

Reassemble `study/records/`(long list/manifest/workflows/runs/measurement).
Does not call the backend. It also **refreshes the `records/reference.json` aggregate** (version,
commit and peak-table SHA-256 taken from disk, 2026-09-19). Outputs the workflow count and status
count.

## Status -- current situation

```bash
python -m nmrforge_api status --study ~/studies/s1
```

Output: Conditional dataset, condition-by-condition reference (script/spectral hash, peak number, peak table), planned workflow number and.
ID, number of recorded workflows, running status count, records directory.

## Exit code

- `0` Success;
- `2` `SensitivityError`(parameter /data/refer to/Measurement/Combination table problem), error written to stderr
  (`Error: ...`), can be determined by script.

## `compat`: behaviour compatibility manifest (needs no study root)

```bash
python -m nmrforge_api compat
python -m nmrforge_api compat --out compat.json
python -m nmrforge_api compat --golden --workdir /tmp/nmrforge_golden
```

| Option | Meaning |
| --- | --- |
| `--out FILE` | write the manifest to a JSON file |
| `--golden` | also run the golden vector (a deterministic synthetic spectrum) and compare it with the declared hashes |
| `--workdir DIR` | directory for the golden vector's artefacts (default: a temporary directory, discarded) |

Use it to take a `behavior_digest` before and after an ensemble run (or read `compat_level` from
the artefacts), and stop or annotate when they differ; only `behavior_changed` /
`contract_changed` require working out the re-run scope from `affected`. Field descriptions are
in the "Behaviour compatibility manifest" section of 03.
