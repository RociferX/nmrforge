# Roadmap

## Phase 1: data understanding and basic processing

```text
Bruker parser -> 2D/3D detection -> uniform/NUS detection -> axis mapping -> basic processing
-> automatic phase correction -> baseline QC -> noise estimation -> quality score
```

Delivered items:

- core/experiment: parse_dataset_params / sampling_detector / dimension_mapper / acquisition_mode_detector
- core/processing primitives: apodization / zero_fill / ft / phase / baseline / calibration
- core/planning: ProcessingDag.execution_order (topological sort) + PipelineRunner cache
- core/qc: noise / snr / phase_quality / baseline_quality / spectrum_quality.evaluate
- backend: NMRPipeBackend.health_check / process (deterministic .com generation)
- AutoProcessor.run - the minimal closed loop (uniform 2D)

## Phase 2: experiment classification

- multi-evidence classifier (pulse program -> nucleus combination -> dimension order -> FnMODE -> parameters -> naming)
- ExperimentTemplate.from_yaml + loading presets/*.yaml
- HSQC / HNCA / HNCO / HNCACB / CBCANH template matching with a confidence gate

## Phase 3: the NUS pipeline

```text
direct optimisation (representative subset) -> reconstruction candidates (fast preview + a few
candidates + local search + early stopping) -> best reconstruction -> indirect optimisation -> DAG cache
```

Delivered items: NusReconstructionParams wired to the backend (SMILE), CandidateGenerator /
GridSearch / LocalSearch, OptimizationBudget execution, NUS quality score (data consistency +
SNR + peak quality + stability - artifact).

## Phase 4: deeper optimisation and reporting

- spectrum centre and referencing offset adjustment (the analysis part): shift the spectrum axis by
  editing the CAR/ORIG headers of ft3/ft2, leaving the data untouched; this aligns the laboratory
  display convention with manual correction (mechanism verified 2026-08-18)
- peak stability analysis
- BayesianOptimizer (expensive tasks)
- ProcessingReport: report.json / report.html / processing_recipe.json
- learning the parameters a user finally accepts (Parameter Predictor is a long-term idea; no ML
  in the first version)

## The optimisers most worth building first

```text
AutoProcessor
├── AxisOptimizer / ApodizationOptimizer / ZFOptimizer
├── PhaseOptimizer / BaselineOptimizer / CalibrationChecker
└── SpectrumQC

NUSOptimizer
├── DirectOptimizer / ReconstructionOptimizer / IndirectOptimizer
├── CandidateManager / CacheManager / StabilityAnalyzer
```

## Explicitly out of scope (first version)

- Full structure determination, automatic assignment, AI structure prediction, NOE.
- Using an LLM to guess phase or NUS parameters from an image; an LLM is for explanation and
  reports only.

## v1.0.1 - the patch release (done 2026-09-23)

A patch on top of 1.0.0, **with no new features**: it fixes the few places that could make a
result silently wrong - the conversion parameter (`-xN` follows the physical `ser` row instead of
overriding it with the `acqus` TD), the FnMODE to bruk2pipe conversion keyword (no longer
rewriting a `fid.com` that `bruker -AUTO` had written correctly) and the inter-part field-drift
alignment before a multi-part merge. Documentation and metadata follow along (affiliation, DOI
citation, execute-bit troubleshooting, the `-xN` note). The per-item list is in the changelog,
which the maintainer keeps in the private repository.

## v1.0.0 - the production release (done 2026-09-23)

The distribution moved from 0.11.0 to **1.0.0**: both bars for 1.0 are met -

- **end-to-end scientific validation**: the evidence page
  [`evidence/real-data-comparison.md`](evidence/real-data-comparison.md) gives tolerance-tiered
  recovery numbers and per-peak attribution on public data (BMRB timedomain **53374**), together
  with the reproduction scripts that ship with the repository (`scripts/vm_truth_benchmark.py`);
- **compatibility promise**: behaviour is managed through the compat process (behaviour digests, the
  `same/additive/behavior_changed/contract_changed` levels and a golden vector), and the
  `nmrforge_api` contract has been at version 1.0 since 2026-09-22;
- released as source plus a Linux AppImage (one artefact; the interface language is switched at run
  time). PyPI is still not published.

## v0.11.0 - the English edition and the first AppImage release (done 2026-09-20)

Goal: an English edition of this project and a first Linux AppImage. The first pass kept the
Chinese original as the development trunk and translated it into a structurally parallel English
tree, with a translation memory and a parity guard to keep the two in step.

## Runtime localisation (done 2026-09-21)

Keeping two parallel trees doubled every edit, so the interface text moved into a run-time layer
instead:

- the code writes the **English original** and wraps it in `tr("...")`: one code base for every
  edition, and no Chinese in the public tree;
- Chinese lives in [`ui_support/locales/zh.json`](../ui_support/locales/zh.json), keyed by the
  English original. A key without a translation falls back to the English text, so nothing goes
  blank and nothing raises;
- the interface language follows the system locale and can be pinned with `NMRFORGE_LANG=zh`
  (see [development](development.md) and the [GUI guide](gui.md));
- `python scripts/i18n_extract_ui.py --check` is the guard: new strings have to be registered,
  files that have been converted may not contain Chinese literals outside `tr()`, and the Chinese
  coverage may not shrink (this is UI-string coverage, not code coverage: the project keeps no
  code-coverage threshold).

Machine-readable values such as the warning codes `no_spectrum_change`, `roi_capped` and
`processing_script_not_found` are language-neutral identifiers and are not touched, so the on-disk
contract is unaffected.

The Chinese edition of the **documentation** lives in
[`Chinese_version/`](../Chinese_version/README.md) and the root README points at it; a bilingual
(side-by-side) documentation edition is not part of v0.11.0.
