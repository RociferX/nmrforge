"""中间 Pipeline 面板:围绕当前数据/实验显示处理步骤与状态。

六步流程(契约 v1.2 / G2B-002,含可选 SMILE 优化):
导入数据 → 生成 FID → 生成谱图(含 SMILE 重构)→ [SMILE 优化,可选] →
峰挑选 → 分析。

- 步骤状态依据前置依赖与产物文件推断(LOCKED/READY/RUNNING/SUCCESS/FAILED);
- READY 步骤提供「运行」按钮,经 ProcessingController 对应方法执行;
- LOCKED 步骤 tooltip 说明缺哪个前置步骤;
- GUI 层不直接触碰 Backend:唯一出口是 ProcessingController。
"""

from __future__ import annotations

from pathlib import Path

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
from gui.pipeline_state import (
    input_fingerprint,
    load_pipeline_state,
    raw_fingerprint,
    script_fingerprint,
)
from gui.processing import ProcessingController

# 步骤定义:id / 名称 / 描述 / 前置步骤 id 列表
PIPELINE_STEPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("import", "导入数据", "读 Bruker 参数并复制到项目(raw),不触发处理", ()),
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
        if path.is_file():
            artifacts['fid'] = path
    if artifacts['fid'] is None:
        proc = manager.data_dir(exp_id, data_id, 'process')
        try:
            fids = sorted(proc.glob('*.fid'))
        except OSError:
            fids = []
        if fids:
            artifacts['fid'] = fids[0]
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
    peaks = manager.data_dir(exp_id, data_id, 'peaks') / f'{exp_id}-{data_id}.csv'
    if peaks.is_file():
        artifacts['peaks'] = peaks
    else:
        flat = manager.dir_path('peaks') / f'{exp_id}.csv'
        if flat.is_file():
            artifacts['peaks'] = flat
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


def _node_step_statuses(
    manager: ProjectManager, exp_id: str, node
) -> dict[str, str]:
    """单数据节点五步状态:产物 + 指纹校验(OUTDATED)+ 前置依赖。"""
    data_id = getattr(node, 'id', exp_id)
    artifacts = _node_artifacts(manager, exp_id, data_id)
    state = load_pipeline_state(manager, exp_id, data_id)
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
            if entry and entry.get('input_hash'):
                current = raw_fingerprint(manager, exp_id, data_id)
                outdated = current is not None and current != entry['input_hash']
        elif step_id == 'smile':
            # 可选步骤:运行过即完成(产物复用谱图,指纹校验输入变化)
            entry = state['steps'].get('smile')
            done = entry is not None
            if done:
                current = input_fingerprint(manager, exp_id, data_id, 'smile')
                if (
                    current is not None
                    and entry.get('input_hash')
                    and current != entry['input_hash']
                ):
                    outdated = True
        else:
            done = artifact is not None
            if done:
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
        if done and not outdated:
            statuses[step_id] = 'SUCCESS'
        elif done:
            statuses[step_id] = 'OUTDATED'
        elif all(statuses.get(dep) in ('SUCCESS', 'OUTDATED') for dep in deps):
            statuses[step_id] = 'READY'
        else:
            statuses[step_id] = 'LOCKED'
    # 上游 OUTDATED 传播:下游即使指纹匹配也视为过期
    for step_id, _, _, deps in PIPELINE_STEPS:
        if statuses.get(step_id) == 'SUCCESS' and any(
            statuses.get(dep) == 'OUTDATED' for dep in deps
        ):
            statuses[step_id] = 'OUTDATED'
    return statuses


def compute_step_statuses(manager: ProjectManager, exp_id: str) -> dict[str, str]:
    """按产物文件、指纹校验与前置依赖推断各步骤状态(支持 OUTDATED)。"""
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


def _outdated_reasons(
    statuses: dict[str, str], manager: ProjectManager, exp_id: str
) -> dict[str, str]:
    """为 OUTDATED 步骤生成原因(输入/脚本变化、上游过期、产物落后)。"""
    reasons: dict[str, str] = {}
    nodes = _data_nodes(manager, exp_id)
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


class PipelineStepRow(QWidget):
    """单个步骤行:状态图标 + 名称 + 描述 + 运行/人工入口按钮。"""

    run_requested = pyqtSignal(str)  # step_id
    manual_requested = pyqtSignal(str)  # step_id:打开人工参数表格/脚本编辑器
    report_requested = pyqtSignal(str)  # step_id:分析完成后打开报告页

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
        self.report_button = QPushButton("报告")
        self.report_button.setToolTip("查看当前数据的报告产物(report/ 目录)")
        self.report_button.setVisible(False)
        self.report_button.clicked.connect(
            lambda: self.report_requested.emit(self.step_id)
        )
        layout.addWidget(self.report_button)
        self.manual_button = QPushButton("人工")
        self.manual_button.setToolTip("人工参数表格 / 脚本编辑器(骨架)")
        self.manual_button.setVisible(False)
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
    import_data_requested = pyqtSignal(str)  # exp_id:在当前实验下导入数据

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
        self.import_button = QPushButton("导入数据...")
        self.import_button.setVisible(False)
        self.import_button.clicked.connect(
            lambda: self.import_data_requested.emit(self._current_exp_id)
        )
        layout.addWidget(self.import_button)

        steps_box = QVBoxLayout()
        for step_id, label, description, _deps in PIPELINE_STEPS:
            row = PipelineStepRow(step_id, label, description)
            row.run_requested.connect(self._on_run_requested)
            row.manual_requested.connect(self.manual_open_requested.emit)
            row.report_requested.connect(self.report_requested.emit)
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
        """兼容入口:按实验设置上下文(data_id 缺省回退首个数据)。"""
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
            self.import_button.setVisible(True)  # 可直接在当前实验导入数据
            return
        self.import_button.setVisible(False)
        exp = project.experiment(self._current_exp_id)
        exp_title = exp.title if exp is not None else self._current_exp_id
        self.context_label.setText(
            f"{project.name} / {exp_title} ({self._current_exp_id})"
        )
        statuses = compute_step_statuses(self.manager, self._current_exp_id)
        outdated_next = next(
            (sid for sid, st in statuses.items() if st == "OUTDATED"), None
        )
        next_step = next((sid for sid, st in statuses.items() if st == "READY"), None)
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
                else "等待导入数据"
            )
        reasons = _lock_reasons(statuses)
        outdated = _outdated_reasons(
            statuses, self.manager, self._current_exp_id
        )
        for step_id, status in statuses.items():
            reason = reasons.get(step_id, "") or outdated.get(step_id, "")
            self._rows[step_id].set_status(status, reason)
            # 导入数据为自动化步骤,无人工入口;其余处理步骤保留人工
            self._rows[step_id].manual_button.setVisible(
                step_id not in ("import", "smile")
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
                        f"{STEP_LABEL.get(step_id, step_id)}: 该实验还没有数据,请先导入数据"
                    )
                    return
                data_node = next(
                    (n for n in nodes if getattr(n, "id", "") == self._current_data_id),
                    nodes[0],
                )
                exp_id = self._current_exp_id
                data_id = getattr(data_node, "id", exp_id)
                if method_name == "import_data":
                    source = getattr(data_node, "source", "") or ""
                    result = method(entry, source)
                else:
                    result = method(data_node, exp_id=exp_id, data_id=data_id)
                message = result if isinstance(result, str) else str(result)
                self.log_message.emit(
                    f"完成 {STEP_LABEL.get(step_id, step_id)}: {message}"
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
