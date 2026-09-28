"""Manual processing workflow test: existing scripts are shown first + dynamic primary script
key + rendering correctness."""

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
    """With no script in process/, the default process.com (uniform 2D) is rendered; fid must be
    ready first (0.2.163-patch14: no next step before the prerequisite completes)."""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001.fid").write_bytes(b"fid")
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["process.com"]
    assert "nmrPipe" in scripts["process.com"]


def test_manual_scripts_prefers_existing(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """Already auto-run (process/ has a same-named script): show the existing script directly,
    no re-render."""
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
    """3D NUS: with no existing script, render the latest nus.com (SMILE without window/phase
    modulation, direction per sampling mode); show d_001_nus.com directly if present."""
    manager = _manager(bruker_dir / "nus_3d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001.fid").write_bytes(b"fid")
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["nus.com"]
    content = scripts["nus.com"]
    assert "nmrPipe -fn SMILE -nDim 3" in content
    assert "-xApod" not in content and "-xP0" not in content  # Window/phase are in step3
    assert "-xAlt" in content  # F2=States-TPPI(5), consistent with step3 FT -alt
    assert "-xNeg" not in content  # handedness undecidable (no pulse program) -> no negation
    assert "-maxIter 1500" in content  # 4/6144 rate -> lowest tier (maxIter follows it)
    assert "-sampleCount 4" in content  # manual mode fills in the real nuslist row count

    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001_nus.com").write_text(
        "# EXISTING nus script\n", encoding="utf-8"
    )
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["d_001_nus.com"]
    assert "# EXISTING nus script" in scripts["d_001_nus.com"]


class FakeRuntime:
    """Simulate csh execution: produce the target spectrum file directly and report success."""

    def __init__(self, spectrum_rel: Path) -> None:
        self.calls: list[tuple] = []
        self.spectrum_rel = spectrum_rel

    def run(self, cmd, cwd=None, timeout=None, on_line=None) -> SimpleNamespace:
        self.calls.append((cmd, cwd, timeout))
        target = Path(cwd) / self.spectrum_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"FT2")
        return SimpleNamespace(returncode=0, stderr="")


def test_run_manual_spectrum_uses_first_script_key(
    bruker_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Primary script key is dynamic: the automatic-path name d_001_process.com runs as-is."""
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
    """An empty script dict gives an explicit error instead of failing silently."""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    with pytest.raises(ManualRunError, match="缺少处理脚本"):
        run_manual_spectrum(manager, "exp_001", "d_001", {})
