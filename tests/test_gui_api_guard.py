"""GUI cross-object private access guard + public interface smoke (0.2.199-patch29hz).

Background: gui/ once had many cross-object private accesses such as
`self.viewer._update_levels()`, `self.project_tree._data_id_of(...)`,
`self.import_panel._on_import()`, `self.center_panel._manager = ...` -- once the
accessed side is renamed they fail silently (no error, the feature quietly stops
working). They are now unified as public interfaces, and this test keeps the fallback
style from creeping back in.

Note: only gui/ is scanned. Inside viewer/, same-file helper classes (such as
_LabelOverlay) read SpectrumViewer private members; that is an implementation detail
and outside this guard's scope.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CROSS_PRIVATE_RE = re.compile(r"self\.[a-z_][a-z0-9_]*\._[a-zA-Z]")

# Allow list: if truly necessary (and reviewed), register "relative path: line content" here
ALLOWED: set[str] = set()


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def host(qapp: QApplication):
    """Widget host: destroyed wholesale at test end, avoiding leftover top-level widgets (Qt
    teardown crash)."""
    from qtcompat.QtWidgets import QWidget

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
    """GUI gating goes through ProcessingController.data_facts (failed read -> empty dict)."""
    from gui.processing import ProcessingController

    manager = ProjectManager.create_project(tmp_path / "facts", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/bruker/1")
    controller = ProcessingController(manager)
    facts = controller.data_facts(exp.id, data.id)
    assert isinstance(facts, dict)  # source dir missing -> empty dict downgrade allowed
    assert not hasattr(controller, "data_facts_private")


def test_panel_public_accessors(
    tmp_path: Path, qapp: QApplication, host
) -> None:
    from core.workspace import WorkspaceManager
    from gui.pipeline_panel import PipelinePanel
    from gui.project_tree import ProjectTreePanel

    # The tree lists only projects inside the workspace, so the test uses a temp workspace
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
    viewer.refresh_levels()  # Should be safe with an empty view too
    panel = Spectrum3DPanel(host)
    assert panel.spectrum3d is None


def test_group_panel_public_refresh(qapp: QApplication, host) -> None:
    from gui.group_panel import GroupBatchPanel

    page = GroupBatchPanel(host)
    assert page.current_group_id == ""
    page.refresh()
