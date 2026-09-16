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

## Known gap (public-release audit)

Automatic corrections are currently recorded as structured log lines and as resolved parameters
in the run record. There is not yet a single machine-readable quality-audit record per run with
the fields `issue_detected / location / detection_rule / action_taken / before_state /
after_state / timestamp / software_version`. The intended shape is:

```text
detect -> flag -> log -> optional correction
```

with no silent modification of raw or intermediate data. See
[../PUBLIC_RELEASE_AUDIT.md](../PUBLIC_RELEASE_AUDIT.md) for the status of that improvement.