# External dependencies

nmrForge automates NMRPipe. It does not contain, download or install it. This page explains what
is external, how it is located, and what happens when it is missing.

## What must be installed by you

| Tool | Needed for | Install source |
| --- | --- | --- |
| NMRPipe | conversion (Bruker to NMRPipe format), Fourier transform, phase correction, baseline correction, window functions, zero filling | upstream NMRPipe distribution (see its own documentation) |
| SMILE | NUS reconstruction | distributed with NMRPipe |
| `bruker` (NMRPipe accessory) | Bruker to NMRPipe conversion scripts | distributed with NMRPipe |
| Java runtime | some NMRPipe accessory tools, depending on installation | your OS package manager, or an OpenJDK distribution |

Nothing from these packages is copied into this repository, and no script from an NMRPipe
installation is vendored under `scripts/` or `nmrforge_data/presets/`. See
[THIRD_PARTY.md](../THIRD_PARTY.md).

## How nmrForge finds them

The NMRPipe executable is located by `find_nmrpipe_bin` in this order:

1. the explicit `backend.nmrpipe.path` setting;
2. the compatibility alias `backend.nmrpipe.nmrpipe_bin`;
3. when no explicit path is configured, the user's `csh`/`tcsh` environment after
   `source ~/.cshrc`, queried with `which`;
4. `PATH`.

Both configuration values are explicit paths and may name an installation directory or an
executable. An invalid configured path produces an error; it is not silently ignored in favour of
the next source. Automatic discovery does not scan conventional installation directories.

For other NMRPipe companion tools, a configured and valid per-tool path takes precedence. Otherwise
the application checks the `csh` environment first, then searches `com/` subdirectories under the
discovered installation directory and its parent directories.

Default configuration lives in `nmrforge_data/config/nmrforge.yaml`. Copy it to
`nmrforge_data/config/nmrforge.local.yaml` for machine-local overrides; that file is git-ignored precisely so
that machine paths never end up in the repository.

```yaml
backend:
  provider: nmrpipe
  nmrpipe:
    nmrpipe_bin: ""      # compatibility alias for an explicit path
    path: ""             # primary explicit path; takes priority over nmrpipe_bin
```

## How missing tools are reported

- The GUI probes external tools at startup, and processing steps check capabilities when needed.
  If NMRPipe is missing, the application reports a user-facing message such as "NMRPipe executable
  was not found", not an import error.
- Version detection is best-effort: the backend records NMRPipe/SMILE versions into the run
  record only after it has actually located them, and leaves the field empty rather than writing
  a made-up value. See `core/version.py`.
- `examples/quickstart.py` prints whether NMRPipe and SMILE were found before doing anything.

## What works without NMRPipe

| Capability | Without NMRPipe |
| --- | --- |
| Bruker parameter parsing | yes |
| Dimensionality and dimension layout | yes |
| Experiment-type classification | yes |
| Sampling classification (uniform / NUS / uncertain) | yes |
| Reading the time-domain data | yes |
| Generating a new processed spectrum (conversion, FT, phase, baseline, windows, zero filling) | no |
| SMILE reconstruction | no |
| Pure-Python QC and peak picking on an existing processed spectrum | yes (NMRPipe is not needed) |
| Peak-table parsing/export | yes (file-level operations) |

This boundary is deliberate: results are never silently produced by a substitute engine.

## Resource notes

- NUS reconstruction memory is dominated by the direct-dimension size multiplied by the
  iterative indirect FT grid, so zero filling inflates it quickly. nmrForge estimates the peak
  before running SMILE and passes an explicit `-maxMem`, and refuses to start a reconstruction
  it expects to exceed the available memory.
- Linux is the target runtime. Do not treat Windows-specific RAM-disk setup or other Windows-only
  resources as supported runtime recommendations.
- The number of SMILE threads is `smile.nthread` (default 2, clamped to CPU count minus two).

## Version compatibility

The backend records the NMRPipe and SMILE versions it used into each run record, so a later
reproduction can prove which engine produced a result. If you change NMRPipe versions between
processing a reference spectrum and processing the parameter combinations derived from it,
rebuild the reference: macro semantics (hence script meaning) can differ between versions.
