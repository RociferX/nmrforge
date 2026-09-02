"""GUI 骨架测试:主窗口建立在 ProjectManager/WorkspaceManager 之上(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog

from core.project import ProjectManager
from gui.dialogs import ConfirmDialog
from gui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _build_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> ProjectManager:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    manager = ProjectManager.create_project(ws / "proj", "demo")
    manager.add_experiment("/sampleD", title="HSQC")
    manager.add_experiment("/sampleE", title="HNCACB")
    manager.save()
    if monkeypatch is not None:
        monkeypatch.setattr(
            "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
        )
        monkeypatch.setattr(
            "core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(ws)
        )
    return manager


class _TempWorkspace:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def list_projects(self) -> list[Path]:
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs):
        return ProjectManager.create_project(self.root / name, name, **kwargs)


def test_window_shows_experiments_from_project(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _build_manager(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert window.experiment_tree.topLevelItemCount() == 2
    first = window.experiment_tree.topLevelItem(0)
    assert first.text(0) == "exp_001"
    assert first.text(1) == "HSQC"
    assert "demo" in window.windowTitle()
    window.close()


def test_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.experiment_tree.topLevelItemCount() == 0
    assert "欢迎" in window.windowTitle()
    assert window.center_panel.welcome_page is not None
    window.close()


def test_new_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未打开项目时,「新建项目」为欢迎页内联命名(不弹窗)。"""
    # 新建项目落在默认工作区(避免污染真实 ~/NMRForgeWorkspace)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager",
        lambda: _WorkspaceStub(workspace),
    )
    window = MainWindow()

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window.new_project()
    page = window.center_panel.welcome_page
    assert not page._name_edit.isHidden()  # 页内输入行出现,非弹窗
    page._name_edit.setText("demo")
    page._commit_name()
    assert window.manager.project is not None
    assert window.manager.project.name == "demo"
    assert window.experiment_tree.topLevelItemCount() == 0
    window.close()


def test_new_project_tree_inline_when_project_open(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """项目已打开时,「新建项目」在项目树内联命名(不弹窗)。"""
    manager = _build_manager(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager",
        lambda: _WorkspaceStub(workspace),
    )

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window = MainWindow(manager=manager)
    window.new_project()
    assert window.project_tree._pending_kind == "project"
    window.project_tree._commit_pending_create("demo2")
    assert window.manager.project is not None
    assert window.manager.project.name == "demo2"
    window.close()


class _WorkspaceStub:
    """测试用工作区桩:在工作区目录下创建项目。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def create_project(self, name: str, **kwargs):
        return ProjectManager.create_project(self.root / name, name, **kwargs)

    def list_projects(self) -> list[Path]:
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )


def test_open_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    ProjectManager.create_project(root, "demo")
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(root)),
    )
    window = MainWindow()
    window.open_project()
    assert window.manager.project is not None
    assert window.manager.project.name == "demo"
    window.close()


def test_add_experiment_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「添加实验类型」= 新建空白实验类型:项目树内联命名(不弹窗)。"""
    manager = _build_manager(tmp_path, monkeypatch)

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window = MainWindow(manager=manager)
    window.add_experiment()
    assert window.project_tree._pending_kind == "experiment"
    window.project_tree._commit_pending_create("3D HNCACB")
    assert window.experiment_tree.topLevelItemCount() == 3
    last = window.experiment_tree.topLevelItem(2)
    assert last.text(0) == "exp_003"
    assert last.text(1) == "3D HNCACB"
    entry = manager.project.experiment("exp_003")
    assert entry is not None and len(entry.data) == 0  # 空白实验类型无样品数据
    window.close()


def test_delete_experiment_action_keeps_audit(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _build_manager(tmp_path, monkeypatch)
    manager.start_run("exp_001", workflow_ref="hsqc_standard")
    monkeypatch.setattr(
        ConfirmDialog,
        "confirm",
        staticmethod(lambda *args, **kwargs: True),
    )

    def fake(path, fallback_dir, rel=None):
        return path

    monkeypatch.setattr("core.project.manager.send_to_trash", fake)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    window.delete_experiment()
    assert window.experiment_tree.topLevelItemCount() == 1  # 报告树非本次刷新范围
    assert manager.project.experiment("exp_001").trashed is True  # 软删除
    assert manager.project is not None
    assert len(manager.project.workflow_runs) == 1  # 审计保留
    window.close()


def test_recent_menu_persists(tmp_path: Path, qapp: QApplication) -> None:
    root = tmp_path / "ws" / "proj"
    (tmp_path / "ws").mkdir(exist_ok=True)
    ProjectManager.create_project(root, "demo")
    recent = __import__(
        "core.project.recent", fromlist=["JsonRecentProjectsStore"]
    ).JsonRecentProjectsStore(tmp_path / "recent.json")
    window = MainWindow(recent=recent)
    window._open_root(root)
    assert recent.list() == [str(root.resolve())]
    assert len(window.recent_menu.actions()) == 1
    window.close()


def test_import_workflow_e2e(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端:真实 Bruker fixture 目录经 workflow.import_workflow.import_data 导入。"""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(ws)
    )
    manager = ProjectManager.create_project(ws / "proj", "demo")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "acqus").write_text(
        "##SIMPLE 1\n##NUC1 1H\n##NUC2 15N\n##TD 1024\n",
        encoding="utf-8",
    )

    class SyncThread:
        def __init__(self, target=None, daemon=None) -> None:
            self._target = target

        def start(self) -> None:
            self._target()

    monkeypatch.setattr("threading.Thread", SyncThread)
    # 0.2.86:导入完成提示可能含质量警告,测试里 stub 模态对话框避免阻塞
    monkeypatch.setattr(
        "gui.dialogs.InfoDialog.show_info",
        staticmethod(lambda *args, **kwargs: None),
    )
    window = MainWindow(manager=manager)
    window.add_experiment_via_import(str(dataset), title="HSQC")
    assert manager.project is not None
    entry = manager.project.experiment("exp_001")
    assert entry is not None and entry.title == "HSQC"
    assert entry.data and entry.data[0].id == "d_001"
    assert manager.data_metadata_path("exp_001", "d_001").is_file()
    assert manager.data_dir("exp_001", "d_001", "raw").is_dir()
    assert any(r.workflow_ref == "import" for r in manager.project.workflow_runs)
    assert window.project_tree.current_experiment_id() == "exp_001"
    window.close()
