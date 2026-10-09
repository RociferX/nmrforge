"""GUI: Peak positioning method control for peak picking step (Parabolic / 2D Gaussian) +
persistence by data.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.pipeline_panel import PipelinePanel


class _FakeController:
    """Minimal stand-in: data_facts sets ndim, which decides whether Gaussian is available."""

    def __init__(self, ndim: int) -> None:
        self.ndim = int(ndim)
        self.last_localization = ""

    def data_facts(self, exp_id: str, data_id: str) -> dict:
        return {"ndim": self.ndim, "direct_nucleus": "1H", "is_nus": False}

    def pick_peaks(
        self,
        _data,
        *,
        exp_id: str,
        data_id: str,
        sigma_multiplier: float,
        localization_method: str,
    ) -> dict:
        self.last_localization = localization_method
        return {"status": "success"}


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()




def _manager(tmp_path: Path):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/bruker/1")
    return manager, entry.id, data.id


def test_peak_row_has_no_localization_selector(tmp_path: Path, qapp: QApplication) -> None:
    """Regression coverage: test peak row has no localization selector."""
    manager, exp_id, data_id = _manager(tmp_path)
    panel = PipelinePanel(manager, _FakeController(2))
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    assert not hasattr(row, "localization_label")
    assert not hasattr(row, "localization_combo")
    assert row.get_localization_method() == "parabolic"
    panel.close()


def test_threshold_control_keeps_its_status_gate_without_localization_ui(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Regression coverage: test threshold control keeps its status gate without localization ui."""
    manager, exp_id, data_id = _manager(tmp_path)
    panel = PipelinePanel(manager, _FakeController(2))
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    row.set_status("LOCKED")
    assert row.threshold_spin.isEnabled() is False
    for status in ("READY", "SUCCESS", "OUTDATED", "FAILED"):
        row.set_status(status)
    row.set_status("SUCCESS")
    assert row.threshold_spin.isEnabled() is True
    panel.refresh()
    panel.close()


def test_fixed_localization_is_safe_for_3d_data(tmp_path: Path, qapp: QApplication) -> None:
    """Regression coverage: test fixed localization is safe for 3d data."""
    manager, exp_id, data_id = _manager(tmp_path)

    panel = PipelinePanel(manager, _FakeController(3))
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["peaks"]
    assert not hasattr(row, "localization_combo")
    assert row.get_localization_method() == "parabolic"
    panel.close()


def test_stale_gaussian_in_ui_state_falls_back_to_parabolic(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Regression coverage: test stale gaussian in ui state falls back to parabolic."""
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
    row = panel._rows["peaks"]
    assert row.get_localization_method() == "parabolic"
    assert not hasattr(row, "localization_combo")

    panel._ndim_cache[(exp_id, data_id)] = 3
    panel.refresh()
    assert row.get_localization_method() == "parabolic"
    panel.close()


def test_peak_run_passes_the_fixed_parabolic_method(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, exp_id, data_id = _manager(tmp_path)
    controller = _FakeController(2)
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", exp_id, data_id)
    monkeypatch.setattr(
        "gui.pipeline_panel.compute_data_step_statuses",
        lambda *_args, **_kwargs: {"peaks": "READY"},
    )
    monkeypatch.setattr("threading.Thread", _SyncThread)

    panel._on_run_requested("peaks")

    assert controller.last_localization == "parabolic"
    panel.close()


def test_switching_data_keeps_each_threshold_separate(tmp_path: Path, qapp: QApplication) -> None:
    """Regression coverage: test switching data keeps each threshold separate."""
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
    row = panel._rows["peaks"]
    assert row.get_localization_method() == "parabolic"
    row.threshold_spin.setValue(21.0)
    first_state = load_ui_state(manager, entry.id, first.id)["peaks"]
    assert float(first_state["threshold"]) == pytest.approx(21.0)

    panel.set_selection("data", entry.id, second.id)
    assert panel._rows["peaks"].get_localization_method() == "parabolic"
    second_state = load_ui_state(manager, entry.id, second.id)["peaks"]
    assert float(second_state["threshold"]) == pytest.approx(25.0)

    panel.set_selection("data", entry.id, first.id)
    assert panel._rows["peaks"].get_localization_method() == "parabolic"
    assert panel._rows["peaks"].threshold_spin.value() == pytest.approx(21.0)
    first_again = load_ui_state(manager, entry.id, first.id)["peaks"]
    assert float(first_again["threshold"]) == pytest.approx(21.0)
    panel.close()
