"""数据组中间页面:对组内所有数据批量处理(0.2.163)。

两种模式:
- 按参考数据处理整组:选择实验内已运行过谱图的数据,取其最近成功谱图
  运行的有效参数应用到组内每个数据(可截止到 pipeline 某一步);
- 依次优化组内数据:对组内每个数据依次执行完整自动处理流程
  (fid → spectrum[统一自动优化] → peaks;analysis 已隐藏,2026-09-03)。

实际执行由 main_window 起后台线程调 ProcessingController.run_group_batch
(workflow.batch.run_batch),本面板只负责参数选择与信号发出。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# 截止步骤选项:值=BATCH_STEPS 子集
STOP_STEP_OPTIONS: list[tuple[str, str]] = [
    ("fid", "生成 FID"),
    ("spectrum", "生成谱图"),
    ("peaks", "峰挑选"),
    # ("analysis", "分析"),  # hidden from GUI (2026-09-03)
]


class GroupBatchPanel(QWidget):
    """数据组批量处理面板(选中组节点时显示)。"""

    log_message = pyqtSignal(str)
    run_group_batch_requested = pyqtSignal(str, str, list, str, dict)
    # (exp_id, group_id, steps, reference_data_id)
    summary_requested = pyqtSignal(dict)  # 批量汇总(信息 + 逐数据结果)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = None
        self._exp_id = ""
        self._group_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("数据组批量处理")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)

        self.context_label = QLabel("")
        self.context_label.setWordWrap(True)
        self.context_label.setStyleSheet("color: #555;")
        layout.addWidget(self.context_label)

        self.member_label = QLabel("")
        self.member_label.setWordWrap(True)
        self.member_label.setStyleSheet("color: #555;")
        layout.addWidget(self.member_label)
        # 0.2.199-补29gw:组内数据注释(字段×数据网格),置于"按参考数据处理"上方
        self.notes_label = QLabel("组内数据注释")
        self.notes_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        self.notes_label.setVisible(False)
        layout.addWidget(self.notes_label)
        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.notes_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.notes_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.notes_scroll.setStyleSheet(
            "background: #1e1e1e; color: #ffffff; border: 1px solid #3c3c3c;"
        )
        self.notes_scroll.setMaximumHeight(200)
        self.notes_scroll.setVisible(False)
        layout.addWidget(self.notes_scroll)
        layout.addSpacing(6)

        # ---- 模式 A:按参考数据处理整组 ----
        ref_box = QGroupBox("按参考数据处理整组")
        ref_layout = QVBoxLayout(ref_box)
        ref_hint = QLabel(
            "选择本实验下已经运行过谱图的数据,程序把该数据的处理参数"
            "应用到组内每个数据;可只处理到指定步骤(如仅生成 FID)。"
        )
        ref_hint.setWordWrap(True)
        ref_hint.setStyleSheet("color: #666;")
        ref_layout.addWidget(ref_hint)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("参考数据:"))
        self.reference_combo = QComboBox()
        self.reference_combo.setMinimumWidth(240)
        row1.addWidget(self.reference_combo, 1)
        ref_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("处理到:"))
        self.stop_combo = QComboBox()
        for _value, label in STOP_STEP_OPTIONS:
            self.stop_combo.addItem(label)
        row2.addWidget(self.stop_combo)
        row2.addStretch(1)
        ref_layout.addLayout(row2)

        self.run_ref_button = QPushButton("按参考数据处理整组")
        self.run_ref_button.setEnabled(False)
        self.run_ref_button.clicked.connect(self._on_run_reference)
        ref_layout.addWidget(self.run_ref_button)
        layout.addWidget(ref_box)

        # ---- 模式 B:依次优化组内数据 ----
        opt_box = QGroupBox("依次优化组内数据")
        opt_layout = QVBoxLayout(opt_box)
        opt_hint = QLabel(
            "对组内每个数据依次执行自动处理流程(FID → 谱图 → 峰挑选),"
            "单数据失败不中断整组。"
        )
        opt_hint.setWordWrap(True)
        opt_hint.setStyleSheet("color: #666;")
        opt_layout.addWidget(opt_hint)
        # 处理到某步骤
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("处理到:"))
        self.opt_stop_combo = QComboBox()
        for _value, _label in STOP_STEP_OPTIONS:
            self.opt_stop_combo.addItem(_label)
        opt_row.addWidget(self.opt_stop_combo)
        opt_row.addStretch(1)
        opt_layout.addLayout(opt_row)
        # 直接维范围设置(处理到 spectrum/peaks 时显示)
        self.ext_group = QGroupBox("直接维范围设置")
        ext_row = QHBoxLayout(self.ext_group)
        self.ext_enable = QCheckBox("指定范围")
        ext_row.addWidget(self.ext_enable)
        ext_row.addWidget(QLabel("下限(ppm):"))
        self.ext_lo_edit = QLineEdit("10.5")
        ext_row.addWidget(self.ext_lo_edit)
        ext_row.addWidget(QLabel("上限(ppm):"))
        self.ext_hi_edit = QLineEdit("6.5")
        ext_row.addWidget(self.ext_hi_edit)
        opt_layout.addWidget(self.ext_group)
        # 峰挑选阈值设置(处理到 peaks 时显示)
        self.thresh_group = QGroupBox("峰挑选阈值设置")
        thr_row = QHBoxLayout(self.thresh_group)
        thr_row.addWidget(QLabel("噪声阈值(σ):"))
        self.thresh_edit = QLineEdit("")
        self.thresh_edit.setPlaceholderText("留空用默认")
        thr_row.addWidget(self.thresh_edit)
        opt_layout.addWidget(self.thresh_group)
        self.opt_stop_combo.currentIndexChanged.connect(
            self._on_opt_stop_changed
        )
        # 0.2.199-补29gp:初始按默认截止步骤(生成 FID)隐藏范围/阈值设置
        self._on_opt_stop_changed()
        self.run_optimize_button = QPushButton("依次优化组内数据")
        self.run_optimize_button.setEnabled(False)
        self.run_optimize_button.clicked.connect(self._on_run_optimize)
        opt_layout.addWidget(self.run_optimize_button)
        layout.addWidget(opt_box)

        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("color: #2c3e50;")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    def set_context(
        self, manager, exp_id: str, group_id: str
    ) -> None:
        """绑定项目上下文并刷新组信息/参考数据下拉。"""
        self._manager = manager
        self._exp_id = exp_id
        self._group_id = group_id
        self._refresh()
        self._refresh_notes()

    def _refresh_notes(self) -> None:
        """组内数据注释:注记字段为行(第一列字段名),每个数据为一列。"""
        from gui.notes import DATA_FIELDS, data_note_fields

        manager = self._manager
        exp_id, group_id = self._exp_id, self._group_id
        self.notes_label.setVisible(False)
        self.notes_scroll.setVisible(False)
        if manager is None or manager.project is None:
            return
        exp = manager.project.experiment(exp_id)
        group = manager.group(exp_id, group_id) if exp is not None else None
        if group is None:
            return
        member_ids = (group.data_ids or [])
        if not member_ids:
            return
        cols_data: list[tuple[str, str, dict]] = []
        for data_id in member_ids:
            d = next((x for x in exp.data if x.id == data_id), None)
            label_text = (d.title or f"样品数据 {data_id}") if d else f"样品数据 {data_id}"
            cols_data.append(
                (data_id, label_text, data_note_fields(manager.project, exp_id, data_id))
            )
        row_keys: list[str] = [k for k, _ in DATA_FIELDS]
        display = {k: v for k, v in DATA_FIELDS}
        for _di, _lb, fields in cols_data:
            for k in fields:
                if k not in row_keys:
                    row_keys.append(k)
                    display[k] = k
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setSpacing(2)
        head0 = QLabel("数据")
        head0.setStyleSheet(
            "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
        )
        grid.addWidget(head0, 0, 0)
        for ci, (_data_id, label_text, _f) in enumerate(cols_data, start=1):
            head = QLabel(f"◆ {label_text}")
            head.setWordWrap(True)
            head.setStyleSheet(
                "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
            )
            grid.addWidget(head, 0, ci)
        for ri, key in enumerate(row_keys, start=1):
            fname = QLabel(display.get(key, key))
            fname.setStyleSheet(
                "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
            )
            grid.addWidget(fname, ri, 0)
            for ci, (_di, _lb, fields) in enumerate(cols_data, start=1):
                val = fields.get(key)
                val_lb = QLabel(str(val) if val not in (None, "") else "—")
                val_lb.setWordWrap(True)
                val_lb.setFixedWidth(190)
                val_lb.setStyleSheet(
                    "color: #ffffff; border: none; background: transparent;"
                )
                grid.addWidget(val_lb, ri, ci)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(len(cols_data) + 1, 0)
        self.notes_scroll.setWidget(container)
        self.notes_label.setVisible(True)
        self.notes_scroll.setVisible(True)

    def _refresh(self) -> None:
        manager = self._manager
        exp_id, group_id = self._exp_id, self._group_id
        if manager is None or manager.project is None:
            return
        exp = manager.project.experiment(exp_id)
        group = manager.group(exp_id, group_id) if exp is not None else None
        if exp is None or group is None:
            self.context_label.setText(f"数据组 {group_id}(不存在)")
            self.run_ref_button.setEnabled(False)
            self.run_optimize_button.setEnabled(False)
            return
        title = group.title or f"数据组 {group_id}"
        self.context_label.setText(f"实验: {exp.title or exp_id}  ·  组: {title}")
        members = [
            d
            for d in exp.data
            if d.id in (group.data_ids or [])
        ]
        names = "、".join(
            f"{d.title or '样品数据 ' + d.id}" for d in members
        )
        self.member_label.setText(
            f"组内数据({len(members)} 个): {names or '(空组)'}"
        )
        self.run_optimize_button.setEnabled(bool(members))
        self._refresh_reference_combo(exp, members)

    def _refresh_reference_combo(self, exp, members) -> None:
        """参考数据下拉:实验内已生成谱图的数据(含组内成员)。"""
        self.reference_combo.clear()
        candidates: list[tuple[str, str]] = []
        for data in exp.data:
            if not data.spectrum_path:
                continue
            label = data.title or f"样品数据 {data.id}"
            candidates.append((data.id, f"{label} ({data.id})"))
        if not candidates:
            self.run_ref_button.setEnabled(False)
            self.reference_combo.addItem("(无已生成谱图的数据)")
            return
        for data_id, label in candidates:
            self.reference_combo.addItem(label, data_id)
        self.run_ref_button.setEnabled(True)

    def _stop_steps(self) -> list[str]:
        """截止步骤对应的 BATCH_STEPS 前缀子集(fid 起)。"""
        value = STOP_STEP_OPTIONS[self.stop_combo.currentIndex()][0]
        order = ("fid", "spectrum", "peaks")
        return list(order[: order.index(value) + 1])

    def _on_run_reference(self) -> None:
        ref_id = str(self.reference_combo.currentData() or "")
        if not ref_id:
            self.log_message.emit("请先选择参考数据")
            return
        steps = self._stop_steps()
        self.run_group_batch_requested.emit(
            self._exp_id, self._group_id, steps, ref_id, {}
        )

    def _opt_steps(self) -> list[str]:
        """依次优化模式的截止步骤(fid 起前缀)。"""
        value = STOP_STEP_OPTIONS[self.opt_stop_combo.currentIndex()][0]
        order = ("fid", "spectrum", "peaks")
        return list(order[: order.index(value) + 1])

    def _on_opt_stop_changed(self) -> None:
        """按截止步骤显隐直接维范围/峰阈值设置。"""
        steps = self._opt_steps()
        self.ext_group.setVisible("spectrum" in steps)
        self.thresh_group.setVisible("peaks" in steps)

    def _collect_params(self) -> dict:
        """收集当前可见设置到处理参数。"""
        params: dict = {}
        steps = self._opt_steps()
        if "spectrum" in steps and self.ext_enable.isChecked():
            lo = self.ext_lo_edit.text().strip()
            hi = self.ext_hi_edit.text().strip()
            if lo:
                params["ext_lo"] = lo
            if hi:
                params["ext_hi"] = hi
        if "peaks" in steps and self.thresh_edit.text().strip():
            params["sigma_multiplier"] = self.thresh_edit.text().strip()
        return params

    def _on_run_optimize(self) -> None:
        steps = self._opt_steps()
        self.run_group_batch_requested.emit(
            self._exp_id,
            self._group_id,
            steps,
            "",
            self._collect_params(),
        )

    def set_progress(self, text: str) -> None:
        """批量进度提示(主线程更新)。"""
        self.progress_label.setText(text)
        self.progress_label.setVisible(bool(text))
