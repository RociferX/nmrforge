"""SMILE 扫描 Rank1 入口(0.2.199-补29hz-修3 第 3 步)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.project import ProjectManager  # noqa: E402
from gui.pipeline_panel import PipelinePanel  # noqa: E402
from gui.processing import ProcessingController  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager(tmp_path: Path):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.create_experiment("HNCA")
    data = manager.import_data(exp.id, "/fake/1")
    return manager, exp, data


def test_rank1_button_visible_only_after_success(
    tmp_path: Path, qapp: QApplication
) -> None:
    """SMILE 步骤成功后才有「按 Rank1 重跑」(方案 B:优化不自动出谱)。"""
    from PyQt6.QtWidgets import QWidget

    host = QWidget()
    manager, _exp, _data = _manager(tmp_path)
    panel = PipelinePanel(manager, parent=host)
    row = panel._rows["smile"]

    row.set_status("READY")
    assert not row.rank1_button.isVisibleTo(row)
    row.set_status("SUCCESS")
    assert row.rank1_button.isVisibleTo(row)
    # 其它步骤不留这个按钮
    assert not panel._rows["fid"].rank1_button.isVisibleTo(panel._rows["fid"])
    panel.close()
    host.deleteLater()
    QApplication.processEvents()


def test_rerun_rank1_requires_script(tmp_path: Path) -> None:
    """没有 Rank1 脚本时明确报错,而不是静默重跑别的脚本。"""
    manager, exp, data = _manager(tmp_path)
    controller = ProcessingController(manager)
    with pytest.raises(RuntimeError, match="Rank1"):
        controller.rerun_smile_rank1(exp.id, data.id)
