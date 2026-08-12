"""三栏布局 GUI 测试:项目树 / Pipeline / 谱图面板 / Task-Log(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.log_panel import LogPanel
from gui.main_window import MainWindow
from gui.pipeline_panel import (
    PIPELINE_STEPS,
    PipelinePanel,
    compute_step_statuses,
)
from gui.project_tree import STAGE_LABELS, ProjectTreePanel
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_experiment(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    for source, title in (("/sampleD", "HSQC"), ("/sampleE", "HNCACB")):
        entry = manager.add_experiment(source, title=title)
        entry.status = "registered"  # 仅登记:无 metadata 产物,状态列显示 registered
    manager.save()
    return manager


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
    """记录 auto_run_async 调用的假控制器(同步执行回调)。"""

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.last_entry = None

    def auto_run_async(self, entry, on_done, on_error) -> None:
        self.calls.append(entry)
        self.last_entry = entry
        on_done(
            {
                "status": "success",
                "message": "QC: accept",
                "logs": ["步骤 1", "步骤 2"],
                "experiment_id": entry.id,
            }
        )


# ----------------------------------------------------------------------
# 项目树
# ----------------------------------------------------------------------
def test_project_tree_structure(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = ProjectTreePanel(manager)
    assert panel.tree.topLevelItemCount() == 1
    project_item = panel.tree.topLevelItem(0)
    assert project_item.text(0) == "demo"
    assert project_item.childCount() == 2
    exp_item = project_item.child(0)
    assert exp_item.text(0) == "HSQC"
    assert exp_item.text(1) == "已登记"
    assert exp_item.childCount() == len(STAGE_LABELS)
    assert exp_item.child(0).text(0) == "Input"
    assert exp_item.child(1).text(0) == "Processing"
    assert exp_item.child(2).text(0) == "Output"
    assert exp_item.child(3).text(0) == "Figures"
    panel.close()


def test_project_tree_current_experiment_from_stage(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = ProjectTreePanel(manager)
    panel.select_experiment("exp_002")
    assert panel.current_experiment_id() == "exp_002"
    # 选中阶段节点仍归一化到所属实验
    exp_item = panel.tree.topLevelItem(0).child(1)
    panel.tree.setCurrentItem(exp_item.child(2))
    assert panel.current_experiment_id() == "exp_002"
    panel.close()


# ----------------------------------------------------------------------
# Pipeline 状态
# ----------------------------------------------------------------------
def test_pipeline_status_registered(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["import"] == "READY"
    for step_id in ("fid", "process", "reconstruct", "peaks", "assign"):
        assert statuses[step_id] == "LOCKED"


def test_pipeline_status_after_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    spectra = manager.dir_path("spectra")
    _write_ft2(spectra / "exp_001.ft2")
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["import"] == "SUCCESS"
    assert statuses["fid"] == "SUCCESS"
    assert statuses["process"] == "SUCCESS"
    assert statuses["reconstruct"] == "SUCCESS"
    assert statuses["peaks"] == "READY"
    assert statuses["assign"] == "LOCKED"


def test_pipeline_panel_refresh_shows_next_step(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_context("exp_001")
    assert "下一步" in panel.next_label.text()
    assert "导入数据" in panel.next_label.text()
    assert not panel._rows["import"].run_button.isHidden()
    assert panel._rows["process"].run_button.isHidden()
    panel.close()


def test_pipeline_panel_run_updates_log_and_status(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_context("exp_001")
    panel._on_run_requested("import")
    assert controller.last_entry is not None
    assert controller.last_entry.id == "exp_001"
    assert "QC" in log.text.toPlainText()
    # 运行完成后按产物文件重新推断(无 ft2 时 import 回到 READY)
    assert panel._rows["import"].status_label.text().startswith("▶")
    panel.close()
    log.close()


# ----------------------------------------------------------------------
# 谱图面板
# ----------------------------------------------------------------------
def test_spectrum_panel_lists_and_loads_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    spectra = manager.dir_path("spectra")
    _write_ft2(spectra / "exp_001.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "exp_001.ft2"
    assert panel.open_spectrum(spectra / "exp_001.ft2") is True
    assert panel.viewer.layer_list.count() == 1
    panel.close()


def test_spectrum_panel_open_corrupt_returns_false(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = SpectrumPanel(manager)
    bad = tmp_path / "bad.ft2"
    bad.write_bytes(b"not a pipe file")
    assert panel.open_spectrum(bad) is False
    panel.close()


# ----------------------------------------------------------------------
# 主窗口三栏
# ----------------------------------------------------------------------
def test_main_window_three_column_layout(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    window = MainWindow(manager=manager)
    assert window.main_splitter.count() == 3
    assert window.project_tree is not None
    assert window.pipeline is not None
    assert window.spectrum_panel is not None
    # 打开项目后默认聚焦第一个实验
    assert window.pipeline.current_experiment_id() == "exp_001"
    assert "demo" in window.windowTitle()
    # 兼容旧测试的扁平实验表仍然同步
    assert window.experiment_tree.topLevelItemCount() == 2
    window.close()


def test_main_window_context_updates_on_tree_selection(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_002")
    assert window.pipeline.current_experiment_id() == "exp_002"
    assert window.spectrum_panel._current_exp_id == "exp_002"
    window.close()


def test_main_window_log_panel_expands_on_message(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    assert window.log_panel.isHidden()
    window.pipeline._on_run_requested("import")
    assert not window.log_panel.isHidden()
    assert "QC" in window.log_panel.text.toPlainText()
    window.close()


def test_main_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.project_tree.tree.topLevelItemCount() == 0
    assert window.experiment_tree.topLevelItemCount() == 0
    assert "未打开项目" in window.windowTitle()
    assert window.pipeline.current_experiment_id() == ""
    window.close()


def test_pipeline_steps_definition_consistent() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    assert len(ids) == len(set(ids))
    assert ids[0] == "import"
    assert ids[-1] == "assign"
    for _, _, _, deps in PIPELINE_STEPS:
        for dep in deps:
            assert dep in ids
