"""GUI 对话框组件:信息/确认/导入实验/样本表单。

不使用 QMessageBox(在 Windows + Qt6 下从菜单触发模态 QMessageBox 会打印
"This plugin supports grabbing the mouse only for popup windows"),统一用普通 QDialog。
"""

from __future__ import annotations

from pathlib import Path

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
    """人工路径 A:参数表格编辑器 UI 骨架。

    展示契约约定的处理参数结构(zero_fill/sampling/stages);后端实现
    尚未落地,「运行」按钮调用 ProcessingController.manual_param_table 占位
    接口并提示待实现。
    """

    def __init__(
        self,
        parent: QWidget | None,
        experiment_label: str,
        params: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"人工参数表格 - {experiment_label}")
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        self.params = dict(params or {})
        self.params.setdefault("zero_fill", 2)
        self.params.setdefault("sampling", {"ft_neg": True, "ft_alt": True})
        stages = self.params.setdefault(
            "stages",
            [
                {"id": "fid", "tool": "nmrPipe", "macro": "fid.com"},
                {"id": "process", "tool": "nmrPipe", "macro": "process.com"},
            ],
        )

        hint = QLabel(
            "人工路径 A(骨架):逐阶段修改参数,后端将按参数生成确定性 .com 脚本。"
            "当前为占位接口,保存后可继续开发。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(len(stages), 4)
        self.table.setHorizontalHeaderLabels(["阶段", "工具", "宏", "参数"])
        for row, stage in enumerate(stages):
            self.table.setItem(row, 0, QTableWidgetItem(str(stage.get("id", ""))))
            self.table.setItem(row, 1, QTableWidgetItem(str(stage.get("tool", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(stage.get("macro", ""))))
            params_text = " ".join(
                f"{k}={v}" for k, v in stage.get("params", {}).items()
            )
            self.table.setItem(row, 3, QTableWidgetItem(params_text))
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_data(self) -> dict:
        """返回编辑后的 params(UI 骨架:目前只回读表格文本)。"""
        params = dict(self.params)
        stages = []
        for row in range(self.table.rowCount()):
            stages.append(
                {
                    "id": self.table.item(row, 0).text() if self.table.item(row, 0) else "",
                    "tool": self.table.item(row, 1).text() if self.table.item(row, 1) else "",
                    "macro": self.table.item(row, 2).text() if self.table.item(row, 2) else "",
                }
            )
        params["stages"] = stages
        return params


class ScriptEditorDialog(QDialog):
    """人工路径 B:脚本编辑器 UI 骨架(模仿 VSCode 的简单文本编辑器)。

    编辑 .com 处理脚本;后端实现尚未落地,「运行」调用
    ProcessingController.manual_script_editor 占位接口并提示待实现。
    """

    def __init__(
        self,
        parent: QWidget | None,
        experiment_label: str,
        script_name: str = "process.com",
        content: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"脚本编辑器 - {experiment_label} ({script_name})")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "人工路径 B(骨架):直接编辑处理脚本(.com)。当前为占位接口,"
            "语法高亮与后端执行将在后续版本接入。"
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
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_data(self) -> dict:
        return {"content": self.editor.toPlainText()}
