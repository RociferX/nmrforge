"""GUI 对话框组件:信息/确认/导入实验/项目表单。

不使用 QMessageBox(在 Windows + Qt6 下从菜单触发模态 QMessageBox 会打印
"This plugin supports grabbing the mouse only for popup windows"),统一用普通 QDialog。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    EXPERIMENT_CATEGORY_OPTIONS,
    NUCLEI_OPTIONS,
    experiment_type_options,
    note_fields,
)


def _center_on_screen(dialog: QDialog) -> None:
    """把对话框移到其所在屏幕中心(所有弹窗统一居中,0.2.112)。"""
    parent = dialog.parentWidget()
    screen = None
    if parent is not None:
        window = parent.window()
        if window is not None:
            screen = QApplication.screenAt(window.frameGeometry().center())
    screen = screen or QApplication.primaryScreen()
    if screen is None:
        return
    geo = screen.availableGeometry()
    dialog.move(
        geo.left() + max(0, (geo.width() - dialog.width()) // 2),
        geo.top() + max(0, (geo.height() - dialog.height()) // 2),
    )


class _DialogCenteringFilter(QObject):
    """QDialog 显示时自动居中到所在屏幕(0.2.112)。"""

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QDialog) and event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, lambda d=obj: _center_on_screen(d))
        return super().eventFilter(obj, event)


def install_dialog_centering(app) -> None:
    """安装应用级对话框居中过滤器(主窗口/独立查看器入口调用)。"""
    app._dialog_centering_filter = _DialogCenteringFilter(app)
    app.installEventFilter(app._dialog_centering_filter)


class InfoDialog(QDialog):
    """带确定按钮的信息对话框(替代 QMessageBox.information/critical/about)。

    0.2.199-补29et:Popup 类型 + show 前屏幕居中——原生 Wayland 下普通
    QDialog 的 move() 被合成器忽略(弹窗落左上角),Popup 走 xdg_popup
    positioner 可可靠居中;点外部即关闭(小信息弹窗语义)。
    """

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Popup)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        message = QLabel(text)
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        # 0.2.199-补29et:show 前居中(Wayland xdg_popup 在显示时按初始
        # 位置锚定,show 后再 move 无效)
        self.adjustSize()
        _center_on_screen(self)

    @staticmethod
    def show_info(parent: QWidget | None, title: str, text: str) -> None:
        InfoDialog(parent, title, text).exec()


class MultiSelectDataDialog(QDialog):
    """多选样品数据对话框(把其它数据加入数据组用)。

    items: [(data_id, label), ...];selected_ids() 返回勾选的数据 id 列表。
    """

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        items: list[tuple[str, str]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        hint = QLabel("勾选要加入该组的样品数据(可多选):")
        layout.addWidget(hint)
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        for data_id, label in items:
            item = QListWidgetItem(f"{label} ({data_id})")
            item.setData(Qt.ItemDataRole.UserRole, data_id)
            self._list.addItem(item)
        layout.addWidget(self._list)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("加入组")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_ids(self) -> list[str]:
        """返回勾选的数据 id 列表。"""
        return [
            str(self._list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self._list.count())
            if self._list.item(index).isSelected()
        ]


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
    """导入实验:选择 Bruker 数据集目录 + 标题 + 关联项目。"""

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
        self.source_edit.textChanged.connect(self._update_segmented_hint)
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
            "勾选后把 Bruker 数据集链接进项目 raw/ 目录并计算输入指纹;"
            "不勾选仅登记引用(源目录需保持可访问)。"
        )
        form.addRow("", self.copy_check)
        # 0.2.108:分段采集导入(容器目录,多个含 acqus 的子目录合并为一条数据)
        self.segmented_check = QCheckBox(
            "分段数据或重复实验叠加导入(容器目录:多个含 acqus 的子目录合并为一条数据)"
        )
        self.segmented_check.setToolTip(
            "适用于同一次采集分成多段(分段 NUS/重复实验叠加)的数据;普通单数据集目录保持不勾选。"
            "选择容器目录时自动勾选。"
        )
        self.segmented_check.setChecked(False)
        form.addRow("", self.segmented_check)
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
        # 0.2.199-补29gg:空输入时从「数据总目录」开始(默认用户主目录)
        from gui.settings import data_root_path

        start = self.source_edit.text().strip() or str(data_root_path())
        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据集目录", start
        )
        if path:
            self.source_edit.setText(path)

    def _update_segmented_hint(self, text: str = "") -> None:
        """源目录为容器目录(≥2 个含 acqus 的子目录)时自动勾选分段导入。"""
        try:
            from gui.processing import is_segmented_container

            if text and is_segmented_container(text):
                self.segmented_check.setChecked(True)
        except Exception:  # noqa: BLE001 - 目录不可读时保持当前状态
            pass

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
            from gui.processing import is_segmented_container

            if not is_segmented_container(source_path):
                # 0.2.198:容器顶层不含 acqus 属正常,提示聚焦「数据段」,
                # 不再把「顶层缺少 acqus」当作问题
                InfoDialog.show_info(
                    self,
                    "提示",
                    "所选目录既不是 Bruker 数据集,也不是分段/重复实验容器\n"
                    "(容器需至少 2 个子目录各含 acqus 数据段;"
                    "非数据子目录已忽略)",
                )
                return
        self.accept()

    def result_data(self) -> dict:
        return {
            "source": self.source_edit.text().strip(),
            "title": self.title_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
            "sample_id": self.sample_combo.currentData() or "",
            "copy": self.copy_check.isChecked(),
            "segmented": self.segmented_check.isChecked(),
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

    样品数据注释先选维度,再按 presets 过滤给出数据类型选项;
    实验注释:「实验类型」为指认实验/动力学实验;核(组合)同样给常用选项。
    其余字段保持文本输入。
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
                if kind == "experiment":
                    # 实验注释:实验类型仅指认实验 / 动力学实验
                    combo.addItems(EXPERIMENT_CATEGORY_OPTIONS)
                else:
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
        # 样品数据注释:维度确定后再填充数据类型选项(先选维度,再选类型)
        self._on_dimension_changed()

    def _dimension_value(self) -> str:
        """当前选中的维度(空表示未选)。"""
        combo = self._combos.get("dimension")
        if combo is None:
            return ""
        return str(combo.currentData() or combo.currentText() or "")

    def _on_dimension_changed(self, *_args) -> None:
        """样品数据注释:维度变化 → 按 presets 重新填充数据类型选项。"""
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

class ScriptEditorDialog(QDialog):
    """脚本编辑器(模仿 VSCode 的简单文本编辑器)。

    从 render_scripts / manual_scripts 加载 .com 内容;保存写入数据
    process 目录(或 raw 目录的 fid.com);「运行」发出 run_requested(content),
    由主窗口经 ProcessingController.run_manual_spectrum / run_manual_fid_com
    执行并登记 WorkflowRun。
    """

    run_requested = pyqtSignal(str)  # 脚本内容:点「运行」时发出

    @staticmethod
    def _hint_text(script_name: str) -> str:
        """按脚本类型给出对应提示:fid.com 是 Bruker 转换脚本,
        处理脚本(process/nus)是 NMRPipe 谱图处理,提示各不相同。"""
        if script_name == "fid.com":
            return (
                "fid.com 是 Bruker 原始数据 → NMRPipe fid 的转换脚本,"
                "由后端按采集参数自动生成,通常无需修改。\n"
                "· 转换参数(OBS/CAR/SW 等)来自 Bruker 参数,勿随意改动;\n"
                "· 如需调整谱图引用/载波,改 -xCAR/-yCAR 等 CAR 项;\n"
                "· NUS 数据请保留 nuslist 相关处理,勿删采样信息;\n"
                "· 输出名已是 {数据 id}.fid(bruker 默认 test.fid 已由\n"
                "  后端自动改写;3D 切片保持 test%03d.fid),运行后由后端\n"
                "  归位到 process/,无需手动移动或改名。"
            )
        return (
            "处理脚本由后端按采样模式自动生成,通常无需修改。\n"
            "· 基线不好(谱图有波浪/伪峰):在对应维 FT 后加 "
            "`| nmrPipe -fn POLY -auto`,或微调 POLY -ord;\n"
            "· 峰形/分辨率不佳:调整窗函数 SP 的 -off/-end/-pow/-c,"
            "或加大 ZF -size;\n"
            "· 相位不好:调 PS -p0/-p1(自动调相后会自行填入,一般不动);\n"
            "· FT 的 -alt/-neg/-real 标志按采样模式自动判定;若认为判断"
            "错误可修改:-neg 谱颠倒,-alt 谱平移半个谱宽。"
        )

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
        hint = QLabel(self._hint_text(script_name))
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
        self.save_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.save_btn.setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.run_btn = buttons.addButton(
            "运行", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.run_btn.setToolTip("保存当前脚本到数据目录并运行(登记 WorkflowRun)")
        self.run_btn.clicked.connect(self._on_run)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.save_message = QLabel("")
        self.save_message.setWordWrap(True)
        layout.addWidget(self.save_message)

    def _on_save(self) -> None:
        """「保存」:写回数据目录后关闭(0.2.192 修复:之前保存不落盘)。"""
        self.save_script()
        self.accept()

    def _on_run(self) -> None:
        """「运行」:先保存当前脚本,发出内容后自动关闭(0.2.192)。"""
        self.save_script()
        self.run_requested.emit(self.editor.toPlainText())
        self.close()

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
            f"流程: {run.workflow_ref}  实验: {run.experiment_id}\n"
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
    """软件设置(精简,阶段 C3):NMRPipe 路径、默认线宽、简单模式;线宽接入
    生成谱图参数;保存到 config/nmrforge.local.yaml,重启生效。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("软件设置")
        # 0.2.199-补29gh(用户):窗口太矮导致行内容被裁剪/文字显示不全
        self.resize(560, 480)
        self.setMinimumWidth(520)
        from gui.settings import (
            DEFAULTS,
            SETTINGS_FILENAME,
            is_appimage,
            load_settings,
        )

        settings = load_settings()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.nmrpipe_edit = QLineEdit(str(settings.get("nmrpipe_path", "")))
        self.nmrpipe_edit.setPlaceholderText("未配置(自动查找)")
        form.addRow("NMRPipe 路径", self.nmrpipe_edit)
        # 0.2.199-补29gg(用户):数据总目录(空=用户主目录,导入浏览起点)
        self.data_root_edit = QLineEdit(str(settings.get("data_root", "")))
        self.data_root_edit.setPlaceholderText(f"默认: {Path.home()}")
        data_root_browse = QPushButton("浏览...")
        data_root_browse.clicked.connect(self._browse_data_root)
        data_root_row = QHBoxLayout()
        data_root_row.addWidget(self.data_root_edit, 1)
        data_root_row.addWidget(data_root_browse)
        data_root_widget = QWidget()
        data_root_widget.setLayout(data_root_row)
        form.addRow("数据总目录", data_root_widget)
        self.linewidth_spins: dict[str, QDoubleSpinBox] = {}
        self.tolerance_spins: dict[str, QDoubleSpinBox] = {}
        for nucleus, default in DEFAULTS["linewidth_hz"].items():
            spin = QDoubleSpinBox()
            spin.setRange(0, 200)
            spin.setDecimals(1)
            spin.setValue(float(settings["linewidth_hz"].get(nucleus, default)))
            form.addRow(f"{nucleus} 默认线宽 (Hz)", spin)
            self.linewidth_spins[nucleus] = spin
            # 0.2.199-补29fx(用户):对齐容差与线宽同处设置。
            # 默认 Poky kr:1H ±0.02、15N/13C ±0.2 ppm。
            tol_default = float(
                DEFAULTS["alignment_tolerance_ppm"].get(nucleus, 0.2)
            )
            tol_spin = QDoubleSpinBox()
            tol_spin.setRange(0.001, 2.0)
            tol_spin.setDecimals(3)
            tol_spin.setValue(
                float(
                    (settings.get("alignment_tolerance_ppm") or {})
                    .get(nucleus, tol_default)
                )
            )
            tol_spin.setToolTip(
                "峰对齐/参考匹配容差(ppm)。Poky kr 默认 1H 0.02、"
                "15N/13C 0.2;越小要求越严"
            )
            form.addRow(f"{nucleus} 对齐容差 (ppm)", tol_spin)
            self.tolerance_spins[nucleus] = tol_spin
        # 0.2.199-补24:SMILE 自动线程预留数(机器线程数 - thread_offset)
        self.thread_offset_spin = QSpinBox()
        self.thread_offset_spin.setRange(0, 16)
        self.thread_offset_spin.setValue(
            int((settings.get("smile") or {}).get("thread_offset", 2))
        )
        self.thread_offset_spin.setToolTip(
            "SMILE 自动线程 = 机器线程数 - 该值(默认 2,给系统留线程);"
            "改小可提升速度,改大可降低 CPU/功耗占用"
        )
        form.addRow("SMILE 线程预留数", self.thread_offset_spin)
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
        # 0.2.199-补29gb(用户):AppImage 打包运行时隐藏「简单模式」
        self.simple_mode_check.setVisible(not is_appimage())
        layout.addWidget(self.simple_mode_check)
        if is_appimage():
            dest = str(
                Path.home() / ".config" / "NMRForge" / SETTINGS_FILENAME
            )
        else:
            dest = "config/" + SETTINGS_FILENAME
        hint = QLabel(
            f"保存到 {dest},重启后生效;未配置时显示默认值。"
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

    def _browse_data_root(self) -> None:
        """选择数据总目录(0.2.199-补29gg)。"""
        start = self.data_root_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "选择数据总目录", start
        )
        if path:
            self.data_root_edit.setText(path)

    def _on_accept(self) -> None:
        from gui.settings import save_settings

        settings = {
            "nmrpipe_path": self.nmrpipe_edit.text().strip(),
            "data_root": self.data_root_edit.text().strip(),
            "linewidth_hz": {
                nucleus: spin.value()
                for nucleus, spin in self.linewidth_spins.items()
            },
            "alignment_tolerance_ppm": {
                nucleus: spin.value()
                for nucleus, spin in self.tolerance_spins.items()
            },
            "pipeline": {
                "simple_mode": self.simple_mode_check.isChecked(),
            },
            "smile": {
                "thread_offset": self.thread_offset_spin.value(),
            },
        }
        save_settings(settings)
        self.accept()

