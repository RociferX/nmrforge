"""人工处理路径测试:查看/修改/运行 fid.com 与 process/nus 脚本。"""

from __future__ import annotations

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
    """模拟 csh 执行:fid.com 产出 test.fid,process/nus 产出终谱。"""

    def __init__(self, spectrum_name: str, fail: bool = False) -> None:
        self.spectrum_name = spectrum_name
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def run(self, argv, *, cwd=None, timeout=3600):
        name = Path(argv[-1]).name
        self.calls.append((name, str(cwd)))
        if self.fail:
            return SimpleNamespace(returncode=1, stderr="boom", stdout="")
        work = Path(cwd)
        if name == "fid.com":
            (work / "test.fid").write_bytes(b"fid")
        elif name in ("process.com", "nus.com"):
            (work / self.spectrum_name).write_bytes(b"ft2")
        return SimpleNamespace(returncode=0, stderr="", stdout="")


class _FakeBackend:
    """自动生成 fid.com 的假后端(manual_fid_com 未生成时调用)。"""

    work_dir: str | None = None

    def convert_to_fid(self, experiment, data_dir):
        work = Path(self.work_dir) if self.work_dir else Path(data_dir)
        (work / "fid.com").write_text("#!/bin/csh\n# auto fid.com\n", encoding="utf-8")
        (work / "test.fid").write_bytes(b"fid")
        return {"success": True, "fid_path": "x.fid", "message": "ok", "logs": []}


def _manager_with_raw(tmp_path: Path, bruker_dir: Path):
    """登记数据:source 指向 fixture 副本(raw 目录含 acqus)。"""
    import shutil

    raw = tmp_path / "data_src"
    shutil.copytree(bruker_dir / "hsqc_2d", raw)
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, str(raw))
    return manager, entry.id, data.id, raw


def test_manual_fid_com_generates_and_reads(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    content = manual_fid_com(manager, exp_id, data_id, _FakeBackend())
    assert "fid.com" in content
    assert (
        manager.data_dir(exp_id, data_id, "process") / "fid.com"
    ).is_file()


def test_run_manual_fid_com_registers(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name="x.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)

    fid_path = run_manual_fid_com(
        manager, exp_id, data_id, "#!/bin/csh\n# edited\n"
    )
    assert fid_path.endswith(".fid")
    assert Path(fid_path).parent == manager.data_dir(exp_id, data_id, "process")
    data = manager.data(exp_id, data_id)
    assert data.fid_path == fid_path
    assert data.status == "fid_ready"
    assert any(r.workflow_ref == "manual_fid" for r in manager.project.workflow_runs)


def test_manual_scripts_renders(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager, exp_id, data_id, _raw = _manager_with_raw(tmp_path, bruker_dir)
    scripts = manual_scripts(manager, exp_id, data_id)
    # 谱图步骤只渲染谱图脚本(process.com),不包含 fid.com
    assert sorted(scripts) == ["process.com"]
    assert scripts["process.com"].startswith("#!/bin/csh")


def test_run_manual_spectrum_uniform(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name=f"{raw.name}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    # 先生成 FID(独立步骤),谱图步骤只消费已转换 fid
    run_manual_fid_com(manager, exp_id, data_id, "#!/bin/csh\n# fid\n")
    runtime.calls.clear()
    spectrum = run_manual_spectrum(
        manager, exp_id, data_id, {"process.com": "#!/bin/csh\n# process\n"}
    )
    assert Path(spectrum).parent == manager.data_dir(exp_id, data_id, "spectra")
    # G2B-009:终谱只存 spectra/,process/ 不留副本
    assert not (manager.data_dir(exp_id, data_id, "process") / Path(spectrum).name).exists()
    data = manager.data(exp_id, data_id)
    assert data.spectrum_path == spectrum
    assert data.status == "processed"
    assert any(r.workflow_ref == "manual_process" for r in manager.project.workflow_runs)
    # 谱图步骤不执行 fid.com(生成 FID 是独立步骤)
    assert all(name != "fid.com" for name, _cwd in runtime.calls)
    assert data.fid_path.endswith(".fid")


def test_run_manual_spectrum_failure(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    ok_runtime = _FakeRuntime(spectrum_name=f"{raw.name}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: ok_runtime)
    run_manual_fid_com(manager, exp_id, data_id, "#!/bin/csh\n# fid\n")
    fail_runtime = _FakeRuntime(spectrum_name=f"{raw.name}.ft2", fail=True)
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: fail_runtime)
    with pytest.raises(ManualRunError, match="运行失败"):
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
    runtime = _FakeRuntime(spectrum_name=f"{raw.name}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    run_manual_fid_com(manager, exp_id, data_id, "#!/bin/csh\n# fid\n")
    with pytest.raises(ManualRunError, match="缺少处理脚本"):
        run_manual_spectrum(manager, exp_id, data_id, {})


def test_run_manual_spectrum_missing_fid(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """谱图步骤缺 fid 时报错并登记 failed run(不自动执行 fid.com)。"""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name=f"{raw.name}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    with pytest.raises(ManualRunError, match="请先生成 FID"):
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
    """0.2.163-补7:3D uniform/NUS 切片 fid(fid/test*.fid)不被误判为缺 fid。"""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)
    runtime = _FakeRuntime(spectrum_name=f"{raw.name}.ft2")
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    # 模拟切片式转换产物:fid_path 指向 work/fid/ 目录
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
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.163-补7:fid.com 转换产物为切片式时整体归位 work/fid/。"""
    manager, exp_id, data_id, raw = _manager_with_raw(tmp_path, bruker_dir)

    class _SliceRuntime:
        def run(self, argv, *, cwd=None, timeout=3600):
            src_dir = Path(cwd) / "fid"
            src_dir.mkdir(parents=True, exist_ok=True)
            (src_dir / "test001.fid").write_bytes(b"fid")
            (src_dir / "test002.fid").write_bytes(b"fid")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: _SliceRuntime())
    fid_path = run_manual_fid_com(
        manager, exp_id, data_id, "#!/bin/csh\n# fid\n"
    )
    fid_path = Path(fid_path)
    assert fid_path.is_dir() and list(fid_path.glob("test*.fid"))
    assert fid_path == manager.data_dir(exp_id, data_id, "process") / "fid"
    assert manager.data(exp_id, data_id).fid_path == str(fid_path)
    assert any(r.workflow_ref == "manual_fid" for r in manager.project.workflow_runs)
