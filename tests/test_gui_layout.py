"""三栏布局 GUI 测试:项目树(Project→Experiment→Data)/ 五步 Pipeline / 谱图面板(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QMenu

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


def _manager_with_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> ProjectManager:
    """在临时工作区创建项目,并让树/主窗口使用该工作区(测试隔离)。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = ProjectManager.create_project(workspace / "proj", "demo")
    for source, title in (("/sampleD", "HSQC"), ("/sampleE", "HNCACB")):
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
    """指向临时目录的工作区桩。"""

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
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
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
def test_project_tree_structure(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    assert panel.tree.topLevelItemCount() == 1
    workspace_item = panel.tree.topLevelItem(0)
    assert workspace_item.text(0) == "NMRForgeWorkspace"  # Workspace 根节点
    project_item = workspace_item.child(0)
    assert project_item.text(0) == "demo"  # 当前项目显示 project.name
    assert project_item.text(1) == "当前"  # 当前项目标记
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
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
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
def test_pipeline_steps_include_optional_smile() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    assert ids == [
        "import", "fid", "spectrum", "smile", "peaks", "analysis"
    ]
    deps = {step[0]: step[3] for step in PIPELINE_STEPS}
    # SMILE 优化为可选:峰挑选不依赖它
    assert "smile" not in deps["peaks"]
    assert deps["smile"] == ("spectrum",)
    for _, _, _, step_deps in PIPELINE_STEPS:
        for dep in step_deps:
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
    # fid 与谱图都需要真实产物:fid 在 process 目录,fid_path 登记
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    spectra = manager.dir_path("spectra")
    _write_ft2(spectra / "exp_001.ft2")
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["import"] == "SUCCESS"
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "READY"
    assert statuses["analysis"] == "LOCKED"


def test_pipeline_fid_unlocks_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    """生成 FID 后,生成谱图步骤应解锁为 READY(回归:曾误用谱图判定 fid)。"""
    manager = _manager_with_experiment(tmp_path)
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["import"] == "SUCCESS"
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "READY"  # 关键:谱图步骤解锁
    assert statuses["peaks"] == "LOCKED"


def test_pipeline_panel_refresh_shows_next_step(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert "下一步" in panel.next_label.text()
    assert "生成 FID" in panel.next_label.text()
    assert not panel._rows["fid"].run_button.isHidden()
    assert panel._rows["spectrum"].run_button.isHidden()
    # 导入数据为自动化步骤,无人工入口
    assert panel._rows["import"].manual_button.isHidden()
    assert not panel._rows["fid"].manual_button.isHidden()
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
def test_main_window_three_column_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert window.main_splitter.count() == 3
    assert window.project_tree is not None
    assert window.pipeline is not None
    assert window.spectrum_panel is not None
    # 默认聚焦第一个实验 → 中间为实验页(内嵌导入数据表单)
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page._exp_id == "exp_001"
    assert "demo" in window.windowTitle()
    assert window.experiment_tree.topLevelItemCount() == 2  # 兼容表同步
    window.close()


def test_main_window_context_updates_on_tree_selection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_002")
    assert window.center_panel.stack.currentIndex() == 2  # 实验页
    assert window.center_panel.experiment_page._exp_id == "exp_002"
    assert window.spectrum_panel._current_exp_id == "exp_002"
    window.close()


def test_main_window_log_panel_expands_on_message(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    assert window.log_panel.isHidden()
    window.center_panel.set_selection("data", "exp_001", "d_001")
    window.pipeline._on_run_requested("fid")
    assert not window.log_panel.isHidden()
    assert "生成 FID" in window.log_panel.text.toPlainText()
    window.close()


def test_main_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace 根
    assert window.experiment_tree.topLevelItemCount() == 0
    assert "欢迎" in window.windowTitle()
    assert window.pipeline.current_experiment_id() == ""
    window.close()

def test_tree_data_node_context_menu_actions(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data 节点右键:删除/打开目录 + 批量组加入(不含生成步骤)。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
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
    assert "加入批量组..." in labels
    delete_action = next(a for a in menu.actions() if a.text() == "删除数据")
    delete_action.trigger()
    assert actions == [("delete", "d_001")]
    panel.close()


def test_tree_subfolder_context_menu_has_open_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw 等子目录右键提供「打开所在目录」。"""
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
    """空白处/Project 右键新建空白实验。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(
        "gui.main_window.QInputDialog.getText",
        staticmethod(lambda *args, **kwargs: ("T4", True)),
    )

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, text):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_text(self):
            return ""

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window._create_experiment()
    assert manager.project is not None
    assert any(e.title == "T4" for e in manager.project.experiments)
    window.close()


def test_delete_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project 右键删除项目:确认后关闭项目并清空树。"""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))
    window._delete_project()
    assert window.manager.project is None
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace 根仍在
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
    """未打开项目时主窗口显示欢迎页(三栏中 Workspace 页)。"""
    window = MainWindow()
    assert window.center_panel.welcome_page is not None
    assert not window.main_splitter.isHidden()  # 三栏可见,欢迎页在中间
    assert window.center_panel.stack.currentIndex() == 0  # Workspace 页
    window.close()

def test_data_selected_shows_pipeline_page(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选中 Data → 中间为 Pipeline 页;导入无人工按钮。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    tree.setCurrentItem(data_item)
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline 页
    assert window.pipeline.current_experiment_id() == "exp_001"
    assert window.pipeline._rows["import"].manual_button.isHidden()  # 导入无人工
    window.close()

def test_import_failure_handled_on_main_thread(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导入失败经信号回主线程处理(不在后台线程弹模态框)。"""
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
    window.add_experiment_via_import(str(tmp_path / "nonexistent"), title="T")
    assert messages and "导入失败" in messages[0]
    assert "导入失败" in window.log_panel.text.toPlainText()
    window.close()


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()

def test_spectrum_panel_scans_data_dir_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """schema 1.3 布局:谱图位于 data_dir(...,"spectra")/ 下,面板可列出并打开。"""
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


def test_pipeline_status_peaks_from_data_dir(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """peaks 状态检查 data_dir(...,"peaks")/<exp>-<data>.csv。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    peaks_dir = manager.data_dir("exp_001", "d_001", "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    (peaks_dir / "exp_001-d_001.csv").write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,G1\n",
        encoding="utf-8",
    )
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "SUCCESS"
    assert statuses["analysis"] == "READY"


def test_pipeline_peaks_step_runs_pick_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """peaks 步骤运行按钮走 pick_peaks 并刷新状态。"""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")

    class PeaksController(FakeProcessingController):
        def pick_peaks(self, data, exp_id=None, data_id=None) -> dict:
            self.calls.append("pick_peaks")
            peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
            peaks_dir.mkdir(parents=True, exist_ok=True)
            (peaks_dir / f"{exp_id}-{data_id}.csv").write_text(
                "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,G1\n",
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
    assert panel._rows["peaks"].status_label.text().startswith("✓")  # 峰表出现 → SUCCESS
    panel.close()
    log.close()


def test_data_delete_wires_manager_delete_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据删除接线:manager.delete_data(不删实验)。"""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))
    window.project_tree.select_experiment("exp_001")
    window._delete_data("exp_001", "d_001")
    entry = manager.project.experiment("exp_001")
    assert entry is not None and entry.data == []  # 数据被删,实验保留
    window.close()


def test_data_rename_persists_title(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据重命名:写 DataEntry.title 并落盘(重启可读)。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(
        "gui.main_window.QInputDialog.getText",
        staticmethod(lambda *a, **k: ("重命名后", True)),
    )
    window._rename_data("exp_001", "d_001")
    data_entry = manager.project.experiment("exp_001").data[0]
    assert data_entry.title == "重命名后"
    # 树显示 title
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    assert data_item.text(0) == "重命名后"
    window.close()

def test_double_click_data_keeps_pipeline_and_opens_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双击数据节点:发出 open_path_requested(不跳导入页),中间保持 Pipeline。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    tree.setCurrentItem(data_item)
    window.project_tree._on_double_clicked(data_item, 0)
    assert opened and Path(opened[0]) == raw_dir  # 打开 raw 目录
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline 页
    window.close()


def test_double_click_folder_opens_folder_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双击子文件夹:打开该文件夹目录,中间保持 Pipeline。"""
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
    """右键「打开所在目录」:data/folder 节点发出 open_path_requested(与双击一致)。"""
    from PyQt6.QtWidgets import QMenu

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    raw_dir = manager.data_dir("exp_001", "d_001", "raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(2)  # spectra
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))

    data_menu = window.project_tree._on_context_menu_impl(QMenu(), data_item)
    data_acts = [a for a in data_menu.actions() if a.text() == "打开所在目录"]
    assert len(data_acts) == 1
    data_acts[0].trigger()
    assert opened and Path(opened[0]) == raw_dir

    folder_menu = window.project_tree._on_context_menu_impl(QMenu(), folder_item)
    folder_acts = [a for a in folder_menu.actions() if a.text() == "打开所在目录"]
    assert len(folder_acts) == 1
    folder_acts[0].trigger()
    assert len(opened) == 2 and Path(opened[1]) == spectra_dir
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline 页
    window.close()

def test_spectrum_panel_vertical_layout(qapp: QApplication) -> None:
    """谱图面板上下布局:文件列表在上、查看器在下。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QSplitter

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
    assert splitter.count() == 4  # viewer / 文件列表 / 工具栏 / 峰表
    assert panel.file_list.maximumWidth() > 1000  # 无横向宽度限制
    panel.close()

def test_viewer_internal_vertical_layout(qapp: QApplication) -> None:
    """SpectrumViewer 内部上下布局:plot 在上、控制面板在下。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QSplitter

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
    assert splitter.widget(0) is viewer.plot_area  # 上方谱图区
    viewer.close()

def test_project_dashboard_stats_and_runs(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project Dashboard:统计 + 最近运行。"""
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
    """Experiment Dashboard:数据列表。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    exp_item = tree.topLevelItem(0).child(0).child(0)
    tree.setCurrentItem(exp_item)
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page.data_table.rowCount() >= 1
    window.close()


def test_run_history_dialog(tmp_path: Path, qapp: QApplication) -> None:
    """运行历史对话框:列表 + 详情。"""
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
    """谱图-峰表联动:峰表加载 + 双向高亮/选中。"""
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
    """有峰表时「导出 Poky」可用,生成 .list 且含 header/峰行。"""
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
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.export_poky_button.isEnabled()

    out = tmp_path / "out.list"
    from gui.peaks_io import export_peaks_poky, load_peaks

    export_peaks_poky(out, load_peaks(peaks / "exp_001-d_001.csv"))
    content = out.read_text(encoding="utf-8")
    assert "G1" in content and "115.0" in content and "8.0" in content
    panel.close()


def test_export_poky_button_disabled_without_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无峰表时「导出 Poky」禁用。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert not panel.export_poky_button.isEnabled()
    panel.close()


def test_peak_linkage_via_load_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_peaks 统一加载后行数展示与联动高亮不受影响。"""
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
    assert panel.peak_table.rowCount() == 2
    assert len(panel.viewer._peaks) == 2
    panel.peak_table.selectRow(1)
    assert panel.viewer._selected_peak == 1
    panel.close()

def test_spectrum_auto_shown_on_data_select(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选中数据后自动显示第一张谱图(有谱图时);重复刷新不重复加载。"""
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
    assert panel.viewer.layer_list.count() == 1  # 自动加载
    assert panel._current_spectrum is not None
    panel.refresh()
    assert panel.viewer.layer_list.count() == 1  # 不重复加载
    panel.close()

def test_folder_node_shows_files(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw 等子文件夹节点下拉显示目录内文件。"""
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
    """树中谱图文件双击 → 右侧谱图面板直接显示。"""
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
    """独立谱图查看器默认打开路径=当前数据 spectra 目录。"""
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
    """B2G-002:run_step 传 data_id 时作用于选中数据(非首个)。"""
    monkeypatch.setattr("threading.Thread", SyncThread)
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    manager.import_data("exp_001", "/sampleE")
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
    """多谱叠加:reset_view 显示所有谱的联合范围。"""
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
    assert x_range[1] >= 255 and y_range[1] >= 63  # 覆盖两张谱
    viewer.close()

def test_rename_project_to_sample_wording(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「项目」用户可见措辞统一为「样本」:菜单 / 欢迎页 / 上下文条。"""
    from gui.welcome_page import _FallbackWorkspaceManager

    workspace = tmp_path / "ws2"
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(workspace)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(workspace),
    )
    monkeypatch.setattr(
        "gui.welcome_page.workspace_manager",
        lambda: _FallbackWorkspaceManager(workspace),
    )
    window = MainWindow()
    # 未打开样本:上下文条与欢迎页入口文案
    assert window.context_bar.text() == "未打开样本"
    assert window.center_panel.welcome_page.new_button.text() == "新建样本..."
    # 菜单栏:「实验(&E)」菜单,不含「项目/样本管理/添加/删除样本」
    menus = [action.text() for action in window.menuBar().actions()]
    assert "实验(&E)" in menus
    assert not any("项目" in text for text in menus if text)
    experiment_menu = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "实验(&E)"
    )
    labels = [action.text() for action in experiment_menu.actions()]
    assert "新建实验..." in labels
    assert "样本管理" not in labels
    assert "添加样本..." not in labels
    assert "删除样本..." not in labels
    window.close()
