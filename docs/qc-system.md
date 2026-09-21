# QC system

Quality control in nmrForge has two jobs: measure the result, and justify what was done to the
data. It runs at three levels - FID, sampling, and spectrum - and it reports rather than
silently fixes.

## Level 1 - FID diagnostics

Run before processing, on the converted FID (`workflow/direct_diagnostics.py`). Detected and
reported:

| Finding | Detection rule |
| --- | --- |
| DC offset | Ratio of the time-domain DC component to the signal level, per dimension |
| Non-finite points | `NaN` or `Inf` in the FID |
| All-zero traces | A uniform-sampling trace whose samples are all zero ("blank row") |
| Abnormally high-energy traces | Trace energy above 100x the median energy of the non-zero traces |
| Corrupted sampling points | Points flagged by the bad-point rule, reported with their indices |

Two behaviours matter:

- **Report first, repair optionally.** Non-finite values, blank traces and anomalous traces are
  reported with their indices and metrics. The code does not silently delete them, because an
  "outlier" is sometimes the signal (a real peak with extreme intensity) and sometimes an
  artefact; the decision needs a human, and the log makes the decision auditable.
- **Grid consistency.** When bad points are removed at the source for NUS data, the sampling grid
  is recomputed from the cleaned sampling list rather than left at its declared size, so the FID
  grid and the cleaned data cannot disagree.

## Level 2 - sampling checks

Sampling classification (`core/experiment/sampling_detector.py`) is itself a QC gate:

- `nuslist` is checked for duplicate coordinates, out-of-range indices and coverage of the
  complete grid. A schedule that actually covers the full grid is reclassified to `uniform`,
  with the evidence recorded, even when the metadata claims NUS.
- A conflict that cannot be resolved becomes `uncertain`, and processing refuses to start. This is
  the documented behaviour for the real case that motivated it: metadata claiming NUS while the
  acquisition was complete.

## Level 3 - spectrum quality

Run on the processed spectrum (`core/qc/`).

| Metric | Module | What it looks at |
| --- | --- | --- |
| Noise level | `core/qc/noise.py` | Robust noise estimate from signal-free regions |
| Signal-to-noise | `core/qc/snr.py` | Peak height against the noise estimate |
| Phase quality | `core/qc/phase_quality.py` | Net absorption within peak windows, sign-mode aware |
| Baseline quality | `core/qc/baseline_quality.py` | Baseline flatness, per stored axis, worst axis reported |
| Artefacts | `core/qc/artifact_detection.py` | Stripe/ripple patterns and other structured artefacts |
| Peak detection | `core/qc/peak_detection.py` | Peak inventory used by the quality report |
| Combined judgement | `core/qc/spectrum_quality.py` | Aggregates the above into an overall verdict |

Two design decisions are worth knowing:

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

## Historic gap (for the record)

Automatic corrections are currently recorded as structured log lines and as resolved parameters
in the run record. There is not yet a single machine-readable quality-audit record per run with
the fields `issue_detected / location / detection_rule / action_taken / before_state /
after_state / timestamp / software_version`. The intended shape is:

```text
detect -> flag -> log -> optional correction
```

with no silent modification of raw or intermediate data. See
the maintainer's private release audit for the status of that improvement.