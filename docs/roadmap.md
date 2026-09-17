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

## v0.10.0 - an English edition

Goal: an English edition of this project, published alongside the Chinese original it is
translated from. The two trees are meant to stay structurally parallel - the same files, the same
structure, only the language of the prose differs. It is a translation task, not a runtime
localisation framework: none of the text is an identifier, so neither the code nor the on-disk
contract has to change.

Measured scope (2026-09-17), for the English side:

| Category | Chinese characters | Files |
| --- | --- | --- |
| Inline comments | 43,159 | 264 |
| Docstrings | 84,057 | 264 |
| User-visible strings | 28,033 | - |
| **Python total** | **155,249** | 264 |
| Documentation (`.md`, `.yaml`, ...) | 126,789 | 135 |
| **Total** | **282,038** | |

`CHANGELOG.md` alone accounts for 93,303 of the documentation characters, because it keeps the
project's history, and the test suite accounts for 38,503 of the Python characters.

How it will be done:

1. **Glossary first.** A single glossary fixes the English word for each NMR term (NMRPipe, SMILE,
   FID, NUS, zero filling, phase, baseline, TD/SI, ...), so the code, the CLI and the documentation
   agree.
2. **A translation memory.** Every translated unit - a paragraph, a heading, a docstring, a comment,
   a user-visible string - is recorded as a Chinese/English pair. That record, not the English
   files alone, is the deliverable: it is what would make a bilingual edition a generation step
   rather than a second translation pass.
3. **A parallel guard.** A test asserts that the two trees have the same file set, that every
   Chinese unit has an English counterpart, and that no Chinese is left on the English side.
4. **Staged batches**, each a self-contained commit: user-visible messages, documentation prose,
   docstrings in production code, inline comments, `tests/`, and finally the historical
   `CHANGELOG` entries. The Chinese tree is the development trunk; the English tree follows it.

Machine-readable values such as the warning codes `no_spectrum_change`, `roi_capped` and
`processing_script_not_found` are language-neutral identifiers and are not touched, so the on-disk
contract is unaffected.

A bilingual (side-by-side) edition is deliberately not part of v0.10.0. The point of keeping a
translation memory is to make it cheap to generate one afterwards, if there is demand for it.
