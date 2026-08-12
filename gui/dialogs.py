"""GUI 对话框组件:信息/确认/导入实验/样本表单。

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
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
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
    """导入实验:选择 Bruker 数据集目录 + 标题 + 关联样本。"""

    def __init__(
        self,
        parent: QWidget | None,
        samples: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入数据")
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

        self.sample_combo = QComboBox()
        self.sample_combo.addItem("(无)", "")
        for sample_id, name in samples or []:
            self.sample_combo.addItem(f"{sample_id} {name}".strip(), sample_id)
        form.addRow("关联样本:", self.sample_combo)
        self.copy_check = QCheckBox("复制数据到项目(raw/<exp_id>,SHA-256 指纹)")
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
            "sample_id": self.sample_combo.currentData() or "",
            "copy": self.copy_check.isChecked(),
        }


class SampleDialog(QDialog):
    """样本表单:名称/蛋白/序列/浓度/缓冲液/备注。"""

    def __init__(self, parent: QWidget | None, sample_id: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(f"样本 {sample_id}".strip() if sample_id else "添加样本")
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
            InfoDialog.show_info(self, "提示", "请填写样本名称")
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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"运行历史 - {project_name or 'NMRForge'}")
        self.resize(720, 480)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(len(runs), 6)
        self.table.setHorizontalHeaderLabels(
            ["运行", "实验", "流程", "状态", "开始", "结束"]
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
        self.detail_label.setText(
            f"运行: {run.run_id}  [{run.status}]\n"
            f"流程: {run.workflow_ref}  实验: {run.experiment_id}\n"
            f"消息: {run.message or '-'}\n产物:\n{outputs}"
        )
