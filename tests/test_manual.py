"""Manual processing path tests: view / edit / run fid.com and process/nus scripts."""

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
    """Fake csh execution: fid.com produces test.fid, process/nus produces the final
    spectrum."""

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
        elif name in ("process.com", "nus.com") or name.endswith(("_process.com", "_nus.com")):
            (work / self.spectrum_name).write_bytes(b"ft2")
        return SimpleNamespace(returncode=0, stderr="", stdout="")


class _FakeBackend:
    """Fake backend that generates fid.com automatically (called when manual_fid_com
    finds none)."""

    work_dir: str | None = None
    last_overrides: dict | None = None

    def convert_to_fid(
        self, experiment, data_dir, progress=None, params=None, fid_com_overrides=None
    ):
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
    """Register data: source points at a fixture copy (the raw directory holds acqus)."""
    import shutil

    raw = tmp_path / "data_src"
    shutil.copytree(bruker_dir / "hsqc_2d", raw)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, str(raw))
    return manager, entry.id, data.id, raw


def test_manual_fid_com_requires_auto_generated(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.199-patch29dm: a missing auto-generated fid.com is reported clearly instead
    of converting automatically."""
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    with pytest.raises(ManualRunError, match="请先自动生成 FID"):
        manual_fid_com(manager, exp_id, data_id, _FakeBackend())


def test_manual_fid_com_reads_existing(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.199-patch29dm: an already generated fid.com is read directly, with no conversion."""
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "fid.com").write_text("#!/bin/csh\n# existing fid.com\n", encoding="utf-8")
    content = manual_fid_com(manager, exp_id, data_id, _FakeBackend())
    assert "# existing fid.com" in content
    assert "# auto fid.com" not in content  # not converted again


def test_run_manual_fid_com_registers(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.199-patch2: a single-dataset manual fid.com matches the segmented path, with
    the parameters handed to the backend as overrides."""
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


def test_manual_scripts_renders(tmp_path: Path, bruker_dir: Path) -> None:
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{data_id}.fid").write_bytes(b"fid")
    scripts = manual_scripts(manager, exp_id, data_id)
    # The spectrum step only renders spectrum scripts (process.com), never fid.com
    assert sorted(scripts) == ["process.com"]
    assert scripts["process.com"].startswith("#!/bin/csh")


def test_run_manual_spectrum_uniform(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    # Generate the FID first (an independent step); the spectrum step only consumes the
    # converted fid
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
    # G2B-009: the final spectrum lives only in spectra/, no copy is left in process/
    assert not (manager.data_dir(exp_id, data_id, "process") / Path(spectrum).name).exists()
    data = manager.data(exp_id, data_id)
    assert data.spectrum_path == spectrum
    assert data.status == "processed"
    assert any(r.workflow_ref == "manual_process" for r in manager.project.workflow_runs)
    # The spectrum step does not execute fid.com (generating the FID is an independent
    # step)
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
    with pytest.raises(ManualRunError, match="运行失败"):
        run_manual_spectrum(
            manager,
            exp_id,
            data_id,
            {"process.com": "#!/bin/csh\n"},
        )
    run = next(r for r in manager.project.workflow_runs if r.workflow_ref == "manual_process")
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
    with pytest.raises(ManualRunError, match="缺少处理脚本"):
        run_manual_spectrum(manager, exp_id, data_id, {})


def test_run_manual_spectrum_missing_fid(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing fid in the spectrum step errors out and registers a failed run (fid.com
    is not run automatically)."""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    with pytest.raises(ManualRunError, match="请先生成 FID"):
        run_manual_spectrum(manager, exp_id, data_id, {"process.com": "#!/bin/csh\n"})
    run = next(r for r in manager.project.workflow_runs if r.workflow_ref == "manual_process")
    assert run.status == "failed"


def test_run_manual_spectrum_accepts_slice_fid(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.163-patch7: a 3D uniform/NUS slice fid (fid/test*.fid) is not mistaken for a
    missing fid."""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="d_001.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    # Simulate a sliced conversion product: fid_path points at the work/fid/ directory
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


def test_run_manual_fid_com_registers_slice_fid(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.163-patch7/0.2.199-patch2: a single-dataset sliced product is put back into
    work/fid/ by the backend."""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)

    class _SliceBackend:
        work_dir: str | None = None

        def convert_to_fid(
            self, experiment, data_dir, progress=None, params=None, fid_com_overrides=None
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


def test_run_manual_fid_com_accepts_data_id_output(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.163-patch13/0.2.199-patch2: fid naming is unified as {data_id}.fid and put
    back into place by the backend."""
    from workflow.manual import run_manual_fid_com

    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)

    class _NamedBackend:
        work_dir: str | None = None

        def convert_to_fid(
            self, experiment, data_dir, progress=None, params=None, fid_com_overrides=None
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
    """Build a segmented container: no acqus at the root, two sub-segments that have one."""
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
    """Fake backend for segmented manual runs: records the parameter overrides and
    produces merged slice fids (mirroring the automatic path)."""

    work_dir: str | None = None
    last_overrides: dict | None = None

    def convert_to_fid(
        self, experiment, data_dir, progress=None, params=None, fid_com_overrides=None
    ):
        work = Path(self.work_dir)
        seg = work / "seg_001"
        seg.mkdir(parents=True, exist_ok=True)
        (seg / "fid.com").write_text("#!/bin/csh\n# seg fid.com\n", encoding="utf-8")
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


def test_manual_fid_com_segmented_returns_reference(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.199-patch29dm: a segmented manual fid.com is read from the reference segment
    (seg_001), with no conversion."""
    from workflow.manual import manual_fid_com

    manager, exp_id, data_id = _segmented_manager(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    seg = work / "seg_001"
    seg.mkdir(parents=True, exist_ok=True)
    (seg / "fid.com").write_text("#!/bin/csh\n# seg fid.com\n", encoding="utf-8")
    content = manual_fid_com(manager, exp_id, data_id, _SegFakeBackend())
    assert "# 分段采集" in content
    assert "# seg fid.com" in content


def test_run_manual_fid_com_segmented_merges(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.163-patch13: a segmented manual fid.com is converted/merged by the backend
    (no longer an error); the manual parameters go to the per-segment scripts as
    overrides."""
    from workflow.manual import run_manual_fid_com

    manager, exp_id, data_id = _segmented_manager(tmp_path, bruker_dir)
    backend = _SegFakeBackend()
    fid_path = run_manual_fid_com(
        manager,
        exp_id,
        data_id,
        "#!/bin/csh\n# 用户改参数\n-ySW 2800.000\n",
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


def test_run_manual_fid_com_only_reports_changed_params(tmp_path: Path, bruker_dir: Path) -> None:
    """2026-09-24 (user): editing the script by hand = hand only the parameters that
    changed relative to the automatic baseline to the backend.

    The baseline is the ``fid.com.auto`` left behind by the conversion (bruker -AUTO plus
    the backend patches, untouched), so a second run (where the user sees the script with
    the previous overrides already applied) does not take the previous manual parameters as
    the baseline and drop them, and a segment's own parameters are not overwritten by the
    reference segment's values.
    """
    from workflow.manual import run_manual_fid_com

    manager, exp_id, data_id = _segmented_manager(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    seg = work / "seg_001"
    seg.mkdir(parents=True, exist_ok=True)
    base = "#!/bin/csh\n-xN 384 -yN 36 -ySW 1824.534\n"
    (seg / "fid.com.auto").write_text(base, encoding="utf-8")
    (seg / "fid.com").write_text(base, encoding="utf-8")
    backend = _SegFakeBackend()
    shown = "#!/bin/csh\n-xN 384 -yN 36 -ySW 1824.534 -xSW 11904.762\n"
    run_manual_fid_com(manager, exp_id, data_id, shown, backend=backend)
    assert backend.last_overrides == {"xSW": "11904.762"}
    # Second run: the user already sees "baseline + previous overrides"; changing one more
    # parameter keeps both changes
    (seg / "fid.com").write_text(shown, encoding="utf-8")
    run_manual_fid_com(manager, exp_id, data_id, shown.replace("-yN 36", "-yN 40"), backend=backend)
    assert backend.last_overrides == {"xSW": "11904.762", "yN": "40"}
    run = manager.project.workflow_runs[-1]
    assert run.params["fid_com_overrides"] == backend.last_overrides
    changes = run.params["manual_script_changes"][0]["changes"]
    assert any(
        c["parameter"] == "-yN" and c["before"] == "36" and c["after"] == "40" for c in changes
    )


def test_run_manual_spectrum_finds_merged_slice_fid(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-24 (user): a multi-part merge product lands in merged/, so the manual
    spectrum run no longer falsely reports a missing fid.

    Field report: a multi-part fid sits elsewhere than a single dataset's, so the manual
    "Run" that generates the spectrum reported "fid not found" -- previously only the
    registered path and ``work/{dataset_id}.fid`` were recognized.
    """
    from workflow.manual import run_manual_spectrum

    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    work = manager.data_dir(exp_id, data_id, "process")
    merged = work / "merged" / "fid"
    merged.mkdir(parents=True, exist_ok=True)
    (merged / "test001.fid").write_bytes(b"fid")
    runtime = _FakeRuntime(spectrum_name=f"{data_id}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    manager.save()  # fid_path not registered (old work directory / upstream did not write it back)
    spectrum = run_manual_spectrum(
        manager, exp_id, data_id, {"process.com": "#!/bin/csh\n# process\n"}
    )
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")


def test_manual_scripts_missing_fid_requires_generate_fid(tmp_path: Path, bruker_dir: Path) -> None:
    """0.2.163-patch14: a missing fid on manual spectrum generation -> the user is asked
    to run the "Generate FID" step first; the spectrum entry point does not sneak in a
    conversion."""
    from workflow.manual import ManualRunError, manual_scripts

    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    with pytest.raises(ManualRunError, match="请先执行「生成 FID」步骤"):
        manual_scripts(manager, exp_id, data_id)


def test_quality_check_runs_on_manual_run_not_open(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.193: opening the editor (manual_scripts) no longer runs the quality diagnosis;
    only clicking "Run" (run_manual_spectrum) does -- opening the script editor of a
    large dataset no longer stalls."""
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
        return SimpleNamespace(reports=["测试报告"], metrics={"snr": 10})

    monkeypatch.setattr("workflow.direct_diagnostics.run_direct_diagnostics", fake_diagnostics)
    # Open: only read the existing scripts; no diagnosis, no quality log written
    scripts = manual_scripts(manager, exp_id, data_id)
    assert final in scripts
    assert "work" not in called
    assert not (work / "manual_quality.log").exists()

    # Run: do the quality diagnosis first, then execute the script
    runtime = _FakeRuntime(spectrum_name=f"{data_id}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    run_manual_spectrum(manager, exp_id, data_id, {final: "#!/bin/csh\n# process\n"})
    assert called.get("work") == str(work)
    log = (work / "manual_quality.log").read_text(encoding="utf-8")
    assert "测试报告" in log


def test_reference_fid_com_never_uses_the_displayed_script(tmp_path: Path) -> None:
    """B8: the manual baseline prefers `fid.com.auto`, then `raw/fid.com`; it never takes
    `seg_001/fid.com`.

    The latter is the copy **shown to a human and already carrying the previous manual
    overrides**: taking it as the baseline makes the second manual run diff to nothing and
    silently drops the previous manual parameters (2026-09-24 review).
    """
    from workflow.manual import _reference_fid_com_path

    work = tmp_path / "process"
    seg = work / "seg_001"
    seg.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir()
    (seg / "fid.com").write_text("manual-values", encoding="utf-8")
    (raw / "fid.com").write_text("auto-values", encoding="utf-8")
    assert _reference_fid_com_path(work, raw, [str(raw)]) == raw / "fid.com"

    (seg / "fid.com.auto").write_text("baseline", encoding="utf-8")
    assert _reference_fid_com_path(work, raw, [str(raw)]) == seg / "fid.com.auto"

    (seg / "fid.com.auto").unlink()
    (raw / "fid.com").unlink()
    # Neither present -> None (the caller falls back to "the keys of the whole script",
    # not to the manual values as the baseline)
    assert _reference_fid_com_path(work, raw, [str(raw)]) is None
