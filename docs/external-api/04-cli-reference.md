# 04 · Command line reference (v1.1.1)

Entry:`python -m nmrforge_api <Order> --study <Research roots>`.
Public parameter:`--study`(required), `--name`(new research name), `--condition <A|B|…>`.
(Default = all conditions).
The CLI uses API **1.1.1** and software **1.0.5**, with the 38-column peak-table contract.
Install the source or wheel to use this entry point; see [installation](../installation.md).

## Init -- Create the study and import the dataset

```bash
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/apo --condition A
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/holo --condition B
python -m nmrforge_api init --study ~/studies/segments --segmented \
    --dataset ~/data/segment1 --dataset ~/data/segment2 --condition A
python -m nmrforge_api init --study ~/studies/s1            # only lists the registered conditions
```

Output: study root + condition + dataset summary (ndim/nuclear/sampling/source/raw_dir/number of files).
The same condition cannot be registered twice (error, no overwrite). `--segmented` is off by default.
Without it, supply at most one `--dataset`; with it, repeat `--dataset` at least twice. The CLI order
defines the complete, ordered segment list for one condition. Duplicate paths are rejected, and the
CLI never discovers missing segments automatically. Each value must name a complete Bruker raw
directory. Segments may have different parent directories. Reopening an existing segmented study for
`reference` or `sweep` needs no segmented flag.

Kinetic layouts and NUS data without a usable `nuslist` are rejected. Segment acquisition parameters,
dimensions, nuclei, effective TD, spectral width, sampling mode, axis layout, SFO frequency and carrier
must agree. The full source list and order are used when deciding whether an existing condition matches.
To replace sources or change their order, create a new condition or research root; `reference --force`
rebuilds the reference but does not change its bound source list.

## Reference -- reference workflow (one copy for each condition)

```bash
python -m nmrforge_api reference --study ~/studies/s1
python -m nmrforge_api reference --study ~/studies/s1 --condition A --force
python -m nmrforge_api reference --study ~/studies/s1 --params auto.yaml
python -m nmrforge_api reference --study ~/studies/s1 --condition-params condition-overrides.json
python -m nmrforge_api reference --study ~/studies/s1 --carrier-ppm F1=120.0 --carrier-ppm F2=4.7 --force
python -m nmrforge_api reference --study ~/studies/s3 --carrier-ppm F3=4.7 --force
```

`--params` is the input override of the automatic process (YAML/JSON); `--phase-route` explicitly specifies the
phase route; `--force` rebuilds; `--direct-range HIGH_PPM LOW_PPM` sets the **direct dimension range**
(`ext_lo` high end / `ext_hi` low end), and the reference spectrum is rebuilt when it disagrees with the established
reference. The source is recorded in `reference.json.direct_range.source` (`explicit` / `params` / `default`).
Output: the frozen spectrum and the reference script path with SHA-256, the phase source, the sampling method, and
whether this condition supports parameter combinations.

`--condition-params` takes a JSON object keyed by condition; per-condition values override common
parameters per key. For carriers, `--carrier-ppm` is repeatable, and a repeated F axis is an error.
Explicit CLI carrier values override the common params per axis; condition-specific carrier values
then override them per axis.

Reference reuse requires a complete normalized input fingerprint match; a mismatch or an old
reference without a valid fingerprint raises an error and requires explicit --force (no automatic
rebuild). In multi-condition runs all references are checked before the engine starts.

`--rebuild-peak-tables` recomputes only the parabolic reference peak table from the existing frozen
spectrum and `reference.list` (the spectrum and the peak identities are untouched and their
SHA-256 values are re-checked), and it **refreshes `software_version` / `software_commit` in the
record plus the study-level aggregate `records/reference.json`**.

## Peaks -- reference peak table (identity + parabolic localisation table)

```bash
python -m nmrforge_api peaks --study ~/studies/s1
python -m nmrforge_api peaks --study ~/studies/s1 --sigma 25 --max-peaks 60
python -m nmrforge_api peaks --study ~/studies/s1 --peak-table external.list
```

Each condition automatically selects its own reference peaks and creates its own `reference.list`;
an external peak table applies only to the main condition and is never copied to other conditions.
The command writes one `reference_peak_table_parabolic.csv` per condition and reports its path,
hash, source, peak count and localisation QC. Peak IDs are local to each table, not cross-condition links.

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
    --combos design.csv --localization parabolic --no-resume
```

Reference mode uses `reference` to build the spectrum/script and `peaks` to create the reference peak table. If no reference exists, `sweep` reports an error and prompts you to run these commands first.

| Option | Meaning |
| --- | --- |
| `--reference` | **Required**: Reference writing method (`<Research roots>` / `<Research roots>#<condition>` / `reference.json`) |
| `--combos` | ** user parameter combination table** (CSV/TSV/YAML/JSON, one combination per row, executed in order as is) |
| `--grid` | Candidate value of each axis (YAML/JSON of `axes:`, interface expansion full factor) |
| `--direct-range` | Direct dimension range `HIGH_PPM LOW_PPM` (overrides the workflow base value of this batch; it raises when it disagrees with the frozen reference range, see the next row) |
| `--allow-ext-override` | Allow this batch's `--direct-range` to disagree with the frozen reference range (every run then carries the `direct_range_override` warning code); without it a disagreement raises instead of changing the window silently |
| `--max-runs` | Maximum number of combinations (default 256) |
| `--localize-peaks` | **Targeted localization**: apply parabolic refinement only to the peaks in a CSV (at least a `peak_id` column); detection, row count and `peak_id` numbering are unchanged, and unlisted peaks retain their integer detection-grid coordinates. The CSV may carry a `condition` column (**multi-condition studies**: each condition reads only its own rows, a missing row is an error; one file can serve A and B) |
| `--localization` | Localisation method; currently only `parabolic` is supported |
| `--edge-margin-ppm` | Optional manual edge-exclusion margin in ppm. By default, experiment/acquisition priors and spectrum evidence determine whether axial-edge screening applies; no unconditional band is excluded |
| `--no-resume` | Do not skip completed workflow |

`--combos` and `--grid` must and can only be given one. Each combination = one `workflow_id`.
(`W0001`…); Run processing on all conditions, and then use the reference lock threshold to independently select peaks on the own spectrum of the combination.
`--localization` uses the three-point parabolic method and outputs one peak table plus the complete log, the three parameter layers and version information. Threshold keys (`sigma_multiplier`/`min_snr`/`threshold_sigma`/.
`detection.sigma_multiplier`) will report an error if written into the combination table (the threshold is locked at the reference).
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
commit and peak-table SHA-256 taken from disk). Outputs the workflow count and status
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
