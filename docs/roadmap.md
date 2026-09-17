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

## v0.10.0 - a bilingual (English / Chinese) application

Goal: the application itself becomes usable in English as well as Chinese, not just the
documentation. Today every user-visible string is a hard-coded Chinese literal and there is no
translation layer at all - no `QTranslator`, no gettext, no `.ts` catalogue.

Planned work:

1. **Message catalogue.** One table (`core/messages.py`) mapping a stable key to
   `{"zh-CN": ..., "en": ...}`, plus a `msg(key, **kwargs)` accessor, used by the GUI, the CLI,
   the Python API and everything written into run records. Machine-readable values are already
   language-neutral: warning codes such as `no_spectrum_change`, `roi_capped` and
   `processing_script_not_found` are ASCII identifiers, so the on-disk contract does not change -
   only the human-readable sentence next to them does.
2. **Language selection.** The default follows the system locale. It can be overridden in the GUI
   settings, through `NMRFORGE_LANG` for the CLI and API, and with a `--lang` option.
3. **Coverage.** Roughly 3,100 user-visible strings in the application layers (`gui/`, `viewer/`,
   `workflow/`, `backend/`, `nmrforge_api/`, `core/`), translated in stages - the GUI shell first,
   then the text that ends up in records and logs. Argument-parsing help and the long-tail scripts
   come last.
4. **Enforcement.** A guard test that rejects new bare Chinese literals in the UI layer, so the two
   languages cannot drift apart as features are added.

Two related but separate tracks:

- **Documentation** stays bilingual with English as the primary file and a Chinese sibling where it
  is worth maintaining;
- **Terminology** is fixed in a shared glossary, so that the GUI, the CLI and the documentation use
  the same English word for the same thing.

Scope and staging are estimates, not commitments; nothing here changes the v0.9.0 source release.
