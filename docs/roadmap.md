# Capabilities and limitations

## Processing support

| Operation | Desktop workflow | Python/CLI API |
| --- | --- | --- |
| 2D uniform | Automatic and manual processing | Reference and parameter combinations |
| 2D NUS | Automatic/manual SMILE reconstruction; SMILE optimisation and rank rerun | Reference and parameter combinations |
| 3D uniform | Automatic and manual processing; complete spectrum or supported plane output | Reference and parameter combinations |
| 3D NUS | Automatic and manual SMILE reconstruction | Reference construction; combination execution is rejected |
| Batch operations | 2D data groups | Explicit workflow/condition combinations with resume |
| Peak localisation | Three-point parabolic | Three-point parabolic; optional target subset |
| Viewing | 1D spectra/FIDs, 2D spectra and 3D slices/projections | Spectrum/peak-table products without Qt |

NMRPipe performs conversion and conventional processing; SMILE supplies NUS reconstruction.
Both engines must be installed separately. The processing code uses the same backend for desktop
and API calls; the entry points have different parameter merging, output registration and resume logic.

## Data and parameter boundaries

- Bruker metadata supplies dimension identities, acquisition modes, calibration and sampling evidence.
  NUS uses a standard or explicitly named schedule; missing sample positions are not inferred from a percentage.
- Conversion settings determine the FID. API combinations reuse the frozen reference FID and cannot
  change conversion calibration or silently reconvert/merge sources.
- Manual scripts expose processing commands; runs preserve the executed text and outcome.
- API parameter rows/grids are explicit inputs. Runs execute serially with successful-work resume;
  callers may divide a grid across separate study roots.

## Peak tables and interpretation

Each API workflow/condition independently detects peaks and writes the 38-column table. Peak IDs
are local to one spectrum. The software does not perform automatic assignment, cross-spectrum
matching, overlap deconvolution, Lorentzian/Voigt/multi-peak fitting or statistical inference.
Use full logical-axis coordinates when nuclei repeat; H/N aliases alone may be ambiguous.
Detection thresholds, noise, phase and line shape affect candidate selection and localisation QC.

## Checks and evidence

Engineering tests exercise orchestration and records with mocked engines. Real-engine comparisons
record inputs, reference provenance, commands, parameters and resulting spectra separately.
[Four-route evidence](evidence/real-data-comparison.md) includes an author 2D uniform spectrum,
controlled 2D NUS at 68/90 retained increments, an author-script full 3D uniform reference and acquired
3D NUS at 25%. Its candidate coverage measures matched pairs/reference candidates; it does not count
assigned 3D peak identities. See the [API support details](external-api/09-limitations-and-roadmap.md),
[processing model](processing-model.md) and [peak picking](peak-picking.md).
