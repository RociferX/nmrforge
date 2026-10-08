# 09 - Limits and extension paths (v1.1)

## 9.1 Support matrix

| Item | v1.1 | Notes |
| --- | --- | --- |
| uniform 1D/2D/3D data | yes, combinations run | goes through NMRPipe `process()`; studies are mostly 2D |
| NUS **2D** data | yes, combinations run | goes through `reconstruct_nus()` (SMILE); candidates are isolated; SMILE parameters can be swept |
| NUS 3D data | reference spectrum only | running combinations raises `SweepError` (see 9.2) |
| reference workflow (script + parabolic peak table) | yes | the reference is a baseline, not a claimed optimum |
| batch execution by `workflow_id` | yes | `W0001...`; each condition uses its own reference defaults and independently detects peaks |
| peak localisation | yes | three-point parabolic method only |
| multiple conditions (A/B) | yes | independent reference and peak table per condition; common parameters may be overridden per condition; no cross-condition peak matching |
| full sampling labelled as NUS | schedule-dependent | a valid full grid in standard order may use uniform processing; full coverage in a different order still requires schedule-based placement |
| peak overlap / deconvolution | no | localisation uses a detected extremum and three-point parabola only |
| Lorentzian / Voigt / multi-peak fitting | no | on the roadmap |
| parallel or cluster scheduling | no | serial with resume; shard along a parameter axis (see 8.4) |
| validation of parameter-axis keys | partial | locked keys raise, deterministic and unknown keys warn; key validity is mostly reported in `notes` |
| Statistical inference and scientific conclusions | no **(not this software)** | computed downstream from the unified peak table |

## 9.2 What NUS support covers

> Sampling is not inferred from a nominal percentage or data length alone. Only a standard
> `nuslist` or the file explicitly named by `acqus.NUSLIST` is used. A valid complete schedule in
> standard order may use uniform processing; a complete but reordered schedule still needs
> schedule-based placement. Explicit NUS without a recoverable schedule is rejected at import.
> Trailing zero padding is not treated as a missing sample.

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
| medium | extend peak fitting to Lorentzian/Voigt/multi-peak | current sub-grid localisation is a three-point parabola |
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
| Real-engine acceptance | Run a documented workflow on a system where NMRPipe and, for NUS, SMILE are installed | Record inputs, software revision, engine versions, parameters and resulting QC; this page makes no current benchmark claim |

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

### Historical external truth check (not a current-source validation)

This is a **2026-09-22 historical snapshot**; its recorded values are retained and have not been
recalculated against the current source revision. "Outside the scope of this software" means the
software **does not draw the conclusion for you**. The conventions and values from that dated
check on **public data** are in
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
- historical public-data evidence is linked from the documentation index; it is not a validation
  guarantee for the current source tree or other data.

## 9.7 Reading the boundary: common misreadings

- "It ran" is not "it is correct": the engineering regression in 9.5 only shows the pipeline is
  self-consistent and traceable. Scientific conclusions still come from downstream analysis
  against your own criteria.
- "CI is green" is not "the engine is green": mocked tests do not call NMRPipe. Engine-level
  conclusions require separate acceptance on a system with the required external tools.
- "The tool rewrote my data" is not "my dataset is gone": the source-level NUS cleanup writes
  into the project's own `raw/` copy, keeps `ser.bak` / `nuslist.bak`, and uses `os.replace`,
  which breaks the link to the original; the scope is stated in the README limitations section.
- "No coverage threshold" is not "no testing policy": the gates are the marker-classified
  suite, the structural guards (`test_qt_independence`, `test_ownership`, the behaviour
  fingerprints and the conformance golden vectors) and the release-readiness checks. A
  code-coverage percentage is deliberately not a gate, and `pytest -m unit` takes about 45 s
  (mostly collection) rather than the "seconds" a logic-only suite would suggest.
- "No number" is not "fast": the four skipped benchmark rows are deliberately not estimated.
- "The conversion record matched" is not "the input was not touched": above 8 MiB the recorded
  fingerprint degrades to `size + mtime_ns` (see `core/data/raw_fingerprint.py`), so it proves
  that the raw input is the one the converter saw - it is not content attestation.

## 9.9 FID reuse boundary

Reference mode imports sources when needed, converts each segment and merges them, then freezes the
FID, conversion evidence and sampling schedule for combination runs. Combination mode processes only
these existing reference artefacts and performs independent peak selection. It supports a single-file
FID, a 3D uniform slice directory and a merged multi-segment FID. It must not automatically reconvert
or re-merge, delete raw sources, modify the reference FID or rewrite the sampling schedule.

If the reference FID is missing or damaged, the source or conversion evidence differs from the
reference, or the requested parameters would require conversion or merging, combination mode raises
an error and asks the user to rebuild the reference with `force=True` (CLI: `reference --force`).
`force` rebuilds at the reference stage; it does not cause combination mode to convert on demand.
Conversion-time settings such as an explicit segment shift must be set when rebuilding the reference.
The GUI's default conversion behavior is unchanged by this API boundary.

Strict reuse for single-file FIDs, 3D uniform slices and merged segments passed Linux engineering
regression. Older references without a frozen-FID record require one explicit rebuild with
`force=True`. Fingerprints use content SHA-256 up to 8 MiB and `size + mtime_ns` for larger files;
they are not content-level authentication. This does not establish real NMRPipe/SMILE engine
validation; engineering regression is not a substitute for real-engine acceptance.
