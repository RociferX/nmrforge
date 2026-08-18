"""中间 Pipeline 面板:围绕当前样品数据/实验类型显示处理步骤与状态。

六步流程(契约 v1.2 / G2B-002,含可选 SMILE 优化):
导入样品数据 → 生成 FID → 生成谱图(含 SMILE 重构)→ [SMILE 优化,可选] →
峰挑选 → 分析。

- 步骤状态依据前置依赖与产物文件推断(LOCKED/READY/RUNNING/SUCCESS/FAILED);
- READY 步骤提供「运行」按钮,经 ProcessingController 对应方法执行;
- LOCKED 步骤 tooltip 说明缺哪个前置步骤;
- GUI 层不直接触碰 Backend:唯一出口是 ProcessingController。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.pipeline_state import (
    batch_data_ids,
    batch_id,
    input_fingerprint,
    load_pipeline_state,
    raw_fingerprint,
    script_fingerprint,
)
from gui.processing import ProcessingController

# 步骤定义:id / 名称 / 描述 / 前置步骤 id 列表
PIPELINE_STEPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("import", "导入样品数据", "读 Bruker 参数并复制到项目(raw),不触发处理", ()),
    ("fid", "生成 FID", "由原始数据转换为 fid(后端 bruker -AUTO/fid.com)", ("import",)),
    ("spectrum", "生成谱图", "后端处理生成谱(自动包含 NUS SMILE 重构)", ("fid",)),
    ("smile", "SMILE 优化", "可选:重构参数网格优化并采用最优谱(仅 NUS)", ("spectrum",)),
    ("peaks", "峰挑选", "自动峰检测与强度/SNR 评估", ("spectrum",)),
    ("analysis", "分析", "峰归属与结果分析", ("peaks",)),
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

# 步骤 → ProcessingController 方法映射(契约 v1.2 §8.3)
STEP_METHOD: dict[str, str] = {
    "import": "import_data",
    "fid": "generate_fid",
    "spectrum": "generate_spectrum",
    "smile": "optimize_smile",
    "peaks": "pick_peaks",
    "analysis": "analyze",
}


def _data_nodes(manager: ProjectManager, exp_id: str) -> list:
    """返回实验下的 Data 节点。

    Backend 落地 DataEntry 层级(契约 v1.2 §8.1)后直接返回 entry.data;
    当前兼容阶段:单数据模型下以实验自身作为数据节点。
    """
    entry = manager.project.experiment(exp_id) if manager.project is not None else None
    if entry is None:
        return []
    return list(getattr(entry, "data", None) or [])



def _first_report(directory: Path) -> Path | None:
    """目录下第一个报告产物(html/pdf/json),无则 None。"""
    if not directory.is_dir():
        return None
    try:
        files = sorted(
            p
            for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in ('.html', '.pdf', '.json')
        )
    except OSError:
        return None
    return files[0] if files else None


def _node_artifacts(
    manager: ProjectManager, exp_id: str, data_id: str
) -> dict[str, Path | None]:
    """单个数据节点各步骤产物路径(兼容 schema 1.3 与旧扁平布局)。"""
    artifacts: dict[str, Path | None] = {
        'fid': None,
        'spectrum': None,
        'peaks': None,
        'analysis': None,
    }
    data = manager.data(exp_id, data_id)
    fid_candidate = getattr(data, 'fid_path', '') or ''
    if fid_candidate:
        path = Path(fid_candidate)
        if not path.is_absolute():
            path = manager.root / path
        # 0.2.108:分段合并 FID 为目录(process/merged/fid),文件或目录均视为产物
        if path.is_file() or path.is_dir():
            artifacts['fid'] = path
    if artifacts['fid'] is None:
        proc = manager.data_dir(exp_id, data_id, 'process')
        try:
            fids = sorted(proc.glob('*.fid'))
        except OSError:
            fids = []
        if fids:
            artifacts['fid'] = fids[0]
        else:
            merged_fid = proc / 'merged' / 'fid'
            if merged_fid.is_dir():
                artifacts['fid'] = merged_fid
    spec_candidate = getattr(data, 'spectrum_path', '') or ''
    if spec_candidate:
        path = Path(spec_candidate)
        if not path.is_absolute():
            path = manager.root / path
        if path.is_file():
            artifacts['spectrum'] = path
    if artifacts['spectrum'] is None:
        spectra = manager.data_dir(exp_id, data_id, 'spectra')
        for ext in ('ft2', 'ft3'):
            path = spectra / f'{exp_id}-{data_id}.{ext}'
            if path.is_file():
                artifacts['spectrum'] = path
                break
    if artifacts['spectrum'] is None:  # 旧扁平布局
        flat = manager.dir_path('spectra')
        for ext in ('ft2', 'ft3'):
            path = flat / f'{exp_id}.{ext}'
            if path.is_file():
                artifacts['spectrum'] = path
                break
    for suffix in ('.list', '.csv'):
        candidate = (
            manager.data_dir(exp_id, data_id, 'peaks')
            / f'{exp_id}-{data_id}{suffix}'
        )
        if candidate.is_file():
            artifacts['peaks'] = candidate
            break
    if artifacts['peaks'] is None:
        for suffix in ('.list', '.csv'):
            flat = manager.dir_path('peaks') / f'{exp_id}{suffix}'
            if flat.is_file():
                artifacts['peaks'] = flat
                break
    report = _first_report(manager.data_dir(exp_id, data_id, 'report'))
    if report is None:
        report = _first_report(manager.dir_path('report'))
    if report is None:
        analysis_dir = manager.dir_path('analysis') / exp_id
        if analysis_dir.is_dir():
            report = analysis_dir
    artifacts['analysis'] = report
    return artifacts


def _upstream_artifact(
    step_id: str, artifacts: dict[str, Path | None]
) -> Path | None:
    prev = {'spectrum': 'fid', 'peaks': 'spectrum', 'analysis': 'peaks'}.get(
        step_id
    )
    return artifacts.get(prev) if prev else None


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _pipeline_flags() -> bool:
    """Pipeline 简单模式开关(config/nmrforge.local.yaml → gui.settings):

    开启后只按上一步产物文件是否存在判断状态(下一步只认对应格式的
    文件),不做输入/脚本指纹与产物新旧比较,也不显示「已过期」。
    """
    try:
        from gui.settings import load_settings

        pipeline = load_settings().get("pipeline") or {}
    except Exception:  # noqa: BLE001 - 设置不可读时按默认关闭
        pipeline = {}
    return bool(pipeline.get("simple_mode", False))


def _node_step_statuses(
    manager: ProjectManager, exp_id: str, node
) -> dict[str, str]:
    """单数据节点五步状态:产物 + 指纹校验(OUTDATED)+ 前置依赖。"""
    data_id = getattr(node, 'id', exp_id)
    artifacts = _node_artifacts(manager, exp_id, data_id)
    state = load_pipeline_state(manager, exp_id, data_id)
    simple_mode = _pipeline_flags()
    statuses: dict[str, str] = {}
    for step_id, _, _, deps in PIPELINE_STEPS:
        artifact = (
            None
            if step_id in ('import', 'smile')
            else artifacts.get(step_id)
        )
        outdated = False
        if step_id == 'import':
            done = True  # 实验下存在数据节点即导入完成
            entry = state['steps'].get('import')
            if (
                entry
                and entry.get('input_hash')
                and not simple_mode
            ):
                current = raw_fingerprint(manager, exp_id, data_id)
                outdated = current is not None and current != entry['input_hash']
        elif step_id == 'smile':
            # 可选步骤:运行过即完成(产物复用谱图,指纹校验输入变化)
            entry = state['steps'].get('smile')
            done = entry is not None
            if done and not simple_mode:
                current = input_fingerprint(manager, exp_id, data_id, 'smile')
                if (
                    current is not None
                    and entry.get('input_hash')
                    and current != entry['input_hash']
                ):
                    outdated = True
        else:
            done = artifact is not None
            if done and not simple_mode:
                entry = state['steps'].get(step_id)
                if entry:
                    current_input = input_fingerprint(
                        manager, exp_id, data_id, step_id
                    )
                    if (
                        current_input is not None
                        and entry.get('input_hash')
                        and current_input != entry['input_hash']
                    ):
                        outdated = True
                    current_script = script_fingerprint(
                        manager, exp_id, data_id, step_id
                    )
                    if (
                        current_script is not None
                        and entry.get('script_hash')
                        and current_script != entry['script_hash']
                    ):
                        outdated = True
                else:
                    # 旧数据/无指纹状态:用上游产物 mtime 启发式
                    upstream = _upstream_artifact(step_id, artifacts)
                    if upstream is not None and _mtime_ns(upstream) > _mtime_ns(
                        artifact
                    ):
                        outdated = True
        failed_run = _last_run_for(
            manager, exp_id, data_id, _step_refs(step_id)
        )
        if failed_run is not None and failed_run.status == 'failed':
            statuses[step_id] = 'FAILED'
        elif done and not outdated:
            statuses[step_id] = 'SUCCESS'
        elif done:
            statuses[step_id] = 'OUTDATED'
        elif all(statuses.get(dep) in ('SUCCESS', 'OUTDATED') for dep in deps):
            statuses[step_id] = 'READY'
        else:
            statuses[step_id] = 'LOCKED'
    # 上游 OUTDATED 传播:下游即使指纹匹配也视为过期(开关关闭时不传播)
    if not simple_mode:
        for step_id, _, _, deps in PIPELINE_STEPS:
            if statuses.get(step_id) == 'SUCCESS' and any(
                statuses.get(dep) == 'OUTDATED' for dep in deps
            ):
                statuses[step_id] = 'OUTDATED'
    return statuses


def compute_step_statuses(manager: ProjectManager, exp_id: str) -> dict[str, str]:
    """按产物文件、指纹校验与前置依赖推断各步骤状态(支持 OUTDATED)。

    实验级聚合:多数据时任一节点成功即 SUCCESS(兼容旧行为/测试)。
    """
    nodes = _data_nodes(manager, exp_id)
    if not nodes:
        return {step_id: 'LOCKED' for step_id, _, _, _ in PIPELINE_STEPS}
    per_node = [_node_step_statuses(manager, exp_id, node) for node in nodes]
    statuses: dict[str, str] = {}
    for step_id, _, _, deps in PIPELINE_STEPS:
        verdicts = [node_status[step_id] for node_status in per_node]
        if 'OUTDATED' in verdicts:
            statuses[step_id] = 'OUTDATED'
        elif 'SUCCESS' in verdicts:
            statuses[step_id] = 'SUCCESS'
        elif all(statuses.get(dep) in ('SUCCESS', 'OUTDATED') for dep in deps):
            statuses[step_id] = 'READY'
        else:
            statuses[step_id] = 'LOCKED'
    return statuses


def compute_data_step_statuses(
    manager: ProjectManager, exp_id: str, data_id: str
) -> dict[str, str]:
    """按单个数据节点计算步骤状态(中间处理页按选中数据显示)。"""
    nodes = _data_nodes(manager, exp_id)
    node = next(
        (n for n in nodes if getattr(n, "id", "") == data_id), None
    )
    if node is None:
        return compute_step_statuses(manager, exp_id)
    return _node_step_statuses(manager, exp_id, node)


def _outdated_reasons(
    statuses: dict[str, str],
    manager: ProjectManager,
    exp_id: str,
    data_id: str = "",
) -> dict[str, str]:
    """为 OUTDATED 步骤生成原因(输入/脚本变化、上游过期、产物落后)。"""
    reasons: dict[str, str] = {}
    nodes = _data_nodes(manager, exp_id)
    if data_id:
        nodes = [n for n in nodes if getattr(n, "id", "") == data_id]
    for step_id, _, _, deps in PIPELINE_STEPS:
        if statuses.get(step_id) != 'OUTDATED':
            continue
        reason = ''
        for node in nodes:
            data_id = getattr(node, 'id', exp_id)
            entry = load_pipeline_state(manager, exp_id, data_id)['steps'].get(
                step_id
            )
            if entry and entry.get('input_hash'):
                current = input_fingerprint(manager, exp_id, data_id, step_id)
                if current is not None and current != entry['input_hash']:
                    reason = '输入已变化(上游重新运行或外部修改),请重新运行'
                    break
                current_script = script_fingerprint(
                    manager, exp_id, data_id, step_id
                )
                if (
                    current_script is not None
                    and entry.get('script_hash')
                    and current_script != entry['script_hash']
                ):
                    reason = '处理脚本已变化,请重新运行'
                    break
        if not reason:
            stale_upstream = [
                STEP_LABEL[dep] for dep in deps if statuses.get(dep) == 'OUTDATED'
            ]
            if stale_upstream:
                reason = '上游步骤已过期: ' + '、'.join(stale_upstream)
            else:
                reason = '输入产物比本步骤产物新,请重新运行'
        reasons[step_id] = reason
    return reasons


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


def _step_refs(step_id: str) -> tuple[str, ...]:
    """步骤 → 可能的工作流 refs(查最近运行用)。"""
    return {
        "import": ("import",),
        "fid": ("convert_to_fid", "manual_fid"),
        "spectrum": ("process", "reconstruct_nus", "manual_process", "manual_nus"),
        "smile": ("smile_optimize",),
        "peaks": ("pick_peaks", "manual_peaks"),
        "analysis": ("analyze",),
    }.get(step_id, ())


def _last_run_for(
    manager: ProjectManager, exp_id: str, data_id: str, refs: tuple[str, ...]
):
    """数据在指定步骤 refs 下的最近一次 WorkflowRun(无则 None)。"""
    if manager.project is None:
        return None
    for candidate in reversed(manager.project.workflow_runs):
        if candidate.experiment_id != exp_id:
            continue
        if candidate.workflow_ref not in refs:
            continue
        if (candidate.inputs or {}).get("data_id", "") not in ("", data_id):
            continue
        return candidate
    return None


def _format_params(params: dict) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(params.items()))


# 生成谱图步骤参数报告的常见键名标签(0.2.86)
_SPECTRUM_PARAM_LABELS = {
    "extract": "提取窗口",
    "ext_lo": "提取下限(ppm)",
    "ext_hi": "提取上限(ppm)",
    "zero_fill": "填零",
    "window": "窗函数",
    "baseline": "基线",
    "phases": "相位",
    "sampling": "采样",
    "smile": "SMILE",
}


def _format_phase_pair(value) -> str:
    """相位对 ((p0,p1) 元组或 {p0,p1,source} dict) → 可读文本。"""
    if isinstance(value, dict):
        p0 = value.get("p0", "")
        p1 = value.get("p1", "")
        text = f"p0={p0}° p1={p1}°"
        if value.get("source"):
            text += " (" + str(value.get("source")) + ")"
        return text
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return f"p0={value[0]}° p1={value[1]}°"
    return str(value)


def _spectrum_param_report(params: dict) -> str:
    """把生成谱图实际生效参数整理为可读参数报告(点击步骤展开查看)。"""
    lines: list[str] = []
    for key, value in sorted(params.items()):
        if key == "phases" and value:
            lines.append("  逐维相位:")
            for axis, pair in sorted((value or {}).items()):
                lines.append(f"    {axis}: {_format_phase_pair(pair)}")
        elif key == "direct_phase":
            lines.append(f"  直接维相位: {_format_phase_pair(value)}")
        elif key == "backend_runs":
            lines.append(f"  后端运行次数: {value}")
        elif key == "phases":
            continue
        else:
            lines.append(f"  {_SPECTRUM_PARAM_LABELS.get(key, key)}: {value}")
    return "\n".join(lines) if lines else "  (无参数记录)"


class PipelineStepRow(QWidget):
    """单个步骤行:状态图标 + 名称 + 描述 + 运行/人工入口 + 内嵌详情。

    点击行展开/收起详情(输入产物、运行记录、参数、脚本快照);LOCKED /
    OUTDATED / FAILED 原因灰字直显;FAILED 提供「查看日志」「重试」。
    """

    run_requested = pyqtSignal(str)  # step_id
    manual_requested = pyqtSignal(str)  # step_id:打开人工参数表格/脚本编辑器
    report_requested = pyqtSignal(str)  # step_id:分析完成后打开报告页
    show_spectrum_requested = pyqtSignal(str)  # step_id:生成谱图完成后展示谱图
    detail_toggled = pyqtSignal(str)  # step_id:点击行切换详情
    view_log_requested = pyqtSignal(str)  # step_id:定位日志面板

    def __init__(
        self, step_id: str, label: str, description: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.step_id = step_id
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(0)

        header = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(24)
        header.addWidget(self.icon_label)
        text_box = QVBoxLayout()
        self.name_label = QLabel(label)
        self.name_label.setStyleSheet("font-weight: bold;")
        self.desc_label = QLabel(description)
        self.desc_label.setStyleSheet("color: #666;")
        self.desc_label.setWordWrap(True)
        text_box.addWidget(self.name_label)
        text_box.addWidget(self.desc_label)
        header.addLayout(text_box, 1)
        self.status_label = QLabel("")
        header.addWidget(self.status_label)
        self.run_button = QPushButton("运行")
        self.run_button.setVisible(False)
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.step_id))
        header.addWidget(self.run_button)
        self.report_button = QPushButton("报告")
        self.report_button.setToolTip("查看当前数据的报告产物(report/ 目录)")
        self.report_button.setVisible(False)
        self.report_button.clicked.connect(
            lambda: self.report_requested.emit(self.step_id)
        )
        header.addWidget(self.report_button)
        self.show_spectrum_button = QPushButton("展示谱图")
        self.show_spectrum_button.setToolTip(
            "在右侧谱图面板显示当前数据 spectra 文件夹的最终谱"
        )
        self.show_spectrum_button.setVisible(False)
        self.show_spectrum_button.clicked.connect(
            lambda: self.show_spectrum_requested.emit(self.step_id)
        )
        header.addWidget(self.show_spectrum_button)
        # 0.2.108:相位优化途径选择(仅生成谱图步骤显示)
        self.phase_route_combo = QComboBox()
        self.phase_route_combo.addItem("Unified", "unified")
        self.phase_route_combo.addItem("None", "none")
        self.phase_route_combo.setToolTip(
            "相位优化途径:Unified=统一方案(逐维复型预览+内存调相,默认);"
            "None=跳过相位优化(逃生口)"
        )
        self.phase_route_combo.setVisible(False)
        header.addWidget(self.phase_route_combo)
        self.manual_button = QPushButton("人工")
        self.manual_button.setToolTip("人工参数表格 / 脚本编辑器")
        self.manual_button.setVisible(False)
        self.manual_button.clicked.connect(
            lambda: self.manual_requested.emit(self.step_id)
        )
        header.addWidget(self.manual_button)
        outer.addLayout(header)

        self.reason_label = QLabel("")
        self.reason_label.setStyleSheet("color: #888;")
        self.reason_label.setWordWrap(True)
        self.reason_label.setVisible(False)
        outer.addWidget(self.reason_label)

        self.detail_frame = QFrame()
        self.detail_frame.setFrameShape(QFrame.Shape.StyledPanel)
        # 显式浅色背景:深色系统主题下 QFrame 会变黑,灰字看不清
        self.detail_frame.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #d5d8dc; "
            "border-radius: 4px; }"
        )
        self.detail_frame.setVisible(False)
        detail_layout = QVBoxLayout(self.detail_frame)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #222;")
        detail_layout.addWidget(self.detail_label)
        detail_buttons = QHBoxLayout()
        self.view_log_button = QPushButton("查看日志")
        self.view_log_button.setVisible(False)
        self.view_log_button.clicked.connect(
            lambda: self.view_log_requested.emit(self.step_id)
        )
        detail_buttons.addWidget(self.view_log_button)
        self.retry_button = QPushButton("重试")
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(
            lambda: self.run_requested.emit(self.step_id)
        )
        detail_buttons.addWidget(self.retry_button)
        detail_layout.addLayout(detail_buttons)
        outer.addWidget(self.detail_frame)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        """点击行(非按钮区域)切换详情展开/收起。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.detail_toggled.emit(self.step_id)
        super().mousePressEvent(event)

    def set_detail(self, text: str, failed: bool = False) -> None:
        """填充详情文本。"""
        self.detail_label.setText(text)
        self.view_log_button.setVisible(failed)
        self.retry_button.setVisible(failed)

    def set_status(self, status: str, reason: str = "") -> None:
        icon = STATUS_ICON.get(status, "·")
        label = STATUS_TEXT.get(status, status)
        self.status_label.setText(f"{icon} {label}")
        tooltip = f"状态: {STATUS_TEXT.get(status, status)}"
        if reason:
            tooltip += f"\n{reason}"
        self.status_label.setToolTip(tooltip)
        # 原因直显:LOCKED/OUTDATED/FAILED 灰字(不只 tooltip)
        if status in ("LOCKED", "OUTDATED", "FAILED"):
            self.reason_label.setText(reason or STATUS_TEXT.get(status, status))
            self.reason_label.setVisible(True)
        else:
            self.reason_label.setVisible(False)
        if status == "OUTDATED":
            self.run_button.setText("重新运行")
            self.run_button.setVisible(True)
            self.run_button.setToolTip("输入/参数已变化,重新运行以更新结果")
        elif status == "SUCCESS" and self.step_id != "import":
            self.run_button.setText("重新处理")
            self.run_button.setVisible(True)
            self.run_button.setToolTip(
                "已处理完成;点击可强制重新处理(下游步骤将标记为过期)"
            )
        else:
            self.run_button.setText("运行")
            self.run_button.setVisible(status == "READY")
            self.run_button.setToolTip("运行当前步骤")
        # 分析步骤产物就绪后提供「报告」入口
        self.report_button.setVisible(status == "SUCCESS" and self.step_id == "analysis")


class PipelinePanel(QWidget):
    """Pipeline 功能区:上下文面包屑 + 下一步提示 + 步骤列表。"""

    log_message = pyqtSignal(str)
    run_finished = pyqtSignal()
    manual_open_requested = pyqtSignal(str)  # step_id:打开人工处理对话框
    report_requested = pyqtSignal(str)  # step_id:打开报告页
    show_spectrum_requested = pyqtSignal(str)  # step_id:展示谱图
    import_data_requested = pyqtSignal(str)  # exp_id:在当前实验类型下导入样品数据
    view_log_requested = pyqtSignal(str)  # step_id:定位日志面板
    progress_updated = pyqtSignal(str)  # 批量进度文本(主线程更新标签)
    batch_summary_requested = pyqtSignal(object)  # 批量汇总 dict

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
        self._selection_kind: str = ""  # data/folder 时显示步骤;project/experiment 显示提示
        self._current_data_id: str = ""
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
        self.import_button = QPushButton("导入样品数据...")
        self.import_button.setVisible(False)
        self.import_button.clicked.connect(
            lambda: self.import_data_requested.emit(self._current_exp_id)
        )
        layout.addWidget(self.import_button)
        self.batch_progress_label = QLabel("")
        self.batch_progress_label.setVisible(False)
        self.batch_progress_label.setStyleSheet(
            "color: #16a085; font-weight: bold; padding: 2px 0;"
        )
        self.batch_progress_label.setWordWrap(True)
        layout.addWidget(self.batch_progress_label)
        self.progress_updated.connect(self._on_progress_updated)
        self.hint_bubble = QLabel("")
        self.hint_bubble.setVisible(False)
        self.hint_bubble.setWordWrap(True)
        self.hint_bubble.setStyleSheet(
            "background: #fef9e7; border: 1px solid #f5b041; "
            "color: #935116; padding: 4px 8px;"
        )
        layout.addWidget(self.hint_bubble)

        steps_box = QVBoxLayout()
        for step_id, label, description, _deps in PIPELINE_STEPS:
            row = PipelineStepRow(step_id, label, description)
            row.run_requested.connect(self._on_run_requested)
            row.manual_requested.connect(self.manual_open_requested.emit)
            row.report_requested.connect(self.report_requested.emit)
            row.show_spectrum_requested.connect(self.show_spectrum_requested.emit)
            row.detail_toggled.connect(self._toggle_step_detail)
            row.view_log_requested.connect(self.view_log_requested.emit)
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
    def set_context(self, exp_id: str, data_id: str | None = None) -> None:
        """兼容入口:按实验类型设置上下文(data_id 缺省回退首个样品数据)。"""
        self._selection_kind = "experiment" if exp_id else ""
        self._current_exp_id = exp_id or ""
        if data_id is not None:
            self._current_data_id = data_id
        self.refresh()

    def set_selection(self, kind: str, exp_id: str, data_id: str = "") -> None:
        """按树选中类型刷新:project/experiment 显示「未选中数据」;data/folder 显示步骤。"""
        self._selection_kind = kind or ""
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id
        self.refresh()

    def current_experiment_id(self) -> str:
        return self._current_exp_id

    def _current_statuses(self) -> dict[str, str]:
        """当前选中样品数据的步骤状态;未选中样品数据/旧单样品数据回退实验类型聚合。"""
        if self._current_data_id:
            return compute_data_step_statuses(
                self.manager, self._current_exp_id, self._current_data_id
            )
        return compute_step_statuses(self.manager, self._current_exp_id)

    def refresh(self) -> None:
        """刷新上下文标签与步骤状态。"""
        project = self.manager.project
        if project is None or not self._current_exp_id:
            self.context_label.setText("未打开项目")
            self.next_label.setText("")
            self.import_button.setVisible(False)
            for row in self._rows.values():
                row.set_status("LOCKED")
            return
        if self._selection_kind in ("project", "experiment"):
            exp = project.experiment(self._current_exp_id)
            label = exp.title if exp is not None else project.name
            self.context_label.setText(f"{label} — 未选中数据")
            self.next_label.setText("请选择左侧的 Data 节点查看/运行处理步骤")
            for row in self._rows.values():
                row.set_status("LOCKED")
                row.manual_button.setVisible(False)  # 未选中数据不显示人工
            self.import_button.setVisible(True)  # 可直接在当前实验类型导入样品数据
            self._rows["import"].setVisible(True)
            return
        self.import_button.setVisible(False)
        # 导入样品数据属于实验类型层(点中实验类型时显示),样品数据层不再展示该步骤
        self._rows["import"].setVisible(False)
        exp = project.experiment(self._current_exp_id)
        exp_title = exp.title if exp is not None else self._current_exp_id
        current_batch = (
            batch_id(self.manager, self._current_exp_id, self._current_data_id)
            if self._current_data_id
            else ""
        )
        context_text = (
            f"{project.name} / {exp_title} ({self._current_exp_id})"
        )
        if current_batch:
            group_count = len(
                batch_data_ids(self.manager, self._current_exp_id, current_batch)
            )
            context_text += f" [批量 {current_batch}: {group_count} 数据]"
        self.context_label.setText(context_text)
        statuses = self._current_statuses()
        # 样品数据层不提示/展示导入步骤(导入属于实验类型层动作)
        outdated_next = next(
            (
                sid
                for sid, st in statuses.items()
                if st == "OUTDATED" and sid != "import"
            ),
            None,
        )
        next_step = next(
            (
                sid
                for sid, st in statuses.items()
                if st == "READY" and sid != "import"
            ),
            None,
        )
        if outdated_next:
            self.next_label.setText(
                f"下一步: 重新运行 {STEP_LABEL[outdated_next]}"
            )
        elif next_step:
            self.next_label.setText(f"下一步: {STEP_LABEL[next_step]}")
        else:
            self.next_label.setText(
                "全部步骤已完成"
                if any(st == "SUCCESS" for st in statuses.values())
                else "等待导入样品数据"
            )
        reasons = _lock_reasons(statuses)
        outdated = _outdated_reasons(
            statuses,
            self.manager,
            self._current_exp_id,
            self._current_data_id,
        )
        for step_id, status in statuses.items():
            reason = reasons.get(step_id, "") or outdated.get(step_id, "")
            if status == "FAILED":
                run = _last_run_for(
                    self.manager,
                    self._current_exp_id,
                    self._current_data_id,
                    _step_refs(step_id),
                )
                if run is not None and run.message:
                    reason = run.message
            self._rows[step_id].set_status(status, reason)
            # 导入样品数据为自动化步骤,无人工入口;其余处理步骤保留人工
            self._rows[step_id].manual_button.setVisible(
                step_id not in ("import", "smile")
            )
            # 0.2.88:生成谱图完成后出现「展示谱图」按钮(不再自动显示谱)
            self._rows[step_id].show_spectrum_button.setVisible(
                step_id == "spectrum" and status == "SUCCESS"
            )
            # 0.2.108:生成谱图步骤提供「相位优化途径」选择
            self._rows[step_id].phase_route_combo.setVisible(
                step_id == "spectrum"
            )

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------
    def run_step(self, step_id: str, data_id: str | None = None) -> None:
        """运行指定步骤(data_id 指定作用域;缺省用当前选中/首个数据)。"""
        if step_id not in self._rows:
            return
        if data_id is not None:
            self._current_data_id = data_id
        row = self._rows[step_id]
        if not row.run_button.isHidden():
            self._on_run_requested(step_id)

    def show_first_import_hint(self) -> None:
        """首次导入后的下一步提示:高亮下一个可运行步骤 + 气泡,8 秒后消失。"""
        statuses = self._current_statuses()
        next_step = next(
            (sid for sid, st in statuses.items() if st in ("READY", "OUTDATED")),
            None,
        )
        if next_step is None:
            return
        row = self._rows.get(next_step)
        if row is None:
            return
        row.name_label.setStyleSheet("font-weight: bold; color: #16a085;")
        self.hint_bubble.setText(
            f"已导入样品数据:下一步可运行「{STEP_LABEL.get(next_step, next_step)}」"
        )
        self.hint_bubble.setVisible(True)
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(8000, self._clear_first_import_hint)

    def _clear_first_import_hint(self) -> None:
        self.hint_bubble.setVisible(False)
        for row in self._rows.values():
            row.name_label.setStyleSheet("font-weight: bold;")

    def _on_progress_updated(self, text: str) -> None:
        """批量进度标签(主线程):空文本隐藏。"""
        self.batch_progress_label.setText(text)
        self.batch_progress_label.setVisible(bool(text))

    def _toggle_step_detail(self, step_id: str) -> None:
        """点击步骤行展开/收起内嵌详情面板。"""
        row = self._rows.get(step_id)
        if row is None:
            return
        text, _params, failed = self._step_detail(step_id)
        row.set_detail(text, failed=failed)
        row.detail_frame.setVisible(row.detail_frame.isHidden())

    def _step_detail(self, step_id: str) -> tuple[str, dict | None, bool]:
        """构建步骤详情(输入/产物/最近运行/参数/脚本快照);返回(文本, params, failed)。"""
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if not (exp_id and data_id):
            return "未选中数据", None, False
        lines: list[str] = []
        params: dict | None = None
        failed = False
        if step_id != "import":
            try:
                artifacts = _node_artifacts(self.manager, exp_id, data_id)
                artifact = artifacts.get(step_id)
                if artifact is not None:
                    lines.append(f"产物: {artifact}")
            except Exception:  # noqa: BLE001 - 产物解析失败忽略
                pass
        run = _last_run_for(self.manager, exp_id, data_id, _step_refs(step_id))
        if run is None:
            lines.append("运行记录: 无")
        else:
            failed = run.status == "failed"
            lines.append(f"运行: {run.run_id} [{run.status}] {run.workflow_ref}")
            if run.message:
                lines.append(f"消息: {run.message}")
            if run.outputs:
                outs = " | ".join(f"{k}={v}" for k, v in run.outputs.items())
                lines.append(f"输出: {outs}")
            if run.snapshot_dir:
                lines.append(f"快照目录: {run.snapshot_dir}")
            if run.scripts:
                lines.append(f"脚本快照: {'、'.join(run.scripts)}")
            if run.params:
                params = dict(run.params)
                lines.append(f"参数: {_format_params(run.params)}")
                if step_id == "spectrum":
                    lines.append("参数报告(生成谱图实际生效参数):")
                    lines.append(_spectrum_param_report(run.params))
        return "\n".join(lines) if lines else "无详情", params, failed

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
        method_name = STEP_METHOD.get(step_id)
        method = getattr(self.controller, method_name, None) if method_name else None
        if method is None:
            self.log_message.emit(
                f"{STEP_LABEL.get(step_id, step_id)}: 后端接口待实现,暂不可运行"
            )
            return
        self._rows[step_id].set_status("RUNNING")
        self.log_message.emit(f"开始 {STEP_LABEL.get(step_id, step_id)}: {entry.id}")

        def worker() -> None:
            try:
                nodes = _data_nodes(self.manager, self._current_exp_id)
                if not nodes:
                    self.log_message.emit(
                        f"{STEP_LABEL.get(step_id, step_id)}: "
                        "该实验类型还没有样品数据,请先导入样品数据"
                    )
                    return
                data_node = next(
                    (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
                    nodes[0],
                )
                exp_id = self._current_exp_id
                target_data_id = getattr(data_node, "id", exp_id)
                # 批量组:同一标记的数据绑定,整组依次执行
                current_batch = batch_id(self.manager, exp_id, target_data_id)
                data_ids = (
                    batch_data_ids(self.manager, exp_id, current_batch)
                    if current_batch
                    else [target_data_id]
                )
                total = len(data_ids)
                results: list[dict] = []
                if total > 1:
                    self.log_message.emit(
                        f"批量组 {current_batch}: 对 {total} 个数据依次"
                        f" {STEP_LABEL.get(step_id, step_id)}"
                    )
                for index, data_id in enumerate(data_ids, start=1):
                    node = next(
                        (n for n in nodes if getattr(n, "id", "") == data_id),
                        None,
                    )
                    if node is None:
                        continue
                    step_label = STEP_LABEL.get(step_id, step_id)
                    if total > 1:
                        self.progress_updated.emit(
                            f"批量 {current_batch}: {index - 1}/{total} 完成 · "
                            f"当前: {data_id} {step_label}"
                        )
                    item: dict = {
                        "data_id": data_id,
                        "step": step_label,
                        "ok": False,
                        "error": "",
                    }
                    try:
                        if method_name == "import_data":
                            source = getattr(node, "source", "") or ""
                            result = method(entry, source)
                        else:
                            import inspect

                            kwargs: dict = {"exp_id": exp_id, "data_id": data_id}
                            if "progress" in inspect.signature(method).parameters:
                                kwargs["progress"] = (
                                    lambda msg, d=data_id: self.log_message.emit(
                                        f"{step_label} {d}: {msg}"
                                    )
                                )
                            if (
                                step_id == "spectrum"
                                and "params" in inspect.signature(method).parameters
                            ):
                                # 0.2.108:相位优化途径(unified/none)透传后端
                                kwargs["params"] = {
                                    "phase_route": (
                                        self._rows["spectrum"]
                                        .phase_route_combo.currentData()
                                        or "unified"
                                    )
                                }
                            result = method(node, **kwargs)
                        item["ok"] = True
                        message = (
                            result if isinstance(result, str) else str(result)
                        )
                        self.log_message.emit(
                            f"完成 {step_label} {data_id}: {message}"
                        )
                    except Exception as exc:  # noqa: BLE001 - 单数据失败不中断整组
                        item["error"] = f"{type(exc).__name__}: {exc}"
                        self.log_message.emit(
                            f"失败 {step_label} {data_id}: {item['error']}"
                        )
                    results.append(item)
                self.progress_updated.emit("")
                if total > 1:
                    ok_count = sum(1 for r in results if r.get("ok"))
                    self.batch_summary_requested.emit(
                        {
                            "info": (
                                f"批量组 {current_batch}: {ok_count}/{total} 成功"
                            ),
                            "items": results,
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - 错误统一回传 UI
                self.log_message.emit(
                    f"失败 {STEP_LABEL.get(step_id, step_id)}: {exc}"
                )
            finally:
                self.refresh()
                self.run_finished.emit()

        import threading

        threading.Thread(target=worker, daemon=True).start()
