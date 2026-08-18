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


def test_is_segmented_container(tmp_path: Path) -> None:
    """0.2.108:容器目录 = 顶层无 acqus 且 ≥2 个子目录含 acqus。"""
    from gui.processing import is_segmented_container

    container = tmp_path / "container"
    container.mkdir()
    for seg in ("seg1", "seg2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    assert is_segmented_container(container)
    # 顶层直接是 Bruker 数据集 → 不是容器
    single = tmp_path / "single"
    single.mkdir()
    (single / "acqus").write_text("x", encoding="utf-8")
    assert not is_segmented_container(single)
    # 只有 1 个分段子目录 → 不算容器
    one = tmp_path / "one"
    one.mkdir()
    (one / "seg1").mkdir()
    (one / "seg1" / "acqus").write_text("x", encoding="utf-8")
    assert not is_segmented_container(one)
    assert not is_segmented_container(tmp_path / "missing")


def test_import_segmented_dataset_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.108:分段采集导入透传 workflow.import_segmented_dataset。"""
    manager = _manager_with_experiment(tmp_path)
    controller = ProcessingController(manager)
    captured: dict = {}

    class _Result:
        experiment_id = "exp_009"
        data_id = "d_001"
        run_id = "R-1"
        warnings: list = []

    def fake(manager, source, *, title="", sample_id="", copy=True):
        captured.update(
            source=str(source),
            title=title,
            sample_id=sample_id,
            copy=copy,
        )
        return _Result()

    monkeypatch.setattr(
        "workflow.import_workflow.import_segmented_dataset", fake
    )
    result = controller.import_segmented_dataset(
        "/data/container", title="seg", copy=False
    )
    assert captured["source"] == "/data/container"
    assert captured["title"] == "seg"
    assert captured["copy"] is False
    assert result.data_id == "d_001"


def test_generate_spectrum_passes_phase_route_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.108:params["phase_route"] 透传 stepwise,且不再叠加旧暴力优化。"""
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

    def boom(*args, **kwargs):
        raise AssertionError("不应调用旧暴力优化")

    monkeypatch.setattr(
        "workflow.stepwise.optimize_phase_brute_force", boom
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
    """0.2.108:phase_route="none" 逃生口跳过相位优化。"""
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

    def boom(*args, **kwargs):
        raise AssertionError("不应调用旧暴力优化")

    monkeypatch.setattr(
        "workflow.stepwise.optimize_phase_brute_force", boom
    )
    path = controller.generate_spectrum(
        None,
        exp_id="exp_001",
        data_id="d_001",
        params={"phase_route": "none"},
    )
    assert path == "/tmp/x.ft2"
    assert captured.get("params") == {"phase_route": "none"}


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
