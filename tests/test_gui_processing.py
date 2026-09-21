"""Processing flow controller test:step-by-step/import/Manual interface wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.project import ProjectManager
from gui.processing import ProcessingController


def _manager_with_experiment(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.add_experiment("/fake/bruker/1", title="HSQC")
    manager.save()
    return manager


def test_is_segmented_container(tmp_path: Path) -> None:
    """0.2.108: Container directory = top-level directory without acqus and >= 2 subdirectories
    containing acqus."""
    from gui.processing import is_segmented_container

    container = tmp_path / "container"
    container.mkdir()
    for seg in ("seg1", "seg2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    assert is_segmented_container(container)
    # The top level is directly the Bruker dataset -> not a container.
    single = tmp_path / "single"
    single.mkdir()
    (single / "acqus").write_text("x", encoding="utf-8")
    assert not is_segmented_container(single)
    # Only 1 staging subdirectory -> not counting containers.
    one = tmp_path / "one"
    one.mkdir()
    (one / "seg1").mkdir()
    (one / "seg1" / "acqus").write_text("x", encoding="utf-8")
    assert not is_segmented_container(one)
    assert not is_segmented_container(tmp_path / "missing")


def test_import_segmented_dataset_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.108: Segmented collection imports transparent transmission
    workflow.import_segmented_dataset."""
    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
    captured: dict = {}

    class _Result:
        experiment_id = "exp_009"
        data_id = "d_001"
        run_id = "R-1"
        warnings: list = []

    def fake(manager, source, *, exp_id="", title="", sample_id="", copy=True):
        captured.update(
            source=str(source),
            exp_id=exp_id,
            title=title,
            sample_id=sample_id,
            copy=copy,
        )
        return _Result()

    monkeypatch.setattr(
        "workflow.import_workflow.import_segmented_dataset", fake
    )
    result = controller.import_segmented_dataset(
        "/data/container", exp_id="exp_001", title="seg", copy=False
    )
    assert captured["source"] == "/data/container"
    # Transparently transmit the current experiment type.
    assert captured["exp_id"] == "exp_001"
    assert captured["title"] == "seg"
    assert captured["copy"] is False
    assert result.data_id == "d_001"


def test_generate_spectrum_passes_phase_route_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.108:params["phase_route"] transparently transmits stepwise, and no longer superimposes
    the old violent optimisation."""
    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
    controller.set_manager(manager)
    captured: dict = {}

    def fake_spectrum(manager_, exp_id, data_id, backend, **kwargs):
        captured.update(kwargs)
        return "/tmp/x.ft2"

    monkeypatch.setattr(
        "workflow.stepwise.generate_spectrum", fake_spectrum
    )

    path = controller.generate_spectrum(
        None,
        exp_id="exp_001",
        data_id="d_001",
        params={"phase_route": "unified"},
    )
    assert path == "/tmp/x.ft2"
    assert captured.get("params") == {"phase_route": "unified"}


def test_generate_spectrum_phase_route_none_skips_optimize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.108:phase_route="none" Escape exit skips phase optimisation."""
    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
    controller.set_manager(manager)
    captured: dict = {}

    def fake_spectrum(manager_, exp_id, data_id, backend, **kwargs):
        captured.update(kwargs)
        return "/tmp/x.ft2"

    monkeypatch.setattr(
        "workflow.stepwise.generate_spectrum", fake_spectrum
    )

    path = controller.generate_spectrum(
        None,
        exp_id="exp_001",
        data_id="d_001",
        params={"phase_route": "none"},
    )
    assert path == "/tmp/x.ft2"
    assert captured.get("params") == {"phase_route": "none"}


def test_manual_interfaces_require_manager() -> None:
    """A clear error is given when the artificial interface is not bound to the project (the
    NotImplementedError placeholder is no longer thrown)."""
    controller = ProcessingController()
    with pytest.raises(RuntimeError, match="not bound to a project"):
        controller.manual_fid_com(None)
    with pytest.raises(RuntimeError, match="not bound to a project"):
        controller.manual_scripts(None)
    with pytest.raises(RuntimeError, match="not bound to a project"):
        controller.run_manual_spectrum(None, {})
    with pytest.raises(RuntimeError, match="not bound to a project"):
        controller.save_peaks_manual(None, [])


def test_save_peaks_manual_writes_list_and_registers_run(
    tmp_path: Path,
) -> None:
    """Save artificial peak table: write data/peaks Poky.list + register manual_peaks and run."""
    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
    from core.peaks.localize import (
        localization_records_path,
        write_localization_records,
    )

    target = manager.data_dir("exp_001", "d_001", "peaks") / "exp_001-d_001.list"
    sidecar = write_localization_records(
        target,
        [{"Peak_ID": 1, "actual_method": "gaussian"}],
    )
    assert sidecar == localization_records_path(target)
    assert sidecar.is_file()
    peaks = [
        {
            "Peak_ID": 1,
            "H_shift": 8.0,
            "N_shift": 115.0,
            "Intensity": 100.0,
            "SN": 20.0,
            "label": "G1",
        },
        {
            "Peak_ID": 2,
            "H_shift": 7.5,
            "N_shift": 118.0,
            "Intensity": 80.0,
            "SN": 15.0,
            "label": "A2",
        },
    ]
    list_path = controller.save_peaks_manual(
        None, peaks, exp_id="exp_001", data_id="d_001"
    )
    assert Path(list_path).suffix == ".list"
    assert Path(list_path).is_file()
    # Old automatic positioning diagnostics must be invalidated after manual overwriting.
    assert not sidecar.exists()
    content = Path(list_path).read_text(encoding="utf-8")
    assert "Assignment w1 w2" in content
    assert "G1" in content and "8.0" in content and "118.0" in content
    runs = [
        run
        for run in manager.project.workflow_runs
        if run.workflow_ref == "manual_peaks"
    ]
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].outputs.get("peaks") == str(list_path)


def test_generate_spectrum_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The progress of the spectrum generation phase is reported through the progress callback
    (0.2.154: unified and automatic processing, no longer superimposing the old violent phase
    optimisation)."""
    manager = _manager_with_experiment(tmp_path)
    data = manager.project.experiment("exp_001").data[0]
    messages: list[str] = []
    monkeypatch.setattr(
        "workflow.stepwise.generate_spectrum",
        lambda *args, **kwargs: "/tmp/x.ft2",
    )
    controller = ProcessingController(manager)
    path = controller.generate_spectrum(
        data,
        exp_id="exp_001",
        data_id="d_001",
        progress=messages.append,
    )
    assert path == "/tmp/x.ft2"
    assert messages
    assert any("Read data" in msg for msg in messages)
    assert any("Spectrum generation is completed" in msg for msg in messages)
    assert any("phase route" in msg for msg in messages)
    assert not any("phase optimisation completed" in msg for msg in messages)


def test_generate_spectrum_phase_optimize_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase_optimize Parameter compatibility is retained: the behaviour is unified (0.2.154, there
    is no longer a distinction between basic spectrum/violent optimisation, all are handled
    uniformly and automatically)."""
    manager = _manager_with_experiment(tmp_path)
    data = manager.project.experiment("exp_001").data[0]
    messages: list[str] = []
    monkeypatch.setattr(
        "workflow.stepwise.generate_spectrum",
        lambda *args, **kwargs: "/tmp/x.ft2",
    )
    controller = ProcessingController(manager)
    path = controller.generate_spectrum(
        data,
        exp_id="exp_001",
        data_id="d_001",
        progress=messages.append,
        phase_optimize=False,
    )
    assert path == "/tmp/x.ft2"
    assert any("Spectrum generation is completed" in msg for msg in messages)
    assert not any("phase optimisation completed" in msg for msg in messages)


def test_generate_spectrum_wires_linewidth_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.112: The software sets the line width (nuclide key) access to generate spectrum params
    (mapping by axis)."""
    from types import SimpleNamespace

    from core.data.internal_data_model import SamplingMode

    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
    controller.set_manager(manager)
    captured: dict = {}

    fake_exp = SimpleNamespace(
        sampling=SimpleNamespace(mode=SamplingMode.UNIFORM),
        dimensions=[
            SimpleNamespace(logical_axis="F2", nucleus="1H"),
            SimpleNamespace(logical_axis="F1", nucleus="15N"),
        ],
    )
    monkeypatch.setattr(
        controller, "_read_experiment", lambda exp_id, data_id: fake_exp
    )
    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"linewidth_hz": {"1H": 10.0, "15N": 12.0, "13C": 14.0}},
    )

    def fake_spectrum(manager_, exp_id, data_id, backend, **kwargs):
        captured.update(kwargs)
        return "/tmp/x.ft2"

    monkeypatch.setattr("workflow.stepwise.generate_spectrum", fake_spectrum)
    path = controller.generate_spectrum(
        None,
        exp_id="exp_001",
        data_id="d_001",
        params={"phase_route": "unified"},
    )
    assert path == "/tmp/x.ft2"
    params = captured.get("params") or {}
    assert params["phase_route"] == "unified"
    assert params["linewidth_hz"] == {"F2": 10.0, "F1": 12.0}


def test_generate_spectrum_explicit_linewidth_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.112: Not overwritten when params["linewidth_hz"] is passed in explicitly."""
    from types import SimpleNamespace

    from core.data.internal_data_model import SamplingMode

    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
    controller.set_manager(manager)
    captured: dict = {}

    fake_exp = SimpleNamespace(
        sampling=SimpleNamespace(mode=SamplingMode.UNIFORM),
        dimensions=[SimpleNamespace(logical_axis="F2", nucleus="1H")],
    )
    monkeypatch.setattr(
        controller, "_read_experiment", lambda exp_id, data_id: fake_exp
    )
    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"linewidth_hz": {"1H": 10.0}},
    )

    def fake_spectrum(manager_, exp_id, data_id, backend, **kwargs):
        captured.update(kwargs)
        return "/tmp/x.ft2"

    monkeypatch.setattr("workflow.stepwise.generate_spectrum", fake_spectrum)
    controller.generate_spectrum(
        None,
        exp_id="exp_001",
        data_id="d_001",
        params={"phase_route": "none", "linewidth_hz": {"F2": 99.0}},
    )
    params = captured.get("params") or {}
    assert params["linewidth_hz"] == {"F2": 99.0}

def test_resolve_import_source(tmp_path: Path) -> None:
    """NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT."""
    from gui.processing import resolve_import_source
    from workflow.import_workflow import ImportWorkflowError

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "acqus").write_text("x", encoding="utf-8")
    src, seg = resolve_import_source(dataset)
    assert src == str(dataset.resolve()) or src == str(dataset)
    assert seg is False

    container = tmp_path / "container"
    container.mkdir()
    for name in ("segA", "segB"):
        d = container / name
        d.mkdir()
        (d / "acqus").write_text("x", encoding="utf-8")
    (container / "notes").mkdir()
    (container / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    src, seg = resolve_import_source(container)
    assert seg is True

    one = tmp_path / "one"
    one.mkdir()
    segd = one / "segA"
    segd.mkdir()
    (segd / "acqus").write_text("x", encoding="utf-8")
    (one / "notes").mkdir()
    (one / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    src, seg = resolve_import_source(one)
    assert seg is False
    assert Path(src).name == "segA"

    nothing = tmp_path / "nothing"
    nothing.mkdir()
    (nothing / "notes").mkdir()
    (nothing / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    try:
        resolve_import_source(nothing)
        raise AssertionError("ImportWorkflowError should be thrown")
    except ImportWorkflowError:
        pass


def test_resolve_import_source_ignores_no_acqus_subdirs(
    tmp_path: Path,
) -> None:
    """0.2.198: The subdirectory only has data files but lacks acqus. It will be treated as a non-
    data file folder and ignored; if there is exactly one acqus section, it will be imported as
    a single file, and acqus will not be reported as missing due to miscellaneous directories."""
    from gui.processing import resolve_import_source
    from workflow.import_workflow import ImportWorkflowError

    # Exactly 1 acqus section + 1 miscellaneous directory with only ser -> ignore the miscellaneous
    # directory and only import the acqus section.
    one = tmp_path / "one"
    one.mkdir()
    seg = one / "segA"
    seg.mkdir()
    (seg / "acqus").write_text("x", encoding="utf-8")
    junk = one / "junk"
    junk.mkdir()
    (junk / "ser").write_text("x", encoding="utf-8")
    src, seg_flag = resolve_import_source(one)
    assert seg_flag is False
    assert Path(src).name == "segA"

    # All subdirectories only have data files but are missing acqus -> explicitly report missing
    # acqus, don’t blame the top level.
    bad = tmp_path / "bad"
    bad.mkdir()
    d1 = bad / "d1"
    d1.mkdir()
    (d1 / "ser").write_text("x", encoding="utf-8")
    try:
        resolve_import_source(bad)
        raise AssertionError("ImportWorkflowError should be thrown")
    except ImportWorkflowError as exc:
        assert "an acqus" in str(exc)
