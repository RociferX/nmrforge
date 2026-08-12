"""三栏布局 GUI 测试:项目树(Project→Experiment→Data)/ 五步 Pipeline / 谱图面板(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QMenu

from core.project import ProjectManager
from gui.log_panel import LogPanel
from gui.main_window import MainWindow
from gui.pipeline_panel import (
    PIPELINE_STEPS,
    PipelinePanel,
    compute_step_statuses,
)
from gui.project_tree import ProjectTreePanel
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class SyncThread:
    """把后台线程变为同步执行,测试不依赖线程时序。"""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def _manager_with_experiment(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    for source, title in (("/sampleD", "HSQC"), ("/sampleE", "HNCACB")):
        entry = manager.add_experiment(source, title=title)
        entry.status = "registered"  # schema 1.1 兼容:登记但未落盘 metadata
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
    """五步流程假控制器:generate_fid/generate_spectrum 记录调用并同步完成。"""

    def set_manager(self, manager) -> None:
        self.manager = manager

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_entry = None

    def auto_run_async(self, entry, on_done, on_error) -> None:
        self.calls.append("auto_run")
        self.last_entry = entry
        on_done(
            {
                "status": "success",
                "message": "QC: accept",
                "logs": ["步骤 1", "步骤 2"],
                "experiment_id": entry.id,
            }
        )

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
# 项目树(Project → Experiment → Data)
# ----------------------------------------------------------------------
def test_project_tree_structure(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = ProjectTreePanel(manager)
    assert panel.tree.topLevelItemCount() == 1
    workspace_item = panel.tree.topLevelItem(0)
    assert workspace_item.text(0) == "NMRForgeWorkspace"  # Workspace 根节点
    project_item = workspace_item.child(0)
    assert project_item.text(0) == "demo"
    assert project_item.childCount() == 2
    exp_item = project_item.child(0)
    assert exp_item.text(0) == "HSQC"
    assert exp_item.childCount() == 1
    data_item = exp_item.child(0)
    assert data_item.text(0) == "数据 d_001"
    assert data_item.text(1) == "已导入"
    assert data_item.childCount() == 6  # raw/process/spectra/peaks/figures/report
    panel.close()


def test_project_tree_current_experiment_from_data(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = ProjectTreePanel(manager)
    panel.select_experiment("exp_002")
    assert panel.current_experiment_id() == "exp_002"
    # 选中 Data 节点仍归一化到所属实验
    exp_item = panel.tree.topLevelItem(0).child(0).child(1)
    panel.tree.setCurrentItem(exp_item.child(0))
    assert panel.current_experiment_id() == "exp_002"
    assert panel._data_id_of(panel.tree.currentItem()) == "d_001"
    panel.close()


def test_project_tree_column_widths_readable(qapp: QApplication) -> None:
    panel = ProjectTreePanel()
    assert panel.tree.columnWidth(0) >= 180  # 对象列最小可读宽
    assert panel.tree.columnWidth(1) >= 70  # 状态列
    panel.close()


# ----------------------------------------------------------------------
# Pipeline 五步状态
# ----------------------------------------------------------------------
def test_pipeline_steps_are_five_step_flow() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    assert ids == ["import", "fid", "spectrum", "peaks", "analysis"]
    for _, _, _, deps in PIPELINE_STEPS:
        for dep in deps:
            assert dep in ids


def test_pipeline_status_registered(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["import"] == "SUCCESS"  # 存在数据节点即导入完成
    assert statuses["fid"] == "READY"
    for step_id in ("spectrum", "peaks", "analysis"):
        assert statuses[step_id] == "LOCKED"


def test_pipeline_status_after_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    spectra = manager.dir_path("spectra")
    _write_ft2(spectra / "exp_001.ft2")
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["import"] == "SUCCESS"
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "READY"
    assert statuses["analysis"] == "LOCKED"


def test_pipeline_panel_refresh_shows_next_step(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert "下一步" in panel.next_label.text()
    assert "生成 FID" in panel.next_label.text()
    assert not panel._rows["fid"].run_button.isHidden()
    assert panel._rows["spectrum"].run_button.isHidden()
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
    panel.set_selection("data", "exp_001", "d_001")
    panel._on_run_requested("fid")
    assert controller.calls == ["generate_fid"]
    assert "完成 生成 FID" in log.text.toPlainText()
    # 运行完成后按产物文件重新推断(无 ft2 时 fid 回到 READY)
    assert panel._rows["fid"].status_label.text().startswith("▶")
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
    assert window.pipeline.current_experiment_id() == "exp_001"
    assert "未选中数据" in window.pipeline.context_label.text()
    assert "demo" in window.windowTitle()
    assert window.experiment_tree.topLevelItemCount() == 2  # 兼容表同步
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
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    assert window.log_panel.isHidden()
    window.pipeline._on_run_requested("fid")
    assert not window.log_panel.isHidden()
    assert "生成 FID" in window.log_panel.text.toPlainText()
    window.close()


def test_main_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.project_tree.tree.topLevelItemCount() == 0
    assert window.experiment_tree.topLevelItemCount() == 0
    assert "欢迎" in window.windowTitle()
    assert window.pipeline.current_experiment_id() == ""
    window.close()

def test_tree_data_node_context_menu_actions(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data 节点右键不再含功能项,仅删除/打开目录。"""
    manager = _manager_with_experiment(tmp_path)
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    actions: list[tuple[str, str]] = []
    panel.data_action_requested.connect(
        lambda action, data_id: actions.append((action, data_id))
    )
    menu = QMenu()
    panel._on_context_menu_impl(menu, data_item)
    labels = [a.text() for a in menu.actions()]
    assert "生成 FID" not in labels and "生成谱图" not in labels
    for action in menu.actions():
        action.trigger()
    assert actions == [("delete", "d_001")]
    panel.close()


def test_tree_subfolder_context_menu_has_open_path(
    tmp_path: Path, qapp: QApplication
) -> None:
    """raw 等子目录右键提供「打开所在目录」。"""
    manager = _manager_with_experiment(tmp_path)
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
    """空白处/Project 右键新建空白实验。"""
    manager = _manager_with_experiment(tmp_path)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(
        "gui.main_window.QInputDialog.getText",
        staticmethod(lambda *args, **kwargs: ("T4", True)),
    )
    window._create_experiment()
    assert manager.project is not None
    assert any(e.title == "T4" for e in manager.project.experiments)
    window.close()


def test_delete_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project 右键删除项目:确认后关闭项目并清空树。"""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))
    window._delete_project()
    assert window.manager.project is None
    assert window.project_tree.tree.topLevelItemCount() == 0
    assert "欢迎" in window.windowTitle()
    window.close()

def test_welcome_page_shows_workspace_and_recent(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """欢迎页:工作区路径 + 最近项目列表 + 新建入口(契约 v1.3 §9.4)。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ProjectManager.create_project(workspace / "projA", "projA")
    ProjectManager.create_project(workspace / "projB", "projB")

    from gui.welcome_page import WelcomePage, _FallbackWorkspaceManager

    monkeypatch.setattr(
        "gui.welcome_page.workspace_manager",
        lambda: _FallbackWorkspaceManager(workspace),
    )
    page = WelcomePage()
    assert str(workspace) in page.workspace_label.text()
    assert page.recent_list.count() == 2
    names = {page.recent_list.item(i).text() for i in range(page.recent_list.count())}
    assert names == {"projA", "projB"}
    page.close()


def test_main_window_welcome_page_on_startup(qapp: QApplication) -> None:
    """未打开项目时主窗口显示欢迎页。"""
    window = MainWindow()
    assert window.welcome_page is not None
    assert window.main_splitter.isHidden()  # 欢迎页优先,三栏隐藏
    window.close()
