"""GUI 对话框组件:信息/确认/导入实验类型/项目表单。

不使用 QMessageBox(在 Windows + Qt6 下从菜单触发模态 QMessageBox 会打印
"This plugin supports grabbing the mouse only for popup windows"),统一用普通 QDialog。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.notes import (
    DIMENSION_OPTIONS,
    NUCLEI_OPTIONS,
    experiment_type_options,
    note_fields,
)


class InfoDialog(QDialog):
    """带确定按钮的信息对话框(替代 QMessageBox.information/critical/about)。"""

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        message = QLabel(text)
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def show_info(parent: QWidget | None, title: str, text: str) -> None:
        InfoDialog(parent, title, text).exec()


class ConfirmDialog(QDialog):
    """是/否确认对话框(替代 QMessageBox.question)。"""

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        message = QLabel(text)
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes
            | QDialogButtonBox.StandardButton.No
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText("是")
        buttons.button(QDialogButtonBox.StandardButton.No).setText("否")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def confirm(parent: QWidget | None, title: str, text: str) -> bool:
        return ConfirmDialog(parent, title, text).exec() == QDialog.DialogCode.Accepted


class ImportExperimentDialog(QDialog):
    """导入实验类型:选择 Bruker 数据集目录 + 标题 + 关联项目。"""

    def __init__(
        self,
        parent: QWidget | None,
        samples: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入样品数据")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.source_edit = QLineEdit()
        browse = QPushButton("浏览...")
        browse.clicked.connect(self._browse)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(browse)
        self.source_widget = QWidget()
        self.source_widget.setLayout(source_row)
        form.addRow("Bruker 数据集目录:", self.source_widget)

        self.title_edit = QLineEdit()
        form.addRow("标题(可留空):", self.title_edit)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(70)
        self.notes_edit.setPlaceholderText(
            "样品数据注释(可选,导入后也可在中间上方编辑)"
        )
        form.addRow("注释(可选):", self.notes_edit)

        self.sample_combo = QComboBox()
        self.sample_combo.addItem("(无)", "")
        for sample_id, name in samples or []:
            self.sample_combo.addItem(f"{sample_id} {name}".strip(), sample_id)
        form.addRow("关联项目:", self.sample_combo)
        self.copy_check = QCheckBox("链接原始数据到项目(只读文件链接,必要时复制)")
        self.copy_check.setChecked(True)
        self.copy_check.setToolTip(
            "勾选后把 Bruker 数据集复制进项目 raw/ 目录并计算输入指纹;"
            "不勾选仅登记引用(源目录需保持可访问)。"
        )
        form.addRow("", self.copy_check)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据集目录", self.source_edit.text() or str(Path.home())
        )
        if path:
            self.source_edit.setText(path)

    def _validate_and_accept(self) -> None:
        source = self.source_edit.text().strip()
        if not source:
            InfoDialog.show_info(self, "提示", "请选择 Bruker 数据集目录")
            return
        source_path = Path(source)
        if not source_path.is_dir():
            InfoDialog.show_info(self, "提示", "所选目录不存在")
            return
        if not source_path.joinpath("acqus").is_file():
            InfoDialog.show_info(self, "提示", "所选目录不是 Bruker 数据集(缺少 acqus 文件)")
            return
        self.accept()

    def result_data(self) -> dict:
        return {
            "source": self.source_edit.text().strip(),
            "title": self.title_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
            "sample_id": self.sample_combo.currentData() or "",
            "copy": self.copy_check.isChecked(),
        }


class SampleDialog(QDialog):
    """项目表单:名称/蛋白/序列/浓度/缓冲液/备注。"""

    def __init__(self, parent: QWidget | None, sample_id: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(f"项目 {sample_id}".strip() if sample_id else "添加项目")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.protein_edit = QLineEdit()
        self.sequence_edit = QLineEdit()
        self.concentration_edit = QLineEdit()
        self.concentration_edit.setPlaceholderText("μM,可留空")
        self.buffer_edit = QLineEdit()
        self.notes_edit = QLineEdit()
        form.addRow("名称:", self.name_edit)
        form.addRow("蛋白:", self.protein_edit)
        form.addRow("序列:", self.sequence_edit)
        form.addRow("浓度(μM):", self.concentration_edit)
        form.addRow("缓冲液:", self.buffer_edit)
        form.addRow("备注:", self.notes_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self.name_edit.text().strip():
            InfoDialog.show_info(self, "提示", "请填写项目名称")
            return
        self.accept()

    def result_data(self) -> dict:
        concentration = 0.0
        raw = self.concentration_edit.text().strip()
        if raw:
            try:
                concentration = float(raw)
            except ValueError:
                concentration = 0.0
        return {
            "name": self.name_edit.text().strip(),
            "protein_name": self.protein_edit.text().strip(),
            "sequence": self.sequence_edit.text().strip(),
            "concentration_um": concentration,
            "buffer": self.buffer_edit.text().strip(),
            "notes": self.notes_edit.text().strip(),
        }


class NotesDialog(QDialog):
    """三级注释表单:按层级字段列表逐行填写;仅有几种取值的字段用下拉。

    实验类型注释先选维度,再按 presets 过滤给出实验类型选项;
    核(组合)同样给常用选项。其余字段保持文本输入。
    """

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        kind: str = "",
        values: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        hint = QLabel("常规信息(可留空):")
        layout.addWidget(hint)
        self._edits: dict[str, QLineEdit] = {}
        self._combos: dict[str, QComboBox] = {}
        self._type_combo: QComboBox | None = None
        values = dict(values or {})
        form = QFormLayout()
        for key, label in note_fields(kind):
            if key == "dimension":
                combo = QComboBox()
                combo.addItem("", "")
                for option in DIMENSION_OPTIONS:
                    combo.addItem(option, option)
                current = str(values.get("dimension", "") or "")
                if current in DIMENSION_OPTIONS:
                    combo.setCurrentText(current)
                combo.currentIndexChanged.connect(self._on_dimension_changed)
                self._combos[key] = combo
                form.addRow(f"{label}:", combo)
            elif key == "experiment_type":
                combo = QComboBox()
                combo.setEditable(True)
                self._type_combo = combo
                self._combos[key] = combo
                current = str(values.get("experiment_type", "") or "")
                if current:
                    combo.setEditText(current)
                form.addRow(f"{label}:", combo)
            elif key == "nuclei":
                combo = QComboBox()
                combo.setEditable(True)
                combo.addItems(NUCLEI_OPTIONS)
                current = str(values.get("nuclei", "") or "")
                if current:
                    combo.setCurrentText(current)
                self._combos[key] = combo
                form.addRow(f"{label}:", combo)
            else:
                edit = QLineEdit()
                edit.setText(str(values.get(key, "") or ""))
                edit.setPlaceholderText("可留空")
                self._edits[key] = edit
                form.addRow(f"{label}:", edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        # 维度确定后再填充实验类型选项(先选维度,再选类型)
        self._on_dimension_changed()

    def _dimension_value(self) -> str:
        """当前选中的维度(空表示未选)。"""
        combo = self._combos.get("dimension")
        if combo is None:
            return ""
        return str(combo.currentData() or combo.currentText() or "")

    def _on_dimension_changed(self, *_args) -> None:
        """维度变化 → 按 presets 重新填充实验类型选项。"""
        type_combo = self._type_combo
        if type_combo is None:
            return
        current = type_combo.currentText().strip()
        options = experiment_type_options(self._dimension_value())
        type_combo.blockSignals(True)
        try:
            type_combo.clear()
            type_combo.addItems(options)
            if current in options:
                type_combo.setCurrentText(current)
            elif current:
                type_combo.setEditText(current)
        finally:
            type_combo.blockSignals(False)

    def result_fields(self) -> dict[str, str]:
        """返回填写后的字段 dict(空值剔除)。"""
        fields: dict[str, str] = {}
        for key, edit in self._edits.items():
            text = edit.text().strip()
            if text:
                fields[key] = text
        for key, combo in self._combos.items():
            text = combo.currentText().strip()
            if text:
                fields[key] = text
        return fields


class ParameterTableDialog(QDialog):
    """人工路径 A:参数表格编辑器(以 param_schema 为准)。

    标量参数(zero_fill/ext_lo/ext_hi/extract/sampling.*)逐行编辑,
    stages 阶段表逐行展示;「渲染脚本」发出 render_requested(params),
    由主窗口经 ProcessingController.manual_scripts 渲染 process.com /
    nus*.com 并打开脚本编辑器。
    """

    render_requested = pyqtSignal(dict)  # params:点「渲染脚本」时发出

    def __init__(
        self,
        parent: QWidget | None,
        experiment_label: str,
        params: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"人工参数表格 - {experiment_label}")
        self.resize(680, 540)
        layout = QVBoxLayout(self)
        self.schema = dict(params or {})
        self.params = self._defaults_from_schema()

        hint = QLabel(
            "人工路径 A:逐阶段修改参数,「渲染脚本」后可按需改脚本再运行。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.param_table = QTableWidget(0, 2)
        self.param_table.setHorizontalHeaderLabels(["参数", "值"])
        self._build_scalar_rows()
        self.param_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.param_table, 1)

        stage_title = QLabel("处理阶段(stages)")
        layout.addWidget(stage_title)
        self.stages_table = QTableWidget(0, 4)
        self.stages_table.setHorizontalHeaderLabels(["阶段", "工具", "宏", "参数"])
        self._build_stages_rows()
        self.stages_table.horizontalHeader().setStretchLastSection(True)
        self.stages_table.setMaximumHeight(160)
        layout.addWidget(self.stages_table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        render_btn = buttons.addButton(
            "渲染脚本", QDialogButtonBox.ButtonRole.ActionRole
        )
        render_btn.setToolTip("按当前参数渲染 process.com / nus*.com 并打开脚本编辑器")
        render_btn.clicked.connect(
            lambda: self.render_requested.emit(self.result_data())
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _defaults_from_schema(self) -> dict:
        """从 schema.default 取默认参数(缺失时用可编辑骨架)。"""
        schema = self.schema
        default = (
            dict(schema.get("default") or {})
            if isinstance(schema, dict) and isinstance(schema.get("default"), dict)
            else {}
        )
        default.setdefault("zero_fill", 2)
        default.setdefault("ext_lo", "11.0")
        default.setdefault("ext_hi", "6.0")
        default.setdefault("extract", True)
        default.setdefault(
            "sampling",
            {"ft_neg": False, "ft_alt": True, "flip_f1": False, "auto_phase": True},
        )
        default.setdefault("stages", [])
        return default

    def _build_scalar_rows(self) -> None:
        """标量参数行(以 schema properties 顺序为准,含 sampling.* 展开)。"""
        props = (
            self.schema.get("properties", {})
            if isinstance(self.schema, dict)
            else {}
        )
        sampling_props = {}
        if isinstance(props.get("sampling"), dict):
            sampling_props = props["sampling"].get("properties", {}) or {}
        sampling = self.params.get("sampling") or {}
        def _desc(key: str) -> str:
            prop = props.get(key, {}) if isinstance(props.get(key), dict) else {}
            return str(prop.get("description", ""))

        rows = [
            ("zero_fill", self.params.get("zero_fill", 2), _desc("zero_fill")),
            ("ext_lo", self.params.get("ext_lo", "11.0"), _desc("ext_lo")),
            ("ext_hi", self.params.get("ext_hi", "6.0"), _desc("ext_hi")),
            ("extract", self.params.get("extract", True), _desc("extract")),
        ]
        for key in ("ft_neg", "ft_alt", "flip_f1", "auto_phase"):
            default = key in ("ft_alt", "auto_phase")
            rows.append(
                (
                    f"sampling.{key}",
                    sampling.get(key, default),
                    sampling_props.get(key, {}).get("description", ""),
                )
            )
        self.param_table.setRowCount(len(rows))
        for row, (key, value, desc) in enumerate(rows):
            key_item = QTableWidgetItem(key)
            key_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.param_table.setItem(row, 0, key_item)
            value_item = QTableWidgetItem(self._fmt_value(value))
            value_item.setToolTip(desc or key)
            self.param_table.setItem(row, 1, value_item)

    def _build_stages_rows(self) -> None:
        stages = self.params.get("stages") or []
        self.stages_table.setRowCount(len(stages))
        for row, stage in enumerate(stages):
            self.stages_table.setItem(row, 0, QTableWidgetItem(str(stage.get("id", ""))))
            self.stages_table.setItem(row, 1, QTableWidgetItem(str(stage.get("tool", ""))))
            self.stages_table.setItem(row, 2, QTableWidgetItem(str(stage.get("macro", ""))))
            params_text = " ".join(
                f"{k}={v}" for k, v in (stage.get("params") or {}).items()
            )
            self.stages_table.setItem(row, 3, QTableWidgetItem(params_text))

    @staticmethod
    def _fmt_value(value) -> str:
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)

    _FALLBACK_PARAM_TYPES = {
        "zero_fill": "integer",
        "ext_lo": "string",
        "ext_hi": "string",
        "extract": "boolean",
        "sampling.ft_neg": "boolean",
        "sampling.ft_alt": "boolean",
        "sampling.flip_f1": "boolean",
        "sampling.auto_phase": "boolean",
    }

    def _coerce_value(self, key: str, raw: str):
        """按 schema 类型转换:boolean/integer/float 转换,string 保持原样。"""
        low = raw.lower()
        if low in ("true", "false"):
            return low == "true"
        props = (
            self.schema.get("properties", {})
            if isinstance(self.schema, dict)
            else {}
        )
        prop_type = ""
        if key.startswith("sampling."):
            sub = key.split(".", 1)[1]
            sampling_props = {}
            if isinstance(props.get("sampling"), dict):
                sampling_props = props["sampling"].get("properties", {}) or {}
            prop_type = str(sampling_props.get(sub, {}).get("type", ""))
        else:
            prop_type = str(props.get(key, {}).get("type", ""))
        if not prop_type:
            prop_type = self._FALLBACK_PARAM_TYPES.get(key, "")
        if prop_type == "string":
            return raw
        try:
            if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
                return int(raw)
            return float(raw)
        except ValueError:
            return raw

    def result_data(self) -> dict:
        """回读编辑后的 params(标量 + sampling + stages)。"""
        params = dict(self.params)
        for row in range(self.param_table.rowCount()):
            key_item = self.param_table.item(row, 0)
            value_item = self.param_table.item(row, 1)
            if key_item is None or value_item is None:
                continue
            key = key_item.text()
            raw = value_item.text().strip()
            value = self._coerce_value(key, raw)
            if key.startswith("sampling."):
                sub = key.split(".", 1)[1]
                sampling = dict(params.get("sampling") or {})
                sampling[sub] = value
                params["sampling"] = sampling
            else:
                params[key] = value
        stages = []
        for row in range(self.stages_table.rowCount()):
            stage_id = self.stages_table.item(row, 0)
            stage_tool = self.stages_table.item(row, 1)
            stage_macro = self.stages_table.item(row, 2)
            stages.append(
                {
                    "id": stage_id.text() if stage_id else "",
                    "tool": stage_tool.text() if stage_tool else "",
                    "macro": stage_macro.text() if stage_macro else "",
                }
            )
        params["stages"] = stages
        return params


class ScriptEditorDialog(QDialog):
    """人工路径 B:脚本编辑器(模仿 VSCode 的简单文本编辑器)。

    从 render_scripts / manual_scripts 加载 .com 内容;保存写入数据
    process 目录(或 raw 目录的 fid.com);「运行」发出 run_requested(content),
    由主窗口经 ProcessingController.run_manual_spectrum / run_manual_fid_com
    执行并登记 WorkflowRun。
    """

    run_requested = pyqtSignal(str)  # 脚本内容:点「运行」时发出

    def __init__(
        self,
        parent: QWidget | None,
        experiment_label: str,
        script_name: str = "process.com",
        content: str = "",
        save_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"脚本编辑器 - {experiment_label} ({script_name})")
        self.resize(760, 540)
        self.script_name = script_name
        self.save_dir = save_dir
        layout = QVBoxLayout(self)
        hint = QLabel(
            "人工路径 B:直接编辑处理脚本。保存写入数据目录;「运行」执行"
            "谱图脚本(process.com / nus*.com,消费已转换 fid)或 fid.com。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(content)
        font = self.editor.font()
        font.setFamily("Consolas")
        font.setStyleHint(font.StyleHint.Monospace)
        self.editor.setFont(font)
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        run_btn = buttons.addButton(
            "运行", QDialogButtonBox.ButtonRole.ActionRole
        )
        run_btn.setToolTip("保存当前脚本到数据目录并运行(登记 WorkflowRun)")
        run_btn.clicked.connect(
            lambda: self.run_requested.emit(self.editor.toPlainText())
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.save_message = QLabel("")
        self.save_message.setWordWrap(True)
        layout.addWidget(self.save_message)

    def result_data(self) -> dict:
        return {"content": self.editor.toPlainText()}

    def save_script(self) -> Path | None:
        """把当前内容保存到数据目录(无目录时返回 None)。"""
        if self.save_dir is None:
            self.save_message.setText("未指定保存目录(需选中数据)")
            return None
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            target = self.save_dir / self.script_name
            target.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.save_message.setText(f"已保存: {target}")
            return target
        except OSError as exc:
            self.save_message.setText(f"保存失败: {exc}")
            return None


class RunHistoryDialog(QDialog):
    """运行历史对话框:workflow_runs 列表 + 详情(状态/时间/消息/产物)。"""

    def __init__(
        self,
        parent: QWidget | None,
        runs: list,
        project_name: str = "",
        project_root: Path | str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"运行历史 - {project_name or 'NMRForge'}")
        self.project_root = Path(project_root) if project_root else None
        self._current_snapshot = ""
        self.resize(720, 480)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(len(runs), 6)
        self.table.setHorizontalHeaderLabels(
            ["运行", "实验类型", "流程", "状态", "开始", "结束"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row, run in enumerate(runs):
            self.table.setItem(row, 0, QTableWidgetItem(run.run_id))
            self.table.setItem(row, 1, QTableWidgetItem(run.experiment_id))
            self.table.setItem(row, 2, QTableWidgetItem(run.workflow_ref))
            self.table.setItem(row, 3, QTableWidgetItem(run.status))
            self.table.setItem(row, 4, QTableWidgetItem(run.started_at[:19]))
            self.table.setItem(row, 5, QTableWidgetItem((run.finished_at or "")[:19]))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, run)
        self.table.itemSelectionChanged.connect(self._show_detail)
        layout.addWidget(self.table, 1)

        self.detail_label = QLabel("选择一行查看详情")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #444;")
        layout.addWidget(self.detail_label)
        self.snapshot_button = QPushButton("打开快照目录")
        self.snapshot_button.setEnabled(False)
        self.snapshot_button.setToolTip(
            "打开该运行的脚本/参数快照目录(如有)"
        )
        self.snapshot_button.clicked.connect(self._open_snapshot)
        layout.addWidget(self.snapshot_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_detail(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        run = items[0].data(Qt.ItemDataRole.UserRole)
        if run is None:
            return
        outputs = "\n".join(f"  {k}: {v}" for k, v in run.outputs.items()) or "  (无)"
        snapshot = run.snapshot_dir or ""
        scripts = "、".join(run.scripts) if run.scripts else "(无)"
        self._current_snapshot = ""
        if snapshot and self.project_root is not None:
            candidate = self.project_root / snapshot
            if candidate.is_dir():
                self._current_snapshot = str(candidate)
        self.snapshot_button.setEnabled(bool(self._current_snapshot))
        self.detail_label.setText(
            f"运行: {run.run_id}  [{run.status}]\n"
            f"流程: {run.workflow_ref}  实验类型: {run.experiment_id}\n"
            f"消息: {run.message or '-'}\n"
            f"快照: {snapshot or '(无)'}  脚本: {scripts}\n"
            f"产物:\n{outputs}"
        )

    def _open_snapshot(self) -> None:
        """打开当前选中运行的脚本/参数快照目录。"""
        if not self._current_snapshot:
            return
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(self._current_snapshot))

class BatchSummaryDialog(QDialog):
    """批量处理汇总:成功/失败清单;失败项双击定位到数据(阶段 C1)。"""

    locate_requested = pyqtSignal(str)  # data_id

    def __init__(
        self,
        parent: QWidget | None,
        summary: dict,
        project_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"批量处理汇总 - {project_name or 'NMRForge'}")
        self.resize(520, 380)
        layout = QVBoxLayout(self)
        info = QLabel(str(summary.get("info", "")))
        info.setWordWrap(True)
        layout.addWidget(info)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)
        for item in summary.get("items", []):
            status = (
                "成功"
                if item.get("ok")
                else "失败: " + str(item.get("error", ""))
            )
            list_item = QListWidgetItem(
                f"{item.get('data_id', '')} {item.get('step', '')} · {status}"
            )
            list_item.setData(Qt.ItemDataRole.UserRole, item.get("data_id", ""))
            self.list_widget.addItem(list_item)
        self.list_widget.itemDoubleClicked.connect(self._on_item_activated)
        hint = QLabel("双击条目可在左侧定位到对应数据(失败项)或查看状态。")
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        data_id = item.data(Qt.ItemDataRole.UserRole)
        if data_id:
            self.locate_requested.emit(str(data_id))


class SettingsDialog(QDialog):
    """软件设置(精简,阶段 C3):NMRPipe 路径、默认线宽、points_per_line、
    SMILE 线程上限;保存到 config/nmrforge.local.yaml,重启生效。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("软件设置")
        self.resize(420, 300)
        from gui.settings import DEFAULTS, load_settings

        settings = load_settings()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.nmrpipe_edit = QLineEdit(str(settings.get("nmrpipe_path", "")))
        self.nmrpipe_edit.setPlaceholderText("未配置(自动查找)")
        form.addRow("NMRPipe 路径", self.nmrpipe_edit)
        self.linewidth_spins: dict[str, QDoubleSpinBox] = {}
        for nucleus, default in DEFAULTS["linewidth_hz"].items():
            spin = QDoubleSpinBox()
            spin.setRange(0, 200)
            spin.setDecimals(1)
            spin.setValue(float(settings["linewidth_hz"].get(nucleus, default)))
            form.addRow(f"{nucleus} 默认线宽 (Hz)", spin)
            self.linewidth_spins[nucleus] = spin
        self.ppl_spin = QSpinBox()
        self.ppl_spin.setRange(1, 8)
        self.ppl_spin.setValue(int(settings.get("points_per_line", 2)))
        form.addRow("填零 points_per_line", self.ppl_spin)
        self.smile_spin = QSpinBox()
        self.smile_spin.setRange(1, 16)
        self.smile_spin.setValue(int(settings.get("smile_thread_cap", 2)))
        form.addRow("SMILE 线程上限", self.smile_spin)
        layout.addLayout(form)
        pipeline = settings.get("pipeline") or {}
        self.simple_mode_check = QCheckBox(
            "简单模式(只按上一步产物文件判断状态)"
        )
        self.simple_mode_check.setToolTip(
            "开启后 Pipeline 只认产物文件是否存在(.fid/.ft2/.ft3/.list/"
            "报告),不再比较输入/脚本指纹,也不显示「已过期」"
        )
        self.simple_mode_check.setChecked(
            bool(pipeline.get("simple_mode", False))
        )
        layout.addWidget(self.simple_mode_check)
        self.fingerprint_check = QCheckBox(
            "文件指纹检测(输入/脚本变化 → 已过期)"
        )
        self.fingerprint_check.setChecked(
            bool(pipeline.get("fingerprint_check", True))
        )
        layout.addWidget(self.fingerprint_check)
        self.outdated_check = QCheckBox("显示「已过期」状态")
        self.outdated_check.setChecked(
            bool(pipeline.get("outdated_enabled", True))
        )
        layout.addWidget(self.outdated_check)
        hint = QLabel(
            "保存到 config/nmrforge.local.yaml,重启后生效;未配置时显示默认值。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        from gui.settings import save_settings

        settings = {
            "nmrpipe_path": self.nmrpipe_edit.text().strip(),
            "linewidth_hz": {
                nucleus: spin.value()
                for nucleus, spin in self.linewidth_spins.items()
            },
            "points_per_line": self.ppl_spin.value(),
            "smile_thread_cap": self.smile_spin.value(),
            "pipeline": {
                "simple_mode": self.simple_mode_check.isChecked(),
                "fingerprint_check": self.fingerprint_check.isChecked(),
                "outdated_enabled": self.outdated_check.isChecked(),
            },
        }
        save_settings(settings)
        self.accept()

