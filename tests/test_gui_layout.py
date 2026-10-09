"""Three-column GUI tests: project tree / five-step Pipeline / spectrum panel (offscreen)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtcompat.QtWidgets import QApplication, QDialog, QLabel, QMenu

from core.project import JsonRecentProjectsStore, ProjectManager
from gui.log_panel import LogPanel
from gui.main_window import MainWindow
from gui.pipeline_panel import (
    PIPELINE_STEPS,
    STEP_LOG_SEPARATOR,
    PipelinePanel,
    PipelineStepRow,
    compute_step_statuses,
)
from gui.project_tree import ProjectTreePanel
from gui.spectrum_panel import SpectrumPanel


class SyncThread:
    """Turn background threads into synchronous execution so tests don't depend on timing."""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class DeferredThread:
    "Regression coverage: DeferredThread."

    pending: list = []

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        type(self).pending.append(self._target)


def _manager_with_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> ProjectManager:
    """Create a project in a temp workspace and point the tree/window at it (test isolation)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = ProjectManager.create_project(workspace / "proj", "demo")
    for source, title in (("/data/1", "HSQC"), ("/data/2", "HNCACB")):
        entry = manager.add_experiment(source, title=title)
        entry.status = "registered"
    manager.save()
    if monkeypatch is not None:
        monkeypatch.setattr(
            "gui.main_window.WorkspaceManager",
            lambda: _TempWorkspace(workspace),
        )
        monkeypatch.setattr(
            "core.workspace.WorkspaceManager",
            lambda *a, **k: _TempWorkspace(workspace),
        )
    return manager


class _TempWorkspace:
    """Workspace stub pointing at a temporary directory."""

    def __init__(self, root) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def delete_project(self, name: str, trash: bool = True) -> None:
        target = self.root / name
        if target.exists():
            import shutil

            shutil.rmtree(target)

    def rename_project(self, old_name: str, new_name: str) -> Path:
        target = self.root / old_name
        new_target = self.root / new_name
        if target.exists():
            target.rename(new_target)
        return new_target

    def list_projects(self) -> list[Path]:
        return sorted(
            p for p in self.root.iterdir() if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs):
        from core.project import ProjectManager

        return ProjectManager.create_project(self.root / name, name, **kwargs)


def _write_ft2(path: Path) -> None:
    from nmrglue.fileio import pipe

    shape = (64, 128)
    data = np.zeros(shape, dtype=np.float32)
    data[32, 64] = 100.0
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = shape[0]
    dic["FDF1SW"] = 6000.0
    dic["FDF1OBS"] = 600.0
    dic["FDF1CAR"] = 118.0
    dic["FDF1ORIG"] = 118.0 * 600.0
    dic["FDF2T"] = shape[1]
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


class FakeProcessingController:
    """Fake five-step controller: records generate_fid/generate_spectrum calls, runs in sync."""

    def set_manager(self, manager) -> None:
        self.manager = manager

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_entry = None

    def import_data(self, entry, source) -> dict:
        self.calls.append("import_data")
        self.last_entry = entry
        return {"experiment_id": entry.id, "status": "imported"}

    def generate_fid(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_fid")
        self.last_entry = data
        return "/tmp/x.fid"

    def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_spectrum")
        self.last_entry = data
        return "/tmp/x.ft2"


# ----------------------------------------------------------------------
# Project tree (Project → Experiment → Data)
# ----------------------------------------------------------------------
def test_project_tree_structure(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    assert panel.tree.topLevelItemCount() == 1
    workspace_item = panel.tree.topLevelItem(0)
    assert workspace_item.text(0) == "NMRForgeWorkspace"  # Workspace root node
    project_item = workspace_item.child(0)
    assert project_item.text(0) == "demo"  # Current project shows project.name
    assert project_item.text(1) == "当前"  # Current-project marker
    assert project_item.childCount() == 2
    exp_item = project_item.child(0)
    assert exp_item.text(0) == "HSQC"
    assert exp_item.childCount() == 1
    data_item = exp_item.child(0)
    assert data_item.text(0) == "Data d_001"
    assert data_item.text(1) == "已导入"
    assert data_item.childCount() == 6  # raw/process/spectra/peaks/figures/report
    panel.close()


def test_project_tree_data_status_shows_running(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch5: a running dataset shows "Running", then reverts to the inferred status."""
    from gui.project_tree import ProjectTreePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)

    def _data_item():
        return panel.tree.topLevelItem(0).child(0).child(0).child(0)

    assert _data_item().text(1) == "已导入"
    panel.mark_running("exp_001", "d_001")
    assert _data_item().text(1) == "运行中"
    panel.clear_running("exp_001", "d_001")
    assert _data_item().text(1) == "已导入"
    panel.close()


def test_project_tree_current_experiment_from_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    panel.select_experiment("exp_002")
    assert panel.current_experiment_id() == "exp_002"
    # Selecting a sample data node still normalises to its experiment type
    exp_item = panel.tree.topLevelItem(0).child(0).child(1)
    panel.tree.setCurrentItem(exp_item.child(0))
    assert panel.current_experiment_id() == "exp_002"
    assert panel._data_id_of(panel.tree.currentItem()) == "d_001"
    panel.close()


def test_project_tree_column_widths_readable(qapp: QApplication) -> None:
    panel = ProjectTreePanel()
    assert panel.tree.columnWidth(0) >= 180  # minimum readable width of the object column
    assert panel.tree.columnWidth(1) >= 70  # status column
    panel.close()


# ----------------------------------------------------------------------
# Pipeline five-step statuses
# ----------------------------------------------------------------------
def test_pipeline_spectrum_row_ext_range_button_before_run(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch15: the spectrum row shows a direct-dimension range button before
    run (other steps hide it)."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    spectrum_row = panel._rows["spectrum"]
    assert not spectrum_row.ext_range_button.isHidden()
    assert panel._rows["fid"].ext_range_button.isHidden()
    assert panel._rows["peaks"].ext_range_button.isHidden()
    # 0.2.163-patch5: the button moved to its own row below the title (ext_range before run)
    button_row = spectrum_row.layout().itemAt(1)
    assert button_row is not None and hasattr(button_row, "count")
    widgets = [button_row.itemAt(i).widget() for i in range(button_row.count())]
    assert widgets.index(spectrum_row.ext_range_button) < widgets.index(spectrum_row.run_button)
    panel.close()


def test_pipeline_run_guard_blocks_repeat(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "0.2.199-patch12: a second click while running is rejected; no duplicate start."
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    messages: list[str] = []
    panel.log_message.connect(messages.append)
    panel._running_targets[("exp_001", "d_001", "fid")] = 2
    panel._on_run_requested("fid")
    assert any("该数据已有任务正在运行" in m for m in messages)
    panel.close()


def test_pipeline_step_row_stylesheet_has_no_parse_warning(qapp: QApplication) -> None:
    "Regression coverage: test pipeline step row stylesheet has no parse warning."
    from qtcompat.QtCore import qInstallMessageHandler

    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _kind, _context, message: messages.append(message))
    try:
        row = PipelineStepRow("fid", "FID", "description")
        qapp.processEvents()
        row.close()
    finally:
        qInstallMessageHandler(previous)
    assert not any("Could not parse stylesheet" in message for message in messages)


def test_spectrum_report_cache_by_fingerprint(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch12: the spectrum param report is cached by spectrum-file fingerprint."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    missing = str(tmp_path / "no.ft3")
    text1 = panel._cached_spectrum_report({"diagnostics": {"reports": []}}, missing)
    text2 = panel._cached_spectrum_report({"diagnostics": {"reports": []}}, missing)
    assert text1 == text2
    # 0.2.199-patch29e: no record prompts a rerun instead of generating on the spot,
    # so nothing is written to the cache
    assert "无报告记录" in text1
    assert not panel._spectrum_report_cache
    panel.close()


def test_pipeline_button_row_wraps_when_narrow(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch4: the step row button area is a flow layout; buttons wrap when narrow."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    row = panel._rows["spectrum"]
    buttons = [
        row.ext_range_button,
        row.run_button,
        row.rerun_final_button,
        row.show_spectrum_button,
        row.manual_button,
    ]
    for button in buttons:
        button.setVisible(True)  # simulate the 5 visible buttons after the spectrum step completes
    # 2026-09-25: "rerun final script" and the flip control share one box (the box counts as
    # a single flow item), so the box must be visible -- otherwise its buttons are hidden
    # by the parent widget and the simulation does not hold
    row.rerun_group.setVisible(True)
    row.show()
    qapp.processEvents()
    button_row = row.layout().itemAt(1)
    visible_items = [
        button_row.itemAt(i).widget()
        for i in range(button_row.count())
        if button_row.itemAt(i).widget() is not None
        and not button_row.itemAt(i).widget().isHidden()
    ]
    assert len(visible_items) == 5  # ext range / re-optimise / flip+rerun / show spectrum / manual

    def _row_ys() -> set[int]:
        return {
            button_row.itemAt(i).widget().y()
            for i in range(button_row.count())
            if button_row.itemAt(i).widget() is not None
        }

    # at narrow width the 5 buttons must wrap (at least two distinct y coordinates)
    row.setFixedWidth(180)
    qapp.processEvents()
    ys = _row_ys()
    assert len(ys) >= 2, f"窄宽度下按钮未折行: y={sorted(ys)}"
    panel.close()


def test_pipeline_final_ext_override_params(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch15: per-data final direct-dim range override -> generate_spectrum params."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert panel._spectrum_ext_params("d_001") is None
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "5.5", True)
    assert panel._spectrum_ext_params("d_001") == {
        "apply_ext_to_opt": "1",
        "final_ext_lo": "11.0",
        "final_ext_hi": "5.5",
    }
    # only one end set: the other is not injected; "apply this range to optimisation" off
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "", False)
    assert panel._spectrum_ext_params("d_001") == {
        "apply_ext_to_opt": "0",
        "final_ext_lo": "11.0",
    }
    panel.close()


def test_pipeline_ext_button_text_reflects_override(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch15: the button label refreshes with the current data's final-run range."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert panel._rows["spectrum"].ext_range_button.text() == "直接维范围"
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "5.5", True)
    panel.refresh()
    assert panel._rows["spectrum"].ext_range_button.text() == "直接维范围 11.0/5.5 · 含优化"
    panel.close()
    assert (
        panel._rows["spectrum"].ext_range_button.toolTip()
        == "直接维窗口: 11.0-5.5 ppm(EXT -x1/-xn,含优化)\n"
        "首遍重构/相位搜索与优化评估同窗口;窗口外峰不进入终谱,\n"
        "p1 按窗口宽度自动重归一化;切换数据后显示各自设置"
    )
    # switch data: with no window set for that data, the tooltip falls back to the default text
    panel.set_selection("data", "exp_001", "d_002")
    assert "未设置时用默认" in panel._rows["spectrum"].ext_range_button.toolTip()
    panel.close()


def test_pipeline_steps_include_optional_smile() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    assert ids == ["fid", "spectrum", "smile", "peaks"]
    deps = {step[0]: step[3] for step in PIPELINE_STEPS}
    # SMILE optimisation is optional: peak picking does not depend on it
    assert "smile" not in deps["peaks"]
    assert deps["smile"] == ("spectrum",)
    for _, _, _, step_deps in PIPELINE_STEPS:
        for dep in step_deps:
            assert dep in ids


def test_pipeline_status_registered(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "READY"
    for step_id in ("spectrum", "peaks"):
        assert statuses[step_id] == "LOCKED"


def test_pipeline_status_after_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    # both fid and spectrum need real artefacts: fid lives in process/, registered via fid_path
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "d_001.ft2")
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "READY"


def test_pipeline_fid_unlocks_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    """After generating FID, the spectrum step unlocks to READY (regression: fid used to be
    judged by the spectrum)."""
    manager = _manager_with_experiment(tmp_path)
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "READY"  # key: the spectrum step unlocks
    assert statuses["peaks"] == "LOCKED"


def test_pipeline_panel_refresh_shows_next_step(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert "下一步" in panel.next_label.text()
    assert "生成 FID" in panel.next_label.text()
    assert not panel._rows["fid"].run_button.isHidden()
    assert panel._rows["spectrum"].run_button.isHidden()
    # 0.2.199-patch29dm: the manual generate-FID button appears only after an
    # automatic run (SUCCESS)
    assert panel._rows["fid"].manual_button.isHidden()
    panel.close()


def test_pipeline_panel_run_generate_fid(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path)
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.log_scoped.connect(log.append)  # 0.2.199-patch29d: run log by scope
    panel.set_selection("data", "exp_001", "d_001")
    # 0.2.199-patch29d: logs land in the data scope; the panel shows them once switched to it
    log.set_scope("data", "exp_001", "d_001")
    panel._on_run_requested("fid")
    assert controller.calls == ["generate_fid"]
    assert "✓ 已完成：生成 FID" in log.text.toPlainText()

    assert panel._rows["fid"].status_label.text().startswith("▶")
    panel.close()
    log.close()


def test_pipeline_running_state_and_worker_stay_with_original_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test pipeline running state and worker stay with original data."
    DeferredThread.pending.clear()
    monkeypatch.setattr("threading.Thread", DeferredThread)
    manager = _manager_with_experiment(tmp_path)
    second = manager.import_data("exp_001", "/data/second")
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", "exp_001", "d_001")

    panel._on_run_requested("fid")
    assert "运行中" in panel._rows["fid"].status_label.text()
    assert len(DeferredThread.pending) == 1

    panel.set_selection("data", "exp_001", second.id)
    assert "运行中" not in panel._rows["fid"].status_label.text()
    panel.set_selection("data", "exp_001", "d_001")
    assert "运行中" in panel._rows["fid"].status_label.text()

    panel.set_selection("data", "exp_001", second.id)
    DeferredThread.pending.pop()()
    assert controller.last_entry.id == "d_001"
    assert not panel._running_targets
    panel.close()


def test_pipeline_allows_different_2d_data_to_run_in_parallel(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test pipeline allows different 2d data to run in parallel."
    DeferredThread.pending.clear()
    monkeypatch.setattr("threading.Thread", DeferredThread)
    manager = _manager_with_experiment(tmp_path)
    second = manager.import_data("exp_001", "/data/second")
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    monkeypatch.setattr(panel, "_data_ndim", lambda _exp_id, _data_id: 2)

    panel.set_selection("data", "exp_001", "d_001")
    panel._on_run_requested("fid")
    panel.set_selection("data", "exp_001", second.id)
    panel._on_run_requested("fid")

    assert len(DeferredThread.pending) == 2
    assert len(panel._running_targets) == 2
    DeferredThread.pending.pop(0)()
    assert len(panel._running_targets) == 1
    assert ("exp_001", second.id, "fid") in panel._running_targets
    DeferredThread.pending.pop(0)()
    assert not panel._running_targets
    panel.close()


def test_pipeline_keeps_3d_processing_exclusive(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test pipeline keeps 3d processing exclusive."
    DeferredThread.pending.clear()
    monkeypatch.setattr("threading.Thread", DeferredThread)
    manager = _manager_with_experiment(tmp_path)
    second = manager.import_data("exp_001", "/data/second")
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    messages: list[str] = []
    panel.log_message.connect(messages.append)
    monkeypatch.setattr(
        panel,
        "_data_ndim",
        lambda _exp_id, data_id: 3 if data_id == second.id else 2,
    )

    panel.set_selection("data", "exp_001", "d_001")
    panel._on_run_requested("fid")
    panel.set_selection("data", "exp_001", second.id)
    panel._on_run_requested("fid")

    assert len(DeferredThread.pending) == 1
    assert any("只有不同的 2D 数据可以并行处理" in message for message in messages)
    DeferredThread.pending.pop(0)()
    panel.close()


def test_pipeline_finish_clears_only_its_data_from_project_tree(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test pipeline finish clears only its data from project tree."
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    second = manager.import_data("exp_001", "/data/second")
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    window.project_tree.mark_running("exp_001", "d_001")
    window.project_tree.mark_running("exp_001", second.id)

    window._on_pipeline_run_finished("exp_001", "d_001")

    assert ("exp_001", "d_001") not in window.project_tree._running
    assert ("exp_001", second.id) in window.project_tree._running
    window.close()


def test_pipeline_step_log_emits_separator_before_start(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test pipeline step log emits separator before start."
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path)
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    emitted: list[tuple[str, object]] = []
    panel.log_scoped.connect(lambda msg, scope: emitted.append((msg, scope)))
    panel.set_selection("data", "exp_001", "d_001")
    panel._on_run_requested("fid")
    messages = [msg for msg, _ in emitted]
    assert STEP_LOG_SEPARATOR in messages, messages
    sep_index = messages.index(STEP_LOG_SEPARATOR)

    start_prefix = "▶"
    start_index = next(i for i, msg in enumerate(messages) if msg.startswith(start_prefix))
    assert sep_index < start_index, messages

    scopes = {scope for msg, scope in emitted if msg == STEP_LOG_SEPARATOR}
    assert scopes == {emitted[start_index][1]}
    panel.close()


# ----------------------------------------------------------------------
# Spectrum panel
# ----------------------------------------------------------------------
def test_spectrum_panel_lists_and_loads_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    spectrum = spectra / "hsqc_2d.ft2"  # backend names it after dataset_id
    _write_ft2(spectrum)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001")  # experiment level: all data spectra of the experiment
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "hsqc_2d.ft2"
    assert panel.open_spectrum(spectrum) is True
    assert panel.viewer.layer_list.count() == 1
    panel.close()


def test_spectrum_panel_open_corrupt_returns_false(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = SpectrumPanel(manager)
    bad = tmp_path / "bad.ft2"
    bad.write_bytes(b"not a pipe file")
    assert panel.open_spectrum(bad) is False
    panel.close()


# ----------------------------------------------------------------------
# Main window three columns
# ----------------------------------------------------------------------
def test_main_window_three_column_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert window.main_splitter.count() == 4
    assert window.project_tree is not None
    assert window.pipeline is not None
    assert window.spectrum_panel is not None
    # 0.2.141: the log column sits between the pipeline and the spectrum viewer
    assert (
        window.main_splitter.indexOf(window.center_panel)
        < window.main_splitter.indexOf(window.log_panel)
        < window.main_splitter.indexOf(window.spectrum_panel)
    )
    # 0.2.143: column widths have no hard cap and drag freely (lower bound is the natural size)
    assert window.log_panel.minimumWidth() <= 400
    assert window.log_panel.maximumWidth() >= 10000
    assert window.project_tree.minimumWidth() <= 1  # 330 no longer forced
    # default initial widths are [420,600,300,600] (1920 total); on small screens the
    # splitter narrows them
    from qtcompat.QtGui import QGuiApplication

    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        cols = window.main_splitter.sizes()
        assert sum(cols) <= avail.width()
        assert min(cols) > 0
        assert window.geometry().top() == avail.top()
        assert window.height() <= avail.height()
    # first experiment type focused by default -> middle shows the experiment page (import form)
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page._exp_id == "exp_001"
    assert "demo" in window.windowTitle()
    # 0.2.199-patch29hr: the flat compatibility table is gone --
    # check the real experiment count instead
    assert len([e for e in window.manager.project.experiments if not e.trashed]) == 2
    window.close()


def test_main_window_spectrum_expand_toggle(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29bp: spectrum expand button -- collapses the three left parts,
    click again to restore."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    btn = window.spectrum_panel.expand_button
    assert btn.text() == "放大"
    assert not window.project_tree.isHidden()
    assert not window.center_panel.isHidden()
    assert not window.log_panel.isHidden()
    btn.setChecked(True)
    assert btn.text() == "收起"
    assert window.project_tree.isHidden()
    assert window.center_panel.isHidden()
    assert window.log_panel.isHidden()
    assert not window.spectrum_panel.isHidden()
    # 0.2.199-patch29bq: only the plot area expands; the right control column
    # stays, the small plot hides
    panel = window.spectrum_panel
    assert panel._expanded
    assert panel._expand_splitter is not None
    assert panel.viewer.plot_area.parent() is panel._expand_splitter
    assert panel._expand_splitter.indexOf(panel.viewer.plot_area) == 0
    assert panel._expand_controls is not None
    assert panel.viewer.controls_layout.parentWidget().parent() is panel._expand_controls
    assert panel.lists_row_widget.parent() is panel._expand_controls
    assert panel.peak_toolbar_widget.parent() is panel._expand_controls
    assert panel.peak_table.parent() is panel._expand_controls
    assert panel.viewer.isHidden()
    # 0.2.199-patch29hz-fix26: after expanding, the top title row must not stretch
    # into a blank block
    window.resize(1200, 800)
    window.show()
    QApplication.processEvents()
    btn.setChecked(False)
    btn.setChecked(True)
    QApplication.processEvents()
    lay = panel.layout()
    header_item = lay.itemAt(0)
    title = next(lb for lb in panel.findChildren(QLabel) if lb.text() == "谱图")
    assert header_item.geometry().height() <= 40  # was 311px (blank block)
    assert title.height() <= 40
    assert panel._expand_splitter is not None
    assert panel._expand_splitter.height() >= panel.height() - 80
    btn.setChecked(False)
    assert btn.text() == "放大"
    assert not window.project_tree.isHidden()
    assert not window.center_panel.isHidden()
    assert not window.log_panel.isHidden()
    assert not panel._expanded
    assert panel._expand_splitter is None
    assert panel.viewer.plot_area.parent() is panel.viewer.view_splitter
    assert not panel.viewer.isHidden()
    assert panel.lists_row_widget.parent() is panel._panel_splitter
    window.close()


def test_spectrum_panel_file_help_menus(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29br: the file/help menus sit right of the expand button; the View
    menu has no spectrum-viewer entry."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    panel = window.spectrum_panel
    assert panel.file_button.menu() is panel.file_menu
    assert panel.help_button.menu() is panel.help_menu
    row = panel.lists_row
    assert row.indexOf(panel.expand_button) < row.indexOf(panel.compare_button)
    assert row.indexOf(panel.compare_button) < row.indexOf(panel.file_button)
    assert row.indexOf(panel.file_button) < row.indexOf(panel.help_button)
    file_texts = [a.text() for a in panel.file_menu.actions()]
    assert "打开当前数据谱图" in file_texts and "清空谱图" in file_texts
    assert "打开任意谱图..." in file_texts
    help_texts = [a.text() for a in panel.help_menu.actions()]
    assert "操作说明" in help_texts
    panel._on_menu_clear_spectrum()  # safe when empty
    view_menu = None
    for action in window.menuBar().actions():
        if action.text() == "查看(&V)":
            view_menu = action.menu()
    assert view_menu is not None
    texts = [a.text() for a in view_menu.actions()]
    assert not any("谱图查看器" in t for t in texts)
    window.close()


def test_expanded_spectrum_can_compare_two_results_with_independent_controls(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    first = manager.data_dir("exp_001", "d_001", "spectra") / "first.ft2"
    second = manager.data_dir("exp_002", "d_001", "spectra") / "second.ft2"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    _write_ft2(first)
    _write_ft2(second)

    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.load_current_spectrum()
    panel.expand_button.setChecked(True)
    assert not panel.compare_button.isHidden()

    panel._refresh_compare_menu()
    action = next(
        action
        for action in panel.compare_menu.actions()
        if action.data() and action.data() != "__close__"
    )
    panel._on_compare_action(action)

    assert panel._comparison_active
    assert panel._expand_splitter is not None
    assert panel._expand_splitter.count() == 3
    assert panel._compare_viewer is not None
    assert panel._expand_splitter.widget(0) is panel._compare_column
    assert panel._expand_splitter.widget(1) is panel._current_column
    assert panel.viewer.controls_layout.parentWidget().parent() is panel.viewer.view_splitter
    assert (
        panel._compare_viewer.controls_layout.parentWidget().parent()
        is panel._compare_viewer.view_splitter
    )
    assert panel.viewer.level_slider is not panel._compare_viewer.level_slider
    main_level = panel.viewer.level_slider.value()
    panel._compare_viewer.level_slider.setValue(
        min(panel._compare_viewer.level_slider.maximum(), main_level + 3)
    )
    assert panel.viewer.level_slider.value() == main_level

    panel._deactivate_comparison()
    assert not panel._comparison_active
    assert panel._expand_splitter.count() == 2
    panel.expand_button.setChecked(False)
    assert panel.compare_button.isHidden()
    assert panel.viewer.plot_area.parent() is panel.viewer.view_splitter
    panel.close()


@pytest.mark.parametrize("current_3d", [False, True])
@pytest.mark.parametrize("compare_3d", [False, True])
def test_comparison_plot_heights_match_after_open_resize_and_drag(
    tmp_path,
    qapp,
    monkeypatch,
    current_3d,
    compare_3d,
) -> None:
    from qtcompat.QtTest import QTest

    from tests.test_viewer3d import _synthetic3d, _write_ft3

    manager = _manager_with_experiment(tmp_path)
    first = manager.data_dir("exp_001", "d_001", "spectra") / (
        "first.ft3" if current_3d else "first.ft2"
    )
    second = manager.data_dir("exp_002", "d_001", "spectra") / (
        "second.ft3" if compare_3d else "second.ft2"
    )
    for path, is_3d in ((first, current_3d), (second, compare_3d)):
        path.parent.mkdir(parents=True, exist_ok=True)
        if is_3d:
            _write_ft3(path, _synthetic3d())
        else:
            _write_ft2(path)
    panel = SpectrumPanel(manager)
    monkeypatch.setattr(panel, "_ensure_plane_stream", lambda: None)
    panel.resize(1800, 950)
    panel.show()
    panel.set_context("exp_001", "d_001")
    QTest.qWait(20)

    panel.viewer.view_splitter.setSizes([800, 110])
    panel.expand_button.setChecked(True)
    assert panel._load_compare_spectrum(second)
    panel._activate_comparison(second)
    QTest.qWait(20)
    left, middle = panel._compare_viewer, panel.viewer

    def assert_equal_height():
        assert abs(left.plot_area.height() - middle.plot_area.height()) <= 1
        assert abs(left.plot.height() - middle.plot.height()) <= 1
        assert left.plot_area.height() >= 300
        for viewer in (left, middle):
            controls = viewer.controls_layout.parentWidget()
            assert controls.height() >= controls.minimumSizeHint().height()

    assert_equal_height()
    panel.resize(1800, 740)
    QTest.qWait(20)
    assert_equal_height()
    for viewer in (left, middle):
        viewer.view_splitter.moveSplitter(viewer.plot_area.height() - 25, 1)
        QTest.qWait(20)
        assert_equal_height()
    main_level = middle.level_label.value()
    left.level_label.setValue(main_level + 1)
    assert middle.level_label.value() == main_level
    panel._deactivate_comparison()
    panel.expand_button.setChecked(False)
    assert middle.plot_area.parent() is middle.view_splitter

    assert not panel._comparison_layout_timer.isActive()
    panel.close()


def test_main_window_has_app_icon(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29eq: the main window sets the app icon (gui/assets/nmrforge.png)."""
    from ui_support.theme import app_icon

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert app_icon() is not None
    assert not window.windowIcon().isNull()
    window.close()


def test_tools_menu_standalone_quality_entries(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29em: the Tools menu holds standalone data-quality check /
    spectrum-quality assessment entries, between View and Settings."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    menus = [a.text() for a in window.menuBar().actions()]
    assert "工具(&T)" in menus
    idx = menus.index("工具(&T)")
    assert menus[idx - 1] == "查看(&V)"
    assert menus[idx + 1] == "设置(&S)"
    tools_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "工具(&T)")
    labels = [a.text() for a in tools_menu.actions()]
    assert "数据质量检测..." in labels
    assert "谱图质量评估..." in labels
    window.close()


def test_tools_run_jumps_to_workspace_log(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29em: running a tool jumps to the top level (workspace root) and
    switches the log to the global (NMRForgeWorkspace) scope."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    top = tree.topLevelItem(0)
    assert top is not None
    child = top.child(0)
    if child is not None:
        tree.setCurrentItem(child)
    monkeypatch.setattr(
        "qtcompat.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(tmp_path / "nope.fid"), ""),
    )
    window._run_standalone_fid_diagnostics()
    assert tree.currentItem() is top
    assert window.log_panel.current_scope() == "global"
    window.close()


def test_menu_mnemonics_unique_and_activate(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29en: top-level menu mnemonics (&X) are unique and Alt+letter pops
    the menu (Tools/Settings once both took T, so Alt+T broke the Settings mnemonic)."""
    from qtcompat.QtCore import Qt
    from qtcompat.QtTest import QTest

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.show()
    qapp.processEvents()
    bar = window.menuBar()
    letters: list[str] = []
    by_letter: dict[str, object] = {}
    for action in bar.actions():
        text = action.text()
        assert "&" in text, f"菜单缺少助记键: {text}"
        letter = text.split("&", 1)[1][0]
        letters.append(letter)
        by_letter[letter] = action.menu()
    assert len(set(letters)) == len(letters), f"助记键重复: {letters}"
    # Alt+T -> Tools; Alt+S -> Settings
    for key, title in ((Qt.Key.Key_T, "工具(&T)"), (Qt.Key.Key_S, "设置(&S)")):
        target = next(a.menu() for a in bar.actions() if a.text() == title)
        bar.setFocus()
        QTest.keyClick(bar, key, Qt.KeyboardModifier.AltModifier)
        qapp.processEvents()
        popup = QApplication.activePopupWidget()
        assert popup is target, f"Alt+{key} 未弹出 {title}"
        popup.close()
        qapp.processEvents()
    window.close()


def test_other_menu_routes_to_standalone_check(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29ej: standalone entries route to a check by kind."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    calls: list[tuple[str, str]] = []
    window._run_standalone_check = (  # type: ignore[method-assign]
        lambda paths, kind: calls.append((str(paths[0]), kind))
    )
    monkeypatch.setattr(
        "qtcompat.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (r"C:\x\d_001.fid", ""),
    )
    window._run_standalone_fid_diagnostics()
    assert calls == [(r"C:\x\d_001.fid", "fid")]
    monkeypatch.setattr(
        "qtcompat.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (r"C:\x\d_001.ft3", ""),
    )
    window._run_standalone_spectrum_quality()
    assert calls[-1] == (r"C:\x\d_001.ft3", "spectrum")
    window.close()


def test_main_window_context_updates_on_tree_selection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_002")
    assert window.center_panel.stack.currentIndex() == 2  # experiment page
    assert window.center_panel.experiment_page._exp_id == "exp_002"
    assert window.spectrum_panel._current_exp_id == "exp_002"
    window.close()


def test_pipeline_no_import_step(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-patch12: import moved into the dropdown; the pipeline has no import step."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    assert "import" not in panel._rows
    panel.set_selection("data", "exp_001", "d_001")
    assert not panel._rows["fid"].isHidden()
    panel.close()


def test_main_window_log_panel_expands_on_message(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    # 0.2.143: the log is always visible
    assert not window.log_panel.isHidden()
    window._update_context("data", "exp_001", "d_001")
    window.pipeline._on_run_requested("fid")
    assert not window.log_panel.isHidden()
    assert "生成 FID" in window.log_panel.text.toPlainText()
    window.close()


@pytest.mark.parametrize("view", ["target", "group", "other_data"])
def test_member_run_does_not_replace_selected_log(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch, view: str
) -> None:
    "Regression coverage: test member run does not replace selected log."
    DeferredThread.pending.clear()
    monkeypatch.setattr("threading.Thread", DeferredThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    second = manager.import_data("exp_001", "/data/second")
    group = manager.create_data_group("exp_001", data_ids=["d_001", second.id])
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    monkeypatch.setattr(window.pipeline, "_data_ndim", lambda *_: 2)
    group_scope = LogPanel.scope_key("group", "exp_001", "", group.id)
    data_scope = LogPanel.scope_key("data", "exp_001", "d_001")
    window._append_log("正在依次运行组内数据", group_scope)
    window._update_context("data", "exp_001", "d_001")
    window.pipeline._on_run_requested("fid")
    if view == "group":
        window._update_context("group", "exp_001", "", group.id)
    elif view == "other_data":
        window._update_context("data", "exp_001", second.id)
    selected_scope = window.log_panel.current_scope()
    DeferredThread.pending.pop(0)()
    window._append_log("组任务仍在继续", group_scope)
    assert window.log_panel.current_scope() == selected_scope
    visible = window.log_panel.text.toPlainText()
    assert ("组任务仍在继续" in visible) == (view == "group")
    assert ("生成 FID" in visible) == (view == "target")
    assert any("生成 FID" in line for line in window.log_panel._buffers[data_scope])
    assert not any("生成 FID" in line for line in window.log_panel._buffers[group_scope])
    window.close()


def test_group_summary_stays_in_group_log_after_member_selected(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test group summary stays in group log after member selected."
    DeferredThread.pending.clear()
    monkeypatch.setattr("threading.Thread", DeferredThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    group = manager.create_data_group("exp_001", data_ids=["d_001"])
    controller = FakeProcessingController()
    controller.run_group_batch = lambda *_a, **_k: {
        "summary": {"success": 1, "total": 1},
        "results": {"d_001": {"status": "success"}},
    }
    window = MainWindow(manager=manager, controller=controller)
    window._update_context("group", "exp_001", "", group.id)
    window._run_group_batch("exp_001", group.id, ["spectrum"])
    window._update_context("data", "exp_001", "d_001")
    DeferredThread.pending.pop(0)()
    assert "batch finished" not in window.log_panel.text.toPlainText()
    assert "批量处理完成" not in window.log_panel.text.toPlainText()
    group_scope = LogPanel.scope_key("group", "exp_001", "", group.id)
    group_text = "\n".join(window.log_panel._buffers[group_scope])
    assert "d_001" in group_text
    assert "Done" in group_text or "完成" in group_text
    window.close()


def test_rank1_logs_keep_target_after_selection_changes(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    DeferredThread.pending.clear()
    monkeypatch.setattr("threading.Thread", DeferredThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    group = manager.create_data_group("exp_001", data_ids=["d_001"])
    controller = FakeProcessingController()

    def rerun(*_args, progress):
        progress("Rank1 目标数据的脚本输出")
        return "result.ft2"

    controller.rerun_smile_rank1 = rerun
    window = MainWindow(manager=manager, controller=controller)
    window._update_context("data", "exp_001", "d_001")
    window._on_rank1_rerun()
    window._update_context("group", "exp_001", "", group.id)
    DeferredThread.pending.pop(0)()
    assert "Rank1" not in window.log_panel.text.toPlainText()
    window._update_context("data", "exp_001", "d_001")
    assert "Rank1 目标数据的脚本输出" in window.log_panel.text.toPlainText()
    window.close()


def test_log_panel_scopes_isolate_data_and_group(
    qapp: QApplication,
) -> None:
    """Per-data logs are independent; a data group shares one log; selection switches the view."""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    # data A and data B are independent
    panel.set_scope("data", "exp_001", "d_001")
    panel.append("A 的日志")
    panel.set_scope("data", "exp_001", "d_002")
    panel.append("B 的日志")
    panel.set_scope("data", "exp_001", "d_001")
    assert "A 的日志" in panel.text.toPlainText()
    assert "B 的日志" not in panel.text.toPlainText()
    # the data group shares one log
    panel.set_scope("group", "exp_001", "", "g_1")
    panel.append("组的日志")
    panel.set_scope("group", "exp_001", "", "g_1")
    assert "组的日志" in panel.text.toPlainText()
    # experiment type and global scopes stay clean
    panel.set_scope("experiment", "exp_001")
    assert "A 的日志" not in panel.text.toPlainText()
    panel.set_scope("", "", "")
    assert panel.text.toPlainText() == ""
    panel.close()


def test_log_panel_explicit_scope_routes_group_batch(
    qapp: QApplication,
) -> None:
    """Group batch logs land explicitly in the group scope, regardless of the selected data."""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    panel.set_scope("data", "exp_001", "d_001")
    panel.append("单数据日志")
    group_scope = panel.scope_key("group", "exp_001", "", "g_1")
    panel.append("批量进度 1/3", scope=group_scope)
    # the data log is still shown; the group log stays in the group scope
    assert "批量进度 1/3" not in panel.text.toPlainText()
    panel.set_scope("group", "exp_001", "", "g_1")
    assert "批量进度 1/3" in panel.text.toPlainText()
    assert "单数据日志" not in panel.text.toPlainText()
    panel.close()


def test_main_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace root
    assert window.manager.project is None
    assert "欢迎" in window.windowTitle()
    assert window.pipeline.current_experiment_id() == ""
    window.close()


def test_tree_data_node_context_menu_actions(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data node context menu: delete/open directory + add to batch group (no generate steps)."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    actions: list[tuple[str, str]] = []
    panel.data_action_requested.connect(lambda action, data_id: actions.append((action, data_id)))
    menu = QMenu()
    panel._on_context_menu_impl(menu, data_item)
    labels = [a.text() for a in menu.actions()]
    assert "生成 FID" not in labels and "生成谱图" not in labels
    delete_action = next(a for a in menu.actions() if a.text() == "删除样品数据")
    delete_action.trigger()
    assert actions == [("delete", "d_001")]
    panel.close()


def test_tree_folder_terminal_menu_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.93: subfolders like raw offer "open in terminal"; clicking emits the path."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    raw_item = data_item.child(0)  # raw subfolder
    seen: list[str] = []
    panel.open_terminal_requested.connect(seen.append)
    menu = QMenu()
    panel._on_context_menu_impl(menu, raw_item)
    labels = [a.text() for a in menu.actions()]
    assert "在终端中打开" in labels
    action = next(a for a in menu.actions() if a.text() == "在终端中打开")
    action.trigger()
    assert seen and Path(seen[0]).name == "raw"
    panel.close()


def test_terminal_argv_prefers_csh(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.2.93: open-in-terminal prefers csh; Windows falls back to cmd."""
    import sys

    from gui.project_tree import _terminal_argv

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/bin/{name}" if name in ("csh", "gnome-terminal") else None,
    )
    argv = _terminal_argv("/data/raw")
    assert argv is not None
    assert "csh" in argv
    assert "--working-directory=/data/raw" in argv

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: (
            "C:/cygwin/bin/csh.exe"
            if name == "csh"
            else (r"C:\Windows\System32\cmd.exe" if name == "cmd" else None)
        ),
    )
    argv = _terminal_argv("C:/data/raw")
    assert argv is not None
    assert "csh" in argv[0]


def test_tree_subfolder_context_menu_has_open_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subdirectories like raw offer "open containing directory" in the context menu."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    folder_item = panel.tree.topLevelItem(0).child(0).child(0).child(0).child(0)
    menu = QMenu()
    panel._on_context_menu_impl(menu, folder_item)
    labels = [a.text() for a in menu.actions()]
    assert "打开所在目录" in labels
    panel.close()


def test_create_blank_experiment_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right-clicking blank space/Project creates a blank experiment type."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window._create_experiment()
    assert window.project_tree._pending_kind == "experiment"
    window.project_tree._commit_pending_create("T4")
    assert manager.project is not None
    assert any(e.title == "T4" for e in manager.project.experiments)
    window.close()


def test_delete_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project context-menu delete: after confirmation the project closes and the tree clears."""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(
        manager=manager,
        recent=JsonRecentProjectsStore(tmp_path / "recent_projects.json"),
    )
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))

    def fake_trash(path, fallback_dir, rel=None):
        return path

    # Keep this GUI wiring test isolated from the real Windows recycle bin.  The
    # recycle-bin/fallback behaviour is covered by the workspace tests; invoking
    # it here can block indefinitely on restricted or headless Windows runners.
    monkeypatch.setattr("core.workspace.send_to_trash", fake_trash)
    window._delete_project()
    assert window.manager.project is None
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace root remains
    assert "欢迎" in window.windowTitle()
    window.close()


def test_welcome_page_shows_workspace_and_recent(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Welcome page: workspace path + recent projects + new-project entry (contract v1.3 §9.4)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ProjectManager.create_project(workspace / "projA", "projA")
    ProjectManager.create_project(workspace / "projB", "projB")

    from core.workspace import WorkspaceManager
    from gui.welcome_page import WelcomePage

    page = WelcomePage(workspace=WorkspaceManager(workspace))
    assert str(workspace) in page.workspace_label.text()
    assert page.recent_list.count() == 2
    names = {page.recent_list.item(i).text() for i in range(page.recent_list.count())}
    assert names == {"projA", "projB"}
    page.close()


def test_main_window_welcome_page_on_startup(qapp: QApplication) -> None:
    """With no project open, the main window shows the welcome page (the Workspace page)."""
    window = MainWindow()
    assert window.center_panel.welcome_page is not None
    assert not window.main_splitter.isHidden()  # three columns visible, welcome page in the middle
    assert window.center_panel.stack.currentIndex() == 0  # Workspace page
    window.close()


def test_data_selected_shows_pipeline_page(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting Data -> the middle shows the Pipeline page; import has no manual button."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    tree.setCurrentItem(data_item)
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline page
    assert window.pipeline.current_experiment_id() == "exp_001"
    assert "import" not in window.pipeline._rows  # 0.2.162-patch12: pipeline has no import step
    window.close()


def test_import_failure_handled_on_main_thread(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import failure returns to the main thread via a signal (no modal dialog in the worker)."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    monkeypatch.setattr(
        "threading.Thread",
        lambda *a, **k: _SyncThread(*a, **k),
    )

    def fail_import(mgr, exp_id, source, *, segments=None, copy=True):
        raise RuntimeError("模拟导入失败")

    monkeypatch.setattr("workflow.import_workflow.import_data", fail_import)
    window = MainWindow(manager=manager)
    window._import_experiment_async(
        {"source": str(tmp_path / "nonexistent"), "title": "T", "copy": True}
    )
    assert messages and ("目录不存在" in messages[0] or "导入失败" in messages[0])
    assert "导入失败" in window.log_panel.text.toPlainText()
    window.close()


def test_kinetics_import_failure_is_explicit_rejection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IMPORT-007 A: Kinetics reports "import refused" rather than implying a read-only import."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append((title, text_))),
    )
    window = MainWindow(manager=manager)
    window._on_import_failed("KineticsUnsupportedError: 检测到动力学实验，当前产品不支持导入")

    assert messages == [("不支持导入", "检测到动力学实验，当前产品不支持导入")]
    log = window.log_panel.text.toPlainText()
    assert "导入已拒绝" in log
    assert "数据已导入" not in log
    window.close()


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def test_spectrum_panel_open_current_data_spectrum(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29hz-fix27: two menu entries plus clear no-spectrum/no-data prompts."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    texts = [a.text() for a in panel.file_menu.actions()]
    assert texts[:3] == [
        "打开当前数据谱图",
        "打开任意谱图...",
        "清空谱图",
    ]

    shown: list[str] = []
    monkeypatch.setattr(
        "gui.spectrum_panel.InfoDialog.show_info",
        staticmethod(lambda _parent, _title, text: shown.append(text)),
    )
    # (1) no data selected
    panel.set_context("", "")
    panel._on_menu_open_current_spectrum()
    assert shown[-1] == "当前没有选中数据"
    # (2) data selected but no spectrum yet (the case users actually hit)
    panel.set_context("exp_001", "d_001")
    panel._on_menu_open_current_spectrum()
    assert shown[-1] == "当前数据还未生成谱图"
    # (3) spectrum exists: same as Pipeline "show spectrum", loads directly with no prompt
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    panel.set_context("exp_001", "d_001")
    panel._on_menu_open_current_spectrum()
    assert panel._current_spectrum is not None
    assert len(shown) == 2
    panel.close()


def test_spectrum_panel_scans_data_dir_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """schema 1.3 layout: spectra live under data_dir(...,"spectra")/; the panel can
    list and open them."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "exp_001-d_001.ft2"
    assert panel.open_spectrum(spectra_dir / "exp_001-d_001.ft2") is True
    panel.close()


def test_spectrum_panel_excludes_fid_from_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.77: the spectrum list shows only .ft2/.ft3; raw.fid from process/ is no
    longer mixed in."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "raw.fid").write_bytes(b"fid")
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "exp_001-d_001.ft2"
    panel.close()


def test_spectrum_panel_empty_spectra_clears_viewer(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.85: with no spectra the right side stays empty (no stale spectrum after switching)."""
    manager = ProjectManager.create_project(tmp_path / "proj2", "demo")
    entry = manager.create_experiment("A")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    spectra1 = manager.data_dir(entry.id, data1.id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{entry.id}-{data1.id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data1.id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.layer_list.count() == 1
    # data2's spectra folder is empty -> the viewer clears
    panel.set_context(entry.id, data2.id)
    assert panel.viewer.layer_list.count() == 0
    assert panel._current_spectrum is None
    panel.close()


def test_spectrum_panel_finds_dataset_id_named_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.112: a spectrum named after dataset_id (not exp_id-data_id) is also found."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("A")
    data = manager.import_data(entry.id, "/data/hsqc_2d")
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "hsqc_2d.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert panel.load_current_spectrum() is True
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "hsqc_2d.ft2"
    panel.close()


def test_spectrum_param_report_shows_phase_results() -> None:
    "0.2.108: the param report shows per-dimension phase / direct-dim phase / backend runs."
    from gui.pipeline_panel import _spectrum_param_report

    report = _spectrum_param_report(
        {
            "phase_route": "unified",
            "phases": {"F2": (0.0, 10.0), "F1": (-45.0, 0.0)},
            "direct_phase": (0.0, 10.0),
            "backend_runs": 3,
            "extract": True,
        }
    )
    assert "自动优化" in report
    assert "各维相位" in report
    assert "F1: p0=-45.00° p1=0.00°" in report
    assert "F2: p0=0.00° p1=10.00°" in report
    assert "直接维相位" in report
    assert "后端运行次数" not in report
    assert "◆ 处理设置" in report

    assert "extract" not in report
    assert "提取窗口" not in report


def test_pipeline_show_spectrum_button_on_spectrum_success(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.88: after the spectrum step, a "show spectrum" button appears and emits a request."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "exp_001-d_001.ft2")
    from gui.pipeline_panel import PipelinePanel

    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    button = panel._rows["spectrum"].show_spectrum_button
    assert not button.isHidden()
    seen: list[str] = []
    panel.show_spectrum_requested.connect(seen.append)
    button.click()
    assert seen == ["spectrum"]
    panel.close()


def test_pipeline_spectrum_row_indirect_flip_control_visibility(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-25: the indirect-dim flip control appears under the same condition as
    "rerun final script"; 2D checkbox / 3D dropdown."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "exp_001-d_001.ft2")
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    row = panel._rows["spectrum"]
    assert not row.rerun_final_button.isHidden()
    assert not row.ext_range_button.isHidden()
    # 2D: checkbox (the dropdown stays hidden)
    assert not row.flip_indirect_check.isHidden()
    assert row.flip_indirect_combo.isHidden()
    # other step rows have no such entry (same condition as "rerun final script")
    for step_id in ("fid", "peaks"):
        assert panel._rows[step_id].rerun_final_button.isHidden()
        assert panel._rows[step_id].rerun_group.isHidden()
        assert panel._rows[step_id].flip_indirect_check.isHidden()
        assert panel._rows[step_id].flip_indirect_combo.isHidden()
    # 3D data: a three-item dropdown replaces it, checkbox hidden
    panel._ndim_cache[("exp_001", "d_001")] = 3
    panel.refresh()
    assert not row.flip_indirect_combo.isHidden()
    assert row.flip_indirect_combo.count() == 3
    assert row.flip_indirect_check.isHidden()
    panel.close()


def test_pipeline_rerun_group_holds_the_flip_before_the_button(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-25 UI wording: "flip" and "rerun final script" are one thing -- same box,
    flip on the left."""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "exp_001-d_001.ft2")
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    row = panel._rows["spectrum"]
    # the box shows/hides with "rerun final script"; the title states what the group means
    assert not row.rerun_group.isHidden()
    assert row.flip_group_label.text() == "间接维翻转"
    # order inside: title -> flip control (2D checkbox / 3D dropdown by ndim) -> rerun button
    group = row.rerun_group.layout()
    widgets = [
        group.itemAt(i).widget()
        for i in range(group.count())
        if group.itemAt(i).widget() is not None
    ]
    assert widgets == [
        row.flip_group_label,
        row.flip_indirect_check,
        row.flip_indirect_combo,
        row.rerun_final_button,
    ]
    visible = [w for w in widgets if not w.isHidden()]
    assert visible == [
        row.flip_group_label,
        row.flip_indirect_check,  # 2D: checkbox visible, dropdown hidden
        row.rerun_final_button,
    ]
    # in the whole row the group is a single flow-layout item (no longer two widgets)
    button_row = row.layout().itemAt(1)
    items = [
        button_row.itemAt(i).widget()
        for i in range(button_row.count())
        if button_row.itemAt(i).widget() is not None
    ]
    assert row.rerun_group in items
    assert row.rerun_final_button not in items
    assert row.flip_indirect_check not in items
    panel.close()


def test_import_done_clears_import_form(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.112: a successful import clears the middle-page import form (name/path)."""
    from types import SimpleNamespace

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.center_panel.experiment_page.name_edit.setText("样品1")
    window.center_panel.experiment_page.source_edit.setText("/data/a")
    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"guide": {"first_import_hint_shown": True}},
    )
    result = SimpleNamespace(
        experiment_id="exp_001",
        data_id="d_001",
        run_id="R-1",
        file_count=1,
        total_bytes=10,
        warnings=[],
    )
    window._on_import_done(result)
    assert window.center_panel.experiment_page.name_edit.text() == ""
    assert window.center_panel.experiment_page.source_edit.text() == ""
    window.close()


def test_pipeline_status_peaks_from_data_dir(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """peaks status checks data_dir(...,"peaks")/<exp>-<data>.list."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    peaks_dir = manager.data_dir("exp_001", "d_001", "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    (peaks_dir / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "SUCCESS"


def test_pipeline_peaks_step_runs_pick_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The peaks step run button calls pick_peaks and refreshes the status."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")

    class PeaksController(FakeProcessingController):
        def pick_peaks(self, data, exp_id=None, data_id=None, sigma_multiplier=None) -> dict:
            self.calls.append("pick_peaks")
            peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
            peaks_dir.mkdir(parents=True, exist_ok=True)
            (peaks_dir / f"{exp_id}-{data_id}.list").write_text(
                "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
                encoding="utf-8",
            )
            return {"status": "success", "peak_count": 1}

    controller = PeaksController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", "exp_001", "d_001")
    assert not panel._rows["peaks"].run_button.isHidden()  # peaks READY
    panel._on_run_requested("peaks")
    assert "pick_peaks" in controller.calls
    assert panel._rows["peaks"].status_label.text().startswith("✓")  # peak table appears -> SUCCESS
    panel.close()
    log.close()


def test_data_delete_wires_manager_delete_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sample data deletion wiring: manager.delete_data (does not delete the experiment type)."""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))

    def fake(path, fallback_dir, rel=None):
        return path

    monkeypatch.setattr("core.project.manager.send_to_trash", fake)
    window.project_tree.select_experiment("exp_001")
    window._delete_data("exp_001", "d_001")
    entry = manager.project.experiment("exp_001")
    assert entry is not None and entry.data and all(d.trashed for d in entry.data)
    window.close()


def test_data_rename_persists_title(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data rename: writes DataEntry.title to disk (readable after restart)."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window._rename_data("exp_001", "d_001", "重命名后")
    data_entry = manager.project.experiment("exp_001").data[0]
    assert data_entry.title == "重命名后"
    # the tree shows title
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    assert data_item.text(0) == "重命名后"
    window.close()


def test_double_click_data_keeps_pipeline_and_opens_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-clicking a data node emits open_path_requested (no import jump); Pipeline stays."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    base = manager.data_base("exp_001", "d_001")
    base.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    tree.setCurrentItem(data_item)
    window.project_tree._on_double_clicked(data_item, 0)
    # 0.2.199-patch29ge: double-click opens the d_xxx base directory, not raw
    assert opened and Path(opened[0]) == base
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline page
    window.close()


def test_double_click_folder_opens_folder_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-clicking a subfolder opens that folder; the middle stays on Pipeline."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(2)  # spectra
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    tree.setCurrentItem(folder_item)
    window.project_tree._on_double_clicked(folder_item, 0)
    assert opened and Path(opened[0]) == spectra_dir
    assert window.center_panel.stack.currentIndex() == 3
    window.close()


def test_right_click_open_path_emits_signal(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Context-menu "open containing directory": data/folder nodes emit
    open_path_requested (same as double-click)."""
    # 0.2.199-patch29gk: MainWindow connects open_terminal_requested to _open_terminal ->
    # open_in_terminal(x-terminal-emulator); the test only checks the signal, so it is
    # mocked to avoid really opening a terminal window
    monkeypatch.setattr("gui.project_tree.open_in_terminal", lambda p: True)
    from qtcompat.QtWidgets import QMenu

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    base = manager.data_base("exp_001", "d_001")
    base.mkdir(parents=True, exist_ok=True)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(2)  # spectra
    opened: list[str] = []
    opened_term: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    window.project_tree.open_terminal_requested.connect(lambda p: opened_term.append(p))

    data_menu = window.project_tree._on_context_menu_impl(QMenu(), data_item)
    data_acts = [a for a in data_menu.actions() if a.text() == "打开所在目录"]
    assert len(data_acts) == 1
    data_acts[0].trigger()
    # 0.2.199-patch29ge: right-click opens the d_xxx base directory, not raw
    assert opened and Path(opened[0]) == base
    # 0.2.199-patch29gf: the data node's terminal also opens d_xxx (raw children open their own)
    data_terms = [a for a in data_menu.actions() if a.text() == "在终端中打开"]
    assert len(data_terms) == 1
    data_terms[0].trigger()
    assert opened_term and Path(opened_term[0]) == base

    folder_menu = window.project_tree._on_context_menu_impl(QMenu(), folder_item)
    folder_acts = [a for a in folder_menu.actions() if a.text() == "打开所在目录"]
    assert len(folder_acts) == 1
    folder_acts[0].trigger()
    assert len(opened) == 2 and Path(opened[1]) == spectra_dir
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline page
    window.close()


def test_spectrum_panel_vertical_layout(qapp: QApplication) -> None:
    """Spectrum panel vertical layout: file list on top, viewer below."""
    from qtcompat.QtCore import Qt
    from qtcompat.QtWidgets import QSplitter

    panel = SpectrumPanel()
    found: list[QSplitter] = []

    def walk(widget) -> None:
        for child in widget.children():
            if isinstance(child, QSplitter):
                found.append(child)
            walk(child)

    walk(panel)
    assert found, "SpectrumPanel 内应有 QSplitter"
    splitter = next(s for s in found if s.count() == 4)
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.count() == 4  # viewer / file list / toolbar / peak table
    assert panel.file_list.maximumWidth() > 1000  # no horizontal width limit
    panel.close()


def test_viewer_internal_vertical_layout(qapp: QApplication) -> None:
    """SpectrumViewer internal vertical layout: plot on top, controls below."""
    from qtcompat.QtCore import Qt
    from qtcompat.QtWidgets import QSplitter

    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    found: list[QSplitter] = []

    def walk(widget) -> None:
        for child in widget.children():
            if isinstance(child, QSplitter):
                found.append(child)
            walk(child)

    walk(viewer)
    assert found, "SpectrumViewer 内应有 QSplitter"
    splitter = found[0]
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.count() == 2
    assert splitter.widget(0) is viewer.plot_area  # upper spectrum area
    viewer.close()


def test_project_dashboard_stats_and_runs(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project Dashboard: stats + recent runs."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    run = manager.start_run("exp_001", workflow_ref="import")
    manager.finish_run(run.run_id, "success", message="ok")
    manager.save()
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    proj_item = tree.topLevelItem(0).child(0)
    tree.setCurrentItem(proj_item)
    assert window.center_panel.stack.currentIndex() == 1  # Project Dashboard
    assert "实验:" in window.center_panel.project_page.stats_label.text()
    assert window.center_panel.project_page.runs_table.rowCount() >= 1
    window.close()


def test_experiment_dashboard_data_rows(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Experiment Dashboard: data list."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    exp_item = tree.topLevelItem(0).child(0).child(0)
    tree.setCurrentItem(exp_item)
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page.data_table.rowCount() >= 1
    window.close()


def test_run_history_dialog(tmp_path: Path, qapp: QApplication) -> None:
    """Run history dialog: list + details."""
    from gui.dialogs import RunHistoryDialog

    manager = ProjectManager.create_project(tmp_path / "ws" / "proj", "demo")
    manager.create_experiment("HSQC")
    run = manager.start_run("exp_001", workflow_ref="import")
    manager.finish_run(run.run_id, "success", outputs={"spectrum": "x.ft2"}, message="ok")
    dialog = RunHistoryDialog(None, manager.project.workflow_runs, "demo")
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    assert "x.ft2" in dialog.detail_label.text()
    dialog.close()


def test_spectrum_peak_linkage(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spectrum-peak table linkage: peak table loading + two-way highlight/selection."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1  115.000  8.000  0  100  0\n"
        "A2  118.000  7.500  0  80  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88: load the peak table explicitly
    assert panel.peak_table.rowCount() == 2
    assert len(panel.viewer._peaks) == 2
    panel.peak_table.selectRow(1)
    assert panel.viewer._selected_peak == 1
    panel._on_viewer_peak_clicked(0)
    assert panel.peak_table.currentRow() == 0
    panel.close()


def test_export_poky_button_generates_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a peak table, "export Poky" works and writes a .list with header/peak rows."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88: load the peak table explicitly
    assert panel.export_poky_button.isEnabled()

    out = tmp_path / "out.list"
    from gui.peaks_io import export_peaks_poky, load_peaks

    export_peaks_poky(out, load_peaks(peaks / "exp_001-d_001.list"))
    content = out.read_text(encoding="utf-8")
    assert "G1" in content and "115.0" in content and "8.0" in content
    panel.close()


def test_export_poky_button_disabled_without_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a peak table, "export Poky" is disabled."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert not panel.export_poky_button.isEnabled()
    panel.close()


def test_peak_linkage_via_load_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After loading via load_peaks, row count and linked highlight still work."""
    import csv

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    with (peaks / "exp_001-d_001.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Peak_ID", "H_shift", "N_shift", "Intensity", "SN", "label"])
        writer.writerow(["1", "8.0", "115.0", "100", "20", "G1"])
        writer.writerow(["2", "7.5", "118.0", "80", "15", "A2"])
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88: load the peak table explicitly
    assert panel.peak_table.rowCount() == 2
    assert len(panel.viewer._peaks) == 2
    panel.peak_table.selectRow(1)
    assert panel.viewer._selected_peak == 1
    panel.close()


def test_spectrum_auto_shown_on_data_select(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting data auto-shows the first spectrum; repeated refreshes do not reload it."""
    import numpy as np
    from nmrglue.fileio import pipe

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    shape = (32, 64)
    data = np.zeros(shape, dtype=np.float32)
    data[16, 32] = 50
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 64
    dic["FDSPECNUM"] = 32
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = 32
    dic["FDF1SW"] = 6000
    dic["FDF1OBS"] = 600
    dic["FDF1CAR"] = 118
    dic["FDF1ORIG"] = 118 * 600
    dic["FDF2T"] = 64
    dic["FDF2SW"] = 6000
    dic["FDF2OBS"] = 600
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600
    pipe.write(str(spectra / "exp_001-d_001.ft2"), dic, data, overwrite=True)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.viewer.layer_list.count() == 1
    assert panel._current_spectrum is not None
    monkeypatch.setattr(
        panel,
        "open_spectrum",
        lambda *_a, **_k: pytest.fail("普通刷新不应重复加载已有谱图"),
    )
    panel.refresh()
    assert panel.viewer.layer_list.count() == 1  # refresh does not reopen
    panel.close()


def test_folder_node_shows_files(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subfolder nodes like raw expand to show the files inside."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    raw = manager.data_dir("exp_001", "d_001", "raw")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "acqus").write_text("x")
    (raw / "ser").write_text("y")
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    raw_item = data_item.child(0)
    names = [raw_item.child(i).text(0) for i in range(raw_item.childCount())]
    assert "acqus" in names and "ser" in names
    panel.close()


def test_spectrum_file_double_click_opens_in_panel(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-clicking a spectrum file in the tree shows it directly in the right panel."""
    import numpy as np
    from nmrglue.fileio import pipe

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    shape = (16, 32)
    data = np.zeros(shape, dtype=np.float32)
    data[8, 16] = 10
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 32
    dic["FDSPECNUM"] = 16
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = 16
    dic["FDF1SW"] = 6000
    dic["FDF1OBS"] = 600
    dic["FDF1CAR"] = 118
    dic["FDF1ORIG"] = 118 * 600
    dic["FDF2T"] = 32
    dic["FDF2SW"] = 6000
    dic["FDF2OBS"] = 600
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600
    pipe.write(str(spectra / "exp_001-d_001.ft2"), dic, data, overwrite=True)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    file_item = data_item.child(2).child(0)  # spectra/exp_001-d_001.ft2
    opened: list[str] = []
    window.project_tree.open_spectrum_requested.connect(lambda p: opened.append(p))
    window.project_tree._on_double_clicked(file_item, 0)
    assert opened and Path(opened[0]).name == "exp_001-d_001.ft2"
    window._open_spectrum_from_tree(opened[0])
    assert window.spectrum_panel.viewer.layer_list.count() == 1
    window.close()


def test_viewer_default_dir_matches_current_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standalone viewer's default directory is the current data's spectra folder."""
    from viewer.app import SpectrumWindow

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    window.spectrum_panel.set_context("exp_001", "d_001")
    viewer = SpectrumWindow(start_dir=str(spectra))
    assert Path(viewer.start_dir) == spectra
    viewer.close()
    window.close()


def test_run_step_uses_selected_data_id(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2G-002: run_step with data_id acts on the selected data (not the first one)."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    manager.import_data("exp_001", "/data/2")
    manager.save()
    # 0.2.163-patch14: unfinished prerequisites block the next step -- first make
    # d_002's fid ready
    from gui.pipeline_state import record_step_success

    fid = manager.data_dir("exp_001", "d_002", "process") / "d_002.fid"
    fid.parent.mkdir(parents=True, exist_ok=True)
    fid.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_002", fid)
    record_step_success(manager, "exp_001", "d_002", "fid")
    manager.save()
    seen: list[str] = []

    class ScopedController(FakeProcessingController):
        def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
            seen.append(data_id or "")
            return "/tmp/x.ft2"

    controller = ScopedController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", "exp_001", "d_002")
    panel._on_run_requested("spectrum")
    assert seen == ["d_002"]
    panel.close()


def test_reset_view_union_of_all_layers(qapp: QApplication) -> None:
    """Multi-spectrum overlay: reset_view shows the union range of all spectra."""
    from viewer.spectrum import Spectrum, SpectrumAxis
    from viewer.spectrum_viewer import SpectrumViewer

    def axis(label: str, size: int) -> SpectrumAxis:
        return SpectrumAxis(label, size, 6000, 600, 4.7, 4.7 * 600)

    import numpy as np

    s1 = Spectrum(np.random.rand(64, 128), [axis("F1", 64), axis("F2", 128)])
    s2 = Spectrum(np.random.rand(32, 256), [axis("F1", 32), axis("F2", 256)])
    viewer = SpectrumViewer()
    viewer.add_spectrum(s1, name="s1")
    viewer.add_spectrum(s2, name="s2")
    viewer.reset_view()
    x_range, y_range = viewer.plot.getViewBox().viewRange()
    assert x_range[1] >= 255 and y_range[1] >= 63  # covers both spectra
    viewer.close()


def test_rename_project_to_sample_wording(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three-level naming: project -> experiment -> sample data (menus/welcome/context bar)."""
    workspace = tmp_path / "ws2"
    monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _TempWorkspace(workspace))
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(workspace),
    )
    window = MainWindow()
    # no project open: context bar and welcome-page entry wording
    assert window.context_bar.text() == "未打开项目"
    assert window.center_panel.welcome_page.new_button.text() == "新建项目..."
    # menu bar: an "Experiment(&E)" menu, without project management/add/delete entries
    menus = [action.text() for action in window.menuBar().actions()]
    assert "实验(&E)" in menus
    experiment_menu = next(
        action.menu() for action in window.menuBar().actions() if action.text() == "实验(&E)"
    )
    labels = [action.text() for action in experiment_menu.actions()]
    assert "新建实验..." in labels
    assert "项目管理" not in labels
    assert "添加项目..." not in labels
    assert "删除项目..." not in labels
    window.close()


def test_welcome_page_new_project_inline_input(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Welcome-page "new project": inline naming on the page (no dialog), Enter submits
    and emits, Esc cancels."""
    from core.workspace import WorkspaceManager
    from gui.welcome_page import WelcomePage

    workspace = tmp_path / "ws"
    workspace.mkdir()
    page = WelcomePage(workspace=WorkspaceManager(workspace))
    page.show()
    names: list[str] = []
    page.new_project_requested.connect(names.append)
    page._on_new_clicked()
    assert page._name_edit.isVisible()
    assert page._name_edit.placeholderText() == "输入项目名称"
    assert page._name_ok_button.isVisible()
    page._name_edit.setText("demo")
    page._commit_name()
    assert names == ["demo"]
    assert page._name_ok_button.isHidden()
    # the OK button submits
    page._on_new_clicked()
    page._name_edit.setText("demo2")
    page._name_ok_button.click()
    assert names == ["demo", "demo2"]
    # Esc cancels: the input row and OK button hide and no signal is emitted
    page._on_new_clicked()
    page._cancel_name()
    assert page._name_edit.isHidden()
    assert page._name_ok_button.isHidden()
    page.close()


def test_tree_inline_create_experiment_editor_commit(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New experiment type: created after the in-tree editor's commitData -> closeEditor
    (simulating Enter)."""
    from qtcompat.QtWidgets import QAbstractItemDelegate

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window._create_experiment()
    assert window.project_tree._pending_kind == "experiment"

    class _Editor:
        def text(self):
            return "HNCACB"

    window.project_tree._on_editor_commit_data(_Editor())
    window.project_tree._on_editor_closed(None, QAbstractItemDelegate.EndEditHint.NoHint)
    assert manager.project is not None
    assert any(e.title == "HNCACB" for e in manager.project.experiments)
    window.close()


def test_tree_inline_create_cancel_removes_pending(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New experiment type: cancelling the edit (Esc) creates nothing and drops the
    pending node."""
    from qtcompat.QtWidgets import QAbstractItemDelegate

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    before = len(manager.project.experiments)
    window._create_experiment()
    assert window.project_tree._pending_item is not None
    window.project_tree._on_editor_closed(None, QAbstractItemDelegate.EndEditHint.RevertModelCache)
    assert window.project_tree._pending_item is None
    assert len(manager.project.experiments) == before
    window.close()


def test_segmented_import_entry_validates_and_calls_async(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.110: segmented-acquisition import entry: validate the container, then import async."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    container = tmp_path / "container"
    container.mkdir()
    for seg in ("s1", "s2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    window._segmented_import("exp_001", str(container))
    assert captured and captured[0]["segmented"] is True
    assert captured[0]["source"] == str(container)
    assert captured[0]["title"] == "container"
    assert captured[0]["experiment_id"] == "exp_001"  # G2B-011: import into this experiment type
    window.close()


def test_segmented_import_rejects_non_container(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Segmented import: a non-container directory prompts and starts no import."""
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    single = tmp_path / "single"
    single.mkdir()
    (single / "acqus").write_text("x", encoding="utf-8")
    window._segmented_import("exp_001", str(single))
    assert not captured
    assert messages and "不是分段/重复实验容器" in messages[0]
    window.close()


def test_segmented_import_emits_current_experiment(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G2B-011: the experiment page's segmented entry sends the current experiment type id."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    container = tmp_path / "container"
    container.mkdir()
    for seg in ("s1", "s2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    page = window.center_panel.experiment_page
    page.set_context(manager, "exp_001", "标签")
    page.segmented_source_edit.setText(str(container))
    page._on_segmented_import()
    assert captured and captured[0]["segmented"] is True
    assert captured[0]["experiment_id"] == "exp_001"
    window.close()


def test_rename_editor_appears_at_click_position(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clicking "rename" opens the rename input at the click position (Enter commits)."""
    from qtcompat.QtCore import QPoint

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.show()
    QApplication.processEvents()
    panel = window.project_tree
    exp_item = panel.tree.topLevelItem(0).child(0).child(0)
    anchor = panel.tree.viewport().mapToGlobal(QPoint(30, 10))
    assert not panel._rename_editor.isVisible()  # hidden by default (0.2.112 regression)
    panel._begin_rename("experiment", exp_item, anchor)
    editor = panel._rename_editor
    assert editor.isVisible()
    # 0.2.163-patch4: an embedded child positioned relative to the tree panel (the
    # right edge pulls in when the panel is narrow)
    expected = panel.mapFromGlobal(anchor)
    assert editor.pos().y() == expected.y()
    assert 0 <= editor.pos().x() <= max(0, panel.width() - editor.width())
    editor._edit.setText("HNCACB2")
    editor._commit()
    assert manager.project is not None
    assert any(e.title == "HNCACB2" for e in manager.project.experiments)
    assert not editor.isVisible()
    window.close()


def test_context_menu_rename_opens_inline_editor(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tree context-menu "rename": triggering the entry opens an input box at the click point."""
    from qtcompat.QtCore import QPoint

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    panel.show()
    QApplication.processEvents()
    assert not panel._rename_editor.isVisible()  # hidden by default (0.2.112 regression)
    project_item = panel.tree.topLevelItem(0).child(0)
    menu = QMenu()
    panel._on_context_menu_impl(menu, project_item, QPoint(10, 20))
    action = next(a for a in menu.actions() if "重命名项目" in a.text())
    action.triggered.emit()
    assert panel._rename_editor.isVisible()
    assert panel._rename_target == ("project",)
    panel.close()


def test_log_panel_stop_button_emits_signal(qapp: QApplication) -> None:
    """Stop-current-task button: clicking emits the stop_requested signal."""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    got: list[int] = []
    panel.stop_requested.connect(lambda: got.append(1))
    assert panel.stop_button.text() == "停止当前任务"
    panel.stop_button.click()
    assert got == [1]
    panel.close()


def test_main_window_stop_button_logs_termination(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clicking stop kills the process tree and logs it (no leftover prompt)."""
    import backend.runtime as rt

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    calls: list[int] = []

    def fake_terminate() -> int:
        calls.append(1)
        return 2

    monkeypatch.setattr(rt, "terminate_current_tasks", fake_terminate)
    window.log_panel.stop_button.click()
    assert calls == [1]
    assert not window.log_panel.isHidden()
    assert "已停止当前任务" in window.log_panel.text.toPlainText()
    assert "2" in window.log_panel.text.toPlainText()
    window.close()


def test_main_window_stop_no_task_notice(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.runtime as rt

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    monkeypatch.setattr(rt, "terminate_current_tasks", lambda: 0)
    window.log_panel.stop_button.click()
    assert "当前没有正在运行的任务" in window.log_panel.text.toPlainText()
    window.close()


def test_default_column_widths_1920(qapp: QApplication) -> None:
    """Default column widths are [420, 600, 300, 600] (1920 total); on small screens
    they narrow without overflowing."""
    from qtcompat.QtGui import QGuiApplication

    window = MainWindow()
    screen = window.screen() or QGuiApplication.primaryScreen()
    cols = window.main_splitter.sizes()
    if screen is None:
        assert sum(cols) == 1920
        window.close()
        return
    avail = screen.availableGeometry()
    if avail.width() >= 1920:
        assert sum(cols) == 1920
        assert abs(cols[0] - 420) <= 1
        assert abs(cols[1] - 600) <= 1
        assert abs(cols[2] - 300) <= 1
        assert abs(cols[3] - 600) <= 1
        assert window.width() == 1920
    else:
        assert sum(cols) <= avail.width()
        assert window.width() == avail.width()
    window.close()


def test_phase_panel_visible_only_in_1d(qapp: QApplication) -> None:
    """0.2.147: the p0/p1 phase panel is one row and appears only in 1D mode."""
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    assert viewer.phase_panel.isHidden()
    viewer.add_spectrum(_synthetic_spectrum_2d())
    assert viewer.phase_panel.isHidden()  # not shown for 2D
    viewer.set_1d_mode(True)
    assert not viewer.phase_panel.isHidden()  # shown in stripe 1D mode
    viewer.set_1d_mode(False)
    assert viewer.phase_panel.isHidden()
    viewer.close()


def test_spectrum_panel_new_layout_constraints(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.147: Files/Layers share a row; Show peaks comes before Add peak; the Poky
    label is gone."""
    from gui.spectrum_panel import SpectrumPanel

    panel = SpectrumPanel()
    assert panel.file_list.parent() is panel.lists_row_widget
    assert panel.viewer.layer_list.parent() is panel.lists_row_widget
    rows = panel.peak_toolbar_widget.layout()
    first = rows.itemAt(0).layout().itemAt(0).widget()
    assert first is panel.viewer.show_peaks_checkbox
    assert panel.import_poky_button.text() == "Import peaks"
    assert panel.export_poky_button.text() == "Export peaks"
    # 0.2.199-patch29bb: the second row holds Delete/Import/Export/Save
    row2 = rows.itemAt(1).layout()
    row2_widgets = [row2.itemAt(i).widget() for i in range(row2.count())]
    assert panel.delete_peak_button in row2_widgets
    assert panel.import_poky_button in row2_widgets
    assert panel.export_poky_button in row2_widgets
    assert panel.save_peaks_button in row2_widgets
    # peak-operation rows have clear spacing
    assert panel.peak_toolbar.spacing() >= 10
    assert panel.peak_toolbar2.spacing() >= 10
    panel.close()


def _synthetic_spectrum_2d():
    from viewer.spectrum import Spectrum, SpectrumAxis

    axis_x = SpectrumAxis(
        label="1H",
        size=64,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=4.7,
        orig_hz=4.7 * 600.0,
    )
    axis_y = SpectrumAxis(
        label="15N",
        size=32,
        sw_hz=2000.0,
        obs_mhz=60.0,
        carrier_ppm=118.0,
        orig_hz=118.0 * 60.0,
    )
    import numpy as np

    return Spectrum(data=np.zeros((32, 64)), axes=[axis_y, axis_x])


def test_spectrum_param_report_has_no_data_quality_section() -> None:
    """2026-09-23 (user request): data-quality diagnostics belong to the "generate FID" step.

    The spectrum param report no longer carries that section (the step's log and step
    report do); processing parameters and optimisation remain.
    """
    from gui.pipeline_panel import _spectrum_param_report

    report = _spectrum_param_report(
        {
            "diagnostics": {
                "reports": [
                    "直流偏置: 自动启用 POLY -time",
                    "坏点: 已修复 3 处",
                ],
                "apply_poly_time": True,
            },
            "backend_runs": 2,
        }
    )
    assert "◆ 数据质量诊断" not in report
    assert "直流偏置" not in report
    assert "◆ 处理设置" in report


def test_experiment_page_import_panel_is_inline_and_scrollable(
    qapp: QApplication,
) -> None:
    "Regression coverage: test experiment page import panel is inline and scrollable."
    from qtcompat.QtCore import Qt

    from gui.dashboards import ExperimentDashboard

    page = ExperimentDashboard()
    assert not hasattr(page, "import_dropdown_button")
    assert not page.import_panel.isHidden()
    assert page.import_panel.parentWidget() is page.page_content
    assert page.scroll_area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded

    page.resize(640, 300)
    page.show()
    QApplication.processEvents()
    assert page.scroll_area.verticalScrollBar().maximum() > 0
    page.close()


def test_rename_editor_text_color(qapp: QApplication) -> None:
    """0.2.162-patch11: the rename input is black on white (fixes invisible text)."""
    from gui.project_tree import _InlineRenameEditor

    editor = _InlineRenameEditor()
    stylesheet = editor._edit.styleSheet()
    assert "background: white" in stylesheet
    assert "color: #222" in stylesheet
    editor.close()


def test_peak_threshold_range_up_to_50(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29bo/patch29cn: peak threshold ceiling 30σ -> 50σ; the spin box is uncapped."""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    row = panel._rows["peaks"]
    assert row.threshold_slider.maximum() == 500  # 50σ
    assert row.threshold_spin.maximum() > 50.0  # no ceiling on the spin box (patch29cn)
    assert row.threshold_spin.value() == pytest.approx(35.0)  # default 35σ (patch29hn)
    assert row.threshold_slider.value() == 350
    row.threshold_spin.setValue(28.5)
    assert row.threshold_slider.value() == 285
    row.threshold_spin.setValue(100.0)  # above the slider ceiling
    assert row.threshold_slider.value() == 500  # slider stops at 50σ
    assert row.threshold_spin.value() == pytest.approx(100.0)  # not written back over
    panel.close()


def test_peaks_threshold_change_does_not_auto_run(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.2.199-patch29au: changing the threshold does not auto-pick peaks; run/reprocess does
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")

    class PeaksController(FakeProcessingController):
        def pick_peaks(self, data, exp_id=None, data_id=None, sigma_multiplier=None) -> dict:
            self.calls.append(("pick_peaks", sigma_multiplier))
            peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
            peaks_dir.mkdir(parents=True, exist_ok=True)
            (peaks_dir / f"{exp_id}-{data_id}.list").write_text(
                "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
                encoding="utf-8",
            )
            return {"status": "success", "peak_count": 1}

    controller = PeaksController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", "exp_001", "d_001")
    row = panel._rows["peaks"]
    row.threshold_spin.setValue(8.0)
    row.threshold_slider.sliderReleased.emit()
    assert controller.calls == []
    from qtcompat.QtCore import Qt
    from qtcompat.QtTest import QTest

    panel.show()
    row.threshold_spin.setFocus()
    row.threshold_spin.selectAll()
    QTest.keyClicks(row.threshold_spin, "100")
    assert row.threshold_spin.value() == 8.0
    assert row.threshold_slider.value() == 80
    QTest.keyClick(row.threshold_spin, Qt.Key.Key_Return)
    assert row.threshold_spin.value() == 100.0
    assert row.threshold_slider.value() == 500
    assert controller.calls == []
    assert row.threshold_spin.singleStep() == 0.5
    row.threshold_spin.setValue(8.0)
    panel._on_run_requested("peaks")
    assert controller.calls == [("pick_peaks", 8.0)]  # running uses the new threshold
    panel.close()
    log.close()


def test_project_tree_data_status_shows_picked(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.2.199-patch29av: with a peak table the data shows "peaks picked"
    from gui.project_tree import ProjectTreePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "exp_001-d_001.ft2")
    manager.set_data_spectrum("exp_001", "d_001", str(spectra / "exp_001-d_001.ft2"))
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    manager.save()
    panel = ProjectTreePanel(manager)

    def _data_item():
        return panel.tree.topLevelItem(0).child(0).child(0).child(0)

    assert _data_item().text(1) == "已选峰"
    panel.close()


def test_spectrum_display_settings_isolated_per_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29fz: contour start / levels / aspect / marker size are isolated per
    dataset -- d_001's adjustments do not leak into d_002, and switching back restores them."""
    from gui.spectrum_panel import SpectrumPanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    exp = manager.project.experiment("exp_001")
    assert exp is not None
    d1_id = str(exp.data[0].id)
    d2 = manager.import_data(exp.id, "/fake/2")
    spectra1 = manager.data_dir(exp.id, d1_id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{exp.id}-{d1_id}.ft2")
    spectra2 = manager.data_dir(exp.id, d2.id, "spectra")
    spectra2.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra2 / f"{exp.id}-{d2.id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    panel.viewer.level_slider.setValue(60)
    panel.viewer.count_slider.setValue(12)
    panel.viewer.aspect_slider.setValue(80)
    panel.peak_size_spin.setValue(2.0)
    assert panel._display_states[(exp.id, d1_id)]["level_slider"] == 60
    panel.viewer.level_label.setValue(21.7)
    assert panel._display_states[(exp.id, d1_id)]["level_percent"] == 21.7

    panel.set_context(exp.id, d2.id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.level_slider.value() == 31
    assert panel.viewer.count_slider.value() == 8
    assert panel.viewer.aspect_slider.value() == 0
    assert panel.peak_size_spin.value() == 1.5
    # switching back to d_001 restores its own adjustments
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.level_slider.value() == 60
    assert panel.viewer.level_label.value() == 21.7
    assert panel.viewer.count_slider.value() == 12
    assert panel.viewer.aspect_slider.value() == 80
    assert panel.peak_size_spin.value() == 2.0
    panel.close()


def test_log_panel_data_scope_persists_to_data_folder(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29ga: the single-data log persists to d_xxx/log.txt, and a new panel
    reads it back."""
    from core.project import ProjectManager
    from gui.log_panel import LogPanel

    manager = ProjectManager.create_project(tmp_path / "proj_log", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/1")
    log = LogPanel()
    log.set_manager(manager)
    scope = log.scope_key("data", exp.id, data.id)
    log.append("第一次处理完成", scope=scope)
    path = manager.data_base(exp.id, data.id) / "report" / "log.txt"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "第一次处理完成" in text
    # a new LogPanel (simulating a restart) sees the history for that data
    log2 = LogPanel()
    log2.set_manager(manager)
    log2.set_scope("data", exp.id, data.id)
    assert any("第一次处理完成" in line for line in log2._buffers[scope])
    # clearing the panel also clears the record file (keeping the title line)
    log2.clear()
    text2 = path.read_text(encoding="utf-8")
    assert "第一次处理完成" not in text2
    assert text2.startswith("#")
    log.close()
    log2.close()


def test_spectrum_display_settings_persisted_in_data_folder(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29ga: display adjustments persist to d_xxx/ui_state.json and are
    restored on restart."""
    import json

    from gui.spectrum_panel import SpectrumPanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    exp = manager.project.experiment("exp_001")
    assert exp is not None
    d1_id = str(exp.data[0].id)
    spectra1 = manager.data_dir(exp.id, d1_id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{exp.id}-{d1_id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    panel.viewer.level_slider.setValue(55)
    panel.viewer.level_label.setValue(16.7)
    panel.viewer.count_slider.setValue(11)
    panel.viewer.aspect_slider.setValue(70)
    panel.peak_size_spin.setValue(2.5)
    path = manager.data_base(exp.id, d1_id) / "ui_state.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    spectrum = payload["spectrum"]
    assert spectrum["level_slider"] == 55
    assert spectrum["level_percent"] == 16.7
    assert spectrum["level_count"] == 11
    assert spectrum["aspect"] == 70
    assert spectrum["peak_size"] == 2.5
    # a new panel (simulating a restart) restores them for that data
    panel2 = SpectrumPanel(manager)
    panel2.set_context(exp.id, d1_id)
    assert panel2.load_current_spectrum() is True
    assert panel2.viewer.level_slider.value() == 55
    assert panel2.viewer.level_label.value() == 16.7
    assert panel2.viewer.count_slider.value() == 11
    assert panel2.viewer.aspect_slider.value() == 70
    assert panel2.peak_size_spin.value() == 2.5
    panel.close()
    panel2.close()


def test_log_panel_group_scope_persists_to_group_folder(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29gb: the data-group log also lands on disk (group log.txt), and a
    new panel reads it back."""
    from core.project import ProjectManager
    from gui.log_panel import LogPanel

    manager = ProjectManager.create_project(tmp_path / "proj_gl", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/1")
    group = manager.create_data_group(exp.id, data_ids=[data.id])
    log = LogPanel()
    log.set_manager(manager)
    scope = log.scope_key("group", exp.id, "", group.id)
    log.append("组日志第一行", scope=scope)
    path = manager.root / exp.id / "groups" / group.id / "report" / "log.txt"
    assert path.is_file()
    assert "组日志第一行" in path.read_text(encoding="utf-8")
    log2 = LogPanel()
    log2.set_manager(manager)
    log2.set_scope("group", exp.id, "", group.id)
    assert any("组日志第一行" in line for line in log2._buffers[scope])
    log2.clear()
    assert "组日志第一行" not in path.read_text(encoding="utf-8")
    log.close()
    log2.close()
