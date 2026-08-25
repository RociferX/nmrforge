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
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.pipeline_state import (
    input_fingerprint,
    load_pipeline_state,
    script_fingerprint,
)
from gui.processing import ProcessingController

# 步骤定义:id / 名称 / 描述 / 前置步骤 id 列表
PIPELINE_STEPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("fid", "生成 FID", "由原始数据转换为 fid(后端 bruker -AUTO/fid.com)", ()),
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
            None if step_id == 'smile' else artifacts.get(step_id)
        )
        outdated = False
        if step_id == 'smile':
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
        "fid": ("convert_to_fid", "manual_fid"),
        "spectrum": (
            "process",
            "reconstruct_nus",
            "manual_process",
            "manual_nus",
            "phase_optimize_unified",
        ),
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


def _spectrum_param_report(
    params: dict, spectrum_path: str | None = None
) -> str:
    """生成谱图参数报告(0.2.169-补 可读化):◆ 数据质量诊断 + ◆ 处理
    参数与优化;spectrum_path 可读时附加 ◆ 最终谱图质量(与日志末尾
    汇总共用 spectrum_quality_report_lines)。"""
    from workflow.optimization_report import (
        format_optimization_report,
        spectrum_quality_report_lines,
    )

    lines: list[str] = []
    reports = list((params.get("diagnostics") or {}).get("reports") or [])
    lines.append("◆ 数据质量诊断(处理前的数据监测,FID 检查)")
    if reports:
        auto_count = int(
            bool((params.get("diagnostics") or {}).get("apply_poly_time"))
        )
        auto_count += int(
            int(
                (params.get("diagnostics") or {}).get("repaired_badpoints") or 0
            )
            > 0
        )
        suffix = f"(已自动处理 {auto_count} 项)" if auto_count else "(未自动处理)"
        lines.append(f"   ⚠ 检出 {len(reports)} 项问题 {suffix}")
        for i, report in enumerate(reports, 1):
            lines.append(f"     {i}. {report}")
    else:
        lines.append("   ✓ 未检出直流偏置、尖峰坏点、首点异常、宽带峰或漂移")
    if spectrum_path:
        lines += spectrum_quality_report_lines(spectrum_path)
    lines.append("◆ 处理参数与优化")
    lines += format_optimization_report(
        {k: v for k, v in params.items() if k != "diagnostics"}
    )
    return "\n".join(lines) if lines else "  (无参数记录)"


class PipelineStepRow(QWidget):
    """单个步骤行:状态图标 + 名称 + 描述 + 运行/人工入口 + 内嵌详情。

    点击行展开/收起详情(输入产物、运行记录、参数、脚本快照);LOCKED /
    OUTDATED / FAILED 原因灰字直显;FAILED 提供「查看日志」「重试」。
    """

    run_requested = pyqtSignal(str)  # step_id
    rerun_final_requested = pyqtSignal(str)  # step_id:重新运行已有终跑脚本(不重新优化)
    manual_requested = pyqtSignal(str)  # step_id:打开脚本编辑器(已有脚本优先)
    report_requested = pyqtSignal(str)  # step_id:分析完成后打开报告页
    show_spectrum_requested = pyqtSignal(str)  # step_id:生成谱图完成后展示谱图
    ext_range_requested = pyqtSignal(str)  # step_id:设置终跑直接维范围
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
        title_row = QHBoxLayout()
        self.name_label = QLabel(label)
        self.name_label.setStyleSheet("font-weight: bold;")
        title_row.addWidget(self.name_label)
        title_row.addStretch(1)
        self.status_label = QLabel("")
        title_row.addWidget(self.status_label)
        text_box.addLayout(title_row)
        self.desc_label = QLabel(description)
        self.desc_label.setStyleSheet("color: #666;")
        self.desc_label.setWordWrap(True)
        text_box.addWidget(self.desc_label)
        header.addLayout(text_box, 1)
        outer.addLayout(header)
        # 0.2.163-补5:按钮放标题/描述下方独立一行(不再挤在右侧)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(24, 0, 0, 0)
        # 0.2.162-补15:生成谱图运行前「直接维范围」按钮(仅终跑生效)
        self.ext_range_button = QPushButton("直接维范围")
        self.ext_range_button.setToolTip(
            "指定终跑脚本的直接维提取窗口(EXT -x1/-xn);"
            "首遍相位搜索保持原窗口"
        )
        self.ext_range_button.setVisible(self.step_id == "spectrum")
        self.ext_range_button.clicked.connect(
            lambda: self.ext_range_requested.emit(self.step_id)
        )
        button_row.addWidget(self.ext_range_button)
        self.run_button = QPushButton("运行")
        self.run_button.setVisible(False)
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.step_id))
        button_row.addWidget(self.run_button)
        # 0.2.163-补5:spectrum 已完成时「重新运行终脚本」(复用已有脚本,不优化)
        self.rerun_final_button = QPushButton("重新运行终脚本")
        self.rerun_final_button.setToolTip(
            "不重新优化,直接复用上次生成的终跑脚本重跑"
        )
        self.rerun_final_button.setVisible(False)
        self.rerun_final_button.clicked.connect(
            lambda: self.rerun_final_requested.emit(self.step_id)
        )
        button_row.addWidget(self.rerun_final_button)
        self.report_button = QPushButton("报告")
        self.report_button.setToolTip("查看当前数据的报告产物(report/ 目录)")
        self.report_button.setVisible(False)
        self.report_button.clicked.connect(
            lambda: self.report_requested.emit(self.step_id)
        )
        button_row.addWidget(self.report_button)
        self.show_spectrum_button = QPushButton("展示谱图")
        self.show_spectrum_button.setToolTip(
            "在右侧谱图面板显示当前数据 spectra 文件夹的最终谱"
        )
        self.show_spectrum_button.setVisible(False)
        self.show_spectrum_button.clicked.connect(
            lambda: self.show_spectrum_requested.emit(self.step_id)
        )
        button_row.addWidget(self.show_spectrum_button)
        self.manual_button = QPushButton("人工")
        self.manual_button.setToolTip("脚本编辑器:自动运行过则展示已有脚本,可直接修改运行")
        self.manual_button.setVisible(False)
        self.manual_button.clicked.connect(
            lambda: self.manual_requested.emit(self.step_id)
        )
        button_row.addWidget(self.manual_button)
        button_row.addStretch(1)
        outer.addLayout(button_row)

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

    def set_ext_override(self, text: str) -> None:
        """更新「直接维范围」按钮文案(已设值时显示当前范围)。"""
        self.ext_range_button.setText(text)

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
        elif status == "SUCCESS":
            if self.step_id == "spectrum":
                # 0.2.163-补5:重新处理拆两按钮——重新优化 / 重新运行终脚本
                self.run_button.setText("重新优化")
                self.run_button.setToolTip(
                    "重新执行完整自动处理(相位/参数优化 + 终跑)"
                )
                self.rerun_final_button.setVisible(True)
            else:
                self.run_button.setText("重新处理")
                self.rerun_final_button.setVisible(False)
            self.run_button.setVisible(True)
            self.run_button.setToolTip(
                "已处理完成;点击可强制重新处理(下游步骤将标记为过期)"
            )
        else:
            self.run_button.setText("运行")
            self.run_button.setVisible(status == "READY")
            self.run_button.setToolTip("运行当前步骤")
            self.rerun_final_button.setVisible(False)
        # 分析步骤产物就绪后提供「报告」入口
        self.report_button.setVisible(status == "SUCCESS" and self.step_id == "analysis")


class PipelinePanel(QWidget):
    """Pipeline 功能区:上下文面包屑 + 下一步提示 + 步骤列表。"""

    log_message = pyqtSignal(str)
    memory_guard_requested = pyqtSignal(str)  # 0.2.112:SMILE 内存不足弹窗
    run_finished = pyqtSignal()
    manual_open_requested = pyqtSignal(str)  # step_id:打开人工处理对话框
    report_requested = pyqtSignal(str)  # step_id:打开报告页
    show_spectrum_requested = pyqtSignal(str)  # step_id:展示谱图
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
        # 0.2.162-补15:(exp_id, data_id) → (终跑 ext_lo, 终跑 ext_hi)
        self._final_ext: dict[tuple[str, str], tuple[str, str]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.context_label = QLabel("未打开项目")
        self.context_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(self.context_label)

        self.next_label = QLabel("")
        self.next_label.setWordWrap(True)
        self.next_label.setStyleSheet("color: #16a085;")
        layout.addWidget(self.next_label)
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
            row.rerun_final_requested.connect(self._on_rerun_final_requested)
            row.manual_requested.connect(self.manual_open_requested.emit)
            row.report_requested.connect(self.report_requested.emit)
            row.show_spectrum_requested.connect(self.show_spectrum_requested.emit)
            row.ext_range_requested.connect(self._on_ext_range_requested)
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
            for row in self._rows.values():
                row.set_status("LOCKED")
            self._refresh_expanded_details()
            return
        if self._selection_kind in ("project", "experiment"):
            exp = project.experiment(self._current_exp_id)
            label = exp.title if exp is not None else project.name
            self.context_label.setText(f"{label} — 未选中数据")
            self.next_label.setText("请选择左侧的 Data 节点查看/运行处理步骤")
            for row in self._rows.values():
                row.set_status("LOCKED")
                row.manual_button.setVisible(False)  # 未选中数据不显示人工
            self._refresh_expanded_details()
            return
        exp = project.experiment(self._current_exp_id)
        exp_title = exp.title if exp is not None else self._current_exp_id
        group = (
            self.manager.group_of_data(self._current_exp_id, self._current_data_id)
            if self._current_data_id
            and self.manager is not None
            and self.manager.project is not None
            else None
        )
        context_text = (
            f"{project.name} / {exp_title} ({self._current_exp_id})"
        )
        if group is not None:
            context_text += f" [数据组 {group.id}: {len(group.data_ids)} 数据]"
        self.context_label.setText(context_text)
        statuses = self._current_statuses()
        # 样品数据层不提示/展示导入步骤(导入属于实验类型层动作)
        outdated_next = next(
            (
                sid
                for sid, st in statuses.items()
                if st == "OUTDATED"
            ),
            None,
        )
        next_step = next(
            (
                sid
                for sid, st in statuses.items()
                if st == "READY"
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
            # 导入样品数据为自动化步骤,无人工入口;其余处理步骤保留人工;
            # 0.2.163-补14:前置步骤未完成(LOCKED)时不提供人工按钮——
            # 上一步没完成就不给下一步的运行入口(与自动「运行」按钮一致)
            self._rows[step_id].manual_button.setVisible(
                step_id != "smile" and status != "LOCKED"
            )
            # 0.2.88:生成谱图完成后出现「展示谱图」按钮(不再自动显示谱)
            self._rows[step_id].show_spectrum_button.setVisible(
                step_id == "spectrum" and status == "SUCCESS"
            )
            # 0.2.108:生成谱图步骤提供「相位优化途径」选择
        self._update_ext_button()
        self._refresh_expanded_details()

    # ------------------------------------------------------------------
    # 终跑直接维范围(0.2.162-补15)
    # ------------------------------------------------------------------
    def _on_ext_range_requested(self, step_id: str) -> None:
        """「直接维范围」按钮:弹输入对话框,按数据保存终跑直接维范围覆盖。"""
        if step_id != "spectrum":
            return
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if not (exp_id and data_id):
            return
        key = (exp_id, data_id)
        current = self._final_ext.get(key, ("", ""))
        dialog = QDialog(self)
        dialog.setWindowTitle("直接维范围(仅终跑)")
        form = QFormLayout(dialog)
        lo_edit = QLineEdit(str(current[0]) if current[0] else "")
        hi_edit = QLineEdit(str(current[1]) if current[1] else "")
        lo_edit.setPlaceholderText("10.5")
        hi_edit.setPlaceholderText("6.5")
        form.addRow("高场端 ppm (EXT -x1):", lo_edit)
        form.addRow("低场端 ppm (EXT -xn):", hi_edit)
        tip = QLabel(
            "只影响终跑完整脚本,首遍重构/相位搜索保持原窗口。\n"
            "留空=使用默认(10.5-6.5);窗口外峰不会出现在终谱中,\n"
            "直接维线性相位 p1 会按窗口宽度自动重归一化。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666;")
        form.addRow(tip)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        lo = lo_edit.text().strip()
        hi = hi_edit.text().strip()
        if not lo and not hi:
            self._final_ext.pop(key, None)
            self.log_message.emit(
                f"直接维范围(终跑): {data_id} 已清除,恢复默认窗口"
            )
        else:
            self._final_ext[key] = (lo, hi)
            self.log_message.emit(
                f"直接维范围(终跑): {data_id} 已设为 "
                f"{lo or '默认'}-{hi or '默认'} ppm(首遍保持原窗口)"
            )
        self._update_ext_button()

    def _update_ext_button(self) -> None:
        """按当前数据的终跑直接维范围覆盖更新按钮文案与提示词。"""
        row = self._rows.get("spectrum")
        if row is None:
            return
        over = self._final_ext.get((self._current_exp_id, self._current_data_id))
        if over and (over[0] or over[1]):
            lo = over[0] or "默认"
            hi = over[1] or "默认"
            row.set_ext_override(f"直接维范围 {lo}/{hi}")
            row.ext_range_button.setToolTip(
                f"终跑直接维窗口: {lo}-{hi} ppm(EXT -x1/-xn)\n"
                "首遍重构/相位搜索保持原窗口;窗口外峰不进入终谱,\n"
                "p1 按窗口宽度自动重归一化;切换数据后显示各自设置"
            )
        else:
            row.set_ext_override("直接维范围")
            row.ext_range_button.setToolTip(
                "指定终跑脚本的直接维提取窗口(EXT -x1/-xn);"
                "首遍相位搜索保持原窗口;未设置时用默认(10.5-6.5)"
            )

    def _spectrum_ext_params(self, data_id: str) -> dict | None:
        """当前实验某数据的终跑直接维范围 → generate_spectrum params(无则 None)。"""
        over = self._final_ext.get((self._current_exp_id, data_id))
        if not over:
            return None
        ext_params: dict[str, str] = {}
        if over[0]:
            ext_params["final_ext_lo"] = over[0]
        if over[1]:
            ext_params["final_ext_hi"] = over[1]
        return ext_params or None

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
            if step_id == "spectrum":
                # 0.2.155:精简——生成谱图只展示可读参数报告,
                # 不再 dump 原始参数/脚本快照等内部细节
                if run.params:
                    params = dict(run.params)
                    lines.append("参数报告(生成谱图实际生效参数):")
                    lines.append(
                        _spectrum_param_report(
                            run.params,
                            str((run.outputs or {}).get("spectrum_path") or ""),
                        )
                    )
            else:
                if run.outputs:
                    outs = " | ".join(f"{k}={v}" for k, v in run.outputs.items())
                    lines.append(f"输出: {outs}")
                if run.snapshot_dir:
                    lines.append(f"快照目录: {run.snapshot_dir}")
                if run.scripts:
                    lines.append(f"脚本快照: {'、'.join(run.scripts)}")
                if run.params:
                    lines.append(f"参数: {_format_params(run.params)}")
        return "\n".join(lines) if lines else "无详情", params, failed

    def _refresh_expanded_details(self) -> None:
        """0.2.161:已展开的步骤详情(参数报告等)随数据/上下文切换立即刷新。"""
        for step_id, row in self._rows.items():
            if not row.detail_frame.isHidden():
                text, _params, failed = self._step_detail(step_id)
                row.set_detail(text, failed=failed)

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
        # 0.2.163-补14:前置步骤未完成(LOCKED)时拒绝运行(峰挑选/分析等
        # 全部后续步骤一致;按钮已隐藏,此处是程序化入口的防御校验)
        if self._current_data_id:
            try:
                statuses = compute_data_step_statuses(
                    self.manager, self._current_exp_id, self._current_data_id
                )
                if statuses.get(step_id) == "LOCKED":
                    reasons = _lock_reasons(statuses)
                    self.log_message.emit(
                        f"{STEP_LABEL.get(step_id, step_id)}: 前置步骤未完成,"
                        f"请先完成 {reasons.get(step_id, '上一步')}"
                    )
                    return
            except Exception:  # noqa: BLE001 - 状态判定失败不阻断原流程
                pass
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
                # 批量组:统一委托新引擎(controller.run_group_batch ->
                # workflow.batch.run_batch);0.2.164-补1 删除旧内联逐数据循环
                group = (
                    self.manager.group_of_data(exp_id, target_data_id)
                    if self.manager is not None and self.manager.project is not None
                    else None
                )
                if group is not None:
                    self._run_group_step(exp_id, group.id, step_id, target_data_id)
                    return
                step_label = STEP_LABEL.get(step_id, step_id)
                try:
                    import inspect

                    kwargs: dict = {"exp_id": exp_id, "data_id": target_data_id}
                    if step_id == "spectrum":
                        ext_params = self._spectrum_ext_params(target_data_id)
                        if ext_params and "params" in inspect.signature(method).parameters:
                            kwargs["params"] = ext_params
                    if "progress" in inspect.signature(method).parameters:
                        kwargs["progress"] = lambda msg: self.log_message.emit(
                            f"{step_label}: {msg}"
                        )
                    result = method(data_node, **kwargs)
                    message = result if isinstance(result, str) else str(result)
                    self.log_message.emit(f"完成 {step_label}: {message}")
                except Exception as exc:  # noqa: BLE001 - 单数据失败
                    self.log_message.emit(
                        f"失败 {step_label}: {type(exc).__name__}: {exc}"
                    )
                    if "无法处理该谱" in str(exc):
                        self.memory_guard_requested.emit(str(exc))
            except Exception as exc:  # noqa: BLE001 - 错误统一回传 UI
                self.log_message.emit(
                    f"失败 {STEP_LABEL.get(step_id, step_id)}: {exc}"
                )
                if "无法处理该谱" in str(exc):
                    self.memory_guard_requested.emit(str(exc))
            finally:
                self.refresh()
                self.run_finished.emit()

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def _run_group_step(
        self,
        exp_id: str,
        group_id: str,
        step_id: str,
        target_data_id: str,
    ) -> None:
        """批量组执行:统一委托新引擎(controller.run_group_batch -> workflow.batch)。

        0.2.164-补1:删除面板内联逐数据循环;失败汇总由引擎按数据记录,
        单数据失败不中断整组。
        """
        step_label = STEP_LABEL.get(step_id, step_id)
        group_count = len(self.manager.group_data_ids(exp_id, group_id))
        self.log_message.emit(
            f"数据组 {group_id}: 对 {group_count} 个数据执行 {step_label}"
        )
        if step_id == "smile":
            self.log_message.emit("SMILE 优化不支持批量组,请在单个数据上执行")
            return
        try:
            kwargs: dict = {}
            if step_id == "spectrum":
                ext_params = self._spectrum_ext_params(target_data_id)
                if ext_params:
                    kwargs["params"] = ext_params
            result = self.controller.run_group_batch(
                exp_id,
                group_id,
                [step_id],
                reference_data_id="",
                progress=lambda msg: self.log_message.emit(f"{step_label}: {msg}"),
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - 引擎级失败
            self.log_message.emit(
                f"失败 {step_label}: {type(exc).__name__}: {exc}"
            )
            if "无法处理该谱" in str(exc):
                self.memory_guard_requested.emit(str(exc))
            return
        summary = dict(result.get("summary") or {})
        failed = list(result.get("failed") or [])
        ok_count = int(summary.get("success", 0))
        total = int(summary.get("total", 0))
        info = f"数据组 {group_id}: {ok_count}/{total} 成功"
        if failed:
            info += " · 失败: " + ",".join(failed)
        items = [
            {
                "data_id": data_id,
                "step": step_label,
                "ok": per.get("status") == "success",
                "error": per.get("error", ""),
            }
            for data_id, per in (result.get("results") or {}).items()
        ]
        self.batch_summary_requested.emit({"info": info, "items": items})

    def _on_rerun_final_requested(self, step_id: str) -> None:
        """「重新运行终脚本」:直接在已有最终脚本上改直接维范围再运行。

        终跑脚本(uniform {data_id}_process.com / NUS {data_id}_nus.com)已含
        优化后的相位/窗/基线等全部参数;用户改了直接维范围后,只需把脚本
        里 EXT 行的 -x1/-xn ppm 窗口替换为最新值再运行——其它参数完全
        不动,谱图不会因重新渲染而改变。
        """
        if step_id != "spectrum":
            return
        exp_id = self._current_exp_id
        if not exp_id:
            return
        self._rows[step_id].set_status("RUNNING")
        self.log_message.emit("开始重新运行终脚本(仅更新直接维范围,其余参数不变)")

        import re

        def worker() -> None:
            try:
                nodes = _data_nodes(self.manager, exp_id)
                if not nodes:
                    self.log_message.emit("该实验类型还没有样品数据")
                    return
                data_node = next(
                    (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
                    nodes[0],
                )
                data_id = getattr(data_node, "id", exp_id)
                # 定位已有终跑脚本(uniform/NUS),找不到则提示先优化生成
                work = self.manager.data_dir(exp_id, data_id, "process")
                script_path = None
                for name in (
                    f"{data_id}_process.com",
                    f"{data_id}_nus.com",
                    "process.com",
                    "nus.com",
                ):
                    candidate = work / name
                    if candidate.is_file():
                        script_path = candidate
                        break
                if script_path is None:
                    self.log_message.emit(
                        "没有可复用的终跑脚本,请先执行「重新优化」生成"
                    )
                    return
                content = script_path.read_text(encoding="utf-8", errors="replace")
                # 应用用户最新设置的直接维范围:替换 EXT 行的 -x1/-xn
                ext = self._spectrum_ext_params(data_id) or {}
                if ext:
                    lo = str(ext.get("final_ext_lo", ""))
                    hi = str(ext.get("final_ext_hi", ""))
                    if lo:
                        content = re.sub(
                            r"(-x1 )([0-9.]+)(ppm)?",
                            lambda m: f"{m.group(1)}{lo}" + (m.group(3) or ""),
                            content,
                        )
                    if hi:
                        content = re.sub(
                            r"(-xn )([0-9.]+)(ppm)?",
                            lambda m: f"{m.group(1)}{hi}" + (m.group(3) or ""),
                            content,
                        )
                    script_path.write_text(content, encoding="utf-8", newline="\n")
                    self.log_message.emit(
                        f"直接维范围已更新: {lo or '默认'}-{hi or '默认'} ppm → {script_path.name}"
                    )
                # 运行修改后的终跑脚本,谱图归位;实时转发脚本输出
                result = self.controller.run_manual_spectrum(
                    data_node,
                    {script_path.name: content},
                    exp_id=exp_id,
                    data_id=data_id,
                    progress=lambda line: self.log_message.emit(
                        f"[{script_path.name}] {line}"
                    ),
                )
                self.log_message.emit(
                    f"重新运行终脚本完成 {data_id}: {result}"
                )
            except Exception as exc:  # noqa: BLE001 - 错误统一回传 UI
                self.log_message.emit(
                    f"重新运行终脚本失败: {type(exc).__name__}: {exc}"
                )
            finally:
                self.refresh()
                self.run_finished.emit()

        import threading

        threading.Thread(target=worker, daemon=True).start()
