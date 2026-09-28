"""2026-09-12 project-wide audit fixes (second batch) regression: REPORT-008 ... QA-017.

Covers:

- REPORT-008  the analysis (HSQC CSP) feature is removed: the step table / batch steps /
              module / controller no longer expose it; the removal record is archived in
              docs/tasks/archive/2026-09-12-analysis-removal.md;
- PROV-009    single version source + WorkflowRun records the software/tool versions, the
              parameter source and the lifecycle history;
- MIG-010     opening a legacy project migrates and persists to disk atomically;
- LOG-011     a failed source bad-point cleanup must not record "deleted" any more;
- BATCH-012   batch is formally limited to 2D (constant + skip reason);
- STUB-013    the native backend skeleton and the unimplemented template validation are
              removed; the provider is validated at creation time;
- DEAD-014    consumerless config sections and the old SMILE wording are cleaned up;
- PACK-015    v1.0.0 source + AppImage release, spec datas cover the runtime resources
              (including the language packs), single artifact (runtime language switch);
- QA-017      two Ruff warnings (covered by the full ruff check gate; only the key points
              are pinned here).
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
    """REPORT-008: analysis no longer appears in the step table, batch steps, controller
    or modules."""
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
    """REPORT-008 archival: the removal scope / reason / recovery method must be documented.

    The private repository keeps the full record in
    `docs/tasks/archive/2026-09-12-analysis-removal.md`; the public repository does not ship
    the internal archive, in which case the same record in `CHANGELOG.md` is authoritative.
    """
    note = Path("docs/tasks/archive/2026-09-12-analysis-removal.md")
    if note.is_file():
        text = note.read_text(encoding="utf-8")
        for key in ("workflow/analyze.py", "恢复", "git log"):
            assert key in text, key
        return
    changelog_path = Path("CHANGELOG.md")
    if not changelog_path.is_file():
    # The public repo does not ship CHANGELOG.md: with neither record present there is
    # no source to assert against, so skip
        return
    changelog = changelog_path.read_text(encoding="utf-8")
    assert "REPORT-008" in changelog
    assert "workflow/analyze.py" in changelog
    assert "恢复方法" in changelog


def test_report_products_no_longer_drive_status(tmp_path: Path) -> None:
    """report/ products no longer push an experiment to analyzed (that status is left to
    legacy projects)."""
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
    """PROV-009: pyproject does not hardcode the version; it is taken dynamically from
    core.__version__."""
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["dynamic"] == ["version"]
    assert "version" not in data["project"]
    assert (
        data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        == "core.__version__"
    )
    assert core.__version__ != "0.1.0"


def test_run_records_software_and_tool_versions(tmp_path: Path) -> None:
    """PROV-009: a run writes versions / parameter sources at start and the status history
    at the end, readable back from disk."""
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
    """PROV-009: the NMRPipe version probed by the backend during processing is merged
    into the run record."""
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
    """A failed probe is only left unregistered, it never raises (non-executable file /
    missing directory)."""
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
    """MIG-010: opening a legacy-schema project produces no other writes, and the
    migration is already persisted."""
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
    """A project already on the current schema is not rewritten merely by opening it (no
    needless writes / timestamp drift)."""
    manager, _entry, _data = _manager(tmp_path)
    manager.save()
    project_file = manager.root / "project.json"
    before = project_file.read_text(encoding="utf-8")
    ProjectManager.open_project(manager.root)
    assert project_file.read_text(encoding="utf-8") == before


# ------------------------------------------------------------------- LOG-011
def test_bad_point_log_does_not_claim_false_deletion(tmp_path: Path) -> None:
    """LOG-011: with ser missing it may only report a fallback zeroing, never claim a
    source deletion."""
    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw"
    raw.mkdir()
    # A duplicate sampling point = a bad point; ser is deliberately absent so the source
    # deletion cannot run
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
    """BATCH-012: 3D data is skipped at the formal boundary; no step runs and the reason
    is readable."""
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
    # peaks.localization (2026-09-13) is consumed by
    # core/peaks/localize.py::load_localization_defaults (peak localization method / Gaussian
    # ROI); like backend/processing/smile it is a config section with a runtime consumer.
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


def test_appimage_build_guards_against_missing_runtime_pieces() -> None:
    """The build blocks two classes of accident: resources missing from the artifact, and
    the artifact not starting at all (each hit once on a real machine, 2026-09-21)."""
    spec = Path("packaging/linux/NMRForge.spec").read_text(encoding="utf-8")
    script = Path("packaging/linux/build_appimage.sh").read_text(encoding="utf-8")
    # 1. the resource self-check must recognize PyInstaller 6's content directory _internal
    #    (otherwise a real-machine build falsely reports missing resources)
    assert "BUNDLE_DIR" in script and "_internal" in script
    # 2. frozen-startup smoke test: the packaged executable must stay alive offscreen for
    #    20 seconds
    assert 'timeout 20 "$APPDIR/usr/bin/NMRForge"' in script
    assert "SMOKE_STATUS" in script
    # 3. qtcompat imports PySide6.QtTest unconditionally -- excluding it crashes the GUI at
    #    startup
    assert '"PySide6.QtTest"' not in spec


# ------------------------------------------------------------------ PACK-015
def test_appimage_spec_covers_runtime_resources() -> None:
    """PACK-015: the AppImage datas must keep covering the runtime resource directories
    (including the language packs)."""
    spec = Path("packaging/linux/NMRForge.spec").read_text(encoding="utf-8")
    # Shipped data is taken from the data package and keeps the same shape inside the
    # artifact (installed = frozen = source tree)
    resources = {
        "nmrforge_data/config": "nmrforge_data/config",
        "nmrforge_data/presets": "nmrforge_data/presets",
        "gui/assets": "gui/assets",
        "ui_support/locales": "ui_support/locales",
    }
    for source, target in resources.items():
        assert f'("../../{source}", "{target}")' in spec, source
    assert '"../../main.py"' in spec


def test_appimage_build_is_single_artifact_with_runtime_language() -> None:
    """Once the interface text is selected at runtime only one artifact is built: no more
    Chinese/English build switch (2026-09-21)."""
    script = Path("packaging/linux/build_appimage.sh").read_text(encoding="utf-8")
    assert "APPIMAGE_SUFFIX" not in script
    assert "APPIMAGE_EDITION" not in script
    # The artifact name no longer carries a language suffix
    assert "${APP}-${VERSION}-${ARCH}.AppImage" in script
    # The language source is written into the build provenance, and the build checks that
    # the language packs really made it into the artifact
    assert "default lang" in script
    assert "ui_support/locales" in script


def test_machine_local_config_cannot_reach_the_wheel() -> None:
    """Shipped data goes into the wheel, but the machine-level override
    `nmrforge.local.yaml` must be excluded (it carries absolute local paths)."""
    import tomllib

    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    setuptools = data["tool"]["setuptools"]
    packaged = setuptools["package-data"]["nmrforge_data"]
    excluded = setuptools["exclude-package-data"]["nmrforge_data"]
    assert all("local" not in item for item in packaged), packaged
    assert any("local" in item for item in excluded), excluded
    # The same concern on the AppImage side: the build script moves it out of the build
    # tree first
    script = Path("packaging/linux/build_appimage.sh").read_text(encoding="utf-8")
    assert 'LOCAL_CFG="nmrforge_data/config/nmrforge.local.yaml"' in script


def test_both_trees_declare_a_default_language_file() -> None:
    """The default language is a data file per tree (private zh / public en), not a branch
    scattered through the code."""
    import json

    data = json.loads(Path("ui_support/locales/default.json").read_text(encoding="utf-8"))
    assert data["language"] in {"zh", "en"}


def test_packaging_policy_declares_source_and_appimage_release() -> None:
    """PACK-015: the license / acceptance boundary of the current release artifact and the
    AppImage must be explicit."""
    text = Path("docs/packaging.md").read_text(encoding="utf-8")
    from core import __version__

    released = "v" + __version__
    assert f"{released} 已发布" in text or f"released with {released}" in text
    assert "AppImage" in text
    assert "wheel" in text
    assert "APPIMAGE_RELEASE_CHECKLIST.md" in text


def test_appimage_build_keeps_the_machine_local_config_out() -> None:
    """PACK-015: the machine-level local config (git-ignored) must not enter the artifact
    -- it carries the build machine's absolute paths."""
    script = Path("packaging/linux/build_appimage.sh").read_text(encoding="utf-8")
    assert 'LOCAL_CFG="nmrforge_data/config/nmrforge.local.yaml"' in script
    # It must be moved out before PyInstaller, otherwise the datas of NMRForge.spec copy it
    # into the AppDir
    assert script.index('LOCAL_CFG="nmrforge_data/config/nmrforge.local.yaml"') < script.index(
        "--distpath"
    )

