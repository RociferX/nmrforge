# Troubleshooting

Errors in nmrForge are meant to say what is missing and what to do about it. If you see a raw
`KeyError`/`TypeError`/`NoneType` traceback in the GUI or the API instead, that is a bug - please
report it (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## "NMRPipe NOT FOUND" / processing cannot start

Cause: the backend could not locate the NMRPipe executables.

1. Check that NMRPipe is installed and that its `bin` directory contains `nmrPipe`.
2. Point nmrForge at it explicitly in `nmrforge_data/config/nmrforge.yaml` (or
   `nmrforge_data/config/nmrforge.local.yaml`):

   ```yaml
   backend:
     nmrpipe:
       path: /opt/NMRPipe/nmrbin.linux212_64   # directory or executable
   ```

   The locator searches, in order: the explicit path, `nmrpipe_bin`, `PATH`, the `csh`
   environment, then common installation locations (`backend/nmrpipe_finder.py`).
3. `python examples/quickstart.py <dataset>` prints whether NMRPipe and SMILE were found before
   anything else, which is the fastest way to confirm detection.

Data understanding and QC work without NMRPipe; conversion, FT, phase, baseline and SMILE do not.

## "tcsh/csh not found on this machine (NMRPipe scripts need a C-shell)"

NMRPipe scripts are C-shell scripts, so nmrForge runs them through `csh`/`tcsh`. Install a
C-shell (`tcsh`) on the machine. This is the message text raised as a `ToolError` by
`backend/runtime.py`.

A related message is `not found proj3D.tcl (NMRPipe projection tool)` - the NMRPipe installation is.
incomplete or its `com/` directory is not where the locator expects it.

## Reconstruction fails or the machine becomes unresponsive / runs out of memory

SMILE reconstruction memory is dominated by the direct-dimension size multiplied by the
iterative indirect FT grid, so zero filling inflates it fast. nmrForge estimates the peak before
starting and passes an explicit `-maxMem`; if the estimate exceeds the available memory it
refuses to start and says so.

What to do:

1. Lower the direct-dimension zero filling, or narrow the direct-dimension range (the extraction
   window) to reduce the per-plane point count.
2. Reduce `smile.nthread`.
3. Make sure the estimate is below what the machine can actually give. A machine that is
   simultaneously doing something else is not a machine with the memory you measured.
4. If the framework reduced the direct-dimension zero filling to fit memory, that is reported:
   "Out of memory: direct dimension zero filling has been reduced to 1 x TD to reduce SMILE memory". Prefer making the reduction explicit so it.
   is recorded as your choice rather than a fallback.

There is a documented case in the project history where an under-estimated reconstruction caused
a host machine to power off; that is why the guard is conservative rather than optimistic.

## Sampling classification says `uncertain` and processing refuses to start

This is the safety gate, not a bug. It means the metadata contradicts itself - most commonly,
`nuslist` or the pulse program claims NUS while the acquired points actually cover the complete
grid, or the sampling list contains duplicate or out-of-range coordinates.

1. Read the sampling evidence lines in the log: they state which rule fired and with which numbers.
2. If the data really is complete, the dataset is reclassified to `uniform` automatically. If it
   stays `uncertain`, the parameters are genuinely inconsistent and need a decision.
3. Fixing the classification matters more than it looks: uniform and NUS change the meaning of
   every processing parameter, which is why there is no "process anyway" flag.

## "Gaussian peak fitting is currently supported only for 2D spectra."

Gaussian localisation is a 2D model, so it is refused for 1D and 3D spectra rather than silently
falling back to parabolic refinement. Use `localization: parabolic`, or run the 2D plane.

If a Gaussian fit fails for a specific peak, that single peak falls back to parabolic and the
reason is recorded in `<peak table>.localization.json` and in the run parameters.

## The AppImage does not start

| Symptom | Fix |
| --- | --- |
| Double-clicking does nothing | Check the execute bit first: `ls -l NMRForge-*.AppImage` should show `-rwxr-xr-x`; if it does not, run `chmod +x NMRForge-*.AppImage`. In a file manager you can also right-click the file and use Properties -> "Allow executing file as program". Copying the file from a Windows shared folder or a USB stick often loses this bit |
| "AppRun: No such file or directory" after extracting | Use `./NMRForge-<version>-x86_64.AppImage --appimage-extract-and-run`, or set `APPIMAGE_EXTRACT_AND_RUN=1` |
| FUSE-related mount errors | Same as above; FUSE is not available on every system, and this is generic AppImage behaviour |
| "could not load the Qt platform plugin" | Make sure you are not overriding `QT_QPA_PLATFORM` in your shell; unset it for normal desktop use |
| No icon in the application menu | Launch it once so it installs its desktop entry, or remove and re-run; deleting the AppImage makes the entry hide itself |
| You want no desktop integration at all | `NMRFORGE_NO_DESKTOP=1 ./NMRForge-<version>-x86_64.AppImage` |

## Wrong experiment type

The classifier uses the pulse program, the nucleus combination per dimension, the dimension order
and acquisition parameters - not file names. Two things to check:

1. Is the pulse program the standard one for that experiment? A locally modified pulse program
   may not be recognised, in which case the classifier falls back to a broader family and reports
   lower confidence with the reason.
2. Correct the experiment template before processing. `presets/*.yaml` is the single source of
   experiment templates; a wrong template propagates into phase handling, sign conventions and
   window defaults, and no quality metric reliably detects that.

## The suite fails with `PermissionError ... pytest-of-<user>`

On Windows, the test suite writes its scratch directories under the system temp directory. If that
path is locked down (a restricted or read-only temporary directory, or a stale
`pytest-of-<user>` directory left by an earlier interrupted run), pytest cannot create its base
temp directory.

Workarounds, in order of preference:

```bash
python -m pytest --basetemp=/tmp/nf_pytest -q              # Linux
python -m pytest --basetemp=$env:TEMP\nf_pytest -q          # PowerShell
```

If a stale `pytest-of-<user>` directory is the cause, deleting it resolves it - it is disposable
by definition.

## GUI tests fail with a display error

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q      # Windows: set QT_QPA_PLATFORM=offscreen
```

## Where are the logs?

- GUI: the log panel is scoped to the current selection (single dataset, data group, experiment
  type, or global).
- Runs: each `WorkflowRun` carries its own message and warning list in the project record.
- Parameter sweeps: per-run directories under the study root hold the script, the candidate
  spectrum, the peak table and the run record; the study root holds `manifest.json` and
  `runs.json`.