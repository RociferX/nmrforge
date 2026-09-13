"""GUI:峰挑选步骤的峰定位方法控件(抛物线 / 2D 高斯)+ 按数据持久化。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.pipeline_panel import PipelinePanel


class _FakeController:
    """最小替身:data_facts 决定 ndim,从而决定高斯是否可用。"""

    def __init__(self, ndim: int) -> None:
        self.ndim = int(ndim)

    def data_facts(self, exp_id: str, data_id: str) -> dict:
        return {"ndim": self.ndim, "direct_nucleus": "1H", "is_nus": False}


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager(tmp_path: Path):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/bruker/1")
    return manager, entry.id, data.id


def test_localization_combo_defaults_to_parabolic(
    tmp_path: Path, qapp: QApplication
) -> None:
    """默认抛物线;两个选项齐备(Parabolic / Gaussian Fit)。"""
    manager, exp_id, data_id = _manager(tmp_path)
    panel = PipelinePanel(manager, _FakeController(2))
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    assert row.localization_combo.count() == 2
    assert [row.localization_combo.itemData(i) for i in range(2)] == [
        "parabolic",
        "gaussian",
    ]
    assert row.get_localization_method() == "parabolic"
    # 可用性与阈值控件同一闸门(未选中可运行数据时一并禁用)
    assert row.localization_combo.isEnabled() == row.threshold_spin.isEnabled()
    panel.close()


def test_localization_select_gaussian_emits_and_persists(
    tmp_path: Path, qapp: QApplication
) -> None:
    """选高斯:发信号 + 写进该数据 ui_state(与阈值同分区不互相覆盖)。"""
    from gui.per_data_records import load_ui_state

    manager, exp_id, data_id = _manager(tmp_path)
    panel = PipelinePanel(manager, _FakeController(2))
    seen: list[str] = []
    panel._rows["peaks"].localization_changed.connect(seen.append)
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    row.threshold_spin.setValue(18.0)
    row.set_localization_method("gaussian")
    row.localization_changed.emit("gaussian")  # 模拟用户在选择框里改选
    assert seen == ["gaussian"]
    state = load_ui_state(manager, exp_id, data_id).get("peaks") or {}
    assert state["localization_method"] == "gaussian"
    assert float(state["threshold"]) == pytest.approx(18.0)  # 阈值没被清掉
    panel.close()


def test_localization_gaussian_disabled_for_3d(
    tmp_path: Path, qapp: QApplication
) -> None:
    """非 2D:高斯项禁用 + 明确提示;已选高斯则回退抛物线。"""
    from core.peaks.localize import GAUSSIAN_UNSUPPORTED_MESSAGE

    manager, exp_id, data_id = _manager(tmp_path)
    panel = PipelinePanel(manager, _FakeController(3))
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    model = row.localization_combo.model()
    item = model.item(row.localization_combo.findData("gaussian"))
    assert item is not None and item.isEnabled() is False
    assert GAUSSIAN_UNSUPPORTED_MESSAGE in row.localization_combo.toolTip()
    # 即使之前存了高斯,3D 数据上也会回退抛物线(不静默跑错算法)
    row.set_localization_method("gaussian")
    row.set_localization_supported(False)
    assert row.get_localization_method() == "parabolic"
    panel.close()


def test_localization_restored_from_ui_state_for_2d(
    tmp_path: Path, qapp: QApplication
) -> None:
    """按数据恢复:重新打开面板后 2D 数据仍显示上次选的高斯。"""
    from gui.per_data_records import update_ui_state

    manager, exp_id, data_id = _manager(tmp_path)
    update_ui_state(
        manager,
        exp_id,
        data_id,
        "peaks",
        {"threshold": 35.0, "custom": False, "localization_method": "gaussian"},
    )
    panel = PipelinePanel(manager, _FakeController(2))
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["peaks"].get_localization_method() == "gaussian"
    panel.close()
