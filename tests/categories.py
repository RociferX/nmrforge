"""Test classification (Phase 12): **Single source** for unit / integration / regression. Why mark
instead of moving directory: Audit (F.2) has confirmed that many tests use
``Path(__file__).parent / fixtures`` to position the fixture, and moving the file is true
reconstruction and has no release period benefits. Here, use pytest to mark the classification,
``tests/conftest.py`` according to this table to mark each test,
``tests/test_test_categories.py`` to ensure that: each test file is in the table, the category
is legal, and the three categories are not empty. Definition (must be added to this table when
adding a new test file): - ``unit``: pure logic, without touching the temporary directory
/Qt/project manager -- the fastest group; - ``integration``: can use ``tmp_path``/``bruker_dir``
fixtures, Qt control or ``ProjectManager``, that is, real I/O or multi-module collaboration; -
``regression``: for a fixed defect, Review correction items or releases/Guard tests for
architectural invariants. Usage:: pytest -m unit # fast pytest -m integration pytest -m
regression."""

from __future__ import annotations

__all__ = ["CATEGORIES", "CATEGORY_MARKERS", "category_of"]

# : file name -> category (single source).
CATEGORIES: dict[str, str] = {
    "test_1d_phase_td.py": "integration",
    "test_1d_processing.py": "unit",
    "test_2d_holdout_skip_log.py": "regression",
    "test_2d_nus_compat.py": "regression",
    "test_api_docstrings.py": "regression",
    "test_audit_rest_fixes.py": "regression",
    "test_axis_layout_audit.py": "integration",
    "test_baseline_optimize.py": "integration",
    "test_baseline_stripe.py": "integration",
    "test_batch.py": "integration",
    "test_bruker_data.py": "integration",
    "test_bruker_dtype.py": "integration",
    "test_bruker_parser.py": "integration",
    "test_bruker_workflow.py": "integration",
    "test_compat.py": "regression",
    "test_config_defaults.py": "integration",
    "test_data_group.py": "integration",
    "test_data_model.py": "unit",
    "test_data_understanding.py": "integration",
    "test_docs_examples.py": "integration",
    "test_direct_diagnostics.py": "integration",
    "test_effective_params.py": "integration",
    "test_final_ext_params.py": "regression",
    "test_full_paths.py": "integration",
    "test_gaussian_localize.py": "integration",
    "test_gaussian_localize_api.py": "integration",
    "test_gaussian_localize_trim.py": "integration",
    "test_gui_api_guard.py": "integration",
    "test_gui_batch.py": "integration",
    "test_gui_combo_width.py": "integration",
    "test_gui_context.py": "integration",
    "test_gui_data_group.py": "integration",
    "test_gui_dialogs.py": "integration",
    "test_gui_layout.py": "integration",
    "test_gui_localization.py": "regression",
    "test_gui_manual.py": "integration",
    "test_gui_notes.py": "integration",
    "test_gui_phase_c.py": "integration",
    "test_gui_phase_d.py": "integration",
    "test_gui_pipeline_detail.py": "integration",
    "test_gui_pipeline_state.py": "integration",
    "test_gui_poky.py": "integration",
    "test_gui_presets.py": "unit",
    "test_gui_processing.py": "integration",
    "test_gui_project.py": "integration",
    "test_gui_raw_quality.py": "integration",
    "test_gui_rerun.py": "integration",
    "test_gui_smile.py": "integration",
    "test_gui_smile_rank1.py": "integration",
    "test_gui_smile_row_labels.py": "integration",
    "test_gui_snapshot.py": "integration",
    "test_gui_step_row_layout.py": "integration",
    "test_gui_theme.py": "unit",
    "test_gui_tutorial.py": "integration",
    "test_ui_i18n.py": "regression",
    "test_import_workflow.py": "integration",
    "test_logging_setup.py": "regression",
    "test_manual.py": "integration",
    "test_manual_workflow.py": "integration",
    "test_memory_disk.py": "integration",
    "test_memory_guard.py": "unit",
    "test_memory_phase_search.py": "unit",
    "test_nmrforge_api.py": "integration",
    "test_nmrpipe_backend.py": "integration",
    "test_nmrpipe_finder.py": "integration",
    "test_nmrpipe_scripts.py": "integration",
    "test_ownership.py": "regression",
    "test_param_optimize.py": "unit",
    "test_peak_align.py": "integration",
    "test_peak_table.py": "integration",
    "test_phase_consensus.py": "unit",
    "test_phase_routes.py": "integration",
    "test_phase_search.py": "unit",
    "test_pick_peaks.py": "integration",
    "test_pipe_io.py": "unit",
    "test_planning.py": "integration",
    "test_progress.py": "integration",
    "test_project_manager.py": "integration",
    "test_qc.py": "unit",
    "test_qc_audit.py": "regression",
    "test_qc_enhance.py": "unit",
    "test_qc_metrics.py": "unit",
    "test_qt_independence.py": "regression",
    "test_qtcompat.py": "regression",
    "test_release_readiness.py": "regression",
    "test_review_fixes.py": "regression",
    "test_run_ownership.py": "regression",
    "test_run_refs_single_source.py": "regression",
    "test_runtime_terminate.py": "integration",
    "test_sampling_params.py": "integration",
    "test_script_check.py": "unit",
    "test_script_schema.py": "integration",
    "test_smile_base_params.py": "integration",
    "test_smile_grid_eta.py": "integration",
    "test_smile_optimize.py": "integration",
    "test_smile_scan_script_split.py": "unit",
    "test_smile_scan_workflow.py": "integration",
    "test_test_categories.py": "regression",
    "test_smoke.py": "unit",
    "test_stepwise.py": "integration",
    "test_targeted_localization.py": "integration",
    "test_third_party_licenses.py": "regression",
    "test_ucsf_export.py": "integration",
    "test_user_errors.py": "regression",
    "test_viewer.py": "integration",
    "test_viewer3d.py": "integration",
    "test_viewer_1d.py": "integration",
    "test_viewer_axis_labels.py": "integration",
    "test_vm_sample_make_nus.py": "integration",
    "test_window_optimize.py": "integration",
    "test_window_parity.py": "unit",
    "test_window_truth_benchmark.py": "integration",
    "test_workspace.py": "integration",
}

# : Category -> pytest marker name (consistent with pyproject’s markers registration).
CATEGORY_MARKERS: dict[str, str] = {
    "unit": "unit",
    "integration": "integration",
    "regression": "regression",
}


def category_of(filename: str) -> str:
    """Select the category according to the file name; if it is not registered, press
    ``integration`` to handle it conservatively (the guard test will require additional
    registration)."""
    return CATEGORIES.get(filename, "integration")
