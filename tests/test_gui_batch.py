"""批量处理测试:批量组标记、批量导入、组内整组运行、树标记显示。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.pipeline_panel import PipelinePanel
from gui.pipeline_state import (
    batch_data_ids,
    batch_id,
    batch_ids_in_experiment,
    clear_batch_id,
    next_batch_id,
    set_batch_id,
)
from gui.processing import ProcessingController


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_experiment(tmp_path: Path) -> tuple[ProjectManager, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    manager.import_data(entry.id, "/fake/1")
    manager.save()
    return manager, entry.id


def test_batch_helpers(tmp_path: Path) -> None:
    manager, exp_id = _manager_with_experiment(tmp_path)
    manager.import_data(exp_id, "/fake/2")
    manager.save()
    data_ids = [d.id for d in manager.project.experiment(exp_id).data]
    for data_id in data_ids:
        assert batch_id(manager, exp_id, data_id) == ""
    # 第一次批量导入 → B1,绑定两组数据
    assert next_batch_id(manager, exp_id) == "B1"
    set_batch_id(manager, exp_id, data_ids[0], "B1")
    set_batch_id(manager, exp_id, data_ids[1], "B1")
    assert batch_id(manager, exp_id, data_ids[0]) == "B1"
    assert batch_data_ids(manager, exp_id, "B1") == data_ids
    # 第二次批量导入 → B2
    assert next_batch_id(manager, exp_id) == "B2"
    assert batch_ids_in_experiment(manager, exp_id) == ["B1"]
    # 移出组:恢复单一数据
    clear_batch_id(manager, exp_id, data_ids[1])
    assert batch_data_ids(manager, exp_id, "B1") == [data_ids[0]]
    assert batch_id(manager, exp_id, data_ids[1]) == ""


def test_batch_import_marks_group(tmp_path: Path, bruker_dir: Path) -> None:
    """批量导入:多个目录导入同一实验,标记同一 batch_id,多次导入序号递增。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    manager.save()
    folders = [
        str(bruker_dir / "hsqc_2d"),
        str(bruker_dir / "nus_2d"),
    ]
    controller = ProcessingController(manager)
    result = controller.batch_import(entry.id, folders)
    assert result["batch_id"] == "B1"
    assert all(item["ok"] for item in result["results"])
    data_ids = [item["data_id"] for item in result["results"]]
    assert len(data_ids) == 2
    assert batch_data_ids(manager, entry.id, "B1") == data_ids
    # 再批量导入一次 → B2
    result2 = controller.batch_import(
        entry.id, [str(bruker_dir / "hsqc_small")]
    )
    assert result2["batch_id"] == "B2"
    assert batch_ids_in_experiment(manager, entry.id) == ["B1", "B2"]


class _FakeController:
    """记录每个 data_id 的步骤调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def set_manager(self, manager) -> None:
        pass

    def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append(data_id or "")
        return "/tmp/x.ft2"


class _ProgressController(_FakeController):
    """带 progress 回调的假控制器(验证阶段日志进面板)。"""

    def generate_spectrum(
        self, data, exp_id=None, data_id=None, progress=None
    ) -> str:
        self.calls.append(data_id or "")
        if progress:
            progress("NUS 数据: 开始 SMILE 重构(含直接维相位)")
        return "/tmp/x.ft2"


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def test_pipeline_group_run_applies_to_all(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中间处理页操作:批量组内所有数据依次执行,上下文显示批量标记。"""
    monkeypatch.setattr("threading.Thread", _SyncThread)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("batch")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    manager.save()
    set_batch_id(manager, entry.id, data1.id, "B1")
    set_batch_id(manager, entry.id, data2.id, "B1")
    controller = _FakeController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", entry.id, data1.id)
    assert "批量 B1" in panel.context_label.text()
    panel._on_run_requested("spectrum")
    assert controller.calls == [data1.id, data2.id]
    panel.close()


class _TempWorkspace:
    """指向临时目录的工作区桩(树面板列出项目用)。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list_projects(self):
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root


def test_tree_data_label_shows_batch_marker(
    tmp_path: Path, qapp: QApplication
) -> None:
    """批量导入的数据在树中带批量标记显示。"""
    from gui.project_tree import ProjectTreePanel

    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    manager.save()
    set_batch_id(manager, entry.id, data.id, "B1")
    panel = ProjectTreePanel(manager, workspace=_TempWorkspace(ws))
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    assert "[B1]" in data_item.text(0)
    panel.close()


def test_pipeline_status_shows_selected_data(
    tmp_path: Path, qapp: QApplication
) -> None:
    """导入新数据后,中间状态按当前选中数据而非首条数据。"""
    from gui.pipeline_panel import compute_data_step_statuses

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("multi")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    # data1 已处理(fid+ft2),data2 空白
    process = manager.data_dir(entry.id, data1.id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{entry.id}-{data1.id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(entry.id, data1.id, fid)
    spectra = manager.data_dir(entry.id, data1.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{entry.id}-{data1.id}.ft2"
    ft2.write_bytes(b"ft2")
    manager.set_data_spectrum(entry.id, data1.id, ft2)
    manager.save()

    st1 = compute_data_step_statuses(manager, entry.id, data1.id)
    assert st1["fid"] == "SUCCESS" and st1["spectrum"] == "SUCCESS"
    st2 = compute_data_step_statuses(manager, entry.id, data2.id)
    assert st2["fid"] == "READY" and st2["spectrum"] == "LOCKED"
    # 面板选中 data2 时显示其状态(不是 data1 的已完成状态)
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", entry.id, data2.id)
    assert panel._rows["fid"].status_label.text().startswith("▶")
    assert panel._rows["spectrum"].status_label.text().startswith("🔒")
    panel.close()


def test_batch_subfolder_scan(tmp_path: Path) -> None:
    """批量添加总文件夹时自动检查子文件夹中的 Bruker 数据集。"""
    from gui.dashboards import ExperimentDashboard

    root = tmp_path / "batch_root"
    (root / "hsqc").mkdir(parents=True)
    (root / "hsqc" / "acqus").write_text("x")
    (root / "nested" / "hnca").mkdir(parents=True)
    (root / "nested" / "hnca" / "acqus").write_text("x")
    (root / "notes.txt").write_text("not a dataset")
    found = ExperimentDashboard._bruker_datasets_under(root)
    names = {p.name for p in found}
    assert names == {"hsqc", "hnca"}
    # 直接选择数据集目录 → 返回自身
    direct = ExperimentDashboard._bruker_datasets_under(root / "hsqc")
    assert [p.name for p in direct] == ["hsqc"]


def test_experiment_dashboard_single_batch_groups(
    qapp: QApplication,
) -> None:
    """实验页:单个导入与批量处理分组展示(视觉区分)。"""
    from PyQt6.QtWidgets import QGroupBox

    from gui.dashboards import ExperimentDashboard

    page = ExperimentDashboard()
    assert isinstance(page.single_group, QGroupBox)
    assert isinstance(page.batch_group, QGroupBox)
    assert page.single_group.title() == "单个导入"
    assert page.batch_group.title() == "批量处理"
    page.close()


def test_project_single_click_opens(
    tmp_path: Path, qapp: QApplication
) -> None:
    """单击项目节点即打开(无需双击);当前项目不重复打开。"""
    from gui.project_tree import ProjectTreePanel

    ws = tmp_path / "ws"
    ws.mkdir()
    ProjectManager.create_project(ws / "projA", "projA")
    manager = ProjectManager.create_project(ws / "projB", "projB")
    manager.create_experiment()
    manager.save()
    panel = ProjectTreePanel(manager, workspace=_TempWorkspace(ws))
    opened: list[str] = []
    panel.open_project_requested.connect(lambda p: opened.append(p))
    workspace_item = panel.tree.topLevelItem(0)

    def project_item(name: str):
        for index in range(workspace_item.childCount()):
            child = workspace_item.child(index)
            if child.text(0) == name:
                return child
        return None

    proj_a = project_item("projA")
    proj_b = project_item("projB")
    assert proj_a is not None and proj_b is not None
    panel._on_item_clicked(proj_a, 0)  # 非当前项目 → 打开
    assert opened and Path(opened[0]).name == "projA"
    panel._on_item_clicked(proj_b, 0)  # 当前项目 → 不重复打开
    assert len(opened) == 1
    panel.close()


def test_pipeline_progress_logs_to_panel(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行生成谱图时阶段进展经 progress 进入日志面板。"""
    from gui.log_panel import LogPanel

    monkeypatch.setattr("threading.Thread", _SyncThread)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("prog")
    data = manager.import_data(entry.id, "/fake/1")
    manager.save()
    controller = _ProgressController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", entry.id, data.id)
    panel._on_run_requested("spectrum")
    assert "SMILE 重构" in log.text.toPlainText()
    panel.close()
    log.close()


def test_welcome_single_click_opens(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """欢迎页最近项目单击打开。"""
    from gui.welcome_page import WelcomePage, _FallbackWorkspaceManager

    ws = tmp_path / "ws"
    ws.mkdir()
    ProjectManager.create_project(ws / "projA", "projA")
    monkeypatch.setattr(
        "gui.welcome_page.workspace_manager",
        lambda: _FallbackWorkspaceManager(ws),
    )
    page = WelcomePage()
    opened: list[str] = []
    page.open_project_requested.connect(lambda p: opened.append(p))
    item = page.recent_list.item(0)
    page._on_recent_clicked(item)
    assert opened and Path(opened[0]).name == "projA"
    page.close()
