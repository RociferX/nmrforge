"""GUI 跨对象私有访问守卫 + 公开接口冒烟(0.2.199-补29hz)。

背景:gui/ 里曾大量出现 `self.viewer._update_levels()`、
`self.project_tree._data_id_of(...)`、`self.import_panel._on_import()`、
`self.center_panel._manager = ...` 这类跨对象私有访问——被访问方一改名就
静默失效(不报错、功能悄悄不生效)。现在统一改为公开接口,本测试负责防止
回退写法再次混入。

注:只扫描 gui/。viewer/ 内部有同文件辅助类(如 _LabelOverlay)读取
SpectrumViewer 私有成员,属实现细节,不在本守卫范围。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.project import ProjectManager  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CROSS_PRIVATE_RE = re.compile(r"self\.[a-z_][a-z0-9_]*\._[a-zA-Z]")

# 允许名单:如确有必要(且经评审),在此登记 "相对路径:行内容片段"
ALLOWED: set[str] = set()


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def host(qapp: QApplication):
    """控件宿主:测试结束整体销毁,避免顶层控件残留(PyQt6 收尾崩溃)。"""
    from PyQt6.QtWidgets import QWidget

    widget = QWidget()
    yield widget
    widget.deleteLater()
    QApplication.processEvents()


def test_no_cross_object_private_access_in_gui() -> None:
    offenders = []
    for path in sorted((ROOT / "gui").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not CROSS_PRIVATE_RE.search(line):
                continue
            if f"{rel}:{line.strip()}" in ALLOWED:
                continue
            offenders.append(f"{rel}:{lineno} {line.strip()}")
    assert not offenders, "跨对象私有访问(请改走公开接口):\n" + "\n".join(offenders)


def test_controller_data_facts_is_public(tmp_path: Path) -> None:
    """GUI 门控统一走 ProcessingController.data_facts(读取失败返回空 dict)。"""
    from gui.processing import ProcessingController

    manager = ProjectManager.create_project(tmp_path / "facts", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/bruker/1")
    controller = ProcessingController(manager)
    facts = controller.data_facts(exp.id, data.id)
    assert isinstance(facts, dict)  # 源目录不存在 → 允许空 dict 降级
    assert not hasattr(controller, "data_facts_private")


def test_panel_public_accessors(
    tmp_path: Path, qapp: QApplication, host
) -> None:
    from core.workspace import WorkspaceManager
    from gui.pipeline_panel import PipelinePanel
    from gui.project_tree import ProjectTreePanel

    # 树只列工作区内的项目,测试用临时工作区
    workspace = WorkspaceManager(root=tmp_path / "ws")
    manager = workspace.create_project("acc")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/1")

    pipeline = PipelinePanel(manager, parent=host)
    pipeline.set_selection("data", exp.id, data.id)
    assert pipeline.current_data_id == data.id

    tree = ProjectTreePanel(manager, workspace=workspace, parent=host)
    assert tree.current_data_id() == ""
    tree.select_data(exp.id, data.id)
    assert tree.current_data_id() == data.id


def test_viewer_public_accessors(qapp: QApplication, host) -> None:
    from viewer.spectrum3d_panel import Spectrum3DPanel
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer(host)
    assert viewer.primary_spectrum is None
    assert viewer.peak_labels_visible is True
    viewer.refresh_levels()  # 空视图下也应安全
    panel = Spectrum3DPanel(host)
    assert panel.spectrum3d is None


def test_group_panel_public_refresh(qapp: QApplication, host) -> None:
    from gui.group_panel import GroupBatchPanel

    page = GroupBatchPanel(host)
    assert page.current_group_id == ""
    page.refresh()
