# 09 - Limits and extension paths (v1.0)

## 9.1 Support matrix

| Item | v1.0 | Notes |
| --- | --- | --- |
| uniform 1D/2D/3D data | yes, combinations run | goes through NMRPipe `process()`; studies are mostly 2D |
| NUS **2D** data | yes, combinations run | goes through `reconstruct_nus()` (SMILE); candidates are isolated; SMILE parameters can be swept |
| NUS 3D data | reference spectrum only | running combinations raises `SweepError` (see 9.2) |
| reference workflow (1 script + 2 peak tables) | yes | the reference is a baseline, not a claimed optimum |
| batch execution by `workflow_id` | yes | `W0001...`; two conditions A/B share parameters and peak identity |
| localisation: parabolic / 2D gaussian | yes | both methods run on the same candidate, producing two isomorphic tables |
| multiple conditions (A/B) | yes | one reference per condition; peak identity and user parameters shared |
| **full sampling labelled as NUS** | yes, treated as uniform | a `nuslist` covering the whole grid, or a 2D `ser` that is full-grid-with-zero-fill and has no zero rows, is treated as full sampling and goes through the ordinary FT (no SMILE); the evidence is written to `sampling_evidence` |
| peak overlap / deconvolution | no | the window extremum plus parabola or single-peak Gaussian only |
| Lorentzian / Voigt / multi-peak fitting | no | on the roadmap |
| parallel or cluster scheduling | no | serial with resume; shard along a parameter axis (see 8.4) |
| validation of parameter-axis keys | partial | locked keys raise, deterministic and unknown keys warn; key validity is mostly reported in `notes` |
| Statistical inference and scientific conclusions | no **(not this software)** | computed downstream from the unified peak table |

## 9.2 What NUS support covers

> **Full sampling wins.** When a dataset is labelled NUS but is actually fully sampled
> (the `nuslist` covers the whole grid, or a 2D `ser` is full-grid with no zero rows), it is
> processed as **uniform** and the evidence is recorded (`sampling="uniform"`,
> `sampling_schedule="full_sampling"`, details in `sampling_evidence`). Only genuine NUS
> (a sampling subset, or a sparse file) takes the SMILE path below.

**Supported: 2D NUS.** Both the reference and the workflows call `reconstruct_nus()`; the only
difference is that batch execution isolates the candidate output per combination:

- phase locking: `phases` (indirect dimensions) plus a flat `direct_phase` (direct dimension);
- candidate output at `study/workflows/<id>/<condition>/spectrum.ft2` (the backend
  `out_file`/`script_name` semantics); the final spectrum in the working directory is never
  overwritten;
- sweepable parameters: `nsigma` (alias `nSigma`), `thresh`, `nthread`, `smile_scaling` and
  others; when the value is chosen automatically the actual one is written to
  `parameters_resolved.smile`.

**Not yet supported: 3D NUS** in combination mode (the slice stream buckets by plane directory
and finalises with independent names).

## 9.3 Splitting long batches

- the number of combinations defaults to a maximum of 256 (`max_runs`); beyond that, split into
  several study roots or run in batches;
- a study root can be re-run; workflow x condition pairs that already succeeded are skipped
  (resume);
- for several machines, shard along a **parameter axis** (each machine takes a sub-grid and its
  own study root) and merge the long table afterwards;
- record `grid_sha256` (present in the plan and the manifest) before changing a parameter table,
  so you can check that two runs used the same design.

## 9.4 Roadmap

| Priority | Item | Deliverable shape |
| --- | --- | --- |
| high | 3D NUS in combination mode | slice stream keyed by `workflow_id` plus isolated finalise output |
| medium | extend peak fitting to Lorentzian/Voigt/multi-peak | today only a 2D single-peak Gaussian |
| medium | schema validation of parameter keys | raise instead of only warning about unknown keys |
| medium | progress file | update `records/progress.json` per combination for external monitoring |
| medium | explicit 3D plane selection | measure on a named plane when the peak table fixes the dimension values |

When filing a request, attach `records/manifest.json` and the `status` output so it can be
reproduced.

## 9.5 Validation boundary: engineering regression vs scientific validation

The question a user asks most often is "what was this batch of numbers validated against?".
This software keeps the two things apart:

### Engineering regression (shows the pipeline and the records are self-consistent and
reproducible)

| Evidence | How to run it | Product / log |
| --- | --- | --- |
| Full test suite | `python -m pytest` (no NMRPipe needed: the engine boundary is stubbed) | terminal output, nothing written to the repository |
| Full suite on a machine that has NMRPipe | `bash scripts/vm_test.sh` (the same suite as above: the engine boundary stays stubbed) | a test log; bytecode and ruff caches are redirected to a temporary directory so the working copy stays clean |
| CI | `.github/workflows/ci.yml` jobs `static` (ruff) / `tests` (3.12, 3.13) / `release-readiness` | GitHub Actions logs |
| CI job on a machine that has NMRPipe | `external-engine`: re-runs the same stubbed suite on a self-hosted runner that has NMRPipe and uploads the log. It does **not** invoke the engine | **skipped unless** a self-hosted runner is registered and the repository variable `NMRFORGE_SELF_HOSTED_CI` is `true`; a licensed dependency must not become a PR gate, and a GitHub-hosted runner cannot install NMRPipe |
| Real-engine API smoke | `python scripts/vm_api_smoke.py --data <Bruker dir> [--data b ...] [--fresh]` on a machine that has NMRPipe | the study root is given by `--study` (default `~/studies/nmrforge_api_smoke`, overridable with the environment variable `NMRFORGE_API_STUDY`); it is an ordinary study root: `records/reference.json`, `records/manifest.json`, `workflows/W0001/<condition>/peak_table_{parabolic,gaussian}.csv`, `run.json`; the last stdout line is `RESULT_JSON {...}` (per-run `peak_tables`/`peak_localization`/`window`/`detection` plus `summary` and `records`) |
| Targeted real-engine validators | `scripts/vm_validate_zero_fill.py`, `vm_validate_nus_indirect_equiv.py`, `vm_validate_phase_score.py`, `vm_sample_regression.py` (with `vm_sample_compare.py`) | prints the per-item metrics directly (zero-fill SI, memory/backend equivalence relative error, phase-score margin, main-peak and water direction); the conclusions are written back to ../API_CONTRACT.md |

Run real-engine acceptances **serially**: concurrent runs compete for CPU and overwrite each
other's logs, which produces failures unrelated to the code.

### Scientific validation (outside the scope of this software)

- engineering regression and real-engine smoke runs only show that the pipeline runs, that the
  products are self-consistent and that the same input gives the same result; they **cannot**
  show that the processing result is scientifically correct on a real system;
- answering the latter needs a ground-truth benchmark (a synthetic benchmark or a system with a
  known answer) plus criteria of your own, and it has to accept the conclusion "on which systems
  the algorithm is biased" - that is done by **downstream analysis** (this software makes no
  statistical judgement and draws no scientific conclusion; see the software boundary in
  01-overview);
- so when you cite a product, write three things separately: (1) which engine behaviour was used
  (`compat` / `behavior_digest`), (2) whether the processing flow is covered by the engineering
  regression (the table above), (3) whether the scientific conclusion holds (downstream analysis).

### The external truth check the maintainer ran (evidence, not a guarantee)

"Outside the scope of this software" means the software **does not draw the conclusion for you**;
it does not mean the check was never made. The maintainer ran one layer of external truth checking on
**public data**; the conventions and the numbers are in
[Real-data evidence](../evidence/real-data-comparison.md) section 2:

| Element | How it is done |
| --- | --- |
| Data | the original Bruker data of one real 2D 15N-1H HSQC (**public entry 53374, downloadable**) |
| Expected positions | the **published deposited chemical shifts** of the same sample and condition (never given to peak picking; used only as an external criterion) |
| Matching | one-to-one greedy nearest first over a tolerance ladder; the global reference shift is calibrated **once** and then frozen |
| Control | the expected table shifted per peak independently under the same tolerance (fixed seed, 200 draws) gives the chance background |

Result: at a tight tolerance (1H 0.01 / 15N 0.05 ppm) **84.1% (90 of 107) of the expected peaks are
matched one-to-one** (93.5% at 0.02 / 0.10 ppm) with median position residuals of 0.0012 / 0.0164 ppm,
against a 2.0% chance background. On the 15N axis that tolerance is smaller than one data point
(0.055 ppm per point), so most of the peaks that fail are stopped by the threshold rather than missing -
the per-peak distances are in the match CSV. That layer answers "can the automatic processing reproduce
external truth"; it does
**not** cover your sample, your parameter choices or your scientific conclusion - so still write the
three things above separately when you cite a product.

## 9.6 Relationship to the main program: the API has no algorithms of its own

- the API does not run a second implementation: the reference spectrum, the combined
  localisation and the parameter sweep call the same code as the main program (the desktop
  application and the command line), and the products come from it. Whatever optimisation the
  main program performs therefore applies to the API as well and **needs no separate proof**;
- the API layer has no special algorithmic design: it only makes calling the main program more
  flexible (batches, combinations, resume, records and machine-readable products). Thresholds,
  defaults, optimisation and QC follow the main program and are versioned in `compat`;
- so this interface document only states what goes in, what comes out and how errors are
  reported; the quality of the processing, and the evidence that it works, belong to the main
  program (documentation entry point in README) and are not duplicated here;
- the evidence for "how well the processing program works" lives with the main program: the external
  truth check against the **published deposited chemical shifts** (public data, entry 53374) is in
  `docs/evidence/real-data-comparison.md`, and the maintainer has additionally validated the
  program against **more than a dozen data sets that cannot be published yet**, all of them
  reaching an optimisation quality comparable to manual processing - that last point is a
  **maintainer statement** and cannot be recomputed from this snapshot.

## 9.7 Reading the boundary: common misreadings

- "It ran" is not "it is correct": the engineering regression in 9.5 only shows the pipeline is
  self-consistent and traceable. Scientific conclusions still come from downstream analysis
  against your own criteria.
- "CI is green" is not "the engine is green": no job in this repository calls NMRPipe - the
  engine boundary is stubbed everywhere, including in the `external-engine` job, which merely
  re-runs the same suite on a machine that has the engine. Engine-level conclusions come from
  your own acceptance run on a machine that has NMRPipe (`scripts/vm_test.sh`,
  `scripts/vm_api_smoke.py`, `scripts/vm_realdata_report.py`).
- "The tool rewrote my data" is not "my dataset is gone": the source-level NUS cleanup writes
  into the project's own `raw/` copy, keeps `ser.bak` / `nuslist.bak`, and uses `os.replace`,
  which breaks the link to the original; the scope is stated in the README limitations section.
- "No coverage threshold" is not "no testing policy": the gates are the marker-classified
  suite, the structural guards (`test_qt_independence`, `test_ownership`, the behaviour
  fingerprints and the conformance golden vectors) and the release-readiness checks. A
  code-coverage percentage is deliberately not a gate, and `pytest -m unit` takes about 45 s
  (mostly collection) rather than the "seconds" a logic-only suite would suggest.
- "One snapshot commit" is not "no history": the public history was restarted to keep internal
  material and real sample names out; the version history is in the release notes.
- "No number" is not "fast": the four skipped benchmark rows are deliberately not estimated.
- "The conversion record matched" is not "the input was not touched": above 8 MiB the recorded
  fingerprint degrades to `size + mtime_ns` (see `core/data/raw_fingerprint.py`), so it proves
  that the raw input is the one the converter saw - it is not content attestation.
