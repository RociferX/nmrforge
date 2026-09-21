"""Reprocessing entry test: SUCCESS step displays "reprocessing" and triggers a rerun."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.log_panel import LogPanel
from gui.pipeline_panel import PipelinePanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class SyncThread:
    """Turn the background thread into synchronous execution (consistent with test_gui_layout)."""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _FakeController:
    """Record the step call; do not write the product (the status will still be inferred based on
    the existing product after re-running)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_fid(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_fid")
        return "/tmp/x.fid"

    def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_spectrum")
        return "/tmp/x.ft2"

    def pick_peaks(
        self, data, exp_id=None, data_id=None, sigma_multiplier=None
    ) -> dict:
        self.calls.append("pick_peaks")
        return {"status": "success", "peak_count": 1}


def _manager_with_artifacts(tmp_path: Path):
    """Experiment type + sample data + full set of products (fid/Spectrum/peak table/Report), no
    fingerprint status."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{exp_id}-{data_id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, fid)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2")
    manager.set_data_spectrum(exp_id, data_id, ft2)
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / f"{exp_id}-{data_id}.csv").write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,G1\n",
        encoding="utf-8",
    )
    report = manager.data_dir(exp_id, data_id, "report")
    report.mkdir(parents=True, exist_ok=True)
    (report / "report.html").write_text("<html>ok</html>", encoding="utf-8")
    manager.save()
    return manager, exp_id, data_id


def test_success_steps_show_reprocess_button(
    tmp_path: Path, qapp: QApplication
) -> None:
    """SUCCESS The step displays the "Reprocess" entry."""
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    for step_id in ("fid", "peaks"):
        assert panel._rows[step_id].run_button.text() == "reprocess"
        assert not panel._rows[step_id].run_button.isHidden()
    # 0.2.163-patch5: spectrum remove "re-optimisation" + "re-run final script".
    spectrum_row = panel._rows["spectrum"]
    assert spectrum_row.run_button.text() == "re-optimisation"
    assert not spectrum_row.run_button.isHidden()
    assert not spectrum_row.rerun_final_button.isHidden()
    panel.close()


def test_run_logs_go_to_data_scope(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29d: The running log is executed according to the target data scope (the string
    is not switched according to the selection)."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("threading.Thread", SyncThread)
    try:
        manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
        controller = _FakeController()
        panel = PipelinePanel(manager, controller)
        log = LogPanel()
        panel.log_message.connect(log.append)
        panel.log_scoped.connect(log.append)
        panel.set_selection("data", exp_id, data_id)

        scoped: list[tuple[str, str]] = []
        panel.log_scoped.connect(
            lambda msg, scope: scoped.append((msg, scope))
        )
        panel._on_run_requested("spectrum")
        assert scoped, "Should have scope log"
        expected_scope = f"data:{exp_id}:{data_id}"
        for _msg, scope in scoped:
            assert scope == expected_scope, (scope, expected_scope)
        # Group scope helper.
        assert (
            panel._run_log_scope(exp_id, data_id) == expected_scope
        )
        assert (
            panel._run_log_scope(exp_id, "", "g1") == f"group:{exp_id}:g1"
        )
        panel.close()
        log.close()
    finally:
        monkeypatch.undo()


def test_run_worker_thread_refreshes_via_queued_signal(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-patch29c: After the real thread has finished running, it returns to the main thread
    through the queue signal for refresh (no more touching controls across threads). In the old
    code, worker finally directly self.refresh()/LogPanel.append triggers QBasicTimer::start:
    Timers cannot be started from another thread and gets stuck."""
    import time

    from qtcompat.QtCore import QEventLoop, QTimer

    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    controller = _FakeController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", exp_id, data_id)

    done = []
    panel.run_finished.connect(lambda: done.append(True))

    class _SlowController(_FakeController):
        def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
            self.calls.append("generate_spectrum")
            time.sleep(0.05)  # Make threads truly asynchronous.
            return "/tmp/x.ft2"

    panel.controller = _SlowController()
    panel._on_run_requested("spectrum")

    loop = QEventLoop()
    QTimer.singleShot(2000, loop.quit)
    while not done:
        loop.exec()
    # The queue signal has been processed: the main thread refresh is completed and the status is no
    # longer RUNNING.
    assert panel._run_active is False
    assert panel._rows["spectrum"].status_label.text().startswith("✓")
    panel.close()
    log.close()


def test_reprocess_spectrum_invokes_controller(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click "Reprocess" to call the corresponding ProcessingController method."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    controller = _FakeController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["spectrum"].run_button.text() == "re-optimisation"
    panel._on_run_requested("spectrum")
    assert controller.calls == ["generate_spectrum"]
    # After re-running, the step is still SUCCESS (the fake controller does not write the product,
    # and the fingerprint status does not change).
    assert panel._rows["spectrum"].status_label.text().startswith("✓")
    assert panel._rows["spectrum"].run_button.text() == "re-optimisation"
    panel.close()
    log.close()


def test_reprocess_peaks_and_fid_available(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peak picking/generate FID also provides reprocessing entrance (click to trigger the
    corresponding method)."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    controller = _FakeController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", exp_id, data_id)
    panel._on_run_requested("peaks")
    panel._on_run_requested("fid")
    assert controller.calls == ["pick_peaks", "generate_fid"]
    panel.close()


def test_rerun_final_applies_latest_ext(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.163-patch5: Rerun the final script and directly change the final script EXT window,
    leaving the other parameters unchanged."""
    from gui.pipeline_panel import PipelinePanel

    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    # Generate a final script (uniform), including phase / window / baseline and other parameters.
    process = manager.data_dir(exp_id, data_id, "process")
    script = process / f"{data_id}_process.com"
    script.write_text(
        "#!/bin/csh\n"
        "| nmrPipe -fn SP -off 0.5 -end 0.95 -pow 2 -c 0.5 \\\n"
        "| nmrPipe -fn FT -auto \\\n"
        "| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \\\n"
        "| nmrPipe -fn PS -p0 10 -p1 1.5 -di \\\n"
        "| nmrPipe -fn POLY -auto -time \\\n",
        encoding="utf-8",
    )
    manager.save()

    calls: list[str] = []
    ran_scripts: dict = {}

    class _CaptureController(_FakeController):
        def run_manual_spectrum(
            self, data, scripts, exp_id=None, data_id=None, progress=None
        ) -> str:
            calls.append("run_manual_spectrum")
            ran_scripts.update(scripts)
            return "/tmp/x.ft2"

    panel = PipelinePanel(manager, _CaptureController())
    panel.set_selection("data", exp_id, data_id)
    # User sets the final run direct dimension range 8.0-6.0 (applying this range to the
    # optimisation process is enabled by default).
    panel._final_ext[(exp_id, data_id)] = ("8.0", "6.0", True)
    panel._on_rerun_final_requested("spectrum")
    assert calls == ["run_manual_spectrum"]
    # The content of the script being run: EXT has been updated to 8.0-6.0, and other parameters
    # (SP/FT/PS/POLY) remain unchanged.
    ran_content = ran_scripts.get(f"{data_id}_process.com", "")
    assert "-x1 8.0ppm -xn 6.0ppm" in ran_content
    assert "-x1 10.5ppm -xn 6.5ppm" not in ran_content
    assert "-p0 10 -p1 1.5" in ran_content  # Phase not moving.
    assert "POLY -auto -time" in ran_content  # Baseline/diagnosis not moved.
    assert "SP -off 0.5 -end 0.95" in ran_content  # The window function is not moved.
    panel.close()



def test_reprocess_downstream_becomes_outdated(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Reprocess spectrum and register fingerprint -> Downstream peak pick flag expired."""
    from gui.pipeline_state import record_step_success

    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    for step in ("fid", "spectrum", "peaks"):
        record_step_success(manager, exp_id, data_id, step)
    ft2 = manager.data_dir(exp_id, data_id, "spectra") / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["spectrum"].run_button.text() == "re-optimisation"
    assert panel._rows["peaks"].status_label.text().startswith("!")
    panel.close()
