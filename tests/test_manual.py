"""Manual processing path testing: Check/Revise/run fid.com and process/nus script."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.project import ProjectManager
from workflow.manual import (
    ManualRunError,
    manual_fid_com,
    manual_scripts,
    run_manual_fid_com,
    run_manual_spectrum,
)


class _FakeRuntime:
    """Simulate csh execution: fid.com produces test.fid, process/nus produces final spectrum."""

    def __init__(self, spectrum_name: str, fail: bool = False) -> None:
        self.spectrum_name = spectrum_name
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
        name = Path(argv[-1]).name
        self.calls.append((name, str(cwd)))
        if self.fail:
            return SimpleNamespace(returncode=1, stderr="boom", stdout="")
        work = Path(cwd)
        if name == "fid.com":
            (work / "test.fid").write_bytes(b"fid")
        elif name in ("process.com", "nus.com") or name.endswith(
            ("_process.com", "_nus.com")
        ):
            (work / self.spectrum_name).write_bytes(b"ft2")
        return SimpleNamespace(returncode=0, stderr="", stdout="")


class _FakeBackend:
    """Automatically generate a fake backend for fid.com (called when manual_fid_com is not
    generated)."""

    work_dir: str | None = None
    last_overrides: dict | None = None

    def convert_to_fid(self, experiment, data_dir, progress=None, fid_com_overrides=None):
        work = Path(self.work_dir) if self.work_dir else Path(data_dir)
        (work / "fid.com").write_text("#!/bin/csh\n# auto fid.com\n", encoding="utf-8")
        fid = work / f"{experiment.dataset_id}.fid"
        fid.write_bytes(b"fid")
        self.last_overrides = dict(fid_com_overrides or {})
        return {
            "success": True,
            "fid_path": str(fid),
            "message": "ok",
            "logs": [],
        }


def _manager_with_raw(tmp_path: Path, bruker_dir: Path):
    """Registration data: source points to the fixture copy (raw directory including acqus)."""
    import shutil

    raw = tmp_path / "data_src"
    shutil.copytree(bruker_dir / "hsqc_2d", raw)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, str(raw))
    return manager, entry.id, data.id, raw


def test_manual_fid_com_requires_auto_generated(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch29dm: When fid.com is not automatically generated, a manual error will be
    reported directly and automatic conversion will no longer occur."""
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    with pytest.raises(ManualRunError, match="automatically generate FID"):
        manual_fid_com(manager, exp_id, data_id, _FakeBackend())


def test_manual_fid_com_reads_existing(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch29dm: fid.com has been generated and read directly without triggering
    conversion."""
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "fid.com").write_text(
        "#!/bin/csh\n# existing fid.com\n", encoding="utf-8"
    )
    content = manual_fid_com(manager, exp_id, data_id, _FakeBackend())
    assert "# existing fid.com" in content
    assert "# auto fid.com" not in content  # Not reconverted.


def test_run_manual_fid_com_registers(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch2: Single dataset manual fid.com is consistent with segmentation, parameter is
    handed over to the backend as an overlay."""
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    backend = _FakeBackend()

    fid_path = run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# edited\n-ySW 2800.000\n",
        backend=backend,
    )
    assert fid_path.endswith(".fid")
    assert backend.last_overrides == {"ySW": "2800.000"}
    assert Path(fid_path).parent == manager.data_dir(exp_id, data_id, "process")
    data = manager.data(exp_id, data_id)
    assert data.fid_path == fid_path
    assert data.status == "fid_ready"
    assert any(r.workflow_ref == "manual_fid" for r in manager.project.workflow_runs)


def test_manual_scripts_renders(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{data_id}.fid").write_bytes(b"fid")
    scripts = manual_scripts(manager, exp_id, data_id)
    # The spectrum step only renders spectrum script (process.com), not fid.com.
    assert sorted(scripts) == ["process.com"]
    assert scripts["process.com"].startswith("#!/bin/csh")


def test_run_manual_spectrum_uniform(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    # First generate FID (independent step), the spectrum step only consumes the converted fid.
    run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# fid\n",
        backend=_FakeBackend(),
    )
    runtime.calls.clear()
    spectrum = run_manual_spectrum(
        manager, exp_id, data_id, {"process.com": "#!/bin/csh\n# process\n"}
    )
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")
    # G2B-009: final spectrum only saves spectra/,process/ without leaving a copy.
    assert not (manager.data_dir(exp_id, data_id, "process") / Path(spectrum).name).exists()
    data = manager.data(exp_id, data_id)
    assert data.spectrum_path == spectrum
    assert data.status == "processed"
    assert any(r.workflow_ref == "manual_process" for r in manager.project.workflow_runs)
    # The spectrum step does not execute fid.com (generating FID is an independent step).
    assert all(name != "fid.com" for name, _cwd in runtime.calls)
    assert data.fid_path.endswith(".fid")


def test_run_manual_spectrum_failure(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    ok_runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: ok_runtime)
    run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# fid\n",
        backend=_FakeBackend(),
    )
    fail_runtime = _FakeRuntime(spectrum_name=f"{raw.name}.ft2", fail=True)
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: fail_runtime)
    with pytest.raises(ManualRunError, match="run failed"):
        run_manual_spectrum(
            manager,
            exp_id,
            data_id,
            {"process.com": "#!/bin/csh\n"},
        )
    run = next(
        r for r in manager.project.workflow_runs if r.workflow_ref == "manual_process"
    )
    assert run.status == "failed"


def test_run_manual_spectrum_missing_script(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# fid\n",
        backend=_FakeBackend(),
    )
    with pytest.raises(ManualRunError, match="Missing processing script"):
        run_manual_spectrum(manager, exp_id, data_id, {})


def test_run_manual_spectrum_missing_fid(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the spectrum step lacks fid, an error will be reported and failed run will be registered
    (not automatically executed fid.com)."""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    with pytest.raises(ManualRunError, match="generate FID first"):
        run_manual_spectrum(
            manager, exp_id, data_id, {"process.com": "#!/bin/csh\n"}
        )
    run = next(
        r
        for r in manager.project.workflow_runs
        if r.workflow_ref == "manual_process"
    )
    assert run.status == "failed"


def test_run_manual_spectrum_accepts_slice_fid(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.163-patch7: 3D uniform/NUS slice fid(fid/test*.fid) is not misjudged as missing fid."""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    # Simulate slicing conversion product: fid_path points to work/fid/ directory.
    work = manager.data_dir(exp_id, data_id, "process")
    slice_dir = work / "fid"
    slice_dir.mkdir(parents=True, exist_ok=True)
    (slice_dir / "test001.fid").write_bytes(b"fid")
    (slice_dir / "test002.fid").write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, slice_dir)
    manager.save()
    spectrum = run_manual_spectrum(
        manager, exp_id, data_id, {"process.com": "#!/bin/csh\n# process\n"}
    )
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")
    assert manager.data(exp_id, data_id).status == "processed"


def test_run_manual_fid_com_registers_slice_fid(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.163-patch7/0.2.199-patch2: Single dataset sliced products are returned to the backend
    work/fid/."""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)

    class _SliceBackend:
        work_dir: str | None = None

        def convert_to_fid(
            self, experiment, data_dir, progress=None, fid_com_overrides=None
        ):
            slice_dir = Path(self.work_dir) / "fid"
            slice_dir.mkdir(parents=True, exist_ok=True)
            (slice_dir / "test001.fid").write_bytes(b"fid")
            (slice_dir / "test002.fid").write_bytes(b"fid")
            return {
                "success": True,
                "fid_path": str(slice_dir),
                "message": "ok",
                "logs": [],
            }

    fid_path = run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# fid\n",
        backend=_SliceBackend(),
    )
    fid_path = Path(fid_path)
    assert fid_path.is_dir() and list(fid_path.glob("test*.fid"))
    assert fid_path == manager.data_dir(exp_id, data_id, "process") / "fid"
    assert manager.data(exp_id, data_id).fid_path == str(fid_path)
    assert any(r.workflow_ref == "manual_fid" for r in manager.project.workflow_runs)


def test_run_manual_fid_com_accepts_data_id_output(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.163-patch13/0.2.199-patch2: The naming of fid is unified as {data_id}.fid, which is
    returned by the backend."""
    from workflow.manual import run_manual_fid_com

    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)

    class _NamedBackend:
        work_dir: str | None = None

        def convert_to_fid(
            self, experiment, data_dir, progress=None, fid_com_overrides=None
        ):
            fid = Path(self.work_dir) / f"{experiment.dataset_id}.fid"
            fid.write_bytes(b"fid")
            return {
                "success": True,
                "fid_path": str(fid),
                "message": "ok",
                "logs": [],
            }

    fid_path = run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# fid\n",
        backend=_NamedBackend(),
    )
    assert Path(fid_path).name == f"{data_id}.fid"
    assert Path(fid_path).parent == manager.data_dir(exp_id, data_id, "process")


def _segmented_manager(tmp_path: Path, bruker_dir: Path):
    """Construct a segmented container: the root directory has no acqus and two sub-segments
    containing acqus."""
    container = tmp_path / "seg_container"
    container.mkdir()
    for seg in ("s1", "s2"):
        shutil.copytree(bruker_dir / "hsqc_2d", container / seg)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(
        entry.id,
        str(container),
        segments=[str(container / "s1"), str(container / "s2")],
    )
    manager.save()
    return manager, entry.id, data.id


class _SegFakeBackend:
    """Segmented artificial fake backend: record parameter coverage, output merged slice fid
    (simulate automatic link)."""

    work_dir: str | None = None
    last_overrides: dict | None = None

    def convert_to_fid(
        self, experiment, data_dir, progress=None, fid_com_overrides=None
    ):
        work = Path(self.work_dir)
        seg = work / "seg_001"
        seg.mkdir(parents=True, exist_ok=True)
        (seg / "fid.com").write_text(
            "#!/bin/csh\n# seg fid.com\n", encoding="utf-8"
        )
        merged = work / "merged" / "fid"
        merged.mkdir(parents=True, exist_ok=True)
        (merged / "test001.fid").write_bytes(b"fid")
        (merged / "test002.fid").write_bytes(b"fid")
        self.last_overrides = dict(fid_com_overrides or {})
        return {
            "success": True,
            "fid_path": str(merged),
            "message": "ok",
            "logs": [],
        }


def test_manual_fid_com_segmented_returns_reference(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch29dm: Segmented manual fid.com directly reads the reference segment (seg_001)
    without triggering conversion."""
    from workflow.manual import manual_fid_com

    manager, exp_id, data_id = _segmented_manager(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    seg = work / "seg_001"
    seg.mkdir(parents=True, exist_ok=True)
    (seg / "fid.com").write_text(
        "#!/bin/csh\n# seg fid.com\n", encoding="utf-8"
    )
    content = manual_fid_com(manager, exp_id, data_id, _SegFakeBackend())
    assert "# Segmented acquisition" in content
    assert "# seg fid.com" in content


def test_run_manual_fid_com_segmented_merges(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.163-patch13: Segmented data is manually fid.com Converted by backend/merge (no more
    errors), the manual parameter is passed to the segmented script in the form of overwriting."""
    from workflow.manual import run_manual_fid_com

    manager, exp_id, data_id = _segmented_manager(tmp_path, bruker_dir)
    backend = _SegFakeBackend()
    fid_path = run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# user change parameter -ySW 2800.000\n",
        backend=backend,
    )
    fid_path = Path(fid_path)
    assert fid_path == manager.data_dir(exp_id, data_id, "process") / "merged" / "fid"
    assert list(fid_path.glob("test*.fid"))
    data = manager.data(exp_id, data_id)
    assert data.fid_path == str(fid_path)
    assert data.status == "fid_ready"
    assert any(r.workflow_ref == "manual_fid" for r in manager.project.workflow_runs)
    assert backend.last_overrides == {"ySW": "2800.000"}


def test_manual_scripts_missing_fid_requires_generate_fid(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.163-patch14: The fid is missing when manually generating spectrum -> Prompt to perform
    the "Generate FID" step first, and do not sneak conversion at the spectrum entrance."""
    from workflow.manual import ManualRunError, manual_scripts

    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    with pytest.raises(ManualRunError, match="perform the \"Generate FID\" step first"):
        manual_scripts(manager, exp_id, data_id)


def test_quality_check_runs_on_manual_run_not_open(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.193: Open the editor (manual_scripts) and no longer run quality diagnosis. Click "Run"
    (run_manual_spectrum) to execute -- Open the big data script editor without lag."""
    from workflow.manual import manual_scripts, run_manual_spectrum

    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    work.mkdir(parents=True, exist_ok=True)
    final = f"{data_id}_process.com"
    (work / final).write_text("#!/bin/csh\n# final\n", encoding="utf-8")
    (work / f"{data_id}.fid").write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, work / f"{data_id}.fid")
    manager.save()

    called: dict = {}

    def fake_diagnostics(work_dir, experiment):
        called["work"] = str(work_dir)
        return SimpleNamespace(reports=["test report"], metrics={"snr": 10})

    monkeypatch.setattr(
        "workflow.direct_diagnostics.run_direct_diagnostics", fake_diagnostics
    )
    # Open: only read existing scripts, do not run diagnostics, do not write quality logs.
    scripts = manual_scripts(manager, exp_id, data_id)
    assert final in scripts
    assert "work" not in called
    assert not (work / "manual_quality.log").exists()

    # Run: Run quality diagnosis first and then execute script.
    runtime = _FakeRuntime(spectrum_name=f"{data_id}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    run_manual_spectrum(
        manager, exp_id, data_id, {final: "#!/bin/csh\n# process\n"}
    )
    assert called.get("work") == str(work)
    log = (work / "manual_quality.log").read_text(encoding="utf-8")
    assert "test report" in log
