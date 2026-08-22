"""人工处理 workflow 测试:已有脚本优先展示 + 动态主脚本键 + 渲染正确性。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.project import ProjectManager
from workflow.manual import (
    ManualRunError,
    manual_scripts,
    run_manual_spectrum,
)


def _manager(bruker_dir: Path, tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.add_experiment(str(bruker_dir), title="fixture")
    manager.save()
    return manager


def test_manual_scripts_renders_default_when_no_existing(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """process/ 无脚本时渲染默认 process.com(uniform 2D)。"""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["process.com"]
    assert "nmrPipe" in scripts["process.com"]


def test_manual_scripts_prefers_existing(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """自动运行过(process/ 已有同名脚本)直接展示已有脚本,不重新渲染。"""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001_process.com").write_text(
        "# EXISTING auto script\n", encoding="utf-8"
    )
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert scripts == {
        "d_001_process.com": "# EXISTING auto script\n"
    }
    assert "process.com" not in scripts


def test_manual_scripts_nus_renders_latest_and_prefers_existing(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """3D NUS:无已有脚本时渲染最新 nus.com(SMILE 无窗/调相、方向按
    采样模式);已有 d_001_nus.com 时直接展示。"""
    manager = _manager(bruker_dir / "nus_3d", tmp_path)
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["nus.com"]
    content = scripts["nus.com"]
    assert "nmrPipe -fn SMILE -nDim 3" in content
    assert "-xApod" not in content and "-xP0" not in content  # 窗/相位在 step3
    assert "-xAlt -xNeg" in content  # F2=States-TPPI(5)+force_neg,与 step3 一致
    assert "-maxIter 1500" in content  # 4/6144 采样率 -> 最低档(maxIter 自动按采样率)
    assert "-sampleCount 4" in content  # manual 自动填真实 nuslist 行数

    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001_nus.com").write_text(
        "# EXISTING nus script\n", encoding="utf-8"
    )
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["d_001_nus.com"]
    assert "# EXISTING nus script" in scripts["d_001_nus.com"]


class FakeRuntime:
    """模拟 csh 执行:直接产出目标谱文件并返回成功。"""

    def __init__(self, spectrum_rel: Path) -> None:
        self.calls: list[tuple] = []
        self.spectrum_rel = spectrum_rel

    def run(self, cmd, cwd=None, timeout=None) -> SimpleNamespace:
        self.calls.append((cmd, cwd, timeout))
        target = Path(cwd) / self.spectrum_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"FT2")
        return SimpleNamespace(returncode=0, stderr="")


def test_run_manual_spectrum_uses_first_script_key(
    bruker_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """主脚本键动态化:自动路径的 d_001_process.com 名称直接可运行。"""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001.fid").write_bytes(b"FID")
    runtime = FakeRuntime(Path("d_001.ft2"))
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)

    result = run_manual_spectrum(
        manager,
        "exp_001",
        "d_001",
        {"d_001_process.com": "#!/bin/csh\nxyz2pipe -in x.fid\n"},
    )
    assert runtime.calls[0][0] == ["csh", "d_001_process.com"]
    assert Path(result).is_file()
    runs = [
        run
        for run in manager.project.workflow_runs
        if run.workflow_ref == "manual_process"
    ]
    assert runs and runs[-1].status == "success"


def test_run_manual_spectrum_empty_scripts_raises(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """空脚本字典给出明确错误,而不是静默失败。"""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    with pytest.raises(ManualRunError, match="缺少处理脚本"):
        run_manual_spectrum(manager, "exp_001", "d_001", {})
