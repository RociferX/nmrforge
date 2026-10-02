# QC system

Quality control in nmrForge has two jobs: measure the result and record what was done to the
data. It covers FID diagnostics, sampling checks and spectrum quality. Findings, recommendations
and applied corrections are distinct; the stand-alone menu check is read-only. QC scores do not
establish scientific correctness.

## Level 1 - FID diagnostics

Run at the **end of the Generate-FID step** (after the fid has been converted/merged and before the spectrum is built; the conclusion and what was done about it go into that step log and into `process/diagnostics.json`, and Generate Spectrum only reads it back). Detected and
reported:

| Finding | Detection rule |
| --- | --- |
| DC offset | Ratio of the time-domain DC component to the signal level, per dimension |
| Non-finite points | `NaN` or `Inf` in the FID |
| All-zero traces | A uniform-sampling trace whose samples are all zero ("blank row") |
| Abnormally high-energy traces | Trace energy above 100 times the median energy of non-zero traces; this alone does not confirm a bad point |
| Corrupted sampling points | Findings, applied removals or FID cleanup, and reasons for actions not taken |
| Field-drift screen | User-specified inter-segment shift applied during conversion; the first segment is 0 Hz. The software does not automatically estimate or recheck the shift. |

Two behaviours matter:

- Non-finite values, uniform blank traces and persistently high-energy traces are reported, not
  automatically removed. A repair follows a specific rule and is recorded.
- Before source-level NUS deletion, the `ser` and schedule layout are validated. When valid, backups
  are kept and the sampling grid is adjusted as needed. An inconsistent layout is not force-deleted.
  The first `.bak` is preserved rather than overwritten; use a working copy if you do not want the
  project copy changed.
- Earlier source deletions are described from audit and backup history in the Generate FID report;
  they are not counted as new findings in the current run.
- Inter-segment shift uses a user-specified `segment_shift_hz`; the first segment uses 0 Hz.
  A correction is reported as applied only when the requested value was applied. The software
  does not estimate the shift or guarantee an automatic residual check; inspect the resulting
  spectrum and decide whether the correction was appropriate.

### Stand-alone data-quality check

The Tools → Data Quality Check menu accepts one NMRPipe FID or a folder of sliced FIDs. For
folders it checks only direct child `.fid` files, sorts naturally, removes duplicates and checks
direct-dimension length; it does not recurse into other experiments. This check is read-only: it
shows findings and suggestions without replacing pipeline diagnostics, run records or audit
records, and it does not perform field-drift correction.

## Level 2 - sampling checks

Sampling classification (`core/experiment/sampling_detector.py`) is an import gate:

- Unsupported kinetic or pseudo-dimension layouts are filtered before ordinary 2D/3D routes.
- Only a standard `nuslist` or a file explicitly named by `acqus.NUSLIST` is used; the importer
  does not search for an arbitrary integer list.
- Classification uses acquisition parameters, the valid data grid, padding and schedule
  coordinates. `NusAMOUNT=100`, N/T in a conversion script or residual `Nus*` fields alone do not
  establish conventional full sampling.
- A valid schedule covering the standard grid in standard order may use the uniform route. Full
  coverage in another order still requires schedule-based placement. If data are explicitly NUS
  but the schedule is missing or positions cannot be recovered, import reports the missing
  schedule rather than inventing coordinates.
- Trailing zero padding is not an unsampled point. Conflicting or insufficient evidence is not
  resolved by guessing. For segmented data, displayed coverage counts unique coordinates across
  the merged schedule, not one segment's fraction.

## Level 3 - spectrum quality

Run on the processed spectrum (`core/qc/`).

| Metric | Module | What it looks at |
| --- | --- | --- |
| Noise level | `core/qc/noise.py` | Robust noise estimate and global median background |
| Signal-to-noise | `core/qc/snr.py` | Peak height relative to the same background and noise estimate |
| Phase quality | `core/qc/phase_quality.py` | Net absorption within peak windows, sign-mode aware |
| Baseline quality | `core/qc/baseline_quality.py` | Baseline flatness, per stored axis, worst axis reported |
| Artefacts | `core/qc/artifact_detection.py` | Stripe/ripple patterns and other structured artefacts |
| Peak detection | `core/qc/peak_detection.py` | Peak inventory used by the quality report |
| Combined judgement | `core/qc/spectrum_quality.py` | Aggregates the above into an overall verdict |

These metrics depend on the software version, window and sign convention and are not universally
comparable percentage grades.

- Noise, peak height and S/N use the global median background. Empty, non-finite, constant or
  zero-noise inputs return `decision=rollback`, `overall=0` and a reason; they do not receive a
  passing result.
- Phase quality uses net absorption and the experiment's expected sign. Real negative peaks in
  mixed-sign experiments are not phase errors. Here `uniform` means same-sign peaks and has no
  relation to uniform sampling.
- Processing paths that know the experiment declare the sign mode. A stand-alone quality check
  with `auto` infers positive-only, negative-only or mixed signs from strong signals and reports
  the convention. Scoring polarity does not change the source spectrum.
- Baseline quality is assessed per stored axis and reports the worst axis. A candidate correction
  is compared with the original and must not introduce worse stripes simply to improve a score.
  Interpret results separately when dimensions, crop windows or processing versions differ.

Implementation details:

- **Phase scoring is sign-mode aware.** For experiments where real negative peaks exist (for
  example certain HNN-type experiments), a negative peak is not a phase error. The experiment
  template declares the expected sign behaviour, and magnitude experiments skip phase search
  entirely.
- **Baseline evaluation is relative, not absolute.** A candidate baseline correction that makes
  the stripes worse than the original is rejected; a correction that visibly improves an already
  stripy baseline is allowed. When the stripes are a data/acquisition artefact rather than a
  baseline offset, the report says so instead of prescribing a correction that cannot work.
- **Signal-to-noise follows the same sign convention as phase (fixed 2026-09-20).** The combined
  verdict derived S/N from a peak search that only looked for positive peaks while the phase
  component was already sign-mode aware, so a mixed experiment lost its negative peaks a second
  time. `evaluate(sign_mode=...)` now maps `mixed` to `both` - the same choice the peak-picking
  step makes for mixed templates - and hands that peak list to `snr.compute`. Property tests
  (`tests/test_qc_metrics.py`) pin the S/N sub-score to that peak list and show the positive-only
  one understates it by a factor of ~1.6 on a negative-dominant mixed fixture.
- **Single-sign experiments are positive by construction (same fix).** `uniform` maps to
  `positive`, because the processing chain resolves the +-180 ambiguity of single-sign data
  towards positive absorption (`core/optimization/phase_consensus` only skips that step for
  `mixed`). A uniform spectrum whose peaks are negative dominant is therefore an anomaly - flipped
  data or a phase that was never disambiguated - and the verdict says so in words
  ("negative signal dominates") instead of adapting to the negated peaks and reporting a valid
  spectrum. The score also drops, because the S/N term no longer finds the signal; the phase term
  flags it as well.
- **The standalone tool judges the convention; the pipeline states it (2026-09-20).** The two
  entry points differ on purpose. Anything that knows the experiment type - the processing
  report, the optimisation paths, `optimize_post_parameters(..., sign_mode=...)`, the
  parameter-optimisation CLI - passes `uniform` or `mixed` and gets the strict reading above.
  Anything that does not - the menu entry "spectrum quality", the low-level API on a spectrum
  the user processed themselves - passes `auto`: the verdict decides for itself whether the
  spectrum is single-sign positive, single-sign negative or a two-sign mixture, using the
  intensity mass above 6 sigma (noise cannot reach it) and the same 0.15 minority-share rule the
  peak-picking step uses for its mixed-sign fallback. The decision is written into the reasons
  ("single-sign positive/negative", "both signs present"), and a negative single-sign spectrum is
  graded after being flipped, so a user's all-negative single-sign spectrum is reported as that -
  a valid spectrum in a different polarity - rather than as flipped data, and it scores the same
  as its mirror image.
- **Unusable input is reported, not raised (2026-09-20).** An empty array, a spectrum containing
  `NaN`/`Inf`, a constant (all-zero) spectrum or a zero noise estimate returns
  `decision=rollback`, `overall=0` and a specific reason instead of raising a numpy reduction
  error or handing a zero-information spectrum a "warning" score.

## The run report

Each run ends with a three-part report in the log:

```text
◆ Final spectrum quality   overall verdict, sub-scores for S/N / phase / baseline / artefacts,
                           baseline metrics, and the checks that were applied
◆ Data quality diagnostics findings count, how many were handled automatically, and the details
◆ Processing parameters    the resolved parameter set, including what the optimiser chose
```

The report is written for the person deciding whether to trust the spectrum: verdicts in words
with advice, not a wall of numbers.

## Structured audit record (implemented)

Automatic corrections are recorded as machine-readable records, not only as log lines.
`core/audit/qc_audit.py` writes one **append-only** `qc_audit.jsonl` per processing work
directory (flushed per record, so a killed run still accounts for what it changed), with the
fields the public-release task requires:

```text
issue_detected / location / detection_rule / action_taken / before_state / after_state /
timestamp / software_version    (+ git_commit / git_commit_dirty when discoverable)
```

Rules this enforces:

- **no change, no record** - the absence of a record is evidence that nothing was modified;
- the timestamp and version are stamped by the log, so a call site cannot forget provenance;
- `before_state`/`after_state` carry the concrete values (for example the real/imaginary parts
  of the repaired samples, or the old and new `NusTD`), and oversized detail lists are capped
  and flagged `truncated` rather than silently trimmed;
- a source deletion that could not be performed is recorded as `reported_only`, never as a
  completed removal.

Where records are written today: bad-point replacement (`workflow/direct_diagnostics.py`),
sampling-grid shrinkage after cleaning, and source-level bad-point deletion
(`backend/nmrpipe_backend.py`). Read them back with `read_audit(work_dir)`, and see
`tests/test_qc_audit.py` for the exact coverage.
