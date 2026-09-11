"""三栏布局 GUI 测试:项目树(Project→Experiment→Data)/ 五步 Pipeline / 谱图面板(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMenu

from core.project import ProjectManager
from gui.log_panel import LogPanel
from gui.main_window import MainWindow
from gui.pipeline_panel import (
    PIPELINE_STEPS,
    PipelinePanel,
    compute_step_statuses,
)
from gui.project_tree import ProjectTreePanel
from gui.spectrum_panel import SpectrumPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


class SyncThread:
    """把后台线程变为同步执行,测试不依赖线程时序。"""

    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


def _manager_with_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch | None = None
) -> ProjectManager:
    """在临时工作区创建项目,并让树/主窗口使用该工作区(测试隔离)。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = ProjectManager.create_project(workspace / "proj", "demo")
    for source, title in (("/sampleD", "HSQC"), ("/sampleE", "HNCACB")):
        entry = manager.add_experiment(source, title=title)
        entry.status = "registered"
    manager.save()
    if monkeypatch is not None:
        monkeypatch.setattr(
            "gui.main_window.WorkspaceManager",
            lambda: _TempWorkspace(workspace),
        )
        monkeypatch.setattr(
            "core.workspace.WorkspaceManager",
            lambda *a, **k: _TempWorkspace(workspace),
        )
    return manager


class _TempWorkspace:
    """指向临时目录的工作区桩。"""

    def __init__(self, root) -> None:
        self.root = Path(root)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root
    def delete_project(self, name: str, trash: bool = True) -> None:
        target = self.root / name
        if target.exists():
            import shutil

            shutil.rmtree(target)

    def rename_project(self, old_name: str, new_name: str) -> Path:
        target = self.root / old_name
        new_target = self.root / new_name
        if target.exists():
            target.rename(new_target)
        return new_target

    def list_projects(self) -> list[Path]:
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str, **kwargs):
        from core.project import ProjectManager

        return ProjectManager.create_project(self.root / name, name, **kwargs)


def _write_ft2(path: Path) -> None:
    from nmrglue.fileio import pipe

    shape = (64, 128)
    data = np.zeros(shape, dtype=np.float32)
    data[32, 64] = 100.0
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = shape[0]
    dic["FDF1SW"] = 6000.0
    dic["FDF1OBS"] = 600.0
    dic["FDF1CAR"] = 118.0
    dic["FDF1ORIG"] = 118.0 * 600.0
    dic["FDF2T"] = shape[1]
    dic["FDF2SW"] = 6000.0
    dic["FDF2OBS"] = 600.0
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600.0
    pipe.write(str(path), dic, data, overwrite=True)


class FakeProcessingController:
    """五步流程假控制器:generate_fid/generate_spectrum 记录调用并同步完成。"""

    def set_manager(self, manager) -> None:
        self.manager = manager

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_entry = None

    def import_data(self, entry, source) -> dict:
        self.calls.append("import_data")
        self.last_entry = entry
        return {"experiment_id": entry.id, "status": "imported"}

    def generate_fid(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_fid")
        self.last_entry = data
        return "/tmp/x.fid"

    def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
        self.calls.append("generate_spectrum")
        self.last_entry = data
        return "/tmp/x.ft2"


# ----------------------------------------------------------------------
# 项目树(Project → Experiment → Data)
# ----------------------------------------------------------------------
def test_project_tree_structure(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    assert panel.tree.topLevelItemCount() == 1
    workspace_item = panel.tree.topLevelItem(0)
    assert workspace_item.text(0) == "NMRForgeWorkspace"  # Workspace 根节点
    project_item = workspace_item.child(0)
    assert project_item.text(0) == "demo"  # 当前项目显示 project.name
    assert project_item.text(1) == "当前"  # 当前项目标记
    assert project_item.childCount() == 2
    exp_item = project_item.child(0)
    assert exp_item.text(0) == "HSQC"
    assert exp_item.childCount() == 1
    data_item = exp_item.child(0)
    assert data_item.text(0) == "Data d_001"
    assert data_item.text(1) == "已导入"
    assert data_item.childCount() == 6  # raw/process/spectra/peaks/figures/report
    panel.close()


def test_project_tree_data_status_shows_running(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补5:运行中的数据显示「运行中」,结束后恢复推断状态。"""
    from gui.project_tree import ProjectTreePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)

    def _data_item():
        return panel.tree.topLevelItem(0).child(0).child(0).child(0)

    assert _data_item().text(1) == "已导入"
    panel.mark_running("exp_001", "d_001")
    assert _data_item().text(1) == "运行中"
    panel.clear_running("exp_001", "d_001")
    assert _data_item().text(1) == "已导入"
    panel.close()


def test_project_tree_current_experiment_from_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    panel.select_experiment("exp_002")
    assert panel.current_experiment_id() == "exp_002"
    # 选中样品数据节点仍归一化到所属实验类型
    exp_item = panel.tree.topLevelItem(0).child(0).child(1)
    panel.tree.setCurrentItem(exp_item.child(0))
    assert panel.current_experiment_id() == "exp_002"
    assert panel._data_id_of(panel.tree.currentItem()) == "d_001"
    panel.close()


def test_project_tree_column_widths_readable(qapp: QApplication) -> None:
    panel = ProjectTreePanel()
    assert panel.tree.columnWidth(0) >= 180  # 对象列最小可读宽
    assert panel.tree.columnWidth(1) >= 70  # 状态列
    panel.close()


# ----------------------------------------------------------------------
# Pipeline 五步状态
# ----------------------------------------------------------------------
def test_pipeline_spectrum_row_ext_range_button_before_run(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-补15:生成谱图行运行按钮前有「直接维范围」按钮(其它步骤隐藏)。"""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    spectrum_row = panel._rows["spectrum"]
    assert not spectrum_row.ext_range_button.isHidden()
    assert panel._rows["fid"].ext_range_button.isHidden()
    assert panel._rows["peaks"].ext_range_button.isHidden()
    # 0.2.163-补5:按钮移到标题下方独立一行(ext_range 在 run 之前)
    button_row = spectrum_row.layout().itemAt(1)
    assert button_row is not None and hasattr(button_row, "count")
    widgets = [button_row.itemAt(i).widget() for i in range(button_row.count())]
    assert widgets.index(spectrum_row.ext_range_button) < widgets.index(
        spectrum_row.run_button
    )
    panel.close()


def test_pipeline_run_guard_blocks_repeat(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补12:运行中再次点击被拒绝,不重复启动。"""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    messages: list[str] = []
    panel.log_message.connect(messages.append)
    panel._run_active = True
    panel._on_run_requested("spectrum")
    assert any("已有任务正在运行" in m for m in messages)
    panel._run_active = False
    panel.close()


def test_spectrum_report_cache_by_fingerprint(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补12:生成谱图参数报告按谱文件指纹缓存复用。"""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    missing = str(tmp_path / "no.ft3")
    text1 = panel._cached_spectrum_report(
        {"diagnostics": {"reports": []}}, missing
    )
    text2 = panel._cached_spectrum_report(
        {"diagnostics": {"reports": []}}, missing
    )
    assert text1 == text2
    # 0.2.199-补29e:无记录不现场生成(提示重新运行),因此不写入缓存
    assert "无报告记录" in text1
    assert not panel._spectrum_report_cache
    panel.close()


def test_pipeline_button_row_wraps_when_narrow(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补4:步骤行按钮区为流式布局,宽度不足时按钮自动折行。"""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    row = panel._rows["spectrum"]
    buttons = [
        row.ext_range_button,
        row.run_button,
        row.rerun_final_button,
        row.show_spectrum_button,
        row.manual_button,
    ]
    for button in buttons:
        button.setVisible(True)  # 模拟谱图步骤完成后的 5 个可见按钮
    row.show()
    qapp.processEvents()
    button_row = row.layout().itemAt(1)

    def _row_ys() -> set[int]:
        return {
            button_row.itemAt(i).widget().y()
            for i in range(button_row.count())
            if button_row.itemAt(i).widget() is not None
        }

    # 窄宽度下 5 个按钮必须折成多行(y 坐标至少两行)
    row.setFixedWidth(180)
    qapp.processEvents()
    ys = _row_ys()
    assert len(ys) >= 2, f"窄宽度下按钮未折行: y={sorted(ys)}"
    panel.close()


def test_pipeline_final_ext_override_params(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-补15:每数据终跑直接维范围覆盖 → generate_spectrum params。"""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert panel._spectrum_ext_params("d_001") is None
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "5.5", True)
    assert panel._spectrum_ext_params("d_001") == {
        "apply_ext_to_opt": "1",
        "final_ext_lo": "11.0",
        "final_ext_hi": "5.5",
    }
    # 只设一端:另一端不注入;关闭「应用此范围到优化过程」
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "", False)
    assert panel._spectrum_ext_params("d_001") == {
        "apply_ext_to_opt": "0",
        "final_ext_lo": "11.0",
    }
    panel.close()


def test_pipeline_ext_button_text_reflects_override(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-补15:按钮文案随当前数据的终跑范围刷新。"""
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert panel._rows["spectrum"].ext_range_button.text() == "直接维范围"
    panel._final_ext[("exp_001", "d_001")] = ("11.0", "5.5", True)
    panel.refresh()
    assert (
        panel._rows["spectrum"].ext_range_button.text()
        == "直接维范围 11.0/5.5 · 含优化"
    )
    panel.close()
    assert (
        panel._rows["spectrum"].ext_range_button.toolTip()
        == "直接维窗口: 11.0-5.5 ppm(EXT -x1/-xn,含优化)\n"
        "首遍重构/相位搜索与优化评估同窗口;窗口外峰不进入终谱,\n"
        "p1 按窗口宽度自动重归一化;切换数据后显示各自设置"
    )
    # 切换数据:未设置该数据窗口时,提示词回到默认说明
    panel.set_selection("data", "exp_001", "d_002")
    assert "未设置时用默认" in panel._rows["spectrum"].ext_range_button.toolTip()
    panel.close()



def test_pipeline_steps_include_optional_smile() -> None:
    ids = [step[0] for step in PIPELINE_STEPS]
    assert ids == [
        "fid", "spectrum", "smile", "peaks", "analysis"
    ]
    deps = {step[0]: step[3] for step in PIPELINE_STEPS}
    # SMILE 优化为可选:峰挑选不依赖它
    assert "smile" not in deps["peaks"]
    assert deps["smile"] == ("spectrum",)
    for _, _, _, step_deps in PIPELINE_STEPS:
        for dep in step_deps:
            assert dep in ids


def test_pipeline_status_registered(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "READY"
    for step_id in ("spectrum", "peaks"):
        assert statuses[step_id] == "LOCKED"


def test_pipeline_status_after_spectrum(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    # fid 与谱图都需要真实产物:fid 在 process 目录,fid_path 登记
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "d_001.ft2")
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "READY"


def test_pipeline_fid_unlocks_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    """生成 FID 后,生成谱图步骤应解锁为 READY(回归:曾误用谱图判定 fid)。"""
    manager = _manager_with_experiment(tmp_path)
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    fid_file = process_dir / "exp_001-d_001.fid"
    fid_file.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_001", fid_file)
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["fid"] == "SUCCESS"
    assert statuses["spectrum"] == "READY"  # 关键:谱图步骤解锁
    assert statuses["peaks"] == "LOCKED"


def test_pipeline_panel_refresh_shows_next_step(tmp_path: Path, qapp: QApplication) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    assert "下一步" in panel.next_label.text()
    assert "生成 FID" in panel.next_label.text()
    assert not panel._rows["fid"].run_button.isHidden()
    assert panel._rows["spectrum"].run_button.isHidden()
    # 0.2.199-补29dm:生成 FID 人工按钮须先自动处理(SUCCESS)才出现
    assert panel._rows["fid"].manual_button.isHidden()
    panel.close()


def test_pipeline_panel_run_generate_fid(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path)
    controller = FakeProcessingController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.log_scoped.connect(log.append)  # 0.2.199-补29d:运行日志按作用域
    panel.set_selection("data", "exp_001", "d_001")
    # 0.2.199-补29d:日志按数据作用域落地,面板切到该数据作用域才显示
    log.set_scope("data", "exp_001", "d_001")
    panel._on_run_requested("fid")
    assert controller.calls == ["generate_fid"]
    assert "完成 生成 FID" in log.text.toPlainText()
    # 运行完成后按产物文件重新推断(无 ft2 时 fid 回到 READY)
    assert panel._rows["fid"].status_label.text().startswith("▶")
    panel.close()
    log.close()


# ----------------------------------------------------------------------
# 谱图面板
# ----------------------------------------------------------------------
def test_spectrum_panel_lists_and_loads_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    spectrum = spectra / "hsqc_2d.ft2"   # 后端按 dataset_id 命名
    _write_ft2(spectrum)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001")          # 实验级:汇总实验下全部数据谱图
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "hsqc_2d.ft2"
    assert panel.open_spectrum(spectrum) is True
    assert panel.viewer.layer_list.count() == 1
    panel.close()


def test_spectrum_panel_open_corrupt_returns_false(
    tmp_path: Path, qapp: QApplication
) -> None:
    manager = _manager_with_experiment(tmp_path)
    panel = SpectrumPanel(manager)
    bad = tmp_path / "bad.ft2"
    bad.write_bytes(b"not a pipe file")
    assert panel.open_spectrum(bad) is False
    panel.close()


# ----------------------------------------------------------------------
# 主窗口三栏
# ----------------------------------------------------------------------
def test_main_window_three_column_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert window.main_splitter.count() == 4
    assert window.project_tree is not None
    assert window.pipeline is not None
    assert window.spectrum_panel is not None
    # 0.2.141:log 竖列位于 pipeline 与谱图查看器之间
    assert (
        window.main_splitter.indexOf(window.center_panel)
        < window.main_splitter.indexOf(window.log_panel)
        < window.main_splitter.indexOf(window.spectrum_panel)
    )
    # 0.2.143:列宽无硬性上限,可自由拖拽(下限为内容自然尺寸)
    assert window.log_panel.minimumWidth() <= 400
    assert window.log_panel.maximumWidth() >= 10000
    assert window.project_tree.minimumWidth() <= 1  # 不再强制 330
    # 默认初始列宽固定 [420,600,300,600](合计 1920),窄屏由 splitter 收窄
    from PyQt6.QtGui import QGuiApplication

    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        cols = window.main_splitter.sizes()
        assert sum(cols) <= avail.width()
        assert min(cols) > 0
        assert window.geometry().top() == avail.top()
        assert window.height() <= avail.height()
    # 默认聚焦第一个实验类型 → 中间为实验类型页(内嵌导入样品数据表单)
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page._exp_id == "exp_001"
    assert "demo" in window.windowTitle()
    # 扁平兼容表已删(0.2.199-补29hr):改为核对真实项目实验数
    assert len([e for e in window.manager.project.experiments if not e.trashed]) == 2
    window.close()


def test_main_window_spectrum_expand_toggle(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29bp:谱图放大按钮——收起左侧三部分,再点还原。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    btn = window.spectrum_panel.expand_button
    assert btn.text() == "放大"
    assert not window.project_tree.isHidden()
    assert not window.center_panel.isHidden()
    assert not window.log_panel.isHidden()
    btn.setChecked(True)
    assert btn.text() == "收起"
    assert window.project_tree.isHidden()
    assert window.center_panel.isHidden()
    assert window.log_panel.isHidden()
    assert not window.spectrum_panel.isHidden()
    # 0.2.199-补29bq:只有绘图区放大,右侧控件列保留,小绘图区隐藏
    panel = window.spectrum_panel
    assert panel._expanded
    assert panel._expand_splitter is not None
    assert panel.viewer.plot_area.parent() is panel._expand_splitter
    assert panel._expand_splitter.indexOf(panel.viewer.plot_area) == 0
    assert panel._expand_controls is not None
    assert panel.viewer.controls_layout.parentWidget().parent() is panel._expand_controls
    assert panel.lists_row_widget.parent() is panel._expand_controls
    assert panel.peak_toolbar_widget.parent() is panel._expand_controls
    assert panel.peak_table.parent() is panel._expand_controls
    assert panel.viewer.isHidden()
    # 0.2.199-补29hz-修26:放大后顶部标题行不得被撑成空白块
    window.resize(1200, 800)
    window.show()
    QApplication.processEvents()
    btn.setChecked(False)
    btn.setChecked(True)
    QApplication.processEvents()
    lay = panel.layout()
    header_item = lay.itemAt(0)
    title = next(
        lb for lb in panel.findChildren(QLabel) if lb.text() == "谱图"
    )
    assert header_item.geometry().height() <= 40  # 原为 311px(空白块)
    assert title.height() <= 40
    assert panel._expand_splitter is not None
    assert panel._expand_splitter.height() >= panel.height() - 80
    btn.setChecked(False)
    assert btn.text() == "放大"
    assert not window.project_tree.isHidden()
    assert not window.center_panel.isHidden()
    assert not window.log_panel.isHidden()
    assert not panel._expanded
    assert panel._expand_splitter is None
    assert panel.viewer.plot_area.parent() is panel.viewer.view_splitter
    assert not panel.viewer.isHidden()
    assert panel.lists_row_widget.parent() is panel._panel_splitter
    window.close()


def test_spectrum_panel_file_help_menus(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29br:文件/帮助菜单在放大按钮右边;查看菜单无谱图查看器入口。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    panel = window.spectrum_panel
    assert panel.file_button.menu() is panel.file_menu
    assert panel.help_button.menu() is panel.help_menu
    row = panel.lists_row
    assert row.indexOf(panel.expand_button) < row.indexOf(panel.file_button)
    assert row.indexOf(panel.file_button) < row.indexOf(panel.help_button)
    file_texts = [a.text() for a in panel.file_menu.actions()]
    assert "打开当前数据谱图" in file_texts and "清空谱图" in file_texts
    assert "打开任意谱图..." in file_texts
    help_texts = [a.text() for a in panel.help_menu.actions()]
    assert "操作说明" in help_texts
    panel._on_menu_clear_spectrum()  # 空状态下安全
    view_menu = None
    for action in window.menuBar().actions():
        if action.text() == "查看(&V)":
            view_menu = action.menu()
    assert view_menu is not None
    texts = [a.text() for a in view_menu.actions()]
    assert not any("谱图查看器" in t for t in texts)
    window.close()


def test_analysis_ref_candidates_filters(tmp_path: Path, qapp: QApplication) -> None:
    """0.2.199-补29er:分析参考候选 = 同实验内已有谱图+峰表、非当前的数据。"""
    from gui.pipeline_panel import PipelinePanel

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    cur = manager.import_data(entry.id, "/data/cur")
    ref = manager.import_data(entry.id, "/data/ref")
    other = manager.import_data(entry.id, "/data/other")
    for data, name in ((cur, "cur"), (ref, "ref")):
        spec_dir = manager.data_dir(entry.id, data.id, "spectra")
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec = spec_dir / f"{name}.ft2"
        spec.write_bytes(b"x")
        manager.set_data_spectrum(entry.id, data.id, str(spec))
        peaks_dir = manager.data_dir(entry.id, data.id, "peaks")
        peaks_dir.mkdir(parents=True, exist_ok=True)
        (peaks_dir / f"{entry.id}-{data.id}.list").write_text(
            "Assignment w1 w2 Data Height Volume\n?-?  110.0  8.0  0  1  0\n",
            encoding="utf-8",
        )
    panel = PipelinePanel(manager)
    panel._current_exp_id = entry.id
    panel._current_data_id = cur.id
    cands = panel._analysis_ref_candidates(entry.id, cur.id)
    ids = [c[2] for c in cands]
    assert ref.id in ids
    assert cur.id not in ids
    assert other.id not in ids


def test_main_window_has_app_icon(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29eq:主窗口设置了应用图标(gui/assets/nmrforge.png)。"""
    from gui.theme import app_icon

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    assert app_icon() is not None
    assert not window.windowIcon().isNull()
    window.close()


def test_tools_menu_standalone_quality_entries(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29em:「工具」菜单含数据质量检测/
    谱图质量评估独立入口,位于查看与设置之间。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    menus = [a.text() for a in window.menuBar().actions()]
    assert "工具(&T)" in menus
    idx = menus.index("工具(&T)")
    assert menus[idx - 1] == "查看(&V)"
    assert menus[idx + 1] == "设置(&S)"
    tools_menu = next(
        a.menu() for a in window.menuBar().actions() if a.text() == "工具(&T)"
    )
    labels = [a.text() for a in tools_menu.actions()]
    assert "数据质量检测..." in labels
    assert "谱图质量评估..." in labels
    window.close()


def test_tools_run_jumps_to_workspace_log(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29em:工具执行时跳转到最顶层(工作区根)并把日志
    切到全局(NMRForgeWorkspace)作用域。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    top = tree.topLevelItem(0)
    assert top is not None
    child = top.child(0)
    if child is not None:
        tree.setCurrentItem(child)
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(tmp_path / "nope.fid"), ""),
    )
    window._run_standalone_fid_diagnostics()
    assert tree.currentItem() is top
    assert window.log_panel.current_scope() == "global"
    window.close()


def test_menu_mnemonics_unique_and_activate(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29en:顶层菜单助记键(&X)唯一,且 Alt+字母 能弹出对应菜单
    (曾出现 工具/设置 都取 T,Alt+T 歧义导致设置助记键失效)。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.show()
    qapp.processEvents()
    bar = window.menuBar()
    letters: list[str] = []
    by_letter: dict[str, object] = {}
    for action in bar.actions():
        text = action.text()
        assert "&" in text, f"菜单缺少助记键: {text}"
        letter = text.split("&", 1)[1][0]
        letters.append(letter)
        by_letter[letter] = action.menu()
    assert len(set(letters)) == len(letters), f"助记键重复: {letters}"
    # Alt+T → 工具;Alt+S → 设置
    for key, title in ((Qt.Key.Key_T, "工具(&T)"), (Qt.Key.Key_S, "设置(&S)")):
        target = next(
            a.menu()
            for a in bar.actions()
            if a.text() == title
        )
        bar.setFocus()
        QTest.keyClick(bar, key, Qt.KeyboardModifier.AltModifier)
        qapp.processEvents()
        popup = QApplication.activePopupWidget()
        assert popup is target, f"Alt+{key} 未弹出 {title}"
        popup.close()
        qapp.processEvents()
    window.close()


def test_other_menu_routes_to_standalone_check(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29ej:独立入口按 kind 路由到检测。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    calls: list[tuple[str, str]] = []
    window._run_standalone_check = (  # type: ignore[method-assign]
        lambda paths, kind: calls.append((str(paths[0]), kind))
    )
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (r"C:\x\d_001.fid", ""),
    )
    window._run_standalone_fid_diagnostics()
    assert calls == [(r"C:\x\d_001.fid", "fid")]
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QFileDialog.getOpenFileName",
        lambda *a, **k: (r"C:\x\d_001.ft3", ""),
    )
    window._run_standalone_spectrum_quality()
    assert calls[-1] == (r"C:\x\d_001.ft3", "spectrum")
    window.close()


def test_main_window_context_updates_on_tree_selection(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_002")
    assert window.center_panel.stack.currentIndex() == 2  # 实验类型页
    assert window.center_panel.experiment_page._exp_id == "exp_002"
    assert window.spectrum_panel._current_exp_id == "exp_002"
    window.close()


def test_pipeline_no_import_step(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.162-补12:导入已移入下拉,pipeline 不再含导入步骤。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    assert "import" not in panel._rows
    panel.set_selection("data", "exp_001", "d_001")
    assert not panel._rows["fid"].isHidden()
    panel.close()


def test_main_window_log_panel_expands_on_message(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    # 0.2.143:log 常驻显示
    assert not window.log_panel.isHidden()
    window.center_panel.set_selection("data", "exp_001", "d_001")
    window.pipeline._on_run_requested("fid")
    assert not window.log_panel.isHidden()
    assert "生成 FID" in window.log_panel.text.toPlainText()
    window.close()


def test_log_panel_scopes_isolate_data_and_group(
    qapp: QApplication,
) -> None:
    """单个数据日志互相独立;数据组内共用同一日志;切换选中即切换显示。"""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    # 数据 A 与数据 B 独立
    panel.set_scope("data", "exp_001", "d_001")
    panel.append("A 的日志")
    panel.set_scope("data", "exp_001", "d_002")
    panel.append("B 的日志")
    panel.set_scope("data", "exp_001", "d_001")
    assert "A 的日志" in panel.text.toPlainText()
    assert "B 的日志" not in panel.text.toPlainText()
    # 数据组共用
    panel.set_scope("group", "exp_001", "", "g_1")
    panel.append("组的日志")
    panel.set_scope("group", "exp_001", "", "g_1")
    assert "组的日志" in panel.text.toPlainText()
    # 实验类型与全局互不污染
    panel.set_scope("experiment", "exp_001")
    assert "A 的日志" not in panel.text.toPlainText()
    panel.set_scope("", "", "")
    assert panel.text.toPlainText() == ""
    panel.close()


def test_log_panel_explicit_scope_routes_group_batch(
    qapp: QApplication,
) -> None:
    """组批量日志显式落到组作用域,不受当前选中数据影响。"""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    panel.set_scope("data", "exp_001", "d_001")
    panel.append("单数据日志")
    group_scope = panel.scope_key("group", "exp_001", "", "g_1")
    panel.append("批量进度 1/3", scope=group_scope)
    # 当前仍显示数据日志,组日志在组作用域
    assert "批量进度 1/3" not in panel.text.toPlainText()
    panel.set_scope("group", "exp_001", "", "g_1")
    assert "批量进度 1/3" in panel.text.toPlainText()
    assert "单数据日志" not in panel.text.toPlainText()
    panel.close()


def test_main_window_empty_state(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace 根
    assert window.manager.project is None
    assert "欢迎" in window.windowTitle()
    assert window.pipeline.current_experiment_id() == ""
    window.close()

def test_tree_data_node_context_menu_actions(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data 节点右键:删除/打开目录 + 批量组加入(不含生成步骤)。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    actions: list[tuple[str, str]] = []
    panel.data_action_requested.connect(
        lambda action, data_id: actions.append((action, data_id))
    )
    menu = QMenu()
    panel._on_context_menu_impl(menu, data_item)
    labels = [a.text() for a in menu.actions()]
    assert "生成 FID" not in labels and "生成谱图" not in labels
    delete_action = next(a for a in menu.actions() if a.text() == "删除样品数据")
    delete_action.trigger()
    assert actions == [("delete", "d_001")]
    panel.close()


def test_tree_folder_terminal_menu_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.93:raw 等子文件夹右键含「在终端中打开」,点击发出路径。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    raw_item = data_item.child(0)  # raw 子文件夹
    seen: list[str] = []
    panel.open_terminal_requested.connect(seen.append)
    menu = QMenu()
    panel._on_context_menu_impl(menu, raw_item)
    labels = [a.text() for a in menu.actions()]
    assert "在终端中打开" in labels
    action = next(a for a in menu.actions() if a.text() == "在终端中打开")
    action.trigger()
    assert seen and Path(seen[0]).name == "raw"
    panel.close()


def test_terminal_argv_prefers_csh(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.2.93:在终端中打开优先 csh;Windows 回退 cmd。"""
    import sys

    from gui.project_tree import _terminal_argv

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/bin/{name}"
        if name in ("csh", "gnome-terminal")
        else None,
    )
    argv = _terminal_argv("/data/raw")
    assert argv is not None
    assert "csh" in argv
    assert "--working-directory=/data/raw" in argv

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "C:/cygwin/bin/csh.exe"
        if name == "csh"
        else (r"C:\Windows\System32\cmd.exe" if name == "cmd" else None),
    )
    argv = _terminal_argv("C:/data/raw")
    assert argv is not None
    assert "csh" in argv[0]


def test_tree_subfolder_context_menu_has_open_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw 等子目录右键提供「打开所在目录」。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    folder_item = panel.tree.topLevelItem(0).child(0).child(0).child(0).child(0)
    menu = QMenu()
    panel._on_context_menu_impl(menu, folder_item)
    labels = [a.text() for a in menu.actions()]
    assert "打开所在目录" in labels
    panel.close()


def test_create_blank_experiment_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空白处/Project 右键新建空白实验类型。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window._create_experiment()
    assert window.project_tree._pending_kind == "experiment"
    window.project_tree._commit_pending_create("T4")
    assert manager.project is not None
    assert any(e.title == "T4" for e in manager.project.experiments)
    window.close()


def test_delete_project_action(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project 右键删除项目:确认后关闭项目并清空树。"""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))
    window._delete_project()
    assert window.manager.project is None
    assert window.project_tree.tree.topLevelItemCount() == 1  # Workspace 根仍在
    assert "欢迎" in window.windowTitle()
    window.close()

def test_welcome_page_shows_workspace_and_recent(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """欢迎页:工作区路径 + 最近项目列表 + 新建入口(契约 v1.3 §9.4)。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ProjectManager.create_project(workspace / "projA", "projA")
    ProjectManager.create_project(workspace / "projB", "projB")

    from core.workspace import WorkspaceManager
    from gui.welcome_page import WelcomePage

    page = WelcomePage(workspace=WorkspaceManager(workspace))
    assert str(workspace) in page.workspace_label.text()
    assert page.recent_list.count() == 2
    names = {page.recent_list.item(i).text() for i in range(page.recent_list.count())}
    assert names == {"projA", "projB"}
    page.close()


def test_main_window_welcome_page_on_startup(qapp: QApplication) -> None:
    """未打开项目时主窗口显示欢迎页(三栏中 Workspace 页)。"""
    window = MainWindow()
    assert window.center_panel.welcome_page is not None
    assert not window.main_splitter.isHidden()  # 三栏可见,欢迎页在中间
    assert window.center_panel.stack.currentIndex() == 0  # Workspace 页
    window.close()

def test_data_selected_shows_pipeline_page(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选中 Data → 中间为 Pipeline 页;导入无人工按钮。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    tree.setCurrentItem(data_item)
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline 页
    assert window.pipeline.current_experiment_id() == "exp_001"
    assert "import" not in window.pipeline._rows  # 0.2.162-补12:pipeline 无导入步骤
    window.close()

def test_import_failure_handled_on_main_thread(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导入失败经信号回主线程处理(不在后台线程弹模态框)。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    monkeypatch.setattr(
        "threading.Thread",
        lambda *a, **k: _SyncThread(*a, **k),
    )

    def fail_import(mgr, exp_id, source, *, segments=None, copy=True):
        raise RuntimeError("模拟导入失败")

    monkeypatch.setattr("workflow.import_workflow.import_data", fail_import)
    window = MainWindow(manager=manager)
    window._import_experiment_async(
        {"source": str(tmp_path / "nonexistent"), "title": "T", "copy": True}
    )
    assert messages and ("目录不存在" in messages[0] or "导入失败" in messages[0])
    assert "导入失败" in window.log_panel.text.toPlainText()
    window.close()


class _SyncThread:
    def __init__(self, target=None, daemon=None) -> None:
        self._target = target

    def start(self) -> None:
        self._target()

def test_spectrum_panel_open_current_data_spectrum(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29hz-修27:菜单拆两条 + 无谱/无数据要有明确提示。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    texts = [a.text() for a in panel.file_menu.actions()]
    assert texts[:3] == [
        "打开当前数据谱图",
        "打开任意谱图...",
        "清空谱图",
    ]

    shown: list[str] = []
    monkeypatch.setattr(
        "gui.spectrum_panel.InfoDialog.show_info",
        staticmethod(lambda _parent, _title, text: shown.append(text)),
    )
    # ① 没选数据
    panel.set_context("", "")
    panel._on_menu_open_current_spectrum()
    assert shown[-1] == "当前没有选中数据"
    # ② 选了数据但还没生成谱图(用户实际会点到的场景)
    panel.set_context("exp_001", "d_001")
    panel._on_menu_open_current_spectrum()
    assert shown[-1] == "当前数据还未生成谱图"
    # ③ 有谱图:与 Pipeline「展示谱图」同效果,直接加载且不弹提示
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    panel.set_context("exp_001", "d_001")
    panel._on_menu_open_current_spectrum()
    assert panel._current_spectrum is not None
    assert len(shown) == 2
    panel.close()


def test_spectrum_panel_scans_data_dir_layout(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """schema 1.3 布局:谱图位于 data_dir(...,"spectra")/ 下,面板可列出并打开。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "exp_001-d_001.ft2"
    assert panel.open_spectrum(spectra_dir / "exp_001-d_001.ft2") is True
    panel.close()


def test_spectrum_panel_excludes_fid_from_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.77:谱图文件列表只列 .ft2/.ft3,process 目录 raw.fid 不再混入。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    process_dir = manager.data_dir("exp_001", "d_001", "process")
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "raw.fid").write_bytes(b"fid")
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "exp_001-d_001.ft2"
    panel.close()


def test_spectrum_panel_empty_spectra_clears_viewer(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.85:样品数据无谱图时右侧留空(切换后不残留上一张谱)。"""
    manager = ProjectManager.create_project(tmp_path / "proj2", "demo")
    entry = manager.create_experiment("A")
    data1 = manager.import_data(entry.id, "/fake/1")
    data2 = manager.import_data(entry.id, "/fake/2")
    spectra1 = manager.data_dir(entry.id, data1.id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{entry.id}-{data1.id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data1.id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.layer_list.count() == 1
    # data2 谱图文件夹为空 → 查看器清空
    panel.set_context(entry.id, data2.id)
    assert panel.viewer.layer_list.count() == 0
    assert panel._current_spectrum is None
    panel.close()


def test_spectrum_panel_finds_dataset_id_named_spectrum(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.112:终谱按 dataset_id 命名(非 exp_id-data_id)也能被找到。"""
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment("A")
    data = manager.import_data(entry.id, "/data/hsqc_2d")
    spectra = manager.data_dir(entry.id, data.id, "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "hsqc_2d.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(entry.id, data.id)
    assert panel.load_current_spectrum() is True
    assert panel.file_list.count() == 1
    assert panel.file_list.item(0).text() == "hsqc_2d.ft2"
    panel.close()


def test_spectrum_param_report_shows_phase_results() -> None:
    """0.2.108:参数报告展示逐维相位/直接维相位/后端运行次数。"""
    from gui.pipeline_panel import _spectrum_param_report

    report = _spectrum_param_report(
        {
            "phase_route": "unified",
            "phases": {"F2": (0.0, 10.0), "F1": (-45.0, 0.0)},
            "direct_phase": (0.0, 10.0),
            "backend_runs": 3,
            "extract": True,
        }
    )
    assert "相位优化途径: 统一自动处理" in report
    assert "逐维相位" in report
    assert "F1: p0=-45.0° p1=0.0°" in report
    assert "F2: p0=0.0° p1=10.0°" in report
    assert "直接维相位" in report
    assert "后端运行次数: 3" in report
    assert "◆ 处理参数与优化" in report
    # 0.2.155:精简——内部参数(如提取窗口)不再出现在报告中
    assert "extract" not in report
    assert "提取窗口" not in report


def test_pipeline_show_spectrum_button_on_spectrum_success(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.88:生成谱图完成后出现「展示谱图」按钮,点击发出请求。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / "exp_001-d_001.ft2")
    from gui.pipeline_panel import PipelinePanel

    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection("data", "exp_001", "d_001")
    button = panel._rows["spectrum"].show_spectrum_button
    assert not button.isHidden()
    seen: list[str] = []
    panel.show_spectrum_requested.connect(seen.append)
    button.click()
    assert seen == ["spectrum"]
    panel.close()


def test_import_done_clears_import_form(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.112:导入成功后清空中间页导入表单(名称/路径)。"""
    from types import SimpleNamespace

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.center_panel.experiment_page.name_edit.setText("样品1")
    window.center_panel.experiment_page.source_edit.setText("/data/a")
    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"guide": {"first_import_hint_shown": True}},
    )
    result = SimpleNamespace(
        experiment_id="exp_001",
        data_id="d_001",
        run_id="R-1",
        file_count=1,
        total_bytes=10,
        warnings=[],
    )
    window._on_import_done(result)
    assert window.center_panel.experiment_page.name_edit.text() == ""
    assert window.center_panel.experiment_page.source_edit.text() == ""
    window.close()


def test_pipeline_status_peaks_from_data_dir(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """peaks 状态检查 data_dir(...,"peaks")/<exp>-<data>.list。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")
    peaks_dir = manager.data_dir("exp_001", "d_001", "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    (peaks_dir / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    statuses = compute_step_statuses(manager, "exp_001")
    assert statuses["spectrum"] == "SUCCESS"
    assert statuses["peaks"] == "SUCCESS"


def test_pipeline_peaks_step_runs_pick_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """peaks 步骤运行按钮走 pick_peaks 并刷新状态。"""
    monkeypatch.setattr("threading.Thread", SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / "exp_001-d_001.ft2")

    class PeaksController(FakeProcessingController):
        def pick_peaks(
            self, data, exp_id=None, data_id=None, sigma_multiplier=None
        ) -> dict:
            self.calls.append("pick_peaks")
            peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
            peaks_dir.mkdir(parents=True, exist_ok=True)
            (peaks_dir / f"{exp_id}-{data_id}.list").write_text(
                "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
                encoding="utf-8",
            )
            return {"status": "success", "peak_count": 1}

    controller = PeaksController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection("data", "exp_001", "d_001")
    assert not panel._rows["peaks"].run_button.isHidden()  # peaks READY
    panel._on_run_requested("peaks")
    assert "pick_peaks" in controller.calls
    assert panel._rows["peaks"].status_label.text().startswith("✓")  # 峰表出现 → SUCCESS
    panel.close()
    log.close()


def test_data_delete_wires_manager_delete_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """样品数据删除接线:manager.delete_data(不删实验类型)。"""
    from gui.dialogs import ConfirmDialog

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))

    def fake(path, fallback_dir, rel=None):
        return path

    monkeypatch.setattr("core.project.manager.send_to_trash", fake)
    window.project_tree.select_experiment("exp_001")
    window._delete_data("exp_001", "d_001")
    entry = manager.project.experiment("exp_001")
    assert entry is not None and entry.data and all(d.trashed for d in entry.data)
    window.close()


def test_data_rename_persists_title(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据重命名:写 DataEntry.title 并落盘(重启可读)。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window._rename_data("exp_001", "d_001", "重命名后")
    data_entry = manager.project.experiment("exp_001").data[0]
    assert data_entry.title == "重命名后"
    # 树显示 title
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    assert data_item.text(0) == "重命名后"
    window.close()

def test_double_click_data_keeps_pipeline_and_opens_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双击数据节点:发出 open_path_requested(不跳导入页),中间保持 Pipeline。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    base = manager.data_base("exp_001", "d_001")
    base.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    tree.setCurrentItem(data_item)
    window.project_tree._on_double_clicked(data_item, 0)
    # 0.2.199-补29ge:双击数据打开 d_xxx 基座,不是 raw
    assert opened and Path(opened[0]) == base
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline 页
    window.close()


def test_double_click_folder_opens_folder_path(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双击子文件夹:打开该文件夹目录,中间保持 Pipeline。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(2)  # spectra
    opened: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    tree.setCurrentItem(folder_item)
    window.project_tree._on_double_clicked(folder_item, 0)
    assert opened and Path(opened[0]) == spectra_dir
    assert window.center_panel.stack.currentIndex() == 3
    window.close()

def test_right_click_open_path_emits_signal(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """右键「打开所在目录」:data/folder 节点发出 open_path_requested(与双击一致)。"""
    # 0.2.199-补29gk 补:MainWindow 把 open_terminal_requested 连到 _open_terminal→
    # open_in_terminal(x-terminal-emulator),测试只验证信号;mock 掉避免真开终端窗口。
    monkeypatch.setattr("gui.project_tree.open_in_terminal", lambda p: True)
    from PyQt6.QtWidgets import QMenu

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    base = manager.data_base("exp_001", "d_001")
    base.mkdir(parents=True, exist_ok=True)
    spectra_dir = manager.data_dir("exp_001", "d_001", "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    folder_item = data_item.child(2)  # spectra
    opened: list[str] = []
    opened_term: list[str] = []
    window.project_tree.open_path_requested.connect(lambda p: opened.append(p))
    window.project_tree.open_terminal_requested.connect(
        lambda p: opened_term.append(p)
    )

    data_menu = window.project_tree._on_context_menu_impl(QMenu(), data_item)
    data_acts = [a for a in data_menu.actions() if a.text() == "打开所在目录"]
    assert len(data_acts) == 1
    data_acts[0].trigger()
    # 0.2.199-补29ge:右键数据打开 d_xxx 基座,不是 raw
    assert opened and Path(opened[0]) == base
    # 0.2.199-补29gf:数据节点终端同样打开 d_xxx(raw 子节点自己可开终端)
    data_terms = [
        a for a in data_menu.actions() if a.text() == "在终端中打开"
    ]
    assert len(data_terms) == 1
    data_terms[0].trigger()
    assert opened_term and Path(opened_term[0]) == base

    folder_menu = window.project_tree._on_context_menu_impl(QMenu(), folder_item)
    folder_acts = [a for a in folder_menu.actions() if a.text() == "打开所在目录"]
    assert len(folder_acts) == 1
    folder_acts[0].trigger()
    assert len(opened) == 2 and Path(opened[1]) == spectra_dir
    assert window.center_panel.stack.currentIndex() == 3  # Pipeline 页
    window.close()

def test_spectrum_panel_vertical_layout(qapp: QApplication) -> None:
    """谱图面板上下布局:文件列表在上、查看器在下。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QSplitter

    panel = SpectrumPanel()
    found: list[QSplitter] = []

    def walk(widget) -> None:
        for child in widget.children():
            if isinstance(child, QSplitter):
                found.append(child)
            walk(child)

    walk(panel)
    assert found, "SpectrumPanel 内应有 QSplitter"
    splitter = next(s for s in found if s.count() == 4)
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.count() == 4  # viewer / 文件列表 / 工具栏 / 峰表
    assert panel.file_list.maximumWidth() > 1000  # 无横向宽度限制
    panel.close()

def test_viewer_internal_vertical_layout(qapp: QApplication) -> None:
    """SpectrumViewer 内部上下布局:plot 在上、控制面板在下。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QSplitter

    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    found: list[QSplitter] = []

    def walk(widget) -> None:
        for child in widget.children():
            if isinstance(child, QSplitter):
                found.append(child)
            walk(child)

    walk(viewer)
    assert found, "SpectrumViewer 内应有 QSplitter"
    splitter = found[0]
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.count() == 2
    assert splitter.widget(0) is viewer.plot_area  # 上方谱图区
    viewer.close()

def test_project_dashboard_stats_and_runs(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project Dashboard:统计 + 最近运行。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    run = manager.start_run("exp_001", workflow_ref="import")
    manager.finish_run(run.run_id, "success", message="ok")
    manager.save()
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    proj_item = tree.topLevelItem(0).child(0)
    tree.setCurrentItem(proj_item)
    assert window.center_panel.stack.currentIndex() == 1  # Project Dashboard
    assert "实验:" in window.center_panel.project_page.stats_label.text()
    assert window.center_panel.project_page.runs_table.rowCount() >= 1
    window.close()


def test_experiment_dashboard_data_rows(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Experiment Dashboard:数据列表。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    tree = window.project_tree.tree
    exp_item = tree.topLevelItem(0).child(0).child(0)
    tree.setCurrentItem(exp_item)
    assert window.center_panel.stack.currentIndex() == 2
    assert window.center_panel.experiment_page.data_table.rowCount() >= 1
    window.close()


def test_run_history_dialog(tmp_path: Path, qapp: QApplication) -> None:
    """运行历史对话框:列表 + 详情。"""
    from gui.dialogs import RunHistoryDialog

    manager = ProjectManager.create_project(tmp_path / "ws" / "proj", "demo")
    manager.create_experiment("HSQC")
    run = manager.start_run("exp_001", workflow_ref="import")
    manager.finish_run(run.run_id, "success", outputs={"spectrum": "x.ft2"}, message="ok")
    dialog = RunHistoryDialog(None, manager.project.workflow_runs, "demo")
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    assert "x.ft2" in dialog.detail_label.text()
    dialog.close()


def test_spectrum_peak_linkage(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """谱图-峰表联动:峰表加载 + 双向高亮/选中。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\n"
        "G1  115.000  8.000  0  100  0\n"
        "A2  118.000  7.500  0  80  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:显式加载峰表
    assert panel.peak_table.rowCount() == 2
    assert len(panel.viewer._peaks) == 2
    panel.peak_table.selectRow(1)
    assert panel.viewer._selected_peak == 1
    panel._on_viewer_peak_clicked(0)
    assert panel.peak_table.currentRow() == 0
    panel.close()

def test_export_poky_button_generates_list(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有峰表时「导出 Poky」可用,生成 .list 且含 header/峰行。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / "exp_001-d_001.list").write_text(
        "Assignment w1 w2 Data Height Volume\nG1  115.000  8.000  0  100  0\n",
        encoding="utf-8",
    )
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:显式加载峰表
    assert panel.export_poky_button.isEnabled()

    out = tmp_path / "out.list"
    from gui.peaks_io import export_peaks_poky, load_peaks

    export_peaks_poky(out, load_peaks(peaks / "exp_001-d_001.list"))
    content = out.read_text(encoding="utf-8")
    assert "G1" in content and "115.0" in content and "8.0" in content
    panel.close()


def test_export_poky_button_disabled_without_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无峰表时「导出 Poky」禁用。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert not panel.export_poky_button.isEnabled()
    panel.close()


def test_peak_linkage_via_load_peaks(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_peaks 统一加载后行数展示与联动高亮不受影响。"""
    import csv

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    (spectra / "exp_001-d_001.ft2").write_bytes(b"x")
    peaks = manager.data_dir("exp_001", "d_001", "peaks")
    peaks.mkdir(parents=True, exist_ok=True)
    with (peaks / "exp_001-d_001.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Peak_ID", "H_shift", "N_shift", "Intensity", "SN", "label"])
        writer.writerow(["1", "8.0", "115.0", "100", "20", "G1"])
        writer.writerow(["2", "7.5", "118.0", "80", "15", "A2"])
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    panel._load_peaks(spectra / "exp_001-d_001.ft2")  # 0.2.88:显式加载峰表
    assert panel.peak_table.rowCount() == 2
    assert len(panel.viewer._peaks) == 2
    panel.peak_table.selectRow(1)
    assert panel.viewer._selected_peak == 1
    panel.close()

def test_spectrum_auto_shown_on_data_select(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选中数据后自动显示第一张谱图(有谱图时);重复刷新不重复加载。"""
    import numpy as np
    from nmrglue.fileio import pipe

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    shape = (32, 64)
    data = np.zeros(shape, dtype=np.float32)
    data[16, 32] = 50
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 64
    dic["FDSPECNUM"] = 32
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = 32
    dic["FDF1SW"] = 6000
    dic["FDF1OBS"] = 600
    dic["FDF1CAR"] = 118
    dic["FDF1ORIG"] = 118 * 600
    dic["FDF2T"] = 64
    dic["FDF2SW"] = 6000
    dic["FDF2OBS"] = 600
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600
    pipe.write(str(spectra / "exp_001-d_001.ft2"), dic, data, overwrite=True)
    panel = SpectrumPanel(manager)
    panel.set_context("exp_001", "d_001")
    assert panel.viewer.layer_list.count() == 0  # 0.2.88:不自动显示
    assert panel.load_current_spectrum() is True
    assert panel.viewer.layer_list.count() == 1
    assert panel._current_spectrum is not None
    panel.refresh()
    assert panel.viewer.layer_list.count() == 1  # 刷新不重开
    panel.close()

def test_folder_node_shows_files(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw 等子文件夹节点下拉显示目录内文件。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    raw = manager.data_dir("exp_001", "d_001", "raw")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "acqus").write_text("x")
    (raw / "ser").write_text("y")
    panel = ProjectTreePanel(manager)
    data_item = panel.tree.topLevelItem(0).child(0).child(0).child(0)
    raw_item = data_item.child(0)
    names = [raw_item.child(i).text(0) for i in range(raw_item.childCount())]
    assert "acqus" in names and "ser" in names
    panel.close()

def test_spectrum_file_double_click_opens_in_panel(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """树中谱图文件双击 → 右侧谱图面板直接显示。"""
    import numpy as np
    from nmrglue.fileio import pipe

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    shape = (16, 32)
    data = np.zeros(shape, dtype=np.float32)
    data[8, 16] = 10
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 32
    dic["FDSPECNUM"] = 16
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDF1T"] = 16
    dic["FDF1SW"] = 6000
    dic["FDF1OBS"] = 600
    dic["FDF1CAR"] = 118
    dic["FDF1ORIG"] = 118 * 600
    dic["FDF2T"] = 32
    dic["FDF2SW"] = 6000
    dic["FDF2OBS"] = 600
    dic["FDF2CAR"] = 4.7
    dic["FDF2ORIG"] = 4.7 * 600
    pipe.write(str(spectra / "exp_001-d_001.ft2"), dic, data, overwrite=True)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    tree = window.project_tree.tree
    data_item = tree.topLevelItem(0).child(0).child(0).child(0)
    file_item = data_item.child(2).child(0)  # spectra/exp_001-d_001.ft2
    opened: list[str] = []
    window.project_tree.open_spectrum_requested.connect(lambda p: opened.append(p))
    window.project_tree._on_double_clicked(file_item, 0)
    assert opened and Path(opened[0]).name == "exp_001-d_001.ft2"
    window._open_spectrum_from_tree(opened[0])
    assert window.spectrum_panel.viewer.layer_list.count() == 1
    window.close()


def test_viewer_default_dir_matches_current_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """独立谱图查看器默认打开路径=当前数据 spectra 目录。"""
    from viewer.app import SpectrumWindow

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir("exp_001", "d_001", "spectra")
    spectra.mkdir(parents=True, exist_ok=True)
    window = MainWindow(manager=manager)
    window.project_tree.select_experiment("exp_001")
    window.spectrum_panel.set_context("exp_001", "d_001")
    viewer = SpectrumWindow(start_dir=str(spectra))
    assert Path(viewer.start_dir) == spectra
    viewer.close()
    window.close()

def test_run_step_uses_selected_data_id(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2G-002:run_step 传 data_id 时作用于选中数据(非首个)。"""
    monkeypatch.setattr("threading.Thread", SyncThread)
    from gui.pipeline_panel import PipelinePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    manager.import_data("exp_001", "/sampleE")
    manager.save()
    # 0.2.163-补14:前置未完成不运行下一步——先让 d_002 的 fid 就绪
    from gui.pipeline_state import record_step_success

    fid = manager.data_dir("exp_001", "d_002", "process") / "d_002.fid"
    fid.parent.mkdir(parents=True, exist_ok=True)
    fid.write_bytes(b"fid")
    manager.set_data_fid("exp_001", "d_002", fid)
    record_step_success(manager, "exp_001", "d_002", "fid")
    manager.save()
    seen: list[str] = []

    class ScopedController(FakeProcessingController):
        def generate_spectrum(self, data, exp_id=None, data_id=None) -> str:
            seen.append(data_id or "")
            return "/tmp/x.ft2"

    controller = ScopedController()
    panel = PipelinePanel(manager, controller)
    panel.set_selection("data", "exp_001", "d_002")
    panel._on_run_requested("spectrum")
    assert seen == ["d_002"]
    panel.close()


def test_reset_view_union_of_all_layers(qapp: QApplication) -> None:
    """多谱叠加:reset_view 显示所有谱的联合范围。"""
    from viewer.spectrum import Spectrum, SpectrumAxis
    from viewer.spectrum_viewer import SpectrumViewer

    def axis(label: str, size: int) -> SpectrumAxis:
        return SpectrumAxis(label, size, 6000, 600, 4.7, 4.7 * 600)

    import numpy as np

    s1 = Spectrum(np.random.rand(64, 128), [axis("F1", 64), axis("F2", 128)])
    s2 = Spectrum(np.random.rand(32, 256), [axis("F1", 32), axis("F2", 256)])
    viewer = SpectrumViewer()
    viewer.add_spectrum(s1, name="s1")
    viewer.add_spectrum(s2, name="s2")
    viewer.reset_view()
    x_range, y_range = viewer.plot.getViewBox().viewRange()
    assert x_range[1] >= 255 and y_range[1] >= 63  # 覆盖两张谱
    viewer.close()

def test_rename_project_to_sample_wording(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件结构三级名称:项目 → 实验 → 样品数据(菜单/欢迎页/上下文条)。"""
    workspace = tmp_path / "ws2"
    monkeypatch.setattr(
        "gui.main_window.WorkspaceManager", lambda: _TempWorkspace(workspace)
    )
    monkeypatch.setattr(
        "core.workspace.WorkspaceManager",
        lambda *a, **k: _TempWorkspace(workspace),
    )
    window = MainWindow()
    # 未打开项目:上下文条与欢迎页入口文案
    assert window.context_bar.text() == "未打开项目"
    assert window.center_panel.welcome_page.new_button.text() == "新建项目..."
    # 菜单栏:「实验(&E)」菜单,不含「项目管理/添加/删除项目」
    menus = [action.text() for action in window.menuBar().actions()]
    assert "实验(&E)" in menus
    experiment_menu = next(
        action.menu()
        for action in window.menuBar().actions()
        if action.text() == "实验(&E)"
    )
    labels = [action.text() for action in experiment_menu.actions()]
    assert "新建实验..." in labels
    assert "项目管理" not in labels
    assert "添加项目..." not in labels
    assert "删除项目..." not in labels
    window.close()


def test_welcome_page_new_project_inline_input(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """欢迎页「新建项目」:页内内联命名(不弹窗),回车提交发信号 / Esc 取消。"""
    from core.workspace import WorkspaceManager
    from gui.welcome_page import WelcomePage

    workspace = tmp_path / "ws"
    workspace.mkdir()
    page = WelcomePage(workspace=WorkspaceManager(workspace))
    page.show()
    names: list[str] = []
    page.new_project_requested.connect(names.append)
    page._on_new_clicked()
    assert page._name_edit.isVisible()
    assert page._name_edit.placeholderText() == "输入项目名称"
    assert page._name_ok_button.isVisible()
    page._name_edit.setText("demo")
    page._commit_name()
    assert names == ["demo"]
    assert page._name_ok_button.isHidden()
    # 确定按钮提交
    page._on_new_clicked()
    page._name_edit.setText("demo2")
    page._name_ok_button.click()
    assert names == ["demo", "demo2"]
    # Esc 取消:输入行与确定按钮隐藏且不发信号
    page._on_new_clicked()
    page._cancel_name()
    assert page._name_edit.isHidden()
    assert page._name_ok_button.isHidden()
    page.close()


def test_tree_inline_create_experiment_editor_commit(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建实验类型:树内编辑器 commitData→closeEditor 后创建(模拟回车)。"""
    from PyQt6.QtWidgets import QAbstractItemDelegate

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)

    class _FakeNotesDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, parent, title, kind="", values=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_fields(self):
            return {}

    monkeypatch.setattr("gui.main_window.NotesDialog", _FakeNotesDialog)
    window._create_experiment()
    assert window.project_tree._pending_kind == "experiment"

    class _Editor:
        def text(self):
            return "HNCACB"

    window.project_tree._on_editor_commit_data(_Editor())
    window.project_tree._on_editor_closed(
        None, QAbstractItemDelegate.EndEditHint.NoHint
    )
    assert manager.project is not None
    assert any(e.title == "HNCACB" for e in manager.project.experiments)
    window.close()


def test_tree_inline_create_cancel_removes_pending(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建实验类型:编辑取消(Esc)不创建并移除待命名节点。"""
    from PyQt6.QtWidgets import QAbstractItemDelegate

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    before = len(manager.project.experiments)
    window._create_experiment()
    assert window.project_tree._pending_item is not None
    window.project_tree._on_editor_closed(
        None, QAbstractItemDelegate.EndEditHint.RevertModelCache
    )
    assert window.project_tree._pending_item is None
    assert len(manager.project.experiments) == before
    window.close()


def test_segmented_import_entry_validates_and_calls_async(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.110:分段采集导入入口:容器目录校验后走分段异步导入。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    container = tmp_path / "container"
    container.mkdir()
    for seg in ("s1", "s2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    window._segmented_import("exp_001", str(container))
    assert captured and captured[0]["segmented"] is True
    assert captured[0]["source"] == str(container)
    assert captured[0]["title"] == "container"
    assert captured[0]["experiment_id"] == "exp_001"  # G2B-011:导入当前实验类型
    window.close()


def test_segmented_import_rejects_non_container(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分段采集导入:非容器目录提示且不发起导入。"""
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.InfoDialog.show_info",
        staticmethod(lambda parent, title, text_: messages.append(text_)),
    )
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    single = tmp_path / "single"
    single.mkdir()
    (single / "acqus").write_text("x", encoding="utf-8")
    window._segmented_import("exp_001", str(single))
    assert not captured
    assert messages and "不是分段/重复实验容器" in messages[0]
    window.close()


def test_segmented_import_emits_current_experiment(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G2B-011:实验类型页分段入口发请求时携带当前实验类型 id。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    captured: list[dict] = []
    monkeypatch.setattr(
        MainWindow,
        "_import_experiment_async",
        lambda self, data: captured.append(data),
    )
    container = tmp_path / "container"
    container.mkdir()
    for seg in ("s1", "s2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    page = window.center_panel.experiment_page
    page.set_context(manager, "exp_001", "标签")
    page.segmented_source_edit.setText(str(container))
    page._on_segmented_import()
    assert captured and captured[0]["segmented"] is True
    assert captured[0]["experiment_id"] == "exp_001"
    window.close()


def test_rename_editor_appears_at_click_position(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点「重命名」后,右键位置直接出现重命名输入框(回车提交)。"""
    from PyQt6.QtCore import QPoint

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager)
    window.show()
    QApplication.processEvents()
    panel = window.project_tree
    exp_item = panel.tree.topLevelItem(0).child(0).child(0)
    anchor = panel.tree.viewport().mapToGlobal(QPoint(30, 10))
    assert not panel._rename_editor.isVisible()  # 默认不显示(0.2.112 回归)
    panel._begin_rename("experiment", exp_item, anchor)
    editor = panel._rename_editor
    assert editor.isVisible()
    # 0.2.163-补4:内嵌子部件,位置为相对树面板坐标(面板过窄时右缘收进)
    expected = panel.mapFromGlobal(anchor)
    assert editor.pos().y() == expected.y()
    assert 0 <= editor.pos().x() <= max(0, panel.width() - editor.width())
    editor._edit.setText("HNCACB2")
    editor._commit()
    assert manager.project is not None
    assert any(e.title == "HNCACB2" for e in manager.project.experiments)
    assert not editor.isVisible()
    window.close()


def test_context_menu_rename_opens_inline_editor(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """树右键「重命名」:菜单项触发后,右键位置变为重命名输入框。"""
    from PyQt6.QtCore import QPoint

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = ProjectTreePanel(manager)
    panel.show()
    QApplication.processEvents()
    assert not panel._rename_editor.isVisible()  # 默认不显示(0.2.112 回归)
    project_item = panel.tree.topLevelItem(0).child(0)
    menu = QMenu()
    panel._on_context_menu_impl(menu, project_item, QPoint(10, 20))
    action = next(a for a in menu.actions() if "重命名项目" in a.text())
    action.triggered.emit()
    assert panel._rename_editor.isVisible()
    assert panel._rename_target == ("project",)
    panel.close()


def test_log_panel_stop_button_emits_signal(qapp: QApplication) -> None:
    """停止当前任务按钮:点击发出 stop_requested 信号。"""
    from gui.log_panel import LogPanel

    panel = LogPanel()
    got: list[int] = []
    panel.stop_requested.connect(lambda: got.append(1))
    assert panel.stop_button.text() == "停止当前任务"
    panel.stop_button.click()
    assert got == [1]
    panel.close()


def test_main_window_stop_button_logs_termination(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点击停止:调用进程树终止并记录日志(无残留提示)。"""
    import backend.runtime as rt

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    calls: list[int] = []

    def fake_terminate() -> int:
        calls.append(1)
        return 2

    monkeypatch.setattr(rt, "terminate_current_tasks", fake_terminate)
    window.log_panel.stop_button.click()
    assert calls == [1]
    assert not window.log_panel.isHidden()
    assert "已停止当前任务" in window.log_panel.text.toPlainText()
    assert "2" in window.log_panel.text.toPlainText()
    window.close()


def test_main_window_stop_no_task_notice(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.runtime as rt

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    window = MainWindow(manager=manager, controller=FakeProcessingController())
    monkeypatch.setattr(rt, "terminate_current_tasks", lambda: 0)
    window.log_panel.stop_button.click()
    assert "当前没有正在运行的任务" in window.log_panel.text.toPlainText()
    window.close()


def test_default_column_widths_1920(qapp: QApplication) -> None:
    """默认列宽固定 [420, 600, 300, 600] 合计 1920;窄屏收窄不溢出。"""
    from PyQt6.QtGui import QGuiApplication

    window = MainWindow()
    screen = window.screen() or QGuiApplication.primaryScreen()
    cols = window.main_splitter.sizes()
    if screen is None:
        assert sum(cols) == 1920
        window.close()
        return
    avail = screen.availableGeometry()
    if avail.width() >= 1920:
        assert sum(cols) == 1920
        assert abs(cols[0] - 420) <= 1
        assert abs(cols[1] - 600) <= 1
        assert abs(cols[2] - 300) <= 1
        assert abs(cols[3] - 600) <= 1
        assert window.width() == 1920
    else:
        assert sum(cols) <= avail.width()
        assert window.width() == avail.width()
    window.close()

def test_phase_panel_visible_only_in_1d(qapp: QApplication) -> None:
    """0.2.147:p0/p1 相位面板单行,仅 1D 模式出现。"""
    from viewer.spectrum_viewer import SpectrumViewer

    viewer = SpectrumViewer()
    assert viewer.phase_panel.isHidden()
    viewer.add_spectrum(_synthetic_spectrum_2d())
    assert viewer.phase_panel.isHidden()  # 2D 不显示
    viewer.set_1d_mode(True)
    assert not viewer.phase_panel.isHidden()  # 条带 1D 模式显示
    viewer.set_1d_mode(False)
    assert viewer.phase_panel.isHidden()
    viewer.close()


def test_spectrum_panel_new_layout_constraints(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.147:Files/Layers 一行;Show peaks 在 Add peak 前;Poky 字样移除。"""
    from gui.spectrum_panel import SpectrumPanel

    panel = SpectrumPanel()
    assert panel.file_list.parent() is panel.lists_row_widget
    assert panel.viewer.layer_list.parent() is panel.lists_row_widget
    rows = panel.peak_toolbar_widget.layout()
    first = rows.itemAt(0).layout().itemAt(0).widget()
    assert first is panel.viewer.show_peaks_checkbox
    assert panel.import_poky_button.text() == "Import peaks"
    assert panel.export_poky_button.text() == "Export peaks"
    # 0.2.199-补29bb:第二行放 Delete/Import/Export/Save
    row2 = rows.itemAt(1).layout()
    row2_widgets = [row2.itemAt(i).widget() for i in range(row2.count())]
    assert panel.delete_peak_button in row2_widgets
    assert panel.import_poky_button in row2_widgets
    assert panel.export_poky_button in row2_widgets
    assert panel.save_peaks_button in row2_widgets
    # 峰操作行间距显明
    assert panel.peak_toolbar.spacing() >= 10
    assert panel.peak_toolbar2.spacing() >= 10
    panel.close()


def _synthetic_spectrum_2d():
    from viewer.spectrum import Spectrum, SpectrumAxis

    axis_x = SpectrumAxis(
        label="1H", size=64, sw_hz=6000.0, obs_mhz=600.0,
        carrier_ppm=4.7, orig_hz=4.7 * 600.0,
    )
    axis_y = SpectrumAxis(
        label="15N", size=32, sw_hz=2000.0, obs_mhz=60.0,
        carrier_ppm=118.0, orig_hz=118.0 * 60.0,
    )
    import numpy as np
    return Spectrum(data=np.zeros((32, 64)), axes=[axis_y, axis_x])



def test_spectrum_param_report_shows_diagnostics_details() -> None:
    """0.2.157:报告直接显示数据质量诊断详情(不再引用运行日志)。"""
    from gui.pipeline_panel import _spectrum_param_report

    report = _spectrum_param_report(
        {
            "diagnostics": {
                "reports": [
                    "直流偏置: 自动启用 POLY -time",
                    "坏点: 已修复 3 处",
                ],
                "apply_poly_time": True,
            },
            "backend_runs": 2,
        }
    )
    assert "◆ 数据质量诊断" in report
    assert "直流偏置: 自动启用 POLY -time" in report
    assert "坏点: 已修复 3 处" in report
    assert "详见运行日志" not in report


def test_experiment_page_import_buttons(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.162-补12:实验类型页(原导入块位置)含「导入数据」按钮与下拉(「数据组间分析」已隐藏,2026-09-03)。"""
    from gui.main_window import MainWindow

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.create_experiment()
    manager.save()
    window = MainWindow(manager=manager)
    page = window.center_panel.experiment_page
    assert page.import_dropdown_button.text() == "导入数据"
    page._open_import_dropdown()
    assert page._import_dropdown is not None
    window.close()


def test_rename_editor_text_color(qapp: QApplication) -> None:
    """0.2.162-补11:重命名输入框白底黑字(修复文字不可见)。"""
    from gui.project_tree import _InlineRenameEditor

    editor = _InlineRenameEditor()
    stylesheet = editor._edit.styleSheet()
    assert "background: white" in stylesheet
    assert "color: #222" in stylesheet
    editor.close()


def test_experiment_page_dropdown_not_covering_button(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.163-补3:下拉过长时限制高度加滚动条,且不遮住触发按钮。"""
    from PyQt6.QtCore import QPoint

    from gui.main_window import MainWindow

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.create_experiment()
    manager.save()
    window = MainWindow(manager=manager)
    window.show()
    QApplication.processEvents()
    # 0.2.194-补2:下拉为实验类型页子部件,需先选中该页(与真实操作一致);
    # 窗口加宽保证中栏能放下 560 宽的下拉(避免 x 钳位)
    window.center_panel.set_selection("experiment", "exp_001")
    window.resize(1400, 900)
    window.main_splitter.setSizes([300, 720, 180, 200])
    QApplication.processEvents()
    page = window.center_panel.experiment_page
    btn = page.import_dropdown_button
    btn_top = btn.mapTo(page, QPoint(0, 0)).y()
    btn_bottom = btn.mapTo(page, QPoint(0, btn.height())).y()
    page._open_import_dropdown()
    drop = page._import_dropdown
    assert drop.isVisible()
    # 下拉不遮按钮:要么在按钮下方(顶部 >= 按钮底部),要么完全在按钮上方
    # 0.2.194-补2:下拉为实验类型页子部件,pos() 相对本页
    covering = drop.pos().y() < btn_bottom and (
        drop.pos().y() + drop.height() > btn_top
    )
    msg = (
        f"下拉 {drop.pos().y()}..{drop.pos().y() + drop.height()}"
        f" 遮住按钮 {btn_top}..{btn_bottom}"
    )
    assert not covering, msg
    # 高度受限:不超过本页高度,且出现滚动区(内容过长时)
    assert drop.height() <= page.height()
    assert hasattr(drop, "_scroll") and drop._scroll.isVisible()
    window.close()


def test_experiment_page_dropdown_switch(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.162-补13:导入下拉开关行为(组间分析下拉已移除)。"""
    from gui.main_window import MainWindow

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    manager.create_experiment()
    manager.save()
    window = MainWindow(manager=manager)
    window.show()
    QApplication.processEvents()
    # 0.2.194-补2:下拉为实验类型页子部件,需先选中该页(与真实操作一致);
    # 窗口加宽保证中栏能放下 560 宽的下拉(避免 x 钳位)
    window.center_panel.set_selection("experiment", "exp_001")
    window.resize(1400, 900)
    window.main_splitter.setSizes([300, 720, 180, 200])
    QApplication.processEvents()
    page = window.center_panel.experiment_page
    page._open_import_dropdown()
    assert page._import_dropdown.isVisible()
    # 0.2.162-补14:下拉应在按钮正下方(先 show 再 move)
    from PyQt6.QtCore import QPoint

    # 0.2.194-补2:下拉为实验类型页子部件,位置相对本页
    expected = page.import_dropdown_button.mapTo(
        page, QPoint(0, page.import_dropdown_button.height())
    )
    drop = page._import_dropdown
    # 与按钮左缘对齐;中栏比下拉最小宽(560)窄时钳到左缘(offscreen 测试环境)
    assert drop.pos().x() == expected.x() or (
        drop.pos().x() == 0 and page.width() < drop.width()
    )
    assert drop.pos().y() >= expected.y() - 1  # 在按钮下方
    assert drop.pos().y() + drop.height() <= page.height() + 1
    window.close()



def test_peak_threshold_range_up_to_50(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29bo/补29cn:选峰阈值上限 30σ → 50σ;输入框不设上限。"""
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    panel = PipelinePanel(manager, FakeProcessingController())
    panel.set_selection('data', 'exp_001', 'd_001')
    row = panel._rows['peaks']
    assert row.threshold_slider.maximum() == 500          # 50σ
    assert row.threshold_spin.maximum() > 50.0            # 输入框不设上限(补29cn)
    assert row.threshold_spin.value() == pytest.approx(35.0)  # 默认 35σ(补29hn)
    assert row.threshold_slider.value() == 350
    row.threshold_spin.setValue(28.5)
    assert row.threshold_slider.value() == 285
    row.threshold_spin.setValue(100.0)                    # 超过滑块上限
    assert row.threshold_slider.value() == 500            # 滑块停在 50σ
    assert row.threshold_spin.value() == pytest.approx(100.0)  # 不回写覆盖
    panel.close()


def test_peaks_threshold_change_does_not_auto_run(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.2.199-补29au:调阈值不自动选峰,点「运行/重新处理」才执行
    monkeypatch.setattr('threading.Thread', SyncThread)
    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra_dir = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra_dir.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra_dir / 'exp_001-d_001.ft2')

    class PeaksController(FakeProcessingController):
        def pick_peaks(
            self, data, exp_id=None, data_id=None, sigma_multiplier=None
        ) -> dict:
            self.calls.append(('pick_peaks', sigma_multiplier))
            peaks_dir = manager.data_dir(exp_id, data_id, 'peaks')
            peaks_dir.mkdir(parents=True, exist_ok=True)
            (peaks_dir / f'{exp_id}-{data_id}.list').write_text(
                'Assignment w1 w2 Data Height Volume\n'
                'G1  115.000  8.000  0  100  0\n',
                encoding='utf-8',
            )
            return {'status': 'success', 'peak_count': 1}

    controller = PeaksController()
    panel = PipelinePanel(manager, controller)
    log = LogPanel()
    panel.log_message.connect(log.append)
    panel.set_selection('data', 'exp_001', 'd_001')
    row = panel._rows['peaks']
    row.threshold_spin.setValue(8.0)
    row.threshold_slider.sliderReleased.emit()
    assert controller.calls == []  # 调阈值不自动运行
    panel._on_run_requested('peaks')
    assert controller.calls == [('pick_peaks', 8.0)]  # 点运行按新阈值执行
    panel.close()
    log.close()



def test_project_tree_data_status_shows_picked(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.2.199-补29av:有峰表时数据显示「已选峰」
    from gui.project_tree import ProjectTreePanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    spectra = manager.data_dir('exp_001', 'd_001', 'spectra')
    spectra.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra / 'exp_001-d_001.ft2')
    manager.set_data_spectrum(
        'exp_001', 'd_001', str(spectra / 'exp_001-d_001.ft2')
    )
    peaks = manager.data_dir('exp_001', 'd_001', 'peaks')
    peaks.mkdir(parents=True, exist_ok=True)
    (peaks / 'exp_001-d_001.list').write_text(
        'Assignment w1 w2 Data Height Volume\n'
        'G1  115.000  8.000  0  100  0\n',
        encoding='utf-8',
    )
    manager.save()
    panel = ProjectTreePanel(manager)

    def _data_item():
        return panel.tree.topLevelItem(0).child(0).child(0).child(0)

    assert _data_item().text(1) == '已选峰'
    panel.close()


def test_spectrum_display_settings_isolated_per_data(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29fz:contour start / levels / aspect / 标记尺寸按数据
    隔离——d_001 的调节不串到 d_002,切回 d_001 恢复。"""
    from gui.spectrum_panel import SpectrumPanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    exp = manager.project.experiment("exp_001")
    assert exp is not None
    d1_id = str(exp.data[0].id)
    d2 = manager.import_data(exp.id, "/fake/2")
    spectra1 = manager.data_dir(exp.id, d1_id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{exp.id}-{d1_id}.ft2")
    spectra2 = manager.data_dir(exp.id, d2.id, "spectra")
    spectra2.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra2 / f"{exp.id}-{d2.id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    panel.viewer.level_slider.setValue(60)
    panel.viewer.count_slider.setValue(12)
    panel.viewer.aspect_slider.setValue(80)
    panel.peak_size_spin.setValue(2.0)
    assert panel._display_states[(exp.id, d1_id)]["level_slider"] == 60
    # d_002 首次打开用默认,不受 d_001 影响
    panel.set_context(exp.id, d2.id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.level_slider.value() == 31
    assert panel.viewer.count_slider.value() == 8
    assert panel.viewer.aspect_slider.value() == 0
    assert panel.peak_size_spin.value() == 1.5
    # 切回 d_001 恢复各自调节
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    assert panel.viewer.level_slider.value() == 60
    assert panel.viewer.count_slider.value() == 12
    assert panel.viewer.aspect_slider.value() == 80
    assert panel.peak_size_spin.value() == 2.0
    panel.close()


def test_log_panel_data_scope_persists_to_data_folder(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-补29ga:单数据日志持久化到 d_xxx/log.txt,新面板可读回。"""
    from core.project import ProjectManager
    from gui.log_panel import LogPanel

    manager = ProjectManager.create_project(tmp_path / "proj_log", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/1")
    log = LogPanel()
    log.set_manager(manager)
    scope = log.scope_key("data", exp.id, data.id)
    log.append("第一次处理完成", scope=scope)
    path = manager.data_base(exp.id, data.id) / "report" / "log.txt"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "第一次处理完成" in text
    # 新 LogPanel(模拟重启)切到该数据能看到历史
    log2 = LogPanel()
    log2.set_manager(manager)
    log2.set_scope("data", exp.id, data.id)
    assert any(
        "第一次处理完成" in line for line in log2._buffers[scope]
    )
    # 清空面板同步清空记录文件(保留标题行)
    log2.clear()
    text2 = path.read_text(encoding="utf-8")
    assert "第一次处理完成" not in text2
    assert text2.startswith("#")
    log.close()
    log2.close()


def test_spectrum_display_settings_persisted_in_data_folder(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29ga:显示调节持久化到 d_xxx/ui_state.json,重启恢复。"""
    import json

    from gui.spectrum_panel import SpectrumPanel

    manager = _manager_with_experiment(tmp_path, monkeypatch)
    exp = manager.project.experiment("exp_001")
    assert exp is not None
    d1_id = str(exp.data[0].id)
    spectra1 = manager.data_dir(exp.id, d1_id, "spectra")
    spectra1.mkdir(parents=True, exist_ok=True)
    _write_ft2(spectra1 / f"{exp.id}-{d1_id}.ft2")
    panel = SpectrumPanel(manager)
    panel.set_context(exp.id, d1_id)
    assert panel.load_current_spectrum() is True
    panel.viewer.level_slider.setValue(55)
    panel.viewer.count_slider.setValue(11)
    panel.viewer.aspect_slider.setValue(70)
    panel.peak_size_spin.setValue(2.5)
    path = manager.data_base(exp.id, d1_id) / "ui_state.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    spectrum = payload["spectrum"]
    assert spectrum["level_slider"] == 55
    assert spectrum["level_count"] == 11
    assert spectrum["aspect"] == 70
    assert spectrum["peak_size"] == 2.5
    # 新面板(模拟重启)打开该数据恢复
    panel2 = SpectrumPanel(manager)
    panel2.set_context(exp.id, d1_id)
    assert panel2.load_current_spectrum() is True
    assert panel2.viewer.level_slider.value() == 55
    assert panel2.viewer.count_slider.value() == 11
    assert panel2.viewer.aspect_slider.value() == 70
    assert panel2.peak_size_spin.value() == 2.5
    panel.close()
    panel2.close()


def test_log_panel_group_scope_persists_to_group_folder(
    tmp_path: Path, qapp: QApplication
) -> None:
    """0.2.199-补29gb:数据组日志也落盘(组目录 log.txt),新面板读回。"""
    from core.project import ProjectManager
    from gui.log_panel import LogPanel

    manager = ProjectManager.create_project(tmp_path / "proj_gl", "demo")
    exp = manager.create_experiment("HSQC")
    data = manager.import_data(exp.id, "/fake/1")
    group = manager.create_data_group(exp.id, data_ids=[data.id])
    log = LogPanel()
    log.set_manager(manager)
    scope = log.scope_key("group", exp.id, "", group.id)
    log.append("组日志第一行", scope=scope)
    path = (
        manager.root
        / exp.id
        / "groups"
        / group.id
        / "report"
        / "log.txt"
    )
    assert path.is_file()
    assert "组日志第一行" in path.read_text(encoding="utf-8")
    log2 = LogPanel()
    log2.set_manager(manager)
    log2.set_scope("group", exp.id, "", group.id)
    assert any(
        "组日志第一行" in line for line in log2._buffers[scope]
    )
    log2.clear()
    assert "组日志第一行" not in path.read_text(encoding="utf-8")
    log.close()
    log2.close()
