"""Reprocessing entrance tests: a SUCCESS step shows "reprocess" and triggers a rerun."""

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
    """Run the background thread synchronously (as in test_gui_layout)."""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _FakeController:
    """Record step calls but write no artefacts (a rerun infers status from existing files)."""

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
    """Experiment type + sample data + the full artefact set (fid/spectrum/peak table/report),
    with no fingerprint state.
    """
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
    """A SUCCESS step shows the reprocess entrance."""
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    for step_id in ("fid", "peaks"):
        assert panel._rows[step_id].run_button.text() == "重新处理"
        assert not panel._rows[step_id].run_button.isHidden()
    # 0.2.163-patch5: spectrum splits into "re-optimisation" + "Re-run the final script"
    spectrum_row = panel._rows["spectrum"]
    assert spectrum_row.run_button.text() == "重新优化"
    assert not spectrum_row.run_button.isHidden()
    assert not spectrum_row.rerun_final_button.isHidden()
    panel.close()


def test_run_logs_go_to_data_scope(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-patch29d: run logs land under the target data scope, not the current selection."""
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
        assert scoped, "应有作用域日志"
        expected_scope = f"data:{exp_id}:{data_id}"
        for _msg, scope in scoped:
            assert scope == expected_scope, (scope, expected_scope)
        # group-scope helper
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
    """0.2.199-patch29c: a real thread finishes and refreshes on the main thread through a queued
    signal (widgets are no longer touched across threads).

    The old code called self.refresh()/LogPanel.append directly in the worker's finally block,
    which raised "QBasicTimer::start: Timers cannot be started from another thread" and hung.
    """
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
            time.sleep(0.05)  # make the thread genuinely asynchronous
            return "/tmp/x.ft2"

    panel.controller = _SlowController()
    panel._on_run_requested("spectrum")

    loop = QEventLoop()
    QTimer.singleShot(2000, loop.quit)
    while not done:
        loop.exec()
    # queued signal handled: the main thread has refreshed, the status is no longer RUNNING
    assert panel._run_active is False
    assert panel._rows["spectrum"].status_label.text().startswith("✓")
    panel.close()
    log.close()


def test_reprocess_spectrum_invokes_controller(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clicking "reprocess" calls the matching ProcessingController method."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    controller = _FakeController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["spectrum"].run_button.text() == "重新优化"
    panel._on_run_requested("spectrum")
    assert controller.calls == ["generate_spectrum"]
    # after the rerun the step is still SUCCESS (the fake controller writes no artefacts, so the
    # fingerprint state does not change)
    assert panel._rows["spectrum"].status_label.text().startswith("✓")
    assert panel._rows["spectrum"].run_button.text() == "重新优化"
    panel.close()
    log.close()


def test_reprocess_peaks_and_fid_available(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peak picking / FID generation also offer the reprocess entrance (the click calls it)."""
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
    """0.2.163-patch5: "Re-run the final script" edits only the final script's EXT window; every
    other parameter stays as it is.
    """
    from gui.pipeline_panel import PipelinePanel

    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    # write an existing final script (uniform) with phase/window/baseline and other parameters
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
    # user sets the final-run direct range to 8.0-6.0 (apply-to-optimisation defaults to on)
    panel._final_ext[(exp_id, data_id)] = ("8.0", "6.0", True)
    panel._on_rerun_final_requested("spectrum")
    assert calls == ["run_manual_spectrum"]
    # the script that ran: EXT updated to 8.0-6.0, the other parameters (SP/FT/PS/POLY) unchanged
    ran_content = ran_scripts.get(f"{data_id}_process.com", "")
    assert "-x1 8.0ppm -xn 6.0ppm" in ran_content
    assert "-x1 10.5ppm -xn 6.5ppm" not in ran_content
    assert "-p0 10 -p1 1.5" in ran_content  # phase untouched
    assert "POLY -auto -time" in ran_content  # baseline/diagnostics untouched
    assert "SP -off 0.5 -end 0.95" in ran_content  # window function untouched
    panel.close()



def test_reprocess_downstream_becomes_outdated(
    tmp_path: Path, qapp: QApplication
) -> None:
    """Reprocessing the spectrum and recording its fingerprint marks the downstream peak picking
    step OUTDATED.
    """
    from gui.pipeline_state import record_step_success

    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    for step in ("fid", "spectrum", "peaks"):
        record_step_success(manager, exp_id, data_id, step)
    ft2 = manager.data_dir(exp_id, data_id, "spectra") / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["spectrum"].run_button.text() == "重新优化"
    assert panel._rows["peaks"].status_label.text().startswith("!")
    panel.close()


# ----------------------------------------------------------------------
# Indirect-dimension flip (FT -neg) controls + rerun (user 2026-09-25)
#
# Contract: 2D uses a checkbox (checked = the final script's indirect dimension F1 already
# carries -neg); 3D uses a three-entry drop-down (indirect F2 / indirect F1 / F1 and F2), and
# the drop-down is a command -- choosing an entry flips the dimensions it covers, and choosing
# an already-flipped entry again cancels it; a rerun only edits the **indirect-dimension FT
# lines**, leaving -alt/direct dimension/EXT/PS/window function alone; for 3D NUS nus3d_rc is
# retained, so a flip only runs the indirect-dimension section and does not re-run SMILE.
# ----------------------------------------------------------------------

# 2D uniform final script shaped like backend/script_generator.generate_process_script
# (one FT for the direct dimension F2, one FT for the indirect dimension F1)
_SCRIPT_2D_UNIFORM = (
    "#!/bin/csh\n"
    "# NMRForge processing script\n"
    "# experiment: d_001\n"
    "xyz2pipe -in d_001.fid -x \\\n"
    "| nmrPipe -fn SP -off 0.5 -end 0.95 -pow 2 -c 0.5 \\\n"
    "| nmrPipe -fn FT -alt \\\n"
    "| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \\\n"
    "| nmrPipe -fn PS -p0 10 -p1 1.5 -di \\\n"
    "| nmrPipe -fn POLY -auto \\\n"
    "| nmrPipe -fn TP \\\n"
    "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \\\n"
    "| nmrPipe -fn ZF -size 512 \\\n"
    "| nmrPipe -fn FT -alt \\\n"
    "| nmrPipe -fn PS -p0 5 -p1 -10 -di \\\n"
    "| nmrPipe -fn POLY -auto \\\n"
    "| nmrPipe -fn TP \\\n"
    "| pipe2xyz -out d_001.ft2 -x\n"
)

# 3D uniform final script: one FT for F3 (direct), then one each for F2 and F1 (same order as
# gui.pipeline_panel._INDIRECT_AXIS_ORDER)
_SCRIPT_3D_UNIFORM = (
    "#!/bin/csh\n"
    "# NMRForge processing script\n"
    "xyz2pipe -in d_001.fid -x \\\n"
    "| nmrPipe -fn SP -off 0.5 -end 0.95 -pow 2 -c 0.5 \\\n"
    "| nmrPipe -fn FT \\\n"
    "| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \\\n"
    "| nmrPipe -fn PS -p0 0 -p1 0 -di \\\n"
    "| nmrPipe -fn TP \\\n"
    "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \\\n"
    "| nmrPipe -fn ZF -size 256 \\\n"
    "| nmrPipe -fn FT -alt \\\n"
    "| nmrPipe -fn PS -p0 1 -p1 2 -di \\\n"
    "| nmrPipe -fn ZTP \\\n"
    "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \\\n"
    "| nmrPipe -fn ZF -size 128 \\\n"
    "| nmrPipe -fn FT -alt \\\n"
    "| nmrPipe -fn PS -p0 3 -p1 4 -di \\\n"
    "| nmrPipe -fn TP \\\n"
    "| pipe2xyz -out d_001.ft3 -x\n"
)

# 3D NUS final script: step 1 direct-dimension FT (the .fid statement), step 2 SMILE writes
# nus3d_rc, step 3 reads the retained nus3d_rc back for the indirect dimensions (F2/F1)
_SCRIPT_3D_NUS = (
    "#!/bin/csh\n"
    "# NMRForge 3D NUS SMILE reconstruction\n"
    "mkdir -p nus3d_1 nus3d_rc\n"
    "# step 1: direct dim (F3) FT + EXT + PS\n"
    "xyz2pipe -in d_001.fid -x \\\n"
    "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 2 -c 0.5 \\\n"
    "| nmrPipe -fn FT \\\n"
    "| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \\\n"
    "| nmrPipe -fn PS -p0 0 -p1 0 -di \\\n"
    "| pipe2xyz -out nus3d_1/test%04d.ft1 -z\n"
    "\n"
    "# step 2: SMILE reconstruct indirect dims (F2/F1)\n"
    "xyz2pipe -in nus3d_1/test%04d.ft1 -x \\\n"
    "| nmrPipe -fn SMILE -nDim 3 \\\n"
    "           -sample nuslist -nThread 2 \\\n"
    "           -xNeg \\\n"
    "| pipe2xyz -out nus3d_rc/test%04d.ft1 -x\n"
    "\n"
    "# step 3: indirect dims (F2/F1) window + ZF + FT + PS\n"
    "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\\n"
    "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \\\n"
    "| nmrPipe -fn ZF -size 256 \\\n"
    "| nmrPipe -fn FT -alt \\\n"
    "| nmrPipe -fn PS -p0 0 -p1 0 -di \\\n"
    "| nmrPipe -fn TP \\\n"
    "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \\\n"
    "| nmrPipe -fn ZF -size 128 \\\n"
    "| nmrPipe -fn FT -alt \\\n"
    "| nmrPipe -fn PS -p0 0 -p1 0 -di \\\n"
    "| nmrPipe -fn TP \\\n"
    "| nmrPipe -fn ZTP \\\n"
    "| pipe2xyz -out d_001.ft3 -x\n"
)


class _CaptureController(_FakeController):
    """Record the scripts run_manual_spectrum receives; report data_facts from the given ndim."""

    def __init__(self, ndim: int = 2) -> None:
        super().__init__()
        self.ndim = ndim
        self.ran_scripts: dict[str, str] = {}

    def data_facts(self, exp_id: str = "", data_id: str = "") -> dict:
        return {"ndim": self.ndim, "is_nus": self.ndim >= 3}

    def run_manual_spectrum(
        self, data, scripts, exp_id=None, data_id=None, progress=None
    ) -> str:
        self.calls.append("run_manual_spectrum")
        self.ran_scripts.update(scripts)
        return "/tmp/x.ft2"


def _write_final_script(
    manager, exp_id: str, data_id: str, content: str, name: str = ""
) -> Path:
    """Write the given final script into process/ (default name matches the backend uniform one)."""
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    path = process / (name or f"{data_id}_process.com")
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _ft_lines(script: str) -> list[str]:
    """The script's FT lines, in order of appearance (a plain split the assertions use)."""
    return [line for line in script.splitlines() if "-fn FT" in line]


def test_2d_spectrum_row_shows_flip_checkbox(
    tmp_path: Path, qapp: QApplication
) -> None:
    """The 2D spectrum step offers the "Indirect flip" checkbox (not the 3D drop-down); other step
    rows have neither."""
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    panel = PipelinePanel(manager, _CaptureController(ndim=2))
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    assert not row.rerun_final_button.isHidden()
    assert not row.flip_indirect_check.isHidden()
    assert row.flip_indirect_combo.isHidden()
    assert panel._rows["fid"].flip_indirect_check.isHidden()
    panel.close()


def test_2d_flip_only_touches_the_indirect_ft_line(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2D: checking the flip puts -neg on the indirect-dimension (F1) FT line only; unchecking
    removes it."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    script_path = _write_final_script(
        manager, exp_id, data_id, _SCRIPT_2D_UNIFORM
    )
    manager.save()
    controller = _CaptureController(ndim=2)
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    assert row.flip_indirect_check.isChecked() is False  # current state: no flip
    # also change the direct dimension range: the flip must not touch the EXT line
    panel._final_ext[(exp_id, data_id)] = ("8.0", "6.0", True)
    row.flip_indirect_check.setChecked(True)
    row.rerun_final_button.click()  # real signal: the choice travels with the signal

    ran = controller.ran_scripts[f"{data_id}_process.com"]
    ft = _ft_lines(ran)
    assert len(ft) == 2
    assert "-neg" not in ft[0]  # direct dimension F2 untouched
    assert ft[1].rstrip().endswith("-neg \\")  # indirect dimension F1 carries -neg
    assert ran.count("-neg") == 1
    assert "-x1 8.0ppm -xn 6.0ppm" in ran  # EXT is still updated to the latest range
    assert "-x1 10.5ppm -xn 6.5ppm" not in ran
    assert "PS -p0 10 -p1 1.5 -di" in ran  # direct-dimension phase untouched
    assert "PS -p0 5 -p1 -10 -di" in ran  # indirect-dimension phase untouched
    assert "SP -off 0.5 -end 0.95" in ran  # window function untouched
    assert "ZF -size 512" in ran
    assert "POLY -auto" in ran  # baseline untouched
    # the flip is recorded in the final script on disk; after a refresh the checkbox stays
    # checked (state mirror)
    assert "-neg" in script_path.read_text(encoding="utf-8")
    panel.refresh()
    assert row.flip_indirect_check.isChecked() is True
    # rerun once more without touching the controls: the flip is kept (it is not silently
    # dropped by a plain rerun)
    row.rerun_final_button.click()
    assert controller.ran_scripts[f"{data_id}_process.com"].count("-neg") == 1
    # unchecking removes -neg on the next rerun
    row.flip_indirect_check.setChecked(False)
    row.rerun_final_button.click()
    ran_again = controller.ran_scripts[f"{data_id}_process.com"]
    assert "-neg" not in ran_again
    assert "-neg" not in script_path.read_text(encoding="utf-8")
    panel.close()


def test_3d_flip_combo_only_touches_the_chosen_indirect_dimension(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3D three-entry drop-down: F2 / F1 / F1 and F2 each edit only their own indirect FT line."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    cases = (
        (0, {"F2": True, "F1": False}),  # indirect dimension (F2)
        (1, {"F2": False, "F1": True}),  # indirect dimension (F1)
        (2, {"F2": True, "F1": True}),  # F1 and F2
    )
    for index, expected in cases:
        case_dir = tmp_path / f"case{index}"
        manager, exp_id, data_id = _manager_with_artifacts(case_dir)
        _write_final_script(manager, exp_id, data_id, _SCRIPT_3D_UNIFORM)
        manager.save()
        controller = _CaptureController(ndim=3)
        panel = PipelinePanel(manager, controller)
        panel.set_selection("data", exp_id, data_id)
        row = panel._rows["spectrum"]
        assert not row.flip_indirect_combo.isHidden()
        assert row.flip_indirect_check.isHidden()
        assert row.flip_indirect_combo.count() == 3
        row.flip_indirect_combo.setCurrentIndex(index)
        row.rerun_final_button.click()

        ran = controller.ran_scripts[f"{data_id}_process.com"]
        ft = _ft_lines(ran)
        assert len(ft) == 3, ran
        assert "-neg" not in ft[0]  # direct dimension F3
        assert ("-neg" in ft[1]) is expected["F2"]
        assert ("-neg" in ft[2]) is expected["F1"]
        # the drop-down is a command: it resets after use (a plain rerun must not flip again)
        assert row.flip_indirect_combo.currentIndex() == -1
        panel.close()


def test_3d_flip_combo_choosing_the_same_entry_again_cancels(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing the same 3D drop-down entry again cancels the flip (user decision, 2026-09-25)."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    script_path = _write_final_script(
        manager, exp_id, data_id, _SCRIPT_3D_UNIFORM
    )
    manager.save()
    controller = _CaptureController(ndim=3)
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    row.flip_indirect_combo.setCurrentIndex(0)  # indirect dimension (F2)
    row.rerun_final_button.click()
    assert controller.ran_scripts[f"{data_id}_process.com"].count("-neg") == 1
    panel.refresh()  # the refresh feeds the final script's current state back into the controls
    row.flip_indirect_combo.setCurrentIndex(0)  # choose the same entry again
    row.rerun_final_button.click()
    ran = controller.ran_scripts[f"{data_id}_process.com"]
    assert "-neg" not in ran
    assert "-neg" not in script_path.read_text(encoding="utf-8")
    panel.close()


def test_3d_nus_flip_reruns_only_the_indirect_section(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3D NUS: nus3d_rc is retained, so a flip runs the indirect-dimension section only (no SMILE
    and no direct dimension)."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    _write_final_script(
        manager, exp_id, data_id, _SCRIPT_3D_NUS, name=f"{data_id}_nus.com"
    )
    process = manager.data_dir(exp_id, data_id, "process")
    planes = process / "nus3d_rc"
    planes.mkdir(parents=True, exist_ok=True)
    (planes / "test0001.ft1").write_bytes(b"ft1")
    manager.save()
    controller = _CaptureController(ndim=3)
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    # the user also changed the direct dimension range: this path does not run the direct
    # dimension, so it must not pretend to have applied it
    panel._final_ext[(exp_id, data_id)] = ("8.0", "6.0", True)
    row.flip_indirect_combo.setCurrentIndex(0)  # indirect dimension (F2)
    row.rerun_final_button.click()

    keys = list(controller.ran_scripts)
    assert keys == [f"{data_id}_nus_indirect.com"], keys
    section = controller.ran_scripts[keys[0]]
    assert "-fn SMILE" not in section  # SMILE is not re-run
    assert not section.lstrip().startswith("#!")  # a fragment: indirect-dimension statements
    assert "xyz2pipe -in nus3d_rc/test%04d.ft1 -x" in section
    assert "EXT" not in section  # the direct dimension (and its EXT) is not in this section
    ft = _ft_lines(section)
    assert len(ft) == 2
    assert "-neg" in ft[0]  # indirect dimension F2 carries -neg
    assert "-neg" not in ft[1]  # indirect dimension F1 untouched
    # the whole final script is still on disk (SMILE included) and records the flip; EXT is
    # unchanged
    full = (process / f"{data_id}_nus.com").read_text(encoding="utf-8")
    assert "-fn SMILE" in full
    assert "-x1 10.5ppm -xn 6.5ppm" in full
    assert _ft_lines(full)[1].rstrip().endswith("-neg \\")  # F2
    assert "-neg" not in _ft_lines(full)[2]  # F1
    panel.close()


def test_flip_indirect_ft_lines_leaves_unknown_layout_alone() -> None:
    """When the layout cannot be recognised (the indirect FT line count does not match), do not
    guess: return the script unchanged with an empty state."""
    from gui.pipeline_panel import flip_indirect_ft_lines, indirect_neg_state

    script = (
        "#!/bin/csh\n"
        "| nmrPipe -fn FT \\\n"
        "| nmrPipe -fn TP \\\n"
        "| nmrPipe -fn FT \\\n"
        "| nmrPipe -fn FT \\\n"
    )
    new, applied = flip_indirect_ft_lines(script, ndim=3, flips={"F1": True})
    assert new == script
    assert applied == {}
    assert indirect_neg_state(script, ndim=3) == {}
    # an unsupported dimensionality (1D) is not guessed either
    assert flip_indirect_ft_lines(script, ndim=1, flips={"F1": True}) == (
        script,
        {},
    )


def test_removing_neg_keeps_the_script_indentation() -> None:
    """Removing ``-neg`` touches **only the token's neighbourhood**; it must not squash other
    whitespace in the line (indentation).

    Regression: the earlier ``re.sub(r"[ \\t]{2,}", " ")`` collapsed an 8-space leading
    indentation to one space, silently corrupting scripts whose indentation the user had edited
    by hand.
    """
    from gui.pipeline_panel import _add_neg_token, _remove_neg_token

    indented = "        | nmrPipe -fn FT -neg \\"
    assert _remove_neg_token(indented) == "        | nmrPipe -fn FT \\"
    # in-line alignment whitespace is preserved too (only the token's own gap is normalised)
    aligned = "| nmrPipe -fn FT -alt -neg \\"
    assert _remove_neg_token(aligned) == "| nmrPipe -fn FT -alt \\"
    # no token -> return the line unchanged
    plain = "   | nmrPipe -fn FT \\"
    assert _remove_neg_token(plain) == plain
    # add/remove round-trips to the original (idempotent)
    assert _add_neg_token(_remove_neg_token(indented)) == indented


def test_row_emits_the_primary_neg_keys_and_accepts_the_alias(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sampling the control emits uses the **official names** ``ft_neg_f1``/``ft_neg_f2``
    (they decide directly whether -neg is added; ``flip_*`` are legacy aliases the worker still
    accepts)."""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    script_path = _write_final_script(
        manager, exp_id, data_id, _SCRIPT_2D_UNIFORM
    )
    controller = _CaptureController(ndim=2)
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", exp_id, data_id)
    row = panel._rows["spectrum"]
    # 2D: checkbox -> ft_neg_f1
    row.flip_indirect_check.setChecked(True)
    assert row._take_flip_sampling() == {"ft_neg_f1": True}
    # 3D: three-entry drop-down -> ft_neg_f2 / ft_neg_f1 / both
    panel._ndim_cache[(exp_id, data_id)] = 3
    panel.refresh()
    row.flip_indirect_combo.setCurrentIndex(0)  # indirect dimension (F2)
    assert row._take_flip_sampling() == {"ft_neg_f2": True}
    row.flip_indirect_combo.setCurrentIndex(2)  # F1 and F2
    assert row._take_flip_sampling() == {"ft_neg_f2": True, "ft_neg_f1": True}
    # the worker still accepts the legacy alias (old signals/old calls); back to a 2D context
    # (the script is 2D)
    panel._ndim_cache[(exp_id, data_id)] = 2
    panel.refresh()
    panel._on_rerun_final_requested("spectrum", {"flip_f1": True})
    assert "-neg" in controller.ran_scripts[f"{data_id}_process.com"]
    assert "-neg" in script_path.read_text(encoding="utf-8")
    panel.close()
