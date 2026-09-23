# Command-line reference

The processing command line lives in the `nmrforge_api` package and is the same engine the GUI
drives:

```bash
python -m nmrforge_api --help
```

Use the command line from the 1.0.1 source checkout after its editable install; see
[installation.md](installation.md).

## Sub-commands

| Sub-command | Purpose |
| --- | --- |
| `init` | Create a study and register one condition's Bruker dataset. |
| `reference` | Generate and freeze the reference spectrum, reference script and reference peak tables. |
| `peaks` | Select peaks on the reference spectrum, or register an external peak table. |
| `sweep` (alias `workflows`) | Run parameter combinations against the frozen reference. |
| `report` | Recompute summaries from existing records without re-processing anything. |
| `status` | Print the current state of the study. |

## Typical sequence

```bash
python -m nmrforge_api init      --study ./study --dataset /path/to/bruker/dataset
python -m nmrforge_api reference --study ./study
python -m nmrforge_api peaks     --study ./study
python -m nmrforge_api sweep     --study ./study --grid grid.yaml --reference study/reference.json
python -m nmrforge_api report    --study ./study
python -m nmrforge_api status    --study ./study
```

## `sweep` options

```text
--study STUDY                     study root directory
--name NAME                       study name when creating a new one
--condition CONDITION             condition label (A/B/...); default runs all conditions
--grid GRID                       parameter grid as YAML/JSON (cartesian expansion)
--reference REFERENCE             reference to sweep against, e.g. study or study#condition
--combos COMBOS                   explicit combination table (CSV/TSV/YAML/JSON)
--direct-range HIGH_PPM LOW_PPM   direct-dimension window, applied over workflow defaults
--max-runs MAX_RUNS               cap the number of workflows executed
--localization {parabolic,gaussian,both}
                                  peak localisation method for the combinations
--edge-margin-ppm EDGE_MARGIN_PPM peak-selection edge exclusion
--gaussian-roi-f1-ppm / --gaussian-roi-f2-ppm
                                  Gaussian fitting ROI radii (indirect / direct, ppm)
--no-resume                       ignore previously completed workflows and rerun
```

Example `grid.yaml`:

```yaml
zero_fill: [1, 2, 4]
window.F1.off: [0.35, 0.45, 0.55]
```

## Peak localisation methods

| Value | Meaning |
| --- | --- |
| `parabolic` | Default. Three-point parabolic refinement, works on any dimensionality. |
| `gaussian` | 2D Gaussian fit; rejected with an explicit error on non-2D spectra. |
| `both` | Run both and keep both peak tables. |

## Sampling classification is a hard gate

`sweep` refuses to proceed on a dataset whose sampling classification is `uncertain`. Resolve the
metadata conflict first: a wrong uniform/NUS decision changes the meaning of every parameter in
the grid, so this is deliberately not overridable by a flag.

## Full reference

- [external-api/04-cli-reference.md](external-api/04-cli-reference.md) - complete options,
  including conditions, combo tables and resumption.
- [external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md) - what each
  sub-command writes.
- [external-api/10-troubleshooting.md](external-api/10-troubleshooting.md) - CLI-specific errors.