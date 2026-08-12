"""中间 Pipeline 面板:围绕当前实验显示处理步骤与状态。

第一版实现"状态驱动的步骤列表 + 下一步提示"(GUI_ARCHITECTURE_VISION §13-16):
- 步骤状态依据前置依赖与产物文件推断(LOCKED/READY/RUNNING/SUCCESS/FAILED);
- READY 步骤提供"运行"按钮,经 ProcessingController.auto_run_async 执行;
- OUTDATED(参数/上游变化)与指纹校验留给后续阶段(见 PROJECT_STATUS 待办)。

GUI 层不直接触碰 Backend:唯一出口是 ProcessingController。
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.processing import ProcessingController

# 步骤定义:id / 名称 / 描述 / 前置步骤 id 列表
PIPELINE_STEPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("import", "导入数据", "登记 Bruker 数据集并识别实验类型", ()),
    ("fid", "生成 FID", "由原始数据生成 ser/fid 复型数据", ("import",)),
    ("process", "数据处理", "NMRPipe 傅里叶变换与相位处理", ("fid",)),
    ("reconstruct", "SMILE 重构", "非均匀采样重构(NUS)", ("process",)),
    ("peaks", "峰挑选", "自动峰检测与强度/SNR 评估", ("process",)),
    ("assign", "归属分析", "峰归属与结果分析", ("peaks",)),
)

STEP_LABEL: dict[str, str] = {step_id: label for step_id, label, _, _ in PIPELINE_STEPS}

STATUS_TEXT = {
    "LOCKED": "未就绪",
    "READY": "可运行",
    "RUNNING": "运行中",
    "SUCCESS": "已完成",
    "FAILED": "失败",
    "OUTDATED": "已过期",
}
STATUS_ICON = {
    "LOCKED": "🔒",
    "READY": "▶",
    "RUNNING": "…",
    "SUCCESS": "✓",
    "FAILED": "×",
    "OUTDATED": "!",
}


def compute_step_statuses(manager: ProjectManager, exp_id: str) -> dict[str, str]:
    """按产物文件与前置依赖推断各步骤状态(第一版启发式,后续换指纹校验)。"""
    entry = manager.project.experiment(exp_id) if manager.project is not None else None
    if entry is None:
        return {step_id: "LOCKED" for step_id, _, _, _ in PIPELINE_STEPS}
    spectra = manager.dir_path("spectra")
    peaks = manager.dir_path("peaks")
    analysis = manager.dir_path("analysis")
    has_ft = any(spectra.joinpath(f"{exp_id}.{ext}").is_file() for ext in ("ft2", "ft3"))
    has_peaks = peaks.joinpath(f"{exp_id}.csv").is_file()
    has_analysis = analysis.joinpath(exp_id).is_dir()
    imported = manager.infer_status(exp_id).value not in ("", "registered")

    artifacts: dict[str, bool] = {
        "import": imported,
        "fid": has_ft,
        "process": has_ft,
        "reconstruct": has_ft,
        "peaks": has_peaks,
        "assign": has_analysis,
    }
    statuses: dict[str, str] = {}
    for step_id, _, _, deps in PIPELINE_STEPS:
        if artifacts.get(step_id, False):
            statuses[step_id] = "SUCCESS"
        elif all(statuses.get(dep) == "SUCCESS" for dep in deps):
            statuses[step_id] = "READY"
        else:
            statuses[step_id] = "LOCKED"
    return statuses


def _lock_reasons(statuses: dict[str, str]) -> dict[str, str]:
    """为 LOCKED 步骤生成依赖提示(告诉用户缺哪个前置产物)。"""
    reasons: dict[str, str] = {}
    for step_id, _, _, deps in PIPELINE_STEPS:
        if statuses.get(step_id) != "LOCKED":
            continue
        missing = [STEP_LABEL[dep] for dep in deps if statuses.get(dep) != "SUCCESS"]
        if missing:
            reasons[step_id] = "前置步骤未完成: " + "、".join(missing)
        else:
            reasons[step_id] = "等待前置产物就绪"
    return reasons


class PipelineStepRow(QWidget):
    """单个步骤行:状态图标 + 名称 + 描述 + 运行/人工入口按钮。"""

    run_requested = pyqtSignal(str)  # step_id
    manual_requested = pyqtSignal(str)  # step_id:打开人工参数表格/脚本编辑器

    def __init__(
        self, step_id: str, label: str, description: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.step_id = step_id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(24)
        layout.addWidget(self.icon_label)
        text_box = QVBoxLayout()
        self.name_label = QLabel(label)
        self.name_label.setStyleSheet("font-weight: bold;")
        self.desc_label = QLabel(description)
        self.desc_label.setStyleSheet("color: #666;")
        self.desc_label.setWordWrap(True)
        text_box.addWidget(self.name_label)
        text_box.addWidget(self.desc_label)
        layout.addLayout(text_box, 1)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        self.run_button = QPushButton("运行")
        self.run_button.setVisible(False)
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.step_id))
        layout.addWidget(self.run_button)
        self.manual_button = QPushButton("人工")
        self.manual_button.setToolTip("人工参数表格 / 脚本编辑器(骨架)")
        self.manual_button.clicked.connect(
            lambda: self.manual_requested.emit(self.step_id)
        )
        layout.addWidget(self.manual_button)

    def set_status(self, status: str, reason: str = "") -> None:
        icon = STATUS_ICON.get(status, "·")
        label = STATUS_TEXT.get(status, status)
        self.status_label.setText(f"{icon} {label}")
        tooltip = f"状态: {STATUS_TEXT.get(status, status)}"
        if reason:
            tooltip += f"\n{reason}"
        self.status_label.setToolTip(tooltip)
        self.run_button.setVisible(status == "READY")


class PipelinePanel(QWidget):
    """Pipeline 功能区:上下文面包屑 + 下一步提示 + 步骤列表。"""

    log_message = pyqtSignal(str)
    run_finished = pyqtSignal()
    manual_open_requested = pyqtSignal(str)  # step_id:打开人工处理对话框

    def __init__(
        self,
        manager: ProjectManager | None = None,
        controller: ProcessingController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager or ProjectManager()
        self.controller = controller or ProcessingController()
        self._current_exp_id: str = ""
        self._rows: dict[str, PipelineStepRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.context_label = QLabel("未打开项目")
        self.context_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(self.context_label)

        self.next_label = QLabel("")
        self.next_label.setWordWrap(True)
        self.next_label.setStyleSheet("color: #16a085;")
        layout.addWidget(self.next_label)

        steps_box = QVBoxLayout()
        for step_id, label, description, _deps in PIPELINE_STEPS:
            row = PipelineStepRow(step_id, label, description)
            row.run_requested.connect(self._on_run_requested)
            row.manual_requested.connect(self.manual_open_requested.emit)
            steps_box.addWidget(row)
            self._rows[step_id] = row
        steps_box.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setLayout(steps_box)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.refresh()

    # ------------------------------------------------------------------
    # 上下文
    # ------------------------------------------------------------------
    def set_context(self, exp_id: str) -> None:
        """设置当前实验并刷新步骤状态。"""
        self._current_exp_id = exp_id or ""
        self.refresh()

    def current_experiment_id(self) -> str:
        return self._current_exp_id

    def refresh(self) -> None:
        """刷新上下文标签与步骤状态。"""
        project = self.manager.project
        if project is None or not self._current_exp_id:
            self.context_label.setText("未打开项目")
            self.next_label.setText("")
            for row in self._rows.values():
                row.set_status("LOCKED")
            return
        exp = project.experiment(self._current_exp_id)
        exp_title = exp.title if exp is not None else self._current_exp_id
        self.context_label.setText(
            f"{project.name} / {exp_title} ({self._current_exp_id})"
        )
        statuses = compute_step_statuses(self.manager, self._current_exp_id)
        next_step = next((sid for sid, st in statuses.items() if st == "READY"), None)
        if next_step:
            self.next_label.setText(f"下一步: {STEP_LABEL[next_step]}")
        else:
            self.next_label.setText("全部步骤已完成" if any(
                st == "SUCCESS" for st in statuses.values()
            ) else "等待导入数据")
        reasons = _lock_reasons(statuses)
        for step_id, status in statuses.items():
            self._rows[step_id].set_status(status, reasons.get(step_id, ""))

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------
    def run_step(self, step_id: str) -> None:
        """运行指定步骤(仅 READY 步骤有效);供菜单/下一步按钮调用。"""
        if step_id not in self._rows:
            return
        row = self._rows[step_id]
        if not row.run_button.isHidden():
            self._on_run_requested(step_id)

    def _on_run_requested(self, step_id: str) -> None:
        if not self._current_exp_id:
            return
        entry = (
            self.manager.project.experiment(self._current_exp_id)
            if self.manager.project is not None
            else None
        )
        if entry is None:
            return
        self._rows[step_id].set_status("RUNNING")
        self.log_message.emit(f"开始 {STEP_LABEL.get(step_id, step_id)}: {entry.id}")

        def on_done(result: dict) -> None:
            status = result.get("status", "?")
            message = result.get("message") or ""
            logs = result.get("logs") or []
            self.log_message.emit(f"完成 {STEP_LABEL.get(step_id, step_id)}: {status} {message}")
            for line in list(logs)[-5:]:
                self.log_message.emit(f"  {line}")
            self.refresh()
            self.run_finished.emit()

        def on_error(error: str) -> None:
            self._rows[step_id].set_status("FAILED")
            self.log_message.emit(f"失败 {STEP_LABEL.get(step_id, step_id)}: {error}")
            self.refresh()
            self.run_finished.emit()

        self.controller.auto_run_async(entry, on_done, on_error)

