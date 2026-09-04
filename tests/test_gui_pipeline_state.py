"""Pipeline 状态机完善测试:OUTDATED + 指纹校验(gui/pipeline_state)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.pipeline_panel import PipelinePanel, compute_step_statuses
from gui.pipeline_state import (
    file_fingerprint,
    input_fingerprint,
    load_pipeline_state,
    record_step_success,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _manager_with_artifacts(tmp_path: Path):
    """项目 + 实验类型 + 样品数据 + 全套产物(fid/谱/峰表/报告,无指纹状态)。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/bruker/1")
    exp_id, data_id = entry.id, data.id
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{exp_id}-{data_id}.fid"
    fid.write_bytes(b"fid-v1")
    manager.set_data_fid(exp_id, data_id, fid)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2-v1")
    manager.set_data_spectrum(exp_id, data_id, ft2)
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    csv_path = peaks / f"{exp_id}-{data_id}.csv"
    csv_path.write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,G1\n",
        encoding="utf-8",
    )
    report = manager.data_dir(exp_id, data_id, "report")
    report.mkdir(parents=True, exist_ok=True)
    (report / "report.html").write_text("<html>ok</html>", encoding="utf-8")
    manager.save()
    return manager, exp_id, data_id, {"fid": fid, "ft2": ft2, "csv": csv_path}


def _record_all(manager: ProjectManager, exp_id: str, data_id: str) -> None:
    for step in ("fid", "spectrum", "smile", "peaks"):
        record_step_success(manager, exp_id, data_id, step)


def test_file_fingerprint_changes_with_content(tmp_path: Path) -> None:
    """文件指纹:内容变化 → 指纹变化;缺失文件返回 None。"""
    path = tmp_path / "a.txt"
    path.write_bytes(b"abc")
    first = file_fingerprint(path)
    path.write_bytes(b"abd")
    second = file_fingerprint(path)
    assert first != second
    assert file_fingerprint(tmp_path / "missing") is None


def test_raw_fingerprint_ignores_mtime_touch() -> None:
    """0.2.84 回归:小文件仅 mtime 被 touch(内容不变)不改变 raw
    指纹——后端转换会 touch profYZ.dat 等辅助文件,纯 mtime 指纹
    曾导致 3D 生成 FID 后导入/生成FID 双双误判 OUTDATED。"""
    import os
    import tempfile
    from pathlib import Path as _Path

    from core.project import ProjectManager
    from gui import pipeline_state as ps

    with tempfile.TemporaryDirectory() as td:
        root = _Path(td)
        manager = ProjectManager.create_project(root / "proj", "demo")
        entry = manager.create_experiment(title="e")
        data = manager.import_data(entry.id, str(root / "src"))
        raw = manager.data_dir(entry.id, data.id, "raw")
        raw.mkdir(parents=True, exist_ok=True)
        data.raw_dir = str(raw)  # 指向项目内 raw 副本(模拟真实导入)
        manager.save()
        # 0.2.89:输入指纹只统计权威输入文件(acqus 等),touch/内容变化以
        # 该文件为准;处理产物(profYZ.dat 等辅助文件)不计入
        (raw / "acqus").write_text("payload-v1", encoding="utf-8")
        f1 = ps.raw_fingerprint(manager, entry.id, data.id)
        st = (raw / "acqus").stat()
        os.utime(raw / "acqus", (st.st_atime + 1, st.st_mtime + 1))
        f2 = ps.raw_fingerprint(manager, entry.id, data.id)
        assert f1 == f2, "mtime touch 不应改变 raw 指纹"
        (raw / "acqus").write_text("payload-v2", encoding="utf-8")
        f3 = ps.raw_fingerprint(manager, entry.id, data.id)
        assert f1 != f3, "内容变化应改变 raw 指纹"


def test_raw_processing_artifacts_ignored(tmp_path: Path) -> None:
    """0.2.89:处理在 raw/ 下写入/移动中间产物(fid/、mask/ 等)不改输入指纹。"""
    from gui import pipeline_state as ps

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="e")
    data = manager.import_data(entry.id, str(tmp_path / "src"))
    raw = manager.data_dir(entry.id, data.id, "raw")
    raw.mkdir(parents=True, exist_ok=True)
    data.raw_dir = str(raw)
    manager.save()
    (raw / "acqus").write_text("acqus-v1", encoding="utf-8")
    (raw / "ser").write_text("ser-v1", encoding="utf-8")
    f1 = ps.raw_fingerprint(manager, entry.id, data.id)
    # 模拟 3D NUS 处理:raw/ 下写入 fid/、mask/ 中间产物并移动个别文件
    (raw / "fid").mkdir()
    (raw / "mask").mkdir()
    (raw / "fid" / "test001.fid").write_text("x", encoding="utf-8")
    (raw / "mask" / "test001.fid").write_text("y", encoding="utf-8")
    (raw / "test.fid").write_text("z", encoding="utf-8")
    (raw / "test.fid").unlink()  # 模拟处理移动文件
    f2 = ps.raw_fingerprint(manager, entry.id, data.id)
    assert f1 == f2, "处理产物不应改变 raw 输入指纹"


def test_statuses_success_without_state(
    tmp_path: Path, qapp: QApplication
) -> None:
    """旧数据(无指纹状态):产物存在即 SUCCESS,不误判 OUTDATED。"""
    manager, exp_id, _data_id, _artifacts = _manager_with_artifacts(tmp_path)
    statuses = compute_step_statuses(manager, exp_id)
    for step in ("fid", "spectrum", "peaks"):
        assert statuses[step] == "SUCCESS"


def test_upstream_regen_marks_downstream_outdated(
    tmp_path: Path, qapp: QApplication
) -> None:
    """重新运行生成谱图并登记 → 峰挑选变为 OUTDATED(指纹校验)。"""
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    statuses = compute_step_statuses(manager, exp_id)
    assert all(st == "SUCCESS" for st in statuses.values())

    artifacts["ft2"].write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "OUTDATED"

    # 重新挑峰(峰表内容更新)并登记 → peaks 恢复 SUCCESS
    artifacts["csv"].write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n"
        "1,8.0,115.0,100,20,G1\n2,7.5,118.0,80,15,A2\n",
        encoding="utf-8",
    )
    record_step_success(manager, exp_id, data_id, "peaks")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["peaks"] == "SUCCESS"


def test_raw_change_marks_fid_outdated_and_propagates(
    tmp_path: Path, qapp: QApplication
) -> None:
    """原始数据变化 → FID OUTDATED,并沿依赖传播到下游。"""
    manager, exp_id, data_id, _artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    meta = manager.data_metadata_path(exp_id, data_id)
    meta.write_text("changed", encoding="utf-8")
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["fid"] == "OUTDATED"
    assert statuses["spectrum"] == "OUTDATED"
    assert statuses["peaks"] == "OUTDATED"


def test_segmented_merged_fid_directory_counts_as_done(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.108:分段合并 FID 为 process/merged/fid 目录时,FID 步骤 SUCCESS。"""
    manager, exp_id, data_id, _artifacts = _manager_with_artifacts(tmp_path)
    process = manager.data_dir(exp_id, data_id, "process")
    merged_fid = process / "merged" / "fid"
    merged_fid.mkdir(parents=True, exist_ok=True)
    (merged_fid / "test001.fid").write_bytes(b"x")
    manager.set_data_fid(exp_id, data_id, merged_fid)
    record_step_success(manager, exp_id, data_id, "fid")
    manager.save()
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["fid"] == "SUCCESS"


def test_simple_mode_disables_outdated(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.91:简单模式——只按产物文件判断,不出现 OUTDATED。"""
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    # spectrum 步骤的输入是 FID 文件:改写 FID → 默认 OUTDATED
    artifacts["fid"].write_bytes(b"fid-v2")
    assert compute_step_statuses(manager, exp_id)["spectrum"] == "OUTDATED"

    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"pipeline": {"simple_mode": True}},
    )
    statuses = compute_step_statuses(manager, exp_id)
    assert "OUTDATED" not in statuses.values()
    assert statuses["spectrum"] == "SUCCESS"


def test_mtime_fallback_without_state(
    tmp_path: Path, qapp: QApplication
) -> None:
    """旧数据(无指纹状态):上游产物比下游新 → OUTDATED(mtime 启发式)。"""
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "SUCCESS"
    artifacts["ft2"].write_bytes(b"ft2-new")
    # 显式把 ft2 mtime 设为 peaks 之后(避免同秒 mtime 相同导致
    # 全量运行时启发式判定不稳定)
    _peaks_mtime = artifacts["csv"].stat().st_mtime
    os.utime(artifacts["ft2"], (_peaks_mtime + 5, _peaks_mtime + 5))
    statuses = compute_step_statuses(manager, exp_id)
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "OUTDATED"


def test_panel_shows_outdated_and_rerun_button(
    tmp_path: Path, qapp: QApplication
) -> None:
    """面板:OUTDATED 步骤显示「重新运行」入口与下一步提示。"""
    manager, exp_id, data_id, artifacts = _manager_with_artifacts(tmp_path)
    _record_all(manager, exp_id, data_id)
    artifacts["ft2"].write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")

    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["peaks"].status_label.text().startswith("!")
    assert panel._rows["peaks"].run_button.text() == "重新运行"
    assert not panel._rows["peaks"].run_button.isHidden()
    assert "重新运行" in panel.next_label.text()
    panel.close()


def test_save_peaks_manual_records_state(tmp_path: Path) -> None:
    """人工保存峰表后登记 peaks 指纹。"""
    from gui.processing import ProcessingController

    manager, exp_id, data_id, _artifacts = _manager_with_artifacts(tmp_path)
    controller = ProcessingController(manager)
    peaks = [
        {
            "Peak_ID": 1,
            "H_shift": 8.0,
            "N_shift": 115.0,
            "Intensity": 100.0,
            "SN": 20.0,
            "label": "G1",
        }
    ]
    controller.save_peaks_manual(None, peaks, exp_id=exp_id, data_id=data_id)
    state = load_pipeline_state(manager, exp_id, data_id)
    assert "peaks" in state["steps"]
    assert state["steps"]["peaks"]["input_hash"] == input_fingerprint(
        manager, exp_id, data_id, "peaks"
    )


class _FakeController:
    """PipelinePanel 构造用最小假控制器(测试只刷新状态,不运行步骤)。"""




def test_next_label_skips_optional_smile(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-补29as:SMILE 未做时下一步指向峰挑选,并单独显示「可选做」。"""
    from gui.pipeline_panel import PipelinePanel
    from gui.pipeline_state import record_step_success

    manager, exp_id, data_id, _ft2 = _manager_with_artifacts(tmp_path)
    # 移除峰表,使 peaks 保持 READY(夹具默认全套产物)
    for f in manager.data_dir(exp_id, data_id, "peaks").glob("*"):
        f.unlink()
    for f in manager.data_dir(exp_id, data_id, "report").glob("*"):
        f.unlink()
    record_step_success(manager, exp_id, data_id, "spectrum")
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    text = panel.next_label.text()
    assert "峰挑选" in text
    assert "SMILE" in text
    assert "可选做" in text
    # SMILE 已完成后不再出现「可选做」,下一步为峰挑选
    record_step_success(manager, exp_id, data_id, "smile")
    panel.refresh()
    text = panel.next_label.text()
    assert "可选做" not in text
    assert "峰挑选" in text
    panel.close()

def test_pipeline_peaks_reference_selection(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-补29dl:峰挑选「参考谱」按钮 + 候选数据 + 参考加载。"""
    from core.peaks.peak_table import export_peaks_poky
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    exp = manager.create_experiment("HSQC")
    d1 = manager.import_data(exp.id, "/fake/1")
    d2 = manager.import_data(exp.id, "/fake/2")
    peaks_dir = manager.data_dir(exp.id, d1.id, "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    export_peaks_poky(
        peaks_dir / f"{exp.id}-{d1.id}.list",
        [{"N_shift": 118.0, "H_shift": 8.0, "Intensity": 1, "label": ""}],
        ndim=2,
    )
    manager.save()
    panel = PipelinePanel(manager)
    row = panel._rows["peaks"]
    assert row.step_id == "peaks"
    assert row.ref_button.text() == "参考谱"
    assert not row.ref_button.isHidden()
    candidates = panel._reference_candidates()
    assert any(did == d1.id for _n, _e, did in candidates)
    assert not any(did == d2.id for _n, _e, did in candidates)
    info = panel._load_reference(exp.id, d1.id)
    assert info is not None and info["peaks"]
    assert info["nuclei"] == ["15N", "1H"]
    # 0.2.199-补29fx:参考按 (exp, data) 隔离,切换数据不残留
    panel.set_selection("data", exp.id, d1.id)
    panel._ref_info[(exp.id, d1.id)] = info
    panel.refresh()
    assert not row.clear_ref_button.isHidden()
    panel.set_selection("data", exp.id, d2.id)
    assert row.clear_ref_button.isHidden()
    panel.set_selection("data", exp.id, d1.id)
    assert not row.clear_ref_button.isHidden()
    panel._on_clear_reference("peaks")
    assert panel._ref_info == {}
    assert row.clear_ref_button.isHidden()
    panel.close()

