"""Phase A tests: context bar + spectrum linkage (step/parameter summary, locate, 3D memory)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtcompat.QtWidgets import QApplication

from core.project import ProjectManager
from gui.main_window import MainWindow
from gui.spectrum_panel import SpectrumPanel


class _TempWorkspace:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def list_projects(self):
        return sorted(
            p for p in self.root.iterdir() if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs):
        return ProjectManager.create_project(self.root / name, name, **kwargs)


def _manager_with_ws(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = ProjectManager.create_project(ws / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    manager.save()
    return manager, ws, entry.id, data.id


def _write_ft2(path: Path, shape=(16, 32), *, amplitude=10.0) -> None:
    from nmrglue.fileio import pipe

    data = np.zeros(shape, dtype=np.float32)
    data[8, 16] = amplitude
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = shape[0]
    dic["FDF1SW"] = 6000.0
    dic["FDF1OBS"] = 600.0
    dic["FDF1CAR"] = 118.0
    dic["FDF1ORIG"] = 118.0 * 600.0
    dic["FDF2T"] = shape[1]
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


def _write_ft3(path: Path, shape=(2, 3, 8), *, amplitude=100.0) -> None:
    from nmrglue.fileio import pipe

    data = np.zeros(shape, dtype=np.float32)
    data[0, 1, 2] = amplitude
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1
    dic["FDSIZE"] = shape[2]
    dic["FDSPECNUM"] = shape[1]
    dic["FDF3SIZE"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    for index, prefix in enumerate(("FDF1", "FDF2", "FDF3")):
        dic[prefix + "T"] = shape[index]
        dic[prefix + "SW"] = 6000.0
        dic[prefix + "OBS"] = 600.0
        dic[prefix + "CAR"] = 4.7
        dic[prefix + "ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


def test_context_bar_follows_selection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, ws, exp_id, data_id = _manager_with_ws(tmp_path)
    monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws))
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(ws),
    )
    window = MainWindow(manager=manager)
    window.project_tree.select_data(exp_id, data_id)
    text = window.context_bar.text()
    assert "demo" in text and "HSQC" in text
    assert data_id in text and "已导入" in text
    window.close()


def test_pipeline_buttons_gated_by_prerequisites(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.163-patch14: while a prerequisite step is incomplete (LOCKED), later steps (spectrum
    generation / peak picking) expose no run or manual button, and programmatic runs are refused."""
    from gui.main_window import MainWindow
    from gui.pipeline_state import record_step_success

    manager, ws, exp_id, data_id = _manager_with_ws(tmp_path)
    monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws))
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(ws),
    )
    window = MainWindow(manager=manager)
    pipeline = window.center_panel.pipeline
    pipeline.set_selection("data", exp_id, data_id)
    rows = pipeline._rows
    # The window is not shown, so isHidden mirrors setVisible's state
    # fid not generated: spectrum/peaks are all LOCKED → no run/manual buttons
    for sid in ("spectrum", "peaks"):
        assert rows[sid].manual_button.isHidden(), sid
        assert rows[sid].run_button.isHidden(), sid
    # 0.2.199-patch29dm: the manual button stays hidden until fid is auto-processed (READY)
    assert rows["fid"].manual_button.isHidden()

    # The programmatic run entry is refused by the prerequisite guard too (never reaches RUNNING)
    messages: list[str] = []
    pipeline.log_message.connect(messages.append)
    pipeline._on_run_requested("peaks")
    assert any("前置步骤未完成" in m for m in messages)
    assert "RUNNING" not in rows["peaks"].status_label.text()

    def _ready(sid: str, product: Path) -> None:
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_bytes(b"x")
        manager.save()
        pipeline.refresh()
        # 0.2.199-patch29dl (user): peak picking has no manual script, so its button stays hidden
        if sid == "peaks":
            assert rows[sid].manual_button.isHidden(), sid
        else:
            assert not rows[sid].manual_button.isHidden(), sid
        assert not rows[sid].run_button.isHidden(), sid

    # FID generated → spectrum READY; peaks still LOCKED
    fid = manager.data_dir(exp_id, data_id, "process") / f"{data_id}.fid"
    fid.parent.mkdir(parents=True, exist_ok=True)
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, fid)
    record_step_success(manager, exp_id, data_id, "fid")
    manager.save()
    pipeline.refresh()
    # 0.2.199-patch29dm: after a successful auto fid run the manual button appears (reads fid.com)
    assert not rows["fid"].manual_button.isHidden()
    assert not rows["spectrum"].manual_button.isHidden()
    assert not rows["spectrum"].run_button.isHidden()
    assert rows["peaks"].manual_button.isHidden()

    # Spectrum generated → peaks READY
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    spec = spectra / f"{data_id}.ft2"
    spec.write_bytes(b"ft2")
    manager.set_data_spectrum(exp_id, data_id, spec)
    record_step_success(manager, exp_id, data_id, "spectrum")
    _ready("peaks", spec)

    # Peak table → peaks done (the analysis step was removed, the flow ends at peak picking)
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    peaks_list = peaks / f"{exp_id}-{data_id}.list"
    peaks_list.write_text("", encoding="utf-8")
    record_step_success(manager, exp_id, data_id, "peaks")
    pipeline.refresh()
    assert "全部步骤已完成" in pipeline.next_label.text()
    window.close()


def test_spectrum_panel_no_locator_bar(tmp_path: Path, qapp: QApplication) -> None:
    """The spectrum panel no longer shows the "locate in Pipeline / data summary" bar (useless)."""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / f"{entry.id}-{data.id}.ft2")
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert not hasattr(panel, "context_summary")
    assert not hasattr(panel, "locate_button")
    panel.close()


@pytest.mark.parametrize("plane_index", [0, 1, 2])
def test_3d_viewer_state_memory(tmp_path: Path, qapp: QApplication, plane_index: int) -> None:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("3D")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    spectra = manager.data_dir(entry.id, data1.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft3(spectra / f"{entry.id}-{data1.id}.ft3")
    manager.save()
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data1.id)
    assert panel._spectrum3d_panel.slice_axis == 0
    panel._spectrum3d_panel.plane_combo.setCurrentIndex(plane_index)
    panel._render_3d_view()
    assert panel._viewer3d_state.get((entry.id, data1.id)) == plane_index

    panel.set_context(entry.id, data2.id)
    panel.set_context(entry.id, data1.id)
    assert panel._spectrum3d_panel.plane_combo.currentIndex() == plane_index
    assert panel._spectrum3d_panel._mode == "slice"
    panel.close()


@pytest.mark.parametrize("defer_panel", [False, True])
def test_tree_selection_automatically_displays_data_spectrum(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    defer_panel: bool,
) -> None:
    from qtcompat.QtTest import QTest

    manager, ws, exp_id, data_id = _manager_with_ws(tmp_path)
    second = manager.import_data(exp_id, "/fake/2")
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    path = spectra / "main.ft2"
    _write_ft2(path)
    monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws))
    monkeypatch.setattr("core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(ws))
    window = MainWindow(manager=manager, defer_spectrum_panel=defer_panel)
    window.project_tree.select_data(exp_id, data_id)
    QTest.qWait(220)
    assert window.spectrum_panel is not None
    assert window.spectrum_panel._current_spectrum == path
    assert window.spectrum_panel.viewer.layer_list.count() == 1

    window.project_tree.select_data(exp_id, second.id)
    QTest.qWait(220)
    assert window.spectrum_panel._current_spectrum is None
    assert window.spectrum_panel.viewer.layer_list.count() == 0
    window.project_tree.select_data(exp_id, data_id)
    QTest.qWait(220)
    assert window.spectrum_panel._current_spectrum == path
    window.close()


def test_data_selection_debounce_opens_only_final_selection(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qtcompat.QtTest import QTest

    manager, ws, exp_id, first_id = _manager_with_ws(tmp_path)
    second = manager.import_data(exp_id, "/fake/2")
    first_path = manager.data_dir(exp_id, first_id, "spectra") / "main.ft2"
    second_path = manager.data_dir(exp_id, second.id, "spectra") / "main.ft2"
    for path in (first_path, second_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_ft2(path)
    monkeypatch.setattr("gui.main_window.WorkspaceManager", lambda: _TempWorkspace(ws))
    monkeypatch.setattr("core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(ws))
    window = MainWindow(manager=manager)
    panel = window.spectrum_panel
    assert panel is not None
    opened: list[Path] = []

    def record_open(path: Path, name: str | None = None) -> bool:
        opened.append(Path(path))
        return True

    monkeypatch.setattr(panel, "open_spectrum", record_open)
    window.project_tree.select_data(exp_id, first_id)
    window.project_tree.select_data(exp_id, second.id)
    QTest.qWait(220)

    assert opened == [second_path]
    assert panel._current_data_id == second.id
    window.close()


def _context_with_two_ft2(tmp_path: Path):
    manager, _ws, exp_id, first_id = _manager_with_ws(tmp_path)
    second = manager.import_data(exp_id, "/fake/2")
    first_path = manager.data_dir(exp_id, first_id, "spectra") / "main.ft2"
    second_path = manager.data_dir(exp_id, second.id, "spectra") / "main.ft2"
    for path in (first_path, second_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_ft2(path)
    return manager, exp_id, first_id, second.id, first_path, second_path


class _DeferredThread:
    "Regression coverage:  DeferredThread."

    instances: list[_DeferredThread] = []

    def __init__(self, target, **_kwargs) -> None:
        self.target = target
        self.__class__.instances.append(self)

    def start(self) -> None:
        pass


def _defer_panel_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    _DeferredThread.instances = []
    monkeypatch.setattr("gui.spectrum_panel.threading.Thread", _DeferredThread)


def test_large_ft2_reads_off_main_thread_and_renders_after_worker(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    from qtcompat.QtTest import QTest

    from viewer.spectrum import Spectrum

    manager, exp_id, data_id, _other_id, path, _other_path = _context_with_two_ft2(tmp_path)
    panel = SpectrumPanel(manager)
    monkeypatch.setattr(panel, "_ASYNC_FT2_MIN_BYTES", 0)
    main_thread_id = threading.get_ident()
    loader_threads: list[int] = []
    load_original = Spectrum.load_from_ft2

    def track_load(target: Path):
        loader_threads.append(threading.get_ident())
        return load_original(target)

    monkeypatch.setattr(Spectrum, "load_from_ft2", staticmethod(track_load))
    panel.set_context(exp_id, data_id, auto_load_delay_ms=0)
    assert panel.viewer.layer_list.count() == 0
    deadline = time.monotonic() + 5.0
    while panel.viewer.layer_list.count() == 0 and time.monotonic() < deadline:
        QTest.qWait(10)
    assert loader_threads and loader_threads[0] != main_thread_id
    assert panel._current_spectrum == path
    assert panel.viewer.layer_list.count() == 1
    panel.close()


def test_stale_ft2_ready_and_failed_are_ignored_after_selection_change(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from viewer.spectrum import Spectrum

    manager, exp_id, first_id, second_id, first_path, second_path = _context_with_two_ft2(tmp_path)
    panel = SpectrumPanel(manager)
    monkeypatch.setattr(panel, "_ASYNC_FT2_MIN_BYTES", 0)
    _defer_panel_threads(monkeypatch)
    errors: list[str] = []
    panel.log_message.connect(errors.append)
    first_result = Spectrum.load_from_ft2(first_path)
    second_result = Spectrum.load_from_ft2(second_path)

    panel.set_context(exp_id, first_id, auto_load_delay_ms=0)
    old_thread = _DeferredThread.instances[-1]
    old_token = panel._ft3_load_token
    panel.set_context(exp_id, second_id, auto_load_delay_ms=0)
    new_token = panel._ft3_load_token
    panel._on_ft2_ready(old_token, first_path, first_result)
    panel._on_ft2_failed(old_token, first_path, "stale read error")

    assert new_token != old_token
    assert panel._current_spectrum == second_path
    assert panel.viewer.layer_list.count() == 0
    assert errors == []
    assert old_thread is _DeferredThread.instances[0]
    assert len(_DeferredThread.instances) == 1
    old_thread.target()
    assert panel._current_spectrum == second_path
    assert panel.viewer.layer_list.count() == 1
    assert second_result.data.shape == panel.viewer.primary_spectrum.data.shape
    panel.close()


def test_ft2_pending_request_is_not_repeated_and_same_path_old_result_is_stale(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from viewer.spectrum import Spectrum

    manager, exp_id, first_id, second_id, first_path, _second_path = _context_with_two_ft2(tmp_path)
    panel = SpectrumPanel(manager)
    monkeypatch.setattr(panel, "_ASYNC_FT2_MIN_BYTES", 0)
    _defer_panel_threads(monkeypatch)
    errors: list[str] = []
    panel.log_message.connect(errors.append)
    result = Spectrum.load_from_ft2(first_path)

    panel.set_context(exp_id, first_id, auto_load_delay_ms=0)
    old_token = panel._ft3_load_token
    panel.refresh()
    panel.set_context(exp_id, second_id, auto_load_delay_ms=0)
    panel.set_context(exp_id, first_id, auto_load_delay_ms=0)
    new_token = panel._ft3_load_token
    assert len(_DeferredThread.instances) == 1
    panel._on_ft2_ready(old_token, first_path, result)
    panel._on_ft2_failed(old_token, first_path, "stale same-path error")
    assert new_token != old_token
    assert panel._current_spectrum == first_path
    assert panel.viewer.layer_list.count() == 0
    assert errors == []
    _DeferredThread.instances[0].target()
    assert panel.viewer.layer_list.count() == 1
    assert result.data.shape == panel.viewer.primary_spectrum.data.shape
    panel.close()


@pytest.mark.parametrize("ndim", [2, 3])
@pytest.mark.parametrize("background", [False, True])
def test_processing_signal_reloads_same_path_and_rejects_old_results(
    tmp_path,
    qapp,
    monkeypatch,
    ndim,
    background,
) -> None:
    manager, _ws, exp_id, data_id = _manager_with_ws(tmp_path)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    path = spectra / f"main.ft{ndim}"
    write = _write_ft2 if ndim == 2 else _write_ft3
    write(path, amplitude=10.0)
    panel = SpectrumPanel(manager)
    monkeypatch.setattr(panel, f"_ASYNC_FT{ndim}_MIN_BYTES", 0 if background else 10**9)
    monkeypatch.setattr(panel, "_ensure_plane_stream", lambda: None)
    _defer_panel_threads(monkeypatch)
    panel.set_context(exp_id, data_id)
    if background:
        _DeferredThread.instances[-1].target()
    old = panel.viewer.primary_spectrum if ndim == 2 else panel._spectrum3d_panel.spectrum3d
    assert old.max_intensity == 10.0
    old_token = panel._ft3_load_token
    panel.viewer.level_label.setValue(0.1)
    panel.refresh()
    assert panel._ft3_load_token == old_token

    stamp = path.stat()
    write(path, amplitude=77.0)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    panel.refresh_after_processing(exp_id, "other-data")
    assert panel._ft3_load_token == old_token
    panel.refresh_after_processing(exp_id, data_id)
    new_token = panel._ft3_load_token
    assert new_token != old_token
    if background:
        _DeferredThread.instances[-1].target()
    fresh = panel.viewer.primary_spectrum if ndim == 2 else panel._spectrum3d_panel.spectrum3d
    assert fresh is not old
    assert fresh.max_intensity == 77.0
    assert panel.viewer.level_label.value() == 0.1
    assert panel._current_spectrum == path

    ready = panel._on_ft2_ready if ndim == 2 else panel._on_ft3_ready
    ready(old_token, path, old)
    assert (
        panel.viewer.primary_spectrum if ndim == 2 else panel._spectrum3d_panel.spectrum3d
    ) is fresh
    panel.refresh()
    assert panel._ft3_load_token == new_token
    panel.close()


@pytest.mark.parametrize("operation", ["manual", "optimise"])
@pytest.mark.parametrize("switch_data", [False, True])
def test_successful_run_signal_updates_view_without_switching_data(
    tmp_path,
    qapp,
    monkeypatch,
    operation,
    switch_data,
) -> None:
    from qtcompat.QtTest import QTest

    manager, exp_id, first_id, second_id, path, _other = _context_with_two_ft2(tmp_path)
    process = manager.data_dir(exp_id, first_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / "input.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, first_id, fid)
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(manager.root.parent)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(manager.root.parent)
    )
    monkeypatch.setattr("workflow.script_check.check_script", lambda *_a: [])
    window = MainWindow(manager=manager)
    window.project_tree.select_data(exp_id, first_id)
    QTest.qWait(220)
    panel = window.spectrum_panel
    initial = panel.viewer.primary_spectrum
    _defer_panel_threads(monkeypatch)

    class Controller:
        def generate_spectrum(self, data, exp_id=None, data_id=None):
            _write_ft2(path, amplitude=77.0)
            return str(path)

        def run_manual_spectrum(self, data, scripts, exp_id=None, data_id=None, progress=None):
            return self.generate_spectrum(data, exp_id, data_id)

    if operation == "manual":
        window.controller = Controller()
        window._run_script_async(
            manager.data(exp_id, first_id), "script", exp_id, first_id, "process.com"
        )
    else:
        window.pipeline.controller = Controller()
        window.pipeline._on_run_requested("spectrum")
    if switch_data:
        window.project_tree.select_data(exp_id, second_id)
        QTest.qWait(220)
        initial = panel.viewer.primary_spectrum
    _DeferredThread.instances[0].target()
    qapp.processEvents()
    assert panel._current_data_id == (second_id if switch_data else first_id)
    if switch_data:
        assert panel.viewer.primary_spectrum is initial
        assert panel.viewer.primary_spectrum.max_intensity == 10.0
    else:
        assert panel.viewer.primary_spectrum is not initial
        assert panel.viewer.primary_spectrum.max_intensity == 77.0
    window.close()


@pytest.mark.parametrize("steps", [["spectrum"], ["fid"]])
@pytest.mark.parametrize("status", ["success", "failed", "skipped"])
def test_group_member_reload_uses_raw_success_status(tmp_path, qapp, monkeypatch, steps, status):
    from qtcompat.QtTest import QTest

    manager, exp_id, first_id, _second_id, path, _other = _context_with_two_ft2(tmp_path)
    group = manager.create_data_group(exp_id, data_ids=[first_id])
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(manager.root.parent)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager", lambda *a, **k: _TempWorkspace(manager.root.parent)
    )
    window = MainWindow(manager=manager)
    window.project_tree.select_data(exp_id, first_id)
    QTest.qWait(220)
    initial = window.spectrum_panel.viewer.primary_spectrum
    _defer_panel_threads(monkeypatch)
    changed = []
    window.spectrum_results_changed.connect(lambda exp, data: changed.append((exp, data)))

    class Controller:
        def set_manager(self, _manager):
            pass

        def run_group_batch(self, _exp, _group, _steps, on_data_done=None, **_kwargs):
            _write_ft2(path, amplitude=77.0)
            on_data_done({"data_id": first_id, "status": status})
            return {"summary": {}, "results": {first_id: {"status": status}}}

    window.controller = Controller()
    window._run_group_batch(exp_id, group.id, steps)
    _DeferredThread.instances[0].target()
    qapp.processEvents()
    should_reload = status == "success" and "spectrum" in steps
    assert changed == ([(exp_id, first_id)] if should_reload else [])
    assert (window.spectrum_panel.viewer.primary_spectrum is not initial) == should_reload
    assert window.spectrum_panel.viewer.primary_spectrum.max_intensity == (
        77.0 if should_reload else 10.0
    )
    window.close()


def test_data_selection_prefers_3d_main_spectrum(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    manager, _ws, exp_id, data_id = _manager_with_ws(tmp_path)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "000_extra.ft2")
    path = spectra / "main.ft3"
    _write_ft3(path)
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    assert panel._current_spectrum == path
    assert panel._spectrum3d_panel.slice_axis == 0
    assert panel.viewer.layer_list.count() == 1
    panel.close()


def test_data_selection_uses_registered_main_spectrum(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    manager, _ws, exp_id, data_id = _manager_with_ws(tmp_path)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "000_old.ft2")
    path = spectra / "main.ft2"
    _write_ft2(path)
    manager.set_data_spectrum(exp_id, data_id, path)
    panel = SpectrumPanel(manager)
    panel.set_context(exp_id, data_id)
    assert panel._current_spectrum == path
    panel.close()


def test_old_async_3d_result_cannot_replace_new_selection(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from viewer.spectrum import Spectrum3D

    manager, _ws, exp_id, data_id = _manager_with_ws(tmp_path)
    second = manager.import_data(exp_id, "/fake/2")
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    path = spectra / "main.ft3"
    _write_ft3(path)
    second_dir = manager.data_dir(exp_id, second.id, "spectra")
    second_dir.mkdir(parents=True, exist_ok=True)
    second_path = second_dir / "main.ft2"
    _write_ft2(second_path)
    panel = SpectrumPanel(manager)
    monkeypatch.setattr(panel, "_ASYNC_FT3_MIN_BYTES", 0)
    requests = []
    monkeypatch.setattr(
        panel,
        "_load_ft3_async",
        lambda target: requests.append((panel._ft3_load_token, target)),
    )
    monkeypatch.setattr(panel, "_ensure_plane_stream", lambda: None)
    panel.set_context(exp_id, data_id)
    old_token, old_path = requests[-1]
    result = Spectrum3D.load_from_ft3(path, lazy=True)
    panel.set_context(exp_id, second.id)
    panel._on_ft3_ready(old_token, old_path, result)
    assert panel._current_spectrum == second_path
    assert panel._spectrum3d_panel.spectrum3d is None

    panel.set_context(exp_id, data_id)
    panel._on_ft3_ready(old_token, old_path, result)
    assert panel._spectrum3d_panel.spectrum3d is None
    panel._on_ft3_ready(*requests[-1], result)
    assert panel._spectrum3d_panel.spectrum3d is result
    assert panel._spectrum3d_panel.slice_axis == 0
    stream_token = panel._stream_token
    panel.refresh()
    assert panel._stream_token == stream_token
    panel.close()
