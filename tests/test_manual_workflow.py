"""Manual processing workflow test: existing scripts are shown first + dynamic primary script
key + rendering correctness."""

from __future__ import annotations

import json
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


def test_manual_scripts_renders_default_when_no_existing(bruker_dir: Path, tmp_path: Path) -> None:
    """With no script in process/, the default process.com (uniform 2D) is rendered; fid must be
    ready first (0.2.163-patch14: no next step before the prerequisite completes)."""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001.fid").write_bytes(b"fid")
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["process.com"]
    assert "nmrPipe" in scripts["process.com"]


def test_manual_scripts_prefers_existing(bruker_dir: Path, tmp_path: Path) -> None:
    """Already auto-run (process/ has a same-named script): show the existing script directly,
    no re-render."""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001_process.com").write_text("# EXISTING auto script\n", encoding="utf-8")
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert scripts == {"d_001_process.com": "# EXISTING auto script\n"}
    assert "process.com" not in scripts


def test_manual_scripts_nus_renders_latest_and_prefers_existing(
    bruker_dir: Path, tmp_path: Path
) -> None:
    """3D NUS: render the latest nus.com when missing (SMILE without window, with explicit
    zero phase, direction per sampling mode); show d_001_nus.com directly if present."""
    manager = _manager(bruker_dir / "nus_3d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001.fid").write_bytes(b"fid")
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["nus.com"]
    content = scripts["nus.com"]
    assert "nmrPipe -fn SMILE -nDim 3" in content
    assert "-xApod" not in content  # Windowing remains in step3
    assert "-xP0 0 -xP1 0" in content  # F2 has an explicit default zero phase
    assert "-yP0 0 -yP1 0" in content  # F1 has an explicit default zero phase
    assert "-xAlt" in content  # F2=States-TPPI(5), consistent with step3 FT -alt
    assert "-xNeg" not in content  # handedness undecidable (no pulse program) -> no negation
    assert "-maxIter 1500" in content  # 4/6144 rate -> lowest tier (maxIter follows it)
    assert "-sampleCount 4" in content  # manual mode fills in the real nuslist row count

    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001_nus.com").write_text("# EXISTING nus script\n", encoding="utf-8")
    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert list(scripts) == ["d_001_nus.com"]
    assert "# EXISTING nus script" in scripts["d_001_nus.com"]


def test_manual_nus_uses_schedule_named_by_acqus(
    bruker_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    "Regression coverage: test manual nus uses schedule named by acqus."
    import shutil

    src = tmp_path / "named_nus"
    shutil.copytree(bruker_dir / "nus_3d", src)
    (src / "nuslist").replace(src / "CANH")
    with (src / "acqus").open("a", encoding="utf-8") as handle:
        handle.write("##$NUSLIST= <CANH>\n")
    manager = _manager(src, tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001.fid").write_bytes(b"fid")

    scripts = manual_scripts(manager, "exp_001", "d_001")
    assert "-sampleCount 4" in scripts["nus.com"]
    runtime = FakeRuntime(Path("d_001.ft3"))
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    run_manual_spectrum(manager, "exp_001", "d_001", scripts)

    assert (work / "nuslist").read_text(encoding="utf-8") == (src / "CANH").read_text(
        encoding="utf-8"
    )


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
    runs = [run for run in manager.project.workflow_runs if run.workflow_ref == "manual_process"]
    assert runs and runs[-1].status == "success"


def test_run_manual_spectrum_empty_scripts_raises(bruker_dir: Path, tmp_path: Path) -> None:
    """An empty script dict gives an explicit error instead of failing silently."""
    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    with pytest.raises(ManualRunError, match="缺少处理脚本"):
        run_manual_spectrum(manager, "exp_001", "d_001", {})


def test_script_parameter_audit_handles_negative_values_flags_and_insertions() -> None:
    from workflow.script_audit import script_changes

    baseline = (
        "#!/bin/csh\n# note\nnmrPipe -in data.fid \\\n"
        "| nmrPipe -fn PS -p0 -90.0 -p1 0 -di \\\n"
        "| nmrPipe -fn FT -auto\n"
    )
    edited = baseline.replace("-90.0", "-80.5").replace(" -di", "")
    edited = edited.replace("-fn FT -auto", "-fn FT -auto -neg | nmrPipe -fn POLY -ord 3")
    audit = script_changes("process.com", edited, baseline, "original")
    changes = audit["changes"]
    assert any(
        c["parameter"] == "-p0" and c["before"] == "-90.0" and c["after"] == "-80.5"
        for c in changes
    )
    assert any(
        c["parameter"] == "-di" and c["before"] is True and c["after"] is None for c in changes
    )
    assert any(c["parameter"] == "-neg" and c["after"] is True for c in changes)
    assert any(c["parameter"] == "command" and "POLY" in c["after"] for c in changes)
    cosmetic = baseline.replace("# note", "# changed note").replace("-p0 ", "-p0    ")
    assert script_changes("process.com", cosmetic, baseline)["changes"] == []
    assert not script_changes("process.com", edited, None)["baseline_available"]


@pytest.mark.parametrize("failed", [False, True])
def test_manual_script_audit_persists_params_and_actual_script(
    bruker_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    from workflow.script_audit import script_change_report

    manager = _manager(bruker_dir / "hsqc_2d", tmp_path)
    work = manager.data_dir("exp_001", "d_001", "process")
    work.mkdir(parents=True, exist_ok=True)
    (work / "d_001.fid").write_bytes(b"FID")
    baseline = "nmrPipe -in d_001.fid | nmrPipe -fn PS -p0 -90 -p1 0\n"
    edited = baseline.replace("-90", "-82.5")

    (work / "process.com").write_text(edited, encoding="utf-8")
    runtime = FakeRuntime(Path("d_001.ft2"))
    if failed:
        monkeypatch.setattr(
            runtime, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="runtime failed")
        )
    monkeypatch.setattr("workflow.manual.CshRuntime", lambda: runtime)
    monkeypatch.setattr("workflow.manual._run_quality_check", lambda *a: None)
    monkeypatch.setattr(
        "workflow.optimization_report.spectrum_quality_report_lines",
        lambda *a, **k: ["quality marker"],
    )
    logs = []
    kwargs = {"script_baselines": {"process.com": baseline}, "progress": logs.append}
    if failed:
        with pytest.raises(ManualRunError, match="runtime failed"):
            run_manual_spectrum(manager, "exp_001", "d_001", {"process.com": edited}, **kwargs)
    else:
        result = run_manual_spectrum(manager, "exp_001", "d_001", {"process.com": edited}, **kwargs)
    run = manager.project.workflow_runs[-1]
    assert run.status == ("failed" if failed else "success")
    audit = run.params["manual_script_changes"][0]
    assert audit["baseline_available"]
    assert audit["changes"][0]["before"] == "-90"
    assert audit["changes"][0]["after"] == "-82.5"
    assert (manager.root / run.snapshot_dir / "process.com").read_text() == edited
    recorded = json.loads((manager.root / run.snapshot_dir / "params.json").read_text())
    assert recorded["manual_script_changes"] == run.params["manual_script_changes"]
    if not failed:
        report = json.loads(Path(f"{result}.quality.json").read_text())["text"]
        assert "-p0" in report and "-90" in report and "-82.5" in report
        assert "quality marker" in report
        assert sum("-p0" in line for line in logs) == 1
    assert "-p0" in "\n".join(script_change_report([audit], failed=failed))


def test_previous_script_only_uses_same_data_successful_snapshot(tmp_path: Path) -> None:
    from workflow.script_audit import previous_script

    manager = ProjectManager.create_project(tmp_path / "history", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    d2 = manager.import_data(exp.id, "/fake/2")
    for data, status, text in [
        (d1, "success", "old"),
        (d2, "success", "other"),
        (d1, "failed", "failed"),
    ]:
        run = manager.start_run(exp.id, workflow_ref="manual_process", inputs={"data_id": data.id})
        manager.finish_run(run.run_id, status)
        manager.snapshot_run(run.run_id, {"process.com": text})
    assert previous_script(manager, exp.id, d1.id, "process.com", ("manual_process",)) == "old"
    assert previous_script(manager, exp.id, d1.id, "unknown.com", ("manual_process",)) is None
    assert (
        previous_script(manager, exp.id, d1.id, "../../project.json", ("manual_process",)) is None
    )
