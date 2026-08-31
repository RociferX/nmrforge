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

import hashlib
import json
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSlider,
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
    # 0.2.199-补29ab:报告顺序 = 数据质量 → 处理参数与优化 → 最终谱图质量
    lines.append("◆ 处理参数与优化")
    lines += format_optimization_report(
        {k: v for k, v in params.items() if k != "diagnostics"}
    )
    if spectrum_path:
        lines += spectrum_quality_report_lines(spectrum_path)
    return "\n".join(lines) if lines else "  (无参数记录)"


class _FlowLayout(QLayout):
    """简单流式布局:子项超过可用宽度时自动换行(0.2.199-补4)。

    用于步骤行按钮区——生成谱图完成后最多 5 个按钮(直接维范围/重新优化/
    重新运行终脚本/展示谱图/人工),单行过宽时自动折行,不撑破面板。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(6)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool = False) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue  # 隐藏按钮不占位(如非 spectrum 行的「直接维范围」)
            hint = item.sizeHint()
            next_x = x + hint.width()
            if line_height > 0 and next_x > rect.right() - m.right():
                x = rect.x() + m.left()
                y += line_height + self.spacing()
                next_x = x + hint.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height + m.bottom() - rect.y()


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
    ref_spectrum_requested = pyqtSignal(str)  # step_id:选择参考谱(峰挑选)
    clear_ref_requested = pyqtSignal(str)  # step_id:清除参考谱约束
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
        # 0.2.163-补5:按钮放标题/描述下方独立一行(不再挤在右侧);
        # 0.2.199-补4:按钮多时(如谱图步骤 5 个)自动换行,避免行过宽
        button_row = _FlowLayout()
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
        # 0.2.199-补29ar/补29au/补29bo/补29cm/补29cn:峰挑选阈值条(3.0–50.0 σ,
        # 默认 15;输入框不设上限,滑块仅到 50);
        # 补29au:调整只更新数值,点「运行/重新处理」才重新选峰
        self.threshold_label = QLabel("阈值(σ)")
        self.threshold_label.setVisible(self.step_id == "peaks")
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(30, 500)
        self.threshold_slider.setValue(150)
        self.threshold_slider.setFixedWidth(120)
        self.threshold_slider.setVisible(self.step_id == "peaks")
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(3.0, 1_000_000.0)  # 输入值不设上限(补29cn)
        self.threshold_spin.setSingleStep(0.5)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setValue(15.0)
        self.threshold_spin.setVisible(self.step_id == "peaks")
        # 联动保护:输入超过滑块上限(50σ)时滑块停在 500,不回写覆盖输入值
        self._threshold_sync = False

        def _slider_to_spin(v: int) -> None:
            if self._threshold_sync:
                return
            self._threshold_sync = True
            try:
                self.threshold_spin.setValue(v / 10.0)
            finally:
                self._threshold_sync = False

        def _spin_to_slider(v: float) -> None:
            if self._threshold_sync:
                return
            self._threshold_sync = True
            try:
                self.threshold_slider.setValue(
                    max(30, min(500, int(round(v * 10.0))))
                )
            finally:
                self._threshold_sync = False

        self.threshold_slider.valueChanged.connect(_slider_to_spin)
        self.threshold_spin.valueChanged.connect(_spin_to_slider)
        tip = "调整选峰阈值(σ);点「运行/重新处理」后按新阈值重新选峰"
        self.threshold_slider.setToolTip(tip)
        self.threshold_spin.setToolTip(tip)
        button_row.addWidget(self.threshold_label)
        button_row.addWidget(self.threshold_slider)
        button_row.addWidget(self.threshold_spin)
        # 0.2.199-补29dl(用户):参考谱——选峰时只保留与参考峰表匹配的峰
        self.ref_button = QPushButton("参考谱")
        self.ref_button.setToolTip(
            "选择参考谱(任意已有峰表的数据):选峰时只保留与参考峰表匹配的峰"
        )
        self.ref_button.setVisible(self.step_id == "peaks")
        self.ref_button.clicked.connect(
            lambda: self.ref_spectrum_requested.emit(self.step_id)
        )
        button_row.addWidget(self.ref_button)
        self.ref_label = QLabel("")
        self.ref_label.setVisible(self.step_id == "peaks")
        self.ref_label.setStyleSheet("color: #16a085;")
        button_row.addWidget(self.ref_label)
        self.clear_ref_button = QPushButton("清除")
        self.clear_ref_button.setVisible(False)
        self.clear_ref_button.setToolTip("清除参考谱约束")
        self.clear_ref_button.clicked.connect(
            lambda: self.clear_ref_requested.emit(self.step_id)
        )
        button_row.addWidget(self.clear_ref_button)
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

    def get_threshold(self) -> float:
        """峰挑选阈值(σ);非 peaks 步骤返回默认 15.0(0.2.199-补29cm)。"""
        return self.threshold_spin.value() if self.step_id == "peaks" else 15.0

    def set_ref_text(self, text: str) -> None:
        """显示已选参考谱(0.2.199-补29dl)。"""
        self.ref_label.setText(text)
        self.clear_ref_button.setVisible(bool(text) and self.step_id == "peaks")

    def clear_ref_display(self) -> None:
        """清除参考谱显示。"""
        self.ref_label.setText("")
        self.clear_ref_button.setVisible(False)

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
        threshold_ok = status in ("READY", "SUCCESS", "OUTDATED", "FAILED")
        if self.step_id == "peaks":
            self.threshold_slider.setEnabled(threshold_ok)
            self.threshold_spin.setEnabled(threshold_ok)
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
    log_scoped = pyqtSignal(str, str)  # (message, scope):运行日志按数据/组作用域(0.2.199-补29d)
    memory_guard_requested = pyqtSignal(str)  # 0.2.112:SMILE 内存不足弹窗
    run_finished = pyqtSignal()
    run_started = pyqtSignal(str, str)  # (exp_id, data_id):某数据开始处理,左侧状态显示运行中
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
        self._final_ext: dict[tuple[str, str], tuple[str, str, bool]] = {}
        # 0.2.199-补12:生成谱图参数报告按谱文件指纹缓存,避免每次刷新主线程
        # 重读大 ft3 算质量导致卡顿;运行中标志防连续点击重复启动
        self._spectrum_report_cache: dict[str, str] = {}
        self._run_active: bool = False
        # 0.2.199-补29dl:参考谱约束 {label, peaks, nuclei, path}
        self._ref_info: dict | None = None

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
        # 0.2.199-补29c:run_finished 由工作线程 emit,经队列连接回到主线程
        # 刷新——旧代码在工作线程 finally 里直接 self.refresh() 跨线程碰
        # 控件,触发 QBasicTimer::start 错误并卡死
        self.run_finished.connect(self._refresh_after_run)
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
            row.ref_spectrum_requested.connect(self._on_pick_reference)
            row.clear_ref_requested.connect(self._on_clear_reference)
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
        # 0.2.199-补29as:SMILE 优化为可选步骤——未做/过期不占「下一步」,
        # 单独显示「可选做」;「下一步」永远指向真实必做步骤
        outdated_next = next(
            (
                sid
                for sid, st in statuses.items()
                if sid != "smile" and st == "OUTDATED"
            ),
            None,
        )
        next_step = next(
            (
                sid
                for sid, st in statuses.items()
                if sid != "smile" and st == "READY"
            ),
            None,
        )
        smile_status = statuses.get("smile")
        if smile_status == "OUTDATED":
            optional_text = "SMILE 优化(重新运行)"
        elif smile_status == "READY":
            optional_text = "SMILE 优化"
        else:
            optional_text = ""
        if outdated_next:
            text = f"下一步: 重新运行 {STEP_LABEL[outdated_next]}"
        elif next_step:
            text = f"下一步: {STEP_LABEL[next_step]}"
        else:
            text = (
                "全部步骤已完成"
                if any(st == "SUCCESS" for st in statuses.values())
                else "等待导入样品数据"
            )
        if optional_text:
            text = f"可选做: {optional_text}　|　{text}"
        self.next_label.setText(text)
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
            # 0.2.199-补29dl(用户):峰挑选无人工脚本(自动检测),不再显示人工按钮
            self._rows[step_id].manual_button.setVisible(
                step_id not in ("smile", "peaks") and status != "LOCKED"
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
        """「直接维范围」按钮:弹输入对话框,按数据保存直接维范围覆盖。"""
        if step_id != "spectrum":
            return
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if not (exp_id and data_id):
            return
        key = (exp_id, data_id)
        current = self._final_ext.get(key, ("", "", True))
        dialog = QDialog(self)
        dialog.setWindowTitle("直接维范围")
        form = QFormLayout(dialog)
        lo_edit = QLineEdit(str(current[0]) if current[0] else "")
        hi_edit = QLineEdit(str(current[1]) if current[1] else "")
        lo_edit.setPlaceholderText("10.5")
        hi_edit.setPlaceholderText("6.5")
        form.addRow("高场端 ppm (EXT -x1):", lo_edit)
        form.addRow("低场端 ppm (EXT -xn):", hi_edit)
        tip = QLabel(
            "留空=使用默认(10.5-6.5);窗口外峰不会出现在终谱中,\n"
            "直接维线性相位 p1 会按窗口宽度自动重归一化。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666;")
        form.addRow(tip)
        apply_check = QCheckBox("应用此范围到优化过程")
        apply_check.setChecked(bool(current[2]))
        form.addRow(apply_check)
        apply_tip = QLabel(
            "默认开启:优化过程(首遍重构/相位搜索与基线/填零/窗函数评估)\n"
            "使用指定范围,与终谱一致,且可降低 SMILE 内存;\n"
            "若优化效果不佳可尝试关闭,用默认 6.5-10.5 大范围优化。"
        )
        apply_tip.setWordWrap(True)
        apply_tip.setStyleSheet("color: #666;")
        form.addRow(apply_tip)
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
        apply_opt = apply_check.isChecked()
        if not lo and not hi:
            self._final_ext.pop(key, None)
            self.log_message.emit(
                f"直接维范围: {data_id} 已清除,恢复默认窗口"
            )
        else:
            self._final_ext[key] = (lo, hi, apply_opt)
            scope = "含优化" if apply_opt else "仅终跑"
            self.log_message.emit(
                f"直接维范围: {data_id} 已设为 "
                f"{lo or '默认'}-{hi or '默认'} ppm({scope})"
            )
        self._update_ext_button()

    def _update_ext_button(self) -> None:
        """按当前数据的直接维范围覆盖更新按钮文案与提示词。"""
        row = self._rows.get("spectrum")
        if row is None:
            return
        over = self._final_ext.get((self._current_exp_id, self._current_data_id))
        if over and (over[0] or over[1]):
            lo = over[0] or "默认"
            hi = over[1] or "默认"
            scope = "含优化" if over[2] else "仅终跑"
            first_note = (
                "首遍重构/相位搜索与优化评估同窗口"
                if over[2]
                else "首遍重构/相位搜索保持原窗口"
            )
            row.set_ext_override(f"直接维范围 {lo}/{hi} · {scope}")
            row.ext_range_button.setToolTip(
                f"直接维窗口: {lo}-{hi} ppm(EXT -x1/-xn,{scope})\n"
                f"{first_note};窗口外峰不进入终谱,\n"
                "p1 按窗口宽度自动重归一化;切换数据后显示各自设置"
            )
        else:
            row.set_ext_override("直接维范围")
            row.ext_range_button.setToolTip(
                "指定直接维提取窗口(EXT -x1/-xn);默认开启「应用此范围到\n"
                "优化过程」,可关闭改用默认 6.5-10.5 大范围优化;\n"
                "未设置时用默认(10.5-6.5)"
            )

    def _spectrum_ext_params(self, data_id: str) -> dict | None:
        """当前实验某数据的直接维范围 → generate_spectrum params(无则 None)。

        apply_ext_to_opt:默认开启,范围同时用于优化过程(首遍重构/相位搜索
        与基线/填零/窗函数评估);关闭时优化用默认 6.5-10.5 大范围,仅终跑
        用该范围。
        """
        over = self._final_ext.get((self._current_exp_id, data_id))
        if not over:
            return None
        ext_params: dict[str, str] = {"apply_ext_to_opt": "1" if over[2] else "0"}
        if over[0]:
            ext_params["final_ext_lo"] = over[0]
        if over[1]:
            ext_params["final_ext_hi"] = over[1]
        return ext_params

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
        # 0.2.199-补29as:可选 SMILE 优化不占「下一步」气泡
        next_step = next(
            (
                sid
                for sid, st in statuses.items()
                if sid != "smile" and st in ("READY", "OUTDATED")
            ),
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

    @staticmethod
    def _run_log_scope(
        exp_id: str, data_id: str, group_id: str = ""
    ) -> str:
        """运行日志作用域键:组内共用一个组日志,单个数据各自独立。"""
        from gui.log_panel import LogPanel

        return LogPanel.scope_key(
            "group" if group_id else "data",
            exp_id,
            data_id,
            group_id,
        )

    def _refresh_after_run(self) -> None:
        """运行结束后的面板刷新(经队列信号,主线程执行)。

        工作线程 finally 只 emit run_finished,不再直接调用 refresh();
        SyncThread 测试下 emit 为直连,行为不变。
        """
        self._run_active = False
        self.refresh()

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

    def _cached_spectrum_report(
        self, params: dict, spectrum_path: str
    ) -> str:
        """生成谱图参数报告(0.2.199-补12):按谱文件指纹缓存(内存+磁盘记录)。

        spectrum_quality_report_lines 会读取整张 ft3 并全谱评估质量,每次
        刷新都在主线程执行会很卡;谱文件未变化时直接读记录,不重读谱。
        记录文件:{spectrum_path}.quality.json,指纹 = mtime_ns+size+参数。
        """
        params_fp = hashlib.sha256(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        try:
            p = Path(spectrum_path)
            if p.is_file():
                st = p.stat()
                fp = f"{st.st_mtime_ns}|{st.st_size}"
                rec = Path(f"{spectrum_path}.quality.json")
            else:
                fp = "missing"
                rec = None
        except OSError:
            fp = "err"
            rec = None
        key = f"{spectrum_path}|{fp}|{params_fp}"
        cached = self._spectrum_report_cache.get(key)
        if cached is not None:
            return cached
        if rec is not None and rec.is_file():
            try:
                data = json.loads(rec.read_text(encoding="utf-8"))
                if (
                    data.get("fp") == fp
                    and data.get("params_fp") == params_fp
                    and isinstance(data.get("text"), str)
                ):
                    self._spectrum_report_cache[key] = data["text"]
                    return data["text"]
            except (OSError, ValueError):
                pass
        # 0.2.199-补29e:报告只显示上次生成时记录的内容(工作线程写入
        # {谱}.quality.json);无记录不现场生成(读整张谱既卡又非真实处理
        # 报告),提示重新运行「生成谱图」
        return "（无报告记录;重新运行「生成谱图」后生成报告）"

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
                        self._cached_spectrum_report(
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

    # ------------------------------------------------------------------
    # 参考谱约束(0.2.199-补29dl,用户)
    # ------------------------------------------------------------------
    def _reference_candidates(self) -> list[tuple[str, str, str]]:
        """项目内已有峰表的数据列表(显示名, exp_id, data_id)。"""
        out: list[tuple[str, str, str]] = []
        if self.manager is None or self.manager.project is None:
            return out
        for exp in self.manager.project.experiments:
            for entry in getattr(exp, "data", []):
                data_id = str(getattr(entry, "id", "") or "")
                if not data_id:
                    continue
                try:
                    peaks_dir = self.manager.data_dir(exp.id, data_id, "peaks")
                except Exception:  # noqa: BLE001 - 单数据异常跳过
                    continue
                has = any(
                    (peaks_dir / f"{exp.id}-{data_id}{suffix}").is_file()
                    for suffix in (".list", ".csv")
                )
                if not has:
                    continue
                title = str(getattr(entry, "title", "") or "")
                name = f"{exp.id}/{data_id}"
                if title:
                    name += f" ({title})"
                out.append((name, exp.id, data_id))
        return out

    def _ref_nuclei_from_spectrum(self, spectrum_path: Path) -> list[str] | None:
        """从参考谱头部取每轴核名(F 序);失败/核不可知返回 None。"""
        symbols = {
            "H": "1H", "N": "15N", "C": "13C",
            "F": "19F", "P": "31P", "D": "2H",
        }
        full = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}
        try:
            if spectrum_path.suffix.lower() == ".ft3":
                from viewer.spectrum import Spectrum3D

                spec = Spectrum3D.load_from_ft3(spectrum_path, lazy=True)
            else:
                from viewer.spectrum import Spectrum

                spec = Spectrum.load_from_ft2(spectrum_path)
        except Exception:  # noqa: BLE001 - 谱读取失败回退 None
            return None
        nuclei: list[str] = []
        for axis in getattr(spec, "axes", []):
            label = str(getattr(axis, "label", "") or "").strip()
            if len(label) > 1 and label[-1:].lower() in ("x", "y", "z"):
                label = label[:-1]
            nuc = symbols.get(label, label)
            nuclei.append(nuc if nuc in full else "")
        return nuclei if nuclei and all(nuclei) else None

    def _load_reference(self, exp_id: str, data_id: str) -> dict | None:
        """加载参考数据峰表 + 核名;失败返回 None。"""
        from core.peaks.peak_table import load_peaks
        from gui.peaks_io import import_peaks_poky

        try:
            peaks_dir = self.manager.data_dir(exp_id, data_id, "peaks")
        except Exception:  # noqa: BLE001
            return None
        path: Path | None = None
        for suffix in (".list", ".csv"):
            cand = peaks_dir / f"{exp_id}-{data_id}{suffix}"
            if cand.is_file():
                path = cand
                break
        if path is None:
            return None
        data_entry = self.manager.data(exp_id, data_id)
        spectrum_path = str(getattr(data_entry, "spectrum_path", "") or "")
        nuclei = (
            self._ref_nuclei_from_spectrum(Path(spectrum_path))
            if spectrum_path and Path(spectrum_path).is_file()
            else None
        )
        if path.suffix.lower() == ".list":
            peaks = import_peaks_poky(path, nuclei=nuclei)
        else:
            peaks = load_peaks(path)
        if not peaks:
            return None
        # 无谱可用时 2D 行按键名推断核(HSQC 参考常见场景)
        if nuclei is None and "N_shift" in peaks[0] and "H_shift" in peaks[0]:
            nuclei = ["15N", "1H"]
        title = str(getattr(data_entry, "title", "") or "")
        label = f"{exp_id}/{data_id}"
        if title:
            label += f" ({title})"
        return {
            "label": label,
            "peaks": peaks,
            "nuclei": nuclei,
            "path": str(path),
        }

    def _on_pick_reference(self, step_id: str) -> None:
        """「参考谱」按钮:选择任意已有峰表的数据作为选峰参考。"""
        if self.manager is None or self.manager.project is None:
            return
        candidates = self._reference_candidates()
        if not candidates:
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self, "参考谱", "项目中没有已有峰表的数据,请先对某数据选峰"
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("选择参考谱(已有峰表)")
        lay = QVBoxLayout(dialog)
        tip = QLabel("选择参考数据:选峰时只保留与参考峰表匹配的峰(按核匹配)")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        lst = QListWidget()
        for name, _exp, _did in candidates:
            lst.addItem(name)
        lay.addWidget(lst)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or lst.currentRow() < 0:
            return
        _name, ref_exp, ref_data = candidates[lst.currentRow()]
        info = self._load_reference(ref_exp, ref_data)
        if info is None:
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self, "参考谱", f"无法加载参考峰表: {ref_exp}/{ref_data}"
            )
            return
        if info.get("nuclei") is None and any(
            "F1_shift" in p for p in info["peaks"]
        ):
            from gui.dialogs import InfoDialog

            InfoDialog.show_info(
                self,
                "参考谱",
                "参考峰表为 3D 且无法确定核名(未加载参考谱),约束将不生效",
            )
            return
        self._ref_info = info
        self._rows["peaks"].set_ref_text(
            f"参考: {info['label']} ({len(info['peaks'])} 峰)"
        )

    def _on_clear_reference(self, step_id: str) -> None:
        """清除参考谱约束。"""
        self._ref_info = None
        row = self._rows.get("peaks")
        if row is not None:
            row.clear_ref_display()

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
        if self._run_active:
            self.log_message.emit(
                "已有任务正在运行,请等待完成后再试"
            )
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
                    self.log_scoped.emit(
                        f"{STEP_LABEL.get(step_id, step_id)}: "
                        "该实验类型还没有样品数据,请先导入样品数据",
                        self._run_log_scope(self._current_exp_id, ""),
                    )
                    return
                data_node = next(
                    (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
                    nodes[0],
                )
                exp_id = self._current_exp_id
                target_data_id = getattr(data_node, "id", exp_id)
                # 0.2.199-补5:处理开始,左侧树该数据显示「运行中」
                self.run_started.emit(exp_id, target_data_id)
                # 批量组:统一委托新引擎(controller.run_group_batch ->
                # workflow.batch.run_batch);0.2.164-补1 删除旧内联逐数据循环
                group = (
                    self.manager.group_of_data(exp_id, target_data_id)
                    if self.manager is not None and self.manager.project is not None
                    else None
                )
                # 0.2.199-补29d:运行日志按目标数据/组作用域落地,切换选中
                # 不再串——旧代码 emit log_message 落当前选中作用域
                run_scope = self._run_log_scope(
                    exp_id, target_data_id, group.id if group is not None else ""
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
                    if step_id == "peaks":
                        kwargs["sigma_multiplier"] = self._rows[
                            step_id
                        ].get_threshold()
                        if self._ref_info:
                            kwargs["ref_peaks"] = self._ref_info["peaks"]
                            kwargs["ref_nuclei"] = self._ref_info.get("nuclei")
                            kwargs["tolerance_ppm"] = None
                    if "progress" in inspect.signature(method).parameters:
                        kwargs["progress"] = lambda msg: self.log_scoped.emit(
                            f"{step_label}: {msg}", run_scope
                        )
                    result = method(data_node, **kwargs)
                    if (
                        step_id == "peaks"
                        and isinstance(result, dict)
                        and result.get("status") == "success"
                    ):
                        # 0.2.199-补29ar:选峰完成立即展示谱图并显示峰
                        self.show_spectrum_requested.emit(step_id)
                    message = result if isinstance(result, str) else str(result)
                    self.log_scoped.emit(f"完成 {step_label}: {message}", run_scope)
                except Exception as exc:  # noqa: BLE001 - 单数据失败
                    self.log_scoped.emit(
                        f"失败 {step_label}: {type(exc).__name__}: {exc}",
                        run_scope,
                    )
                    if "无法处理该谱" in str(exc):
                        self.memory_guard_requested.emit(str(exc))
            except Exception as exc:  # noqa: BLE001 - 错误统一回传 UI
                self.log_scoped.emit(
                    f"失败 {STEP_LABEL.get(step_id, step_id)}: {exc}",
                    run_scope,
                )
                if "无法处理该谱" in str(exc):
                    self.memory_guard_requested.emit(str(exc))
            finally:
                self._run_active = False
                self.run_finished.emit()

        import threading

        # 0.2.199-补6:新任务开始前清除上次的取消标志
        from backend.runtime import clear_cancel

        clear_cancel()
        self._run_active = True
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
        group_scope = self._run_log_scope(exp_id, "", group_id)
        self.log_scoped.emit(
            f"数据组 {group_id}: 对 {group_count} 个数据执行 {step_label}",
            group_scope,
        )
        if step_id == "smile":
            self.log_scoped.emit(
                "SMILE 优化不支持批量组,请在单个数据上执行", group_scope
            )
            return
        # 0.2.199-补5:组内各数据开始处理,左侧树显示「运行中」
        for data_id in self.manager.group_data_ids(exp_id, group_id):
            self.run_started.emit(exp_id, data_id)
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
                progress=lambda msg: self.log_scoped.emit(
                    f"{step_label}: {msg}", group_scope
                ),
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - 引擎级失败
            self.log_scoped.emit(
                f"失败 {step_label}: {type(exc).__name__}: {exc}",
                group_scope,
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
        if self._run_active:
            self.log_message.emit(
                "已有任务正在运行,请等待完成后再试"
            )
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
                    self.log_scoped.emit(
                        "该实验类型还没有样品数据", self._run_log_scope(exp_id, "")
                    )
                    return
                data_node = next(
                    (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
                    nodes[0],
                )
                data_id = getattr(data_node, "id", exp_id)
                run_scope = self._run_log_scope(exp_id, data_id)
                # 0.2.199-补5:处理开始,左侧树该数据显示「运行中」
                self.run_started.emit(exp_id, data_id)
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
                    self.log_scoped.emit(
                        "没有可复用的终跑脚本,请先执行「重新优化」生成",
                        run_scope,
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
                    self.log_scoped.emit(
                        f"直接维范围已更新: {lo or '默认'}-{hi or '默认'} ppm → {script_path.name}",
                        run_scope,
                    )
                # 0.2.199-补29h:修改后运行前检测常见错误(worker 内,日志提示)
                from workflow.script_check import check_script

                for w in check_script(content, script_path.name):
                    self.log_scoped.emit(f"⚠ 脚本检查: {w}", run_scope)
                # 运行修改后的终跑脚本,谱图归位;实时转发脚本输出
                result = self.controller.run_manual_spectrum(
                    data_node,
                    {script_path.name: content},
                    exp_id=exp_id,
                    data_id=data_id,
                    progress=lambda line: self.log_scoped.emit(
                        f"[{script_path.name}] {line}", run_scope
                    ),
                )
                self.log_scoped.emit(
                    f"重新运行终脚本完成 {data_id}: {result}", run_scope
                )
            except Exception as exc:  # noqa: BLE001 - 错误统一回传 UI
                self.log_scoped.emit(
                    f"重新运行终脚本失败: {type(exc).__name__}: {exc}",
                    run_scope,
                )
            finally:
                self._run_active = False
                self.run_finished.emit()

        import threading

        self._run_active = True
        threading.Thread(target=worker, daemon=True).start()
