"""NMRForge parameter-combination API (v0.2, specification update of 2026-09-13).

What it does (Qt-free, scriptable, cluster friendly):

1. read the raw NMR data and a **user-defined table of parameter combinations**;
2. build a **reference workflow** by automatic optimisation: one reference script and two
      reference peak tables (parabolic / 2D Gaussian); a baseline, not a claimed optimum;
3. one parameter combination becomes one **workflow_id** (W0001...): each condition starts from
      its own reference parameters, applies batch and combination overrides, then processes;
      localisation is the chosen parabolic / gaussian / both, and only that table is written;
4. keep full provenance: ``parameters_requested`` / ``parameters_used`` and the **actual**
      results of automatic parameters (``actual_p0``/``actual_p1``, the SMILE nSigma used and
      the noise sigma), the full script and log, versions and status;
   (success / success_with_warning / failed);
5. two conditions (A/B) run in one workflow with one parameter set, sharing
      ``reference_peak_id`` while each writes its own peak table.

What it does **not** do (software boundary, 2026-09-13): CSP, robustness, statistical analysis,
significance testing and scientific conclusions - downstream analysis does those.

Minimal usage::

    from nmrforge_api import run_parameter_study

    result = run_parameter_study(
        "~/studies/hsqc_params",
        datasets={"A": "~/data/bmr12345/1", "B": "~/data/bmr12345/2"},
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
    )

Step-by-step usage::

    from nmrforge_api import (
        open_study, add_dataset, build_reference, ensure_reference_peaks,
        plan_sweep, run_sweep, write_records,
    )

Public documentation: ``docs/external-api/README.md``; compliance ledger:
``docs/reviews/2026-09-13-api-spec-compliance.md``; internal design notes:
``docs/proposals/external-api/001-parameter-sweep-api.md``.
"""

from __future__ import annotations

from nmrforge_api.compat import (
    AFFECTED_STEPS,
    COMPAT_LEVELS,
    COMPAT_SCHEMA,
    compat_manifest,
    compat_status,
    record_stamp,
    write_compat_manifest,
)
from nmrforge_api.conformance import (
    check_conformance,
    golden_hashes,
)
from nmrforge_api.direct_range import DirectRange, parse_direct_range
from nmrforge_api.errors import (
    DatasetError,
    MeasurementError,
    ReferenceError,
    SensitivityError,
    SweepError,
)
from nmrforge_api.localization_targets import (
    ConditionalTargets,
    LocalizationTargets,
    localization_targets_from_ids,
    read_localization_targets,
    resolve_conditional_targets,
    resolve_localization_targets,
)
from nmrforge_api.peak_tables import (
    GAUSSIAN_ONLY_COLUMNS,
    PEAK_TABLE_COLUMNS,
    REFERENCE_WORKFLOW_ID,
    gaussian_fallback_rows,
    mark_duplicate_localization,
    peak_table_digest,
    peak_table_row,
    peak_table_rows,
    read_peak_table,
    reference_peak_id,
    write_peak_table,
)
from nmrforge_api.peaks import (
    DEFAULT_DETECTION_SIGMA,
    PeakMeasurement,
    detect_and_localize,
    measure_peak_positions,
    pick_reference_peaks,
    read_reference_peaks,
    window_points_by_axis,
)
from nmrforge_api.records import (
    BOUNDARY_STATEMENT,
    WINDOW_POLICY,
    combined_peak_table,
    write_records,
    write_reference_records,
)
from nmrforge_api.reference import (
    ReferenceHandle,
    ReferenceSpectrum,
    build_reference,
    build_reference_peak_tables,
    ensure_reference_peaks,
    load_reference,
    load_references,
    parse_reference_spec,
    rebuild_reference_peak_tables,
    resolve_reference,
    sanitize_sweep_params,
    set_reference_peaks,
)
from nmrforge_api.session import (
    CONDITION_LETTERS,
    DatasetRef,
    StudySession,
    add_dataset,
    condition_token,
    dataset_info,
    open_study,
)
from nmrforge_api.study import (
    ReferenceResult,
    StudyResult,
    run_combination_study,
    run_parameter_study,
    run_reference_study,
)
from nmrforge_api.sweep import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_WARNING,
    SweepPlan,
    SweepRun,
    combos_from_rows,
    design_diagnostics,
    expand_grid,
    infer_axes,
    load_combo_table,
    load_plan,
    load_runs,
    load_workflows,
    merge_overrides,
    plan_sweep,
    run_sweep,
    workflow_id_for,
    workflow_summary,
    write_combo_table,
)

# --- verification/detection helpers (not part of the processing contract) -----------------
# sigma / range / delta_std serve tests, review and downstream reference implementations:
# the processing chain (study/sweep/records/cli) never calls them. Guarded by
# tests/test_nmrforge_api.py::test_statistics_helper_is_not_in_processing_contract
from nmrforge_api.uncertainty import (
    DEFAULT_CSP_N_WEIGHT,
    PeakUncertainty,
    position_uncertainty,
    uncertainty_summary,
)

API_VERSION = "0.2"

__all__ = [
    "AFFECTED_STEPS",
    "API_VERSION",
    "COMPAT_LEVELS",
    "COMPAT_SCHEMA",
    "DirectRange",
    "BOUNDARY_STATEMENT",
    "ReferenceHandle",
    "ReferenceResult",
    "CONDITION_LETTERS",
"DEFAULT_CSP_N_WEIGHT",
    "DEFAULT_DETECTION_SIGMA",
    "DatasetError",
    "ConditionalTargets",
    "DatasetRef",
    "LocalizationTargets",
    "GAUSSIAN_ONLY_COLUMNS",
    "MeasurementError",
    "PEAK_TABLE_COLUMNS",
    "PeakMeasurement",
"PeakUncertainty",
    "REFERENCE_WORKFLOW_ID",
    "ReferenceError",
    "ReferenceSpectrum",
    "STATUS_FAILED",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "SensitivityError",
    "StudyResult",
    "StudySession",
    "SweepError",
    "SweepPlan",
    "SweepRun",
    "WINDOW_POLICY",
    "add_dataset",
    "build_reference",
    "build_reference_peak_tables",
    "rebuild_reference_peak_tables",
    "check_conformance",
    "combined_peak_table",
    "compat_manifest",
    "compat_status",
    "combos_from_rows",
    "condition_token",
    "dataset_info",
    "design_diagnostics",
    "detect_and_localize",
    "ensure_reference_peaks",
    "expand_grid",
    "gaussian_fallback_rows",
    "golden_hashes",
    "infer_axes",
    "load_combo_table",
    "load_plan",
    "load_reference",
    "load_references",
    "load_runs",
    "load_workflows",
    "localization_targets_from_ids",
    "measure_peak_positions",
    "merge_overrides",
    "open_study",
    "parse_direct_range",
    "peak_table_digest",
    "peak_table_row",
    "peak_table_rows",
    "mark_duplicate_localization",
    "pick_reference_peaks",
    "plan_sweep",
"position_uncertainty",
    "read_localization_targets",
    "record_stamp",
    "read_peak_table",
    "read_reference_peaks",
    "parse_reference_spec",
    "reference_peak_id",
    "resolve_conditional_targets",
    "resolve_localization_targets",
    "resolve_reference",
    "run_combination_study",
    "run_parameter_study",
    "run_reference_study",
    "run_sweep",
    "sanitize_sweep_params",
    "set_reference_peaks",
"uncertainty_summary",
    "workflow_id_for",
    "workflow_summary",
    "window_points_by_axis",
    "write_combo_table",
    "write_compat_manifest",
    "write_peak_table",
    "write_records",
    "write_reference_records",
]
