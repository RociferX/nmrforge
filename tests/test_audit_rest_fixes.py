"""2026-09-12 全项目审查整改(第二批)回归:REPORT-008 … QA-017。

覆盖:

- REPORT-008  分析(HSQC CSP)功能删除:步骤表/批量/模块/控制器全部不再出现,
              删除记录留档在 docs/tasks/archive/2026-09-12-analysis-removal.md;
- PROV-009    单一版本源 + WorkflowRun 写入软件/工具版本、参数来源与生命周期历史;
- MIG-010     旧项目迁移打开即原子落盘;
- LOG-011     源头坏点清理失败时不得再记「已删除」;
- BATCH-012   批量正式限定 2D(常量 + 跳过原因);
- STUB-013    native 后端骨架与未实现的模板校验删除,provider 创建期即校验;
- DEAD-014    无消费者的配置段与旧 SMILE 文案清理;
- PACK-015    AppImage 为唯一受支持发行物,spec datas 覆盖运行资源;
- QA-017      两项 Ruff 告警(由 ruff check 全量门禁覆盖,此处只锁关键点)。
"""

from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path

import pytest

import core
from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    Sampling,
    SamplingMode,
)
from core.project import ProjectManager
from core.project.models import SCHEMA_VERSION, ExperimentStatus


def _manager(tmp_path: Path):
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    return manager, entry, data


# ---------------------------------------------------------------- REPORT-008
def test_analysis_removed_from_product_surface() -> None:
    """REPORT-008:分析不再出现在步骤表、批量步骤、控制器与模块里。"""
    from gui.pipeline_panel import PIPELINE_STEPS
    from gui.processing import ProcessingController
    from workflow.batch import BATCH_STEPS

    assert [step[0] for step in PIPELINE_STEPS] == [
        "fid",
        "spectrum",
        "smile",
        "peaks",
    ]
    assert "analysis" not in BATCH_STEPS
    assert not hasattr(ProcessingController, "analyze")
    with pytest.raises(ImportError):
        importlib.import_module("workflow.analyze")
    with pytest.raises(ImportError):
        importlib.import_module("gui.report_panel")


def test_analysis_removal_is_documented() -> None:
    """REPORT-008 留档:删除范围/原因/恢复方法必须有文档记录。

    私有仓库里完整记录在 `docs/tasks/archive/2026-09-12-analysis-removal.md`;
    公开仓库不随附内部归档,此时以 `CHANGELOG.md` 里的同一记录为准。
    """
    note = Path("docs/tasks/archive/2026-09-12-analysis-removal.md")
    if note.is_file():
        text = note.read_text(encoding="utf-8")
        for key in ("workflow/analyze.py", "恢复", "git log"):
            assert key in text, key
        return
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert "REPORT-008" in changelog
    assert "workflow/analyze.py" in changelog
    assert "恢复方法" in changelog


def test_report_products_no_longer_drive_status(tmp_path: Path) -> None:
    """report/ 产物不再把实验推到 analyzed(该状态只留给旧项目)。"""
    manager, entry, data = _manager(tmp_path)
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / f"{data.id}.ft2").write_bytes(b"x")
    peaks = manager.data_dir(entry.id, data.id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / f"{data.id}.list").write_text("", encoding="utf-8")
    assert manager.infer_status(entry.id) is ExperimentStatus.PICKED
    report = manager.data_dir(entry.id, data.id, "report")
    report.mkdir(parents=True, exist_ok=True)
    (report / "whatever.html").write_text("<html/>", encoding="utf-8")
    assert manager.infer_status(entry.id) is ExperimentStatus.PICKED


# ------------------------------------------------------------------ PROV-009
def test_version_single_source() -> None:
    """PROV-009:pyproject 不写死版本,从 core.__version__ 动态取。"""
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dynamic"] == ["version"]
    assert "version" not in data["project"]
    assert (
        data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        == "core.__version__"
    )
    assert core.__version__ != "0.1.0"


def test_run_records_software_and_tool_versions(tmp_path: Path) -> None:
    """PROV-009:run 启动写版本/参数来源,结束写状态历史,落盘可读回。"""
    manager, entry, _data = _manager(tmp_path)
    run = manager.start_run(
        entry.id,
        workflow_ref="process",
        inputs={"data_id": "d_001"},
        params={"phase_route": "none"},
    )
    assert run.software_version == core.__version__
    assert run.tool_versions["nmrforge"] == core.__version__
    assert run.tool_versions.get("python")
    assert run.history == [{"at": run.started_at, "event": "started"}]
    assert run.decisions[0]["kind"] == "params"
    assert run.decisions[0]["source"] == "explicit"
    assert run.decisions[0]["keys"] == ["phase_route"]

    manager.finish_run(run.run_id, "success", outputs={"spectrum": "x.ft2"})
    assert run.history[-1]["event"] == "finished"
    assert run.history[-1]["status"] == "success"
    manager.save()

    reopened = ProjectManager.open_project(manager.root)
    stored = reopened.project.run(run.run_id)
    assert stored is not None
    assert stored.software_version == core.__version__
    assert stored.tool_versions["nmrforge"] == core.__version__
    assert stored.history[0]["event"] == "started"
    assert stored.history[-1]["event"] == "finished"


def test_run_records_default_param_source(tmp_path: Path) -> None:
    manager, entry, _data = _manager(tmp_path)
    run = manager.start_run(entry.id, workflow_ref="import")
    assert run.decisions[0]["source"] == "default"
    assert run.decisions[0]["keys"] == []


def test_finish_run_merges_probed_tool_versions(tmp_path: Path) -> None:
    """PROV-009:处理期间 backend 探测到的 NMRPipe 版本并入运行记录。"""
    from core.version import register_tool_version, reset_tool_versions

    reset_tool_versions()
    manager, entry, _data = _manager(tmp_path)
    run = manager.start_run(entry.id, workflow_ref="process")
    assert "nmrpipe" not in run.tool_versions
    register_tool_version("nmrpipe", "10.9")
    register_tool_version("smile", "10.9")
    manager.finish_run(run.run_id, "success")
    assert run.tool_versions["nmrpipe"] == "10.9"
    assert run.tool_versions["smile"] == "10.9"
    reset_tool_versions()


def test_nmrpipe_version_probe_is_defensive(tmp_path: Path) -> None:
    """探测失败只是不登记,不抛错(非可执行文件/目录不存在)。"""
    from backend.nmrpipe_version import clear_cache, register_nmrpipe_versions
    from core.version import registered_tool_versions, reset_tool_versions

    clear_cache()
    reset_tool_versions()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "nmrPipe").write_text("not an executable", encoding="utf-8")
    assert register_nmrpipe_versions(bin_dir) == {}
    assert "nmrpipe" not in registered_tool_versions()
    assert register_nmrpipe_versions(None) == {}
    reset_tool_versions()


# ------------------------------------------------------------------- MIG-010
def test_open_project_persists_migration(tmp_path: Path) -> None:
    """MIG-010:旧 schema 项目打开后不产生其它写操作,迁移也已落盘。"""
    root = tmp_path / "legacy"
    root.mkdir()
    legacy = {
        "schema_version": "1.1",
        "name": "legacy",
        "experiments": [
            {
                "id": "exp_001",
                "title": "HSQC",
                "source": "/sampleD",
                "segments": [],
                "imported_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        "processing_history": [],
    }
    (root / "project.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )
    ProjectManager.open_project(root)
    stored = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == SCHEMA_VERSION
    assert any(
        item.get("action") == "project_migrated"
        for item in stored["processing_history"]
    )


def test_open_current_schema_project_is_not_rewritten(tmp_path: Path) -> None:
    """已是当前 schema 的项目不因打开而重写(避免无谓写入/时间戳漂移)。"""
    manager, _entry, _data = _manager(tmp_path)
    manager.save()
    project_file = manager.root / "project.json"
    before = project_file.read_text(encoding="utf-8")
    ProjectManager.open_project(manager.root)
    assert project_file.read_text(encoding="utf-8") == before


# ------------------------------------------------------------------- LOG-011
def test_bad_point_log_does_not_claim_false_deletion(tmp_path: Path) -> None:
    """LOG-011:ser 缺失时只能说明回退清零,不得声称已从源头删除。"""
    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw"
    raw.mkdir()
    # 重复采样点 = 坏点;故意不提供 ser,使源头删除无法执行
    (raw / "nuslist").write_text("10\n10\n11\n", encoding="utf-8")
    experiment = Experiment(
        dataset_id="d_001",
        source_path=raw,
        ndim=2,
        dimensions=[
            Dimension(logical_axis="F2", nucleus="1H", role=AxisRole.DIRECT, td=8),
            Dimension(logical_axis="F1", nucleus="15N", td=16),
        ],
        sampling=Sampling(mode=SamplingMode.NUS),
    )
    logs: list[str] = []
    _valid, bad, removed = NMRPipeBackend()._clean_source_nus(
        experiment, [raw], logs
    )
    assert bad, "重复采样点应被识别为坏点"
    assert removed is False
    assert (raw / "nuslist").read_text(encoding="utf-8") == "10\n10\n11\n"
    joined = "\n".join(logs)
    assert "已从源头 ser/nuslist 删除" not in joined
    assert "回退为生成 FID 时清零" in joined
    assert "原始 ser/nuslist 未改动" in joined


# ----------------------------------------------------------------- BATCH-012
def test_batch_boundary_constant_is_2d() -> None:
    from workflow.batch import BATCH_SUPPORTED_NDIM

    assert BATCH_SUPPORTED_NDIM == 2


def test_batch_skips_3d_with_documented_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BATCH-012:3D 数据按正式边界跳过,不跑任何步骤且原因可读。"""
    from workflow import batch as batch_mod

    manager, entry, data = _manager(tmp_path)

    class _Exp3D:
        ndim = 3

    monkeypatch.setattr(
        "workflow.stepwise._read_experiment", lambda *a, **k: _Exp3D()
    )
    result = batch_mod.run_batch(
        manager, entry.id, [data.id], ["fid"], backend=object()
    )
    per = result["results"][data.id]
    assert per["status"] == "skipped"
    assert per["steps"] == {}
    assert "仅支持 2D" in per["error"]
    assert "能力边界" in per["error"]
    assert result["skipped"] == [data.id]


# ------------------------------------------------------------------ STUB-013
def test_native_backend_skeleton_removed() -> None:
    from backend.factory import SUPPORTED_PROVIDERS, create_backend

    assert SUPPORTED_PROVIDERS == ("nmrpipe",)
    with pytest.raises(ValueError, match="nmrpipe"):
        create_backend({"backend": {"provider": "native"}})
    with pytest.raises(ImportError):
        importlib.import_module("backend.native_backend")


def test_experiment_template_has_no_unimplemented_api() -> None:
    from core.experiments.registry import ExperimentTemplate

    assert not hasattr(ExperimentTemplate, "validate")


def test_default_provider_is_nmrpipe() -> None:
    from backend.config import load_config

    config = load_config({})
    assert config.get("backend", {}).get("provider", "nmrpipe") == "nmrpipe"


# ------------------------------------------------------------------ DEAD-014
def test_default_config_has_no_consumerless_sections() -> None:
    import yaml

    from core.app_paths import resource_path

    raw = yaml.safe_load(
        resource_path("config/nmrforge.yaml").read_text(encoding="utf-8")
    )
    for section in ("app", "optimization", "qc", "reporting", "logging"):
        assert section not in raw, section
    # peaks.localization(2026-09-13)的消费者是
    # core/peaks/localize.py::load_localization_defaults(峰定位方法/高斯 ROI),
    # 与 backend/processing/smile 一样属于「有运行时消费者」的配置段。
    assert set(raw) == {"backend", "processing", "smile", "peaks"}
    assert "localization" in raw["peaks"]


def test_smile_wording_matches_scheme_b() -> None:
    from gui.pipeline_panel import PIPELINE_STEPS

    description = next(
        text for step_id, _label, text, _deps in PIPELINE_STEPS
        if step_id == "smile"
    )
    assert "采用最优谱" not in description
    assert "不自动替换" in description


# ------------------------------------------------------------------ PACK-015
def test_appimage_spec_covers_runtime_resources() -> None:
    """PACK-015:唯一发行物(AppImage)的 datas 必须覆盖运行期资源目录。"""
    spec = Path("packaging/linux/NMRForge.spec").read_text(encoding="utf-8")
    for resource in ("config", "presets", "gui/assets"):
        assert f'("../../{resource}", "{resource}")' in spec, resource
    assert '"../../main.py"' in spec


def test_packaging_policy_declares_appimage_only() -> None:
    """PACK-015:发行边界必须写进打包文档(wheel/pip 只用于开发)。"""
    text = Path("docs/packaging.md").read_text(encoding="utf-8")
    assert "发行策略" in text
    assert "唯一受支持" in text
    assert "wheel" in text
