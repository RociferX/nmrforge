"""重新处理入口测试:SUCCESS 步骤显示「重新处理」并触发重跑。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from core.project import ProjectManager
from gui.log_panel import LogPanel
from gui.pipeline_panel import PipelinePanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class SyncThread:
    """把后台线程变为同步执行(与 test_gui_layout 一致)。"""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _FakeController:
    """记录步骤调用;不写产物(重跑后仍按现有产物推断状态)。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_fid(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_fid")
        return "/tmp/x.fid"

    def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_spectrum")
        return "/tmp/x.ft2"

    def pick_peaks(self, data, exp_id=None, data_id=None) -> dict:
        self.calls.append("pick_peaks")
        return {"status": "success", "peak_count": 1}

    def analyze(self, data, exp_id=None, data_id=None) -> dict:
        self.calls.append("analyze")
        return {"status": "pending"}


def _manager_with_artifacts(tmp_path: Path):
    """实验类型 + 样品数据 + 全套产物(fid/谱/峰表/报告),无指纹状态。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("HSQC")
    data = manager.import_data(entry.id, "/fake/1")
    exp_id, data_id = entry.id, data.id
    process = manager.data_dir(exp_id, data_id, "process")
    process.mkdir(parents=True, exist_ok=True)
    fid = process / f"{exp_id}-{data_id}.fid"
    fid.write_bytes(b"fid")
    manager.set_data_fid(exp_id, data_id, fid)
    spectra = manager.data_dir(exp_id, data_id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    ft2 = spectra / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2")
    manager.set_data_spectrum(exp_id, data_id, ft2)
    peaks = manager.data_dir(exp_id, data_id, "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / f"{exp_id}-{data_id}.csv").write_text(
        "Peak_ID,H_shift,N_shift,Intensity,SN,label\n1,8.0,115.0,100,20,G1\n",
        encoding="utf-8",
    )
    report = manager.data_dir(exp_id, data_id, "report")
    report.mkdir(parents=True, exist_ok=True)
    (report / "report.html").write_text("<html>ok</html>", encoding="utf-8")
    manager.save()
    return manager, exp_id, data_id


def test_success_steps_show_reprocess_button(
    tmp_path: Path, qapp: QApplication
) -> None:
    """SUCCESS 步骤显示「重新处理」入口。"""
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    for step_id in ("fid", "peaks", "analysis"):
        assert panel._rows[step_id].run_button.text() == "重新处理"
        assert not panel._rows[step_id].run_button.isHidden()
    # 0.2.163-补5:spectrum 拆「重新优化」+「重新运行终脚本」
    spectrum_row = panel._rows["spectrum"]
    assert spectrum_row.run_button.text() == "重新优化"
    assert not spectrum_row.run_button.isHidden()
    assert not spectrum_row.rerun_final_button.isHidden()
    panel.close()


def test_reprocess_spectrum_invokes_controller(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点击「重新处理」调用对应 ProcessingController 方法。"""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    controller = _FakeController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["spectrum"].run_button.text() == "重新优化"
    panel._on_run_requested("spectrum")
    assert controller.calls == ["generate_spectrum"]
    # 重跑后步骤仍为 SUCCESS(假控制器不写产物,指纹状态无变化)
    assert panel._rows["spectrum"].status_label.text().startswith("✓")
    assert panel._rows["spectrum"].run_button.text() == "重新优化"
    panel.close()
    log.close()


def test_reprocess_peaks_and_fid_available(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """峰挑选/生成 FID 同样提供重新处理入口(点击触发对应方法)。"""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    controller = _FakeController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", exp_id, data_id)
    panel._on_run_requested("peaks")
    panel._on_run_requested("fid")
    assert controller.calls == ["pick_peaks", "generate_fid"]
    panel.close()


def test_rerun_final_applies_latest_ext(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.163-补5:重新运行终脚本直接改最终脚本 EXT 窗口,其余参数不变。"""
    from gui.pipeline_panel import PipelinePanel

    monkeypatch.setattr("threading.Thread", SyncThread)
    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    # 生成一份已有终跑脚本(uniform),含相位/窗/基线等其它参数
    process = manager.data_dir(exp_id, data_id, "process")
    script = process / f"{data_id}_process.com"
    script.write_text(
        "#!/bin/csh\n"
        "| nmrPipe -fn SP -off 0.5 -end 0.95 -pow 2 -c 0.5 \\\n"
        "| nmrPipe -fn FT -auto \\\n"
        "| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2 \\\n"
        "| nmrPipe -fn PS -p0 10 -p1 1.5 -di \\\n"
        "| nmrPipe -fn POLY -auto -time \\\n",
        encoding="utf-8",
    )
    manager.save()

    calls: list[str] = []
    ran_scripts: dict = {}

    class _CaptureController(_FakeController):
        def run_manual_spectrum(
            self, data, scripts, exp_id=None, data_id=None, progress=None
        ) -> str:
            calls.append("run_manual_spectrum")
            ran_scripts.update(scripts)
            return "/tmp/x.ft2"

    panel = PipelinePanel(manager, _CaptureController())
    panel.set_selection("data", exp_id, data_id)
    # 用户设置终跑直接维范围 8.0-6.0(应用此范围到优化过程默认开启)
    panel._final_ext[(exp_id, data_id)] = ("8.0", "6.0", True)
    panel._on_rerun_final_requested("spectrum")
    assert calls == ["run_manual_spectrum"]
    # 运行的脚本内容:EXT 已更新为 8.0-6.0,其它参数(SP/FT/PS/POLY)原样
    ran_content = ran_scripts.get(f"{data_id}_process.com", "")
    assert "-x1 8.0ppm -xn 6.0ppm" in ran_content
    assert "-x1 10.5ppm -xn 6.5ppm" not in ran_content
    assert "-p0 10 -p1 1.5" in ran_content  # 相位未动
    assert "POLY -auto -time" in ran_content  # 基线/诊断未动
    assert "SP -off 0.5 -end 0.95" in ran_content  # 窗函数未动
    panel.close()



def test_reprocess_downstream_becomes_outdated(
    tmp_path: Path, qapp: QApplication
) -> None:
    """重新处理谱图并登记指纹 → 下游峰挑选/分析标记过期。"""
    from gui.pipeline_state import record_step_success

    manager, exp_id, data_id = _manager_with_artifacts(tmp_path)
    for step in ("fid", "spectrum", "peaks", "analysis"):
        record_step_success(manager, exp_id, data_id, step)
    ft2 = manager.data_dir(exp_id, data_id, "spectra") / f"{exp_id}-{data_id}.ft2"
    ft2.write_bytes(b"ft2-v2")
    record_step_success(manager, exp_id, data_id, "spectrum")
    panel = PipelinePanel(manager, _FakeController())
    panel.set_selection("data", exp_id, data_id)
    assert panel._rows["spectrum"].run_button.text() == "重新优化"
    assert panel._rows["peaks"].status_label.text().startswith("!")
    assert panel._rows["analysis"].status_label.text().startswith("!")
    panel.close()
