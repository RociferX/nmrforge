"""测试分类(Phase 12):unit / integration / regression 的**单一来源**。

为什么是标记而不是挪目录:审计(F.2)已确认很多测试用 ``Path(__file__).parent / fixtures``
定位夹具,挪文件是真重构且没有发布期收益。这里用 pytest 标记分类,``tests/conftest.py`` 按本表
给每个测试打标,``tests/test_test_categories.py`` 保证:每个测试文件都在表里、类别合法、三类非空。

定义(新增测试文件时必须加进本表):

- ``unit``:纯逻辑,不碰临时目录/Qt/项目管理器——最快的一组;
- ``integration``:会用 ``tmp_path``/``bruker_dir`` 夹具、Qt 控件或 ``ProjectManager``,
  即真实 I/O 或多模块协同;
- ``regression``:为某个已修复的缺陷、审查整改项或发布/架构不变量而存在的守卫测试。

用法::

    pytest -m unit            # 快
    pytest -m integration
    pytest -m regression
"""

from __future__ import annotations

__all__ = ["CATEGORIES", "CATEGORY_MARKERS", "category_of"]

#: 文件名 → 类别(单一来源)
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
    "test_config_defaults.py": "integration",
    "test_data_group.py": "integration",
    "test_data_model.py": "unit",
    "test_data_understanding.py": "integration",
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
    "test_workspace.py": "integration",
}

#: 类别 → pytest 标记名(与 pyproject 的 markers 注册一致)
CATEGORY_MARKERS: dict[str, str] = {
    "unit": "unit",
    "integration": "integration",
    "regression": "regression",
}


def category_of(filename: str) -> str:
    """按文件名取类别;未登记的按 ``integration`` 保守处理(守卫测试会要求补登记)。"""
    return CATEGORIES.get(filename, "integration")
