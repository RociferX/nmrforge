# Command-line reference

The command line is part of the versioned `nmrforge_api` package and uses the same processing
implementation as the desktop application. It does not require Qt:

```bash
python -m nmrforge_api --help
```

The current CLI contract is API v1.1. Install from a source checkout containing that contract;
the software version remains 1.0.2 and previously released AppImages do not update automatically.
See
[Installation](installation.md). Processing requires separately installed NMRPipe; NUS
reconstruction also requires SMILE.

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Create a study and register a Bruker dataset under a condition. |
| `reference` | Build and freeze the reference spectrum, processing script, and reference metadata. |
| `peaks` | Select reference peaks or register an external peak list; writes one parabolic reference table. |
| `sweep` (alias `workflows`) | Execute user-specified parameter combinations against an explicit frozen reference. |
| `report` | Rebuild summaries from existing records without reprocessing spectra. |
| `status` | Show the study's registered conditions, references, workflows, and statuses. |
| `compat` | Print the compatibility manifest and optionally run the deterministic golden vector. |

## Typical sequence

```bash
python -m nmrforge_api init --study ./study --dataset /path/to/bruker/dataset --condition A
python -m nmrforge_api reference --study ./study
python -m nmrforge_api peaks --study ./study
python -m nmrforge_api sweep --study ./study --reference ./study --combos design.csv
python -m nmrforge_api report --study ./study
python -m nmrforge_api status --study ./study
```

For multiple conditions, register each dataset with a unique condition label before building
references. The reference stage creates the peak identities; the sweep stage requires the reference
explicitly and independently detects peaks on each candidate spectrum under the locked reference
threshold.

## `sweep` options

Reference caches are reused only when the complete normalized processing input matches.
Changed inputs or a missing legacy fingerprint require `reference --force` to rebuild.
`--rebuild-peak-tables` rebuilds tables without rerunning reference processing.
Reference construction accepts repeatable logical-axis CAR overrides such as
`--carrier-ppm F1=118.0` (ppm). Without an override, the AUTO conversion value is preserved,
or the parsed Bruker carrier is used by the fallback converter.
Different `--study` and `--reference` roots are currently rejected explicitly; sweep results
are never silently written into a separate read-only reference root.

```text
--study STUDY                     study root directory
--name NAME                       study name when creating a new one
--condition CONDITION             select a condition; by default, process all registered conditions
--grid GRID                       YAML/JSON axes expanded as a Cartesian product
--reference REFERENCE             required frozen reference: study, study#condition, or reference.json
--combos COMBOS                   explicit CSV/TSV/YAML/JSON parameter rows
--direct-range HIGH_PPM LOW_PPM   direct-dimension extraction window in ppm
--allow-ext-override              allow a direct range that differs from the frozen reference; records a warning
--max-runs MAX_RUNS                cap the number of workflows
--localize-peaks FILE              refine only listed detected peaks (CSV must contain peak_id)
--edge-margin-ppm PPM              explicit manual edge margin for peak selection
--no-resume                        rerun completed workflows
```

Supply exactly one of `--grid` and `--combos`. Combination order follows the input table/grid
order. Peak localization supports only the three-point parabolic method; Gaussian and mixed-method
flags were removed. Use `--localize-peaks` to target peaks without changing detection, row count,
or per-spectrum `peak_id` numbering. A target CSV may include `condition` to provide condition-
specific peak IDs.

An explicit `--edge-margin-ppm` is a user override. Without it, the default does not exclude a
fixed edge band: a conservative axial screen uses experiment/acquisition evidence and many narrow,
aligned candidates at the original data edges. Isolated edge peaks, internal carrier peaks, and
unknown or ambiguous cases are retained. See [Peak picking](peak-picking.md).

## Sampling gate

Sampling classification is not inferred from a nominal percentage or data length alone. The
importer uses a standard `nuslist` or a file explicitly named by `acqus.NUSLIST`. A complete
schedule in standard order can use uniform processing; a complete but reordered schedule still
requires schedule-based placement. Explicit NUS data with a missing or unrecoverable schedule is
rejected at import. Trailing block padding is not treated as missing data. NUS sweep support is
2D; 3D NUS currently supports reference construction only.

## Outputs and errors

A successful sweep writes per-workflow records and one unified
`peak_table_parabolic.csv`, plus a study manifest and status summaries. See
[Outputs and records](external-api/06-outputs-and-records.md) for the current schema.

Exit code `0` indicates success. Invalid inputs, unresolved sampling metadata, incompatible
references, or invalid target lists produce an actionable error and a nonzero exit code. CLI
diagnostics go to stderr; keep stdout available for machine-readable output when using JSON modes.

## Full API details

- [External API CLI reference](external-api/04-cli-reference.md)
- [Inputs and parameter tables](external-api/05-inputs-and-data.md)
- [Outputs and records](external-api/06-outputs-and-records.md)
- [Troubleshooting](external-api/10-troubleshooting.md)
