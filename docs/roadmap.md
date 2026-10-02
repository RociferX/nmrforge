# Roadmap and support boundaries

This page describes the current public support boundary and possible extension areas. It is not a
release schedule or a promise that every listed idea will be implemented. For delivered changes,
consult the public [release notes](https://github.com/RociferX/nmrforge/releases) and the source/API
revision used for a study.

## Current support

| Area | Current boundary |
| --- | --- |
| Uniform sampling | Processing supports 1D, 2D, and 3D routes. |
| NUS | 2D NUS supports reference construction and parameter combinations through SMILE; 3D NUS currently supports reference construction only. |
| Batch processing | Batch entry points are 2D-only. |
| Peak localization | One supported method: three-point parabolic localization. |
| Peak overlap/deconvolution | Not provided; peaks are selected and localized individually. |
| Parameter execution | User-provided rows or grids are executed in order; the software does not invent a research design. |
| Statistical inference | Outside the processing software; perform it downstream with explicit assumptions and missing-data handling. |
| External engines | NMRPipe and, for NUS, SMILE must be installed separately. |

The detailed processing and API contracts are maintained in
[Processing model](processing-model.md), [Peak picking](peak-picking.md),
[External API](external-api/README.md), and [API contract](API_CONTRACT.md).

## Possible extension areas

The following are areas that may merit future work, subject to user need, scientific review, and
compatibility with the existing contracts. They are not currently supported features:

- 3D NUS parameter-combination execution;
- explicit selection of measurement planes for dimension-specific analyses;
- broader parameter-key validation with clearer errors for unsupported keys;
- additional peak-shape or overlap models, if supported by validation data and a suitable output
  contract;
- optional progress/status integration for external orchestration.

New processing algorithms should be evaluated against appropriate reference data and should record
their limitations. Do not infer scientific correctness from a successful software run or from
engineering regression alone.

## Validation and evidence

Engineering tests check implemented software paths and record consistency. NMRPipe/SMILE execution
must be validated separately on a system with those tools. Scientific conclusions require suitable
ground truth and analysis criteria chosen for the question being asked.

The [real-data comparison](evidence/real-data-comparison.md) is a historical snapshot dated
2026-09-22. Its recorded figures have not been recalculated against the current source revision and
must not be presented as current-release validation. See that page for the dataset, method, and
limitations.
