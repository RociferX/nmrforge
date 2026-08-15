"""处理流程控制器测试:自动化调用链 + 人工接口接线。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.project import ProjectManager
from gui.processing import ProcessingController
from workflow.engine import RunResult


def _manager_with_experiment(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.add_experiment("/fake/bruker/1", title="HSQC")
    manager.save()
    return manager


class FakeAutoProcessor:
    def __init__(self, backend) -> None:
        self.backend = backend

    def run(self, experiment) -> RunResult:
        assert experiment is not None
        return RunResult(
            status="success", logs=["后端处理完成"], report=Path("spectra/x.ft2")
        )


def test_auto_run_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_experiment(tmp_path)
    entry = manager.project.experiment("exp_001")
    monkeypatch.setattr(
        "gui.processing.read_dataset", lambda path: {"id": str(path)}
    )
    monkeypatch.setattr(
        "backend.factory.create_backend", lambda config: object()
    )
    monkeypatch.setattr("gui.processing.AutoProcessor", FakeAutoProcessor)
    controller = ProcessingController()
    result = controller.auto_run_sync(entry)
    assert result["status"] == "success"
    assert "后端处理完成" in result["logs"]
    assert result["experiment_id"] == "exp_001"


def test_auto_run_sync_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager_with_experiment(tmp_path)
    entry = manager.project.experiment("exp_001")
    monkeypatch.setattr(
        "gui.processing.read_dataset", lambda path: (_ for _ in ()).throw(OSError("no data"))
    )
    controller = ProcessingController()
    with pytest.raises(OSError, match="no data"):
        controller.auto_run_sync(entry)


def test_manual_interfaces_require_manager() -> None:
    """人工接口未绑定项目时给出明确错误(不再抛 NotImplementedError 占位)。"""
    controller = ProcessingController()
    with pytest.raises(RuntimeError, match="未绑定项目"):
        controller.manual_fid_com(None)
    with pytest.raises(RuntimeError, match="未绑定项目"):
        controller.manual_scripts(None)
    with pytest.raises(RuntimeError, match="未绑定项目"):
        controller.run_manual_spectrum(None, {})
    with pytest.raises(RuntimeError, match="未绑定项目"):
        controller.save_peaks_manual(None, [])


def test_save_peaks_manual_writes_list_and_registers_run(
    tmp_path: Path,
) -> None:
    """人工峰表保存:写 data/peaks Poky .list + 登记 manual_peaks 运行。"""
    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
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
    """生成谱图阶段进展经 progress 回调上报,并默认接相位优化。"""
    manager = _manager_with_experiment(tmp_path)
    data = manager.project.experiment("exp_001").data[0]
    messages: list[str] = []
    monkeypatch.setattr(
        "workflow.stepwise.generate_spectrum",
        lambda *args, **kwargs: "/tmp/x.ft2",
    )
    monkeypatch.setattr(
        "workflow.stepwise.optimize_phase_brute_force",
        lambda *args, **kwargs: {
            "spectrum_path": "/tmp/opt.ft2",
            "phase": {"F2": (0.0, -127.5), "F1": (60.0, 35.0)},
            "optimized": True,
        },
    )
    controller = ProcessingController(manager)
    path = controller.generate_spectrum(
        data,
        exp_id="exp_001",
        data_id="d_001",
        progress=messages.append,
    )
    assert path == "/tmp/opt.ft2"
    assert messages
    assert any("读取数据" in msg for msg in messages)
    assert any("基础谱图完成" in msg for msg in messages)
    assert any("相位优化完成" in msg for msg in messages)


def test_generate_spectrum_phase_optimize_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """phase_optimize=False 只生成基础谱(调试路径)。"""
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
    assert any("终谱已就位" in msg for msg in messages)
    assert not any("相位优化完成" in msg for msg in messages)


def test_param_schema_returns_editable_defaults() -> None:
    """param_schema:返回可编辑参数结构(zero_fill/ext/sampling 等键)。"""
    schema = ProcessingController().param_schema()
    assert "properties" in schema
    props = schema["properties"]
    assert "zero_fill" in props
    assert "ext_lo" in props and "ext_hi" in props and "extract" in props
    assert "sampling" in props
    default = schema["default"]
    assert default["zero_fill"] == 2
    assert default["ext_lo"] == "10.5"
    assert default["sampling"]["ft_alt"] is True
