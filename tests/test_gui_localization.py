"""GUI: Peak positioning method control for peak picking step (Parabolic / 2D Gaussian) +
persistence by data."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.pipeline_panel import PipelinePanel


class _FakeController:
    """Minimum substitute: data_facts determines ndim and thus whether Gaussian is available."""

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
    """Default is Parabolic; both options are available (Parabolic / Gaussian Fit)."""
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
    # Availability and threshold controls are on the same gate (disabled when runnable data is not
    # selected).
    assert row.localization_combo.isEnabled() == row.threshold_spin.isEnabled()
    panel.close()


def test_localization_select_gaussian_emits_and_persists(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Select Gaussian: signal + write the data ui_state (the same partition as the threshold does
    not cover each other)."""
    from gui.per_data_records import load_ui_state

    manager, exp_id, data_id = _manager(tmp_path)
    panel = PipelinePanel(manager, _FakeController(2))
    seen: list[str] = []
    panel._rows["peaks"].localization_changed.connect(seen.append)
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    row.threshold_spin.setValue(18.0)
    row.set_localization_method("gaussian")
    row.localization_changed.emit("gaussian")  # Simulate user to change selection in selection box.
    assert seen == ["gaussian"]
    state = load_ui_state(manager, exp_id, data_id).get("peaks") or {}
    assert state["localization_method"] == "gaussian"
    assert float(state["threshold"]) == pytest.approx(18.0)  # The threshold has not been cleared.
    panel.close()


def test_localization_gaussian_disabled_for_3d(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Non-2D: Gaussian item is disabled + clear prompt; if Gaussian is selected, parabola will be
    reverted."""
    from core.peaks.localize import GAUSSIAN_UNSUPPORTED_MESSAGE

    manager, exp_id, data_id = _manager(tmp_path)
    panel = PipelinePanel(manager, _FakeController(3))
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    model = row.localization_combo.model()
    item = model.item(row.localization_combo.findData("gaussian"))
    assert item is not None and item.isEnabled() is False
    assert GAUSSIAN_UNSUPPORTED_MESSAGE in row.localization_combo.toolTip()
    # Even if Gaussian has been saved before, the 3D data will fall back to the parabola (the error
    # algorithm will not run silently).
    row.set_localization_method("gaussian")
    row.set_localization_supported(False)
    assert row.get_localization_method() == "parabolic"
    panel.close()


def test_localization_restored_from_ui_state_for_2d(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Press data recovery: After reopening the panel, the 2D data still displays the last selected
    Gaussian."""
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


def test_switching_data_does_not_copy_previous_localization(
    tmp_path: Path, qapp: QApplication
) -> None:
    """When switching, the threshold signal cannot overwrite the positioning method of the previous
    data to the current data."""
    from gui.per_data_records import load_ui_state, update_ui_state

    manager = ProjectManager.create_project(tmp_path / "switch", "demo")
    entry = manager.create_experiment("HSQC")
    first = manager.import_data(entry.id, "/fake/bruker/1")
    second = manager.import_data(entry.id, "/fake/bruker/2")
    update_ui_state(
        manager,
        entry.id,
        first.id,
        "peaks",
        {"threshold": 20.0, "custom": True, "localization_method": "gaussian"},
    )
    update_ui_state(
        manager,
        entry.id,
        second.id,
        "peaks",
        {"threshold": 25.0, "custom": True, "localization_method": "parabolic"},
    )

    panel = PipelinePanel(manager, _FakeController(2))
    panel.set_selection("data", entry.id, first.id)
    assert panel._rows["peaks"].get_localization_method() == "gaussian"
    panel.set_selection("data", entry.id, second.id)
    assert panel._rows["peaks"].get_localization_method() == "parabolic"
    state = load_ui_state(manager, entry.id, second.id).get("peaks") or {}
    assert state["localization_method"] == "parabolic"
    panel.close()
