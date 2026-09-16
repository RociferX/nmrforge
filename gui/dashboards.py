"""Dashboard 面板:项目/实验概览(GUI_ARCHITECTURE_VISION §11-12)。

- ProjectDashboard:项目统计(实验/样品数据/处理完成度)+ 最近运行 + 新建实验表单;
- ExperimentDashboard:样品数据列表(每样品数据状态)+ 导入样品数据表单。

数据来源:core.project(ProjectManager);运行历史来自 workflow_runs。
"""

from __future__ import annotations

from pathlib import Path

from qtcompat.QtCore import QEvent, QPoint, Qt, QTimer
from qtcompat.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.dialogs import InfoDialog
from qtcompat import Signal
from ui_support.theme import TEXT_MUTED, TEXT_PRIMARY


def _active_data_of(exp) -> list:
    """实验下未软删除的数据条目。"""
    return [
        d for d in (getattr(exp, "data", None) or []) if not getattr(d, "trashed", False)
    ]


def _data_count(project) -> int:
    return sum(
        len(_active_data_of(exp))
        for exp in project.experiments
        if not getattr(exp, "trashed", False)
    )


def _data_processed(project) -> int:
    count = 0
    for exp in project.experiments:
        if getattr(exp, "trashed", False):
            continue
        for data in _active_data_of(exp):
            status = getattr(data, "status", "") or ""
            if status in ("fid_ready", "processed"):
                count += 1
    return count


class ProjectDashboard(QWidget):
    """项目概览:统计 + 处理完成度 + 最近运行 + 新建实验。"""

    create_experiment_requested = Signal(str)  # 实验标题

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("项目")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(8)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        layout.addWidget(self.stats_label)
        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)
        layout.addSpacing(10)

        recent_title = QLabel("最近运行")
        recent_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(recent_title)
        self.runs_table = QTableWidget(0, 4)
        self.runs_table.setHorizontalHeaderLabels(["运行", "流程", "状态", "时间"])
        self.runs_table.horizontalHeader().setStretchLastSection(True)
        self.runs_table.setMaximumHeight(150)
        layout.addWidget(self.runs_table)
        layout.addSpacing(10)

        form = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("实验标题(可留空)")
        form.addWidget(self.title_edit, 1)
        self.create_button = QPushButton("新建实验")
        self.create_button.clicked.connect(self._on_create)
        form.addWidget(self.create_button)
        layout.addLayout(form)
        layout.addStretch(1)

    def set_context(self, manager: ProjectManager) -> None:
        self.manager = manager
        self.refresh()

    def refresh(self) -> None:
        if self.manager is None or self.manager.project is None:
            return
        project = self.manager.project
        self.context_label.setText(project.name)
        exp_count = len(project.experiments)
        data_count = _data_count(project)
        processed = _data_processed(project)
        self.stats_label.setText(
            f"实验: {exp_count}  |  样品数据: {data_count}  |  已处理: {processed}"
        )
        if data_count:
            pct = round(processed * 100 / data_count)
            self.progress_label.setText(f"处理完成度: {pct}%")
        else:
            self.progress_label.setText("处理完成度: - (暂无数据)")

        self.runs_table.setRowCount(0)
        recent = list(project.workflow_runs)[-8:]
        for run in recent:
            row = self.runs_table.rowCount()
            self.runs_table.insertRow(row)
            self.runs_table.setItem(row, 0, QTableWidgetItem(run.run_id))
            self.runs_table.setItem(row, 1, QTableWidgetItem(run.workflow_ref))
            self.runs_table.setItem(row, 2, QTableWidgetItem(run.status))
            self.runs_table.setItem(
                row, 3, QTableWidgetItem((run.finished_at or run.started_at)[:19])
            )

    def _on_create(self) -> None:
        self.create_experiment_requested.emit(self.title_edit.text().strip())


class ExperimentImportPanel(QWidget):
    """导入数据面板:单个/分段/批量导入 + 链接选项(0.2.162-补11)。

    实验页不再内联展示,由主界面「导入数据」按钮下拉弹出。"""

    import_options_requested = Signal(str, str, str, bool)  # (exp_id, name, source, copy)
    segmented_import_requested = Signal(str, str)  # (exp_id, 分段采集容器目录)
    batch_import_requested = Signal(str, list, bool)  # (exp_id, folders, group)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.copy_check = QCheckBox("链接原始数据到项目(只读,必要时复制)")
        self.copy_check.setChecked(True)
        layout.addWidget(self.copy_check)
        layout.addSpacing(4)

        self.single_group = QGroupBox("单个导入")
        single_layout = QVBoxLayout(self.single_group)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("样品数据名称(可选)")
        single_layout.addWidget(self.name_edit)
        form = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Bruker 数据集目录(含 acqus)")
        form.addWidget(self.source_edit, 1)
        browse = QPushButton("浏览...")
        browse.clicked.connect(self._browse)
        form.addWidget(browse)
        single_layout.addLayout(form)
        self.import_button = QPushButton("导入样品数据")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._on_import)
        single_layout.addWidget(self.import_button)
        self.source_edit.textChanged.connect(
            lambda _t: self.import_button.setEnabled(
                bool(self.source_edit.text().strip())
            )
        )
        layout.addWidget(self.single_group)

        self.segmented_group = QGroupBox("分段数据或重复实验叠加导入")
        segmented_layout = QVBoxLayout(self.segmented_group)
        segmented_hint = QLabel(
            "用于同一次实验分多段采集(NUS 互补采样点补全网格)或重复实验叠加"
            "(同参数/同采样点,提高信噪比):选择容器目录(顶层无 acqus,至少 2 个"
            "子目录各含 acqus),导入后自动合并为一条样品数据"
        )
        segmented_hint.setWordWrap(True)
        segmented_hint.setStyleSheet(f"color: {TEXT_MUTED};")
        segmented_layout.addWidget(segmented_hint)
        segmented_form = QHBoxLayout()
        self.segmented_source_edit = QLineEdit()
        self.segmented_source_edit.setPlaceholderText(
            "分段/重复实验容器目录(含多个 acqus 子目录)"
        )
        segmented_form.addWidget(self.segmented_source_edit, 1)
        segmented_browse = QPushButton("浏览...")
        segmented_browse.clicked.connect(self._on_segmented_browse)
        segmented_form.addWidget(segmented_browse)
        segmented_layout.addLayout(segmented_form)
        self.segmented_import_button = QPushButton("分段/重复实验叠加导入")
        self.segmented_import_button.setEnabled(False)
        self.segmented_import_button.clicked.connect(self._on_segmented_import)
        segmented_layout.addWidget(self.segmented_import_button)
        self.segmented_source_edit.textChanged.connect(
            lambda _t: self.segmented_import_button.setEnabled(
                bool(self.segmented_source_edit.text().strip())
            )
        )
        layout.addWidget(self.segmented_group)

        self.batch_group = QGroupBox("批量处理")
        batch_layout = QVBoxLayout(self.batch_group)
        batch_hint = QLabel(
            "可添加总文件夹(自动检查子文件夹中的 Bruker 数据集)或多个数据目录;"
            "同批数据绑定同一批量组标记,中间处理页操作对整组数据执行。"
            "注:批量暂仅支持 2D 谱,3D 数据会跳过(可单个处理)"
        )
        batch_hint.setWordWrap(True)
        batch_hint.setStyleSheet(f"color: {TEXT_MUTED};")
        batch_layout.addWidget(batch_hint)
        self.batch_list = QListWidget()
        self.batch_list.setMaximumHeight(110)
        batch_layout.addWidget(self.batch_list)
        self.batch_group_check = QCheckBox("批量导入并成组(仅支持 2D 谱)")
        self.batch_group_check.setToolTip(
            "勾选:导入到同一数据组,后续一起处理(仅支持 2D 谱);"
            "不勾选:不成组,相当于多个单次导入"
        )
        self.batch_group_check.setChecked(True)
        batch_layout.addWidget(self.batch_group_check)
        batch_buttons = QHBoxLayout()
        self.batch_add_button = QPushButton("添加数据文件夹...")
        self.batch_add_button.clicked.connect(self._on_batch_add_folder)
        batch_buttons.addWidget(self.batch_add_button)
        self.batch_clear_button = QPushButton("清空列表")
        self.batch_clear_button.clicked.connect(self._on_batch_clear)
        batch_buttons.addWidget(self.batch_clear_button)
        self.batch_import_button = QPushButton("批量导入")
        self.batch_import_button.setEnabled(False)
        self.batch_import_button.clicked.connect(self._on_batch_import)
        batch_buttons.addWidget(self.batch_import_button)
        batch_layout.addLayout(batch_buttons)
        layout.addWidget(self.batch_group)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # 公开动作接口(0.2.199-补29hz)
    # 仪表盘快捷按钮原来直接调 _browse/_on_import 等私有方法,内部改名会
    # 静默失效;这里给出稳定入口。
    # ------------------------------------------------------------------
    def browse_single(self) -> None:
        """选择单个 Bruker 数据集目录。"""
        self._browse()

    def add_batch_folder(self) -> None:
        """把目录加入批量导入列表。"""
        self._on_batch_add_folder()

    def clear_batch_list(self) -> None:
        """清空批量导入列表。"""
        self._on_batch_clear()

    def import_batch(self) -> None:
        """按列表执行批量导入(或重复实验叠加)。"""
        self._on_batch_import()

    def import_single(self) -> None:
        """导入当前选择的单个数据集。"""
        self._on_import()

    def browse_segmented(self) -> None:
        """选择分段数据/重复叠加的容器目录。"""
        self._on_segmented_browse()

    def import_segmented(self) -> None:
        """执行分段数据/重复叠加导入。"""
        self._on_segmented_import()

    def set_context(self, exp_id: str) -> None:
        self._exp_id = exp_id

    def _browse(self) -> None:
        # 0.2.199-补29gg:空输入时从「数据总目录」开始(默认用户主目录)
        from gui.settings import data_root_path

        start = self.source_edit.text().strip() or str(data_root_path())
        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据集目录", start
        )
        if path:
            self.source_edit.setText(path)

    def _on_batch_add_folder(self) -> None:
        """批量列表添加数据文件夹(自动检查子文件夹中的 Bruker 数据集)。
        0.2.199-补29gg:浏览起点=数据总目录。"""
        from gui.settings import data_root_path

        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据文件夹(批量)", str(data_root_path())
        )
        if not path:
            return
        found = self._bruker_datasets_under(Path(path))
        if not found:
            InfoDialog.show_info(
                self,
                "未找到数据",
                "所选目录及其子文件夹中没有含 acqus 的 Bruker 数据集",
            )
            return
        for dataset_dir in found:
            if not self.batch_list.findItems(
                str(dataset_dir), Qt.MatchFlag.MatchExactly
            ):
                self.batch_list.addItem(str(dataset_dir))
        self.batch_import_button.setEnabled(self.batch_list.count() > 0)

    @staticmethod
    def _bruker_datasets_under(root: Path) -> list[Path]:
        """root 及子文件夹中所有含 acqus 的数据集目录(排序去重)。"""
        datasets: set[Path] = set()
        try:
            candidates = [
                Path(p).parent for p in root.rglob("acqus") if p.is_file()
            ]
            candidates.append(root)
        except OSError:
            candidates = [root]
        for cand in candidates:
            if (cand / "acqus").is_file():
                # 0.2.199-补29hd:批量仅支持 2D——1D(无 acqu2s)/3D(含 acqu3s/
                # acqu3)均不进批量列表(1D 无选峰、3D SMILE 易断电)。
                if (
                    not (cand / "acqu2s").is_file()
                    or (cand / "acqu3s").is_file()
                    or (cand / "acqu3").is_file()
                ):
                    continue
                datasets.add(cand.resolve())
        return sorted(datasets)

    def _on_batch_clear(self) -> None:
        self.batch_list.clear()
        self.batch_import_button.setEnabled(False)

    def _on_batch_import(self) -> None:
        """把列表中的多个数据目录导入当前实验;group=True 同一批量组。"""
        if not self._exp_id or self.batch_list.count() == 0:
            return
        folders = [
            self.batch_list.item(index).text()
            for index in range(self.batch_list.count())
        ]
        self.batch_import_requested.emit(
            self._exp_id, folders, self.batch_group_check.isChecked()
        )
        # 0.2.199-补29gn:导入后清空待导入列表,避免文件夹一直占着
        self._on_batch_clear()

    def _on_import(self) -> None:
        source = self.source_edit.text().strip()
        if not source:
            InfoDialog.show_info(
                self, "导入样品数据", "请先选择 Bruker 数据集目录(含 acqus)"
            )
            return
        # exp_id 为空(未选中实验)时交由主窗口自动创建实验,
        # 不静默无反应
        self.import_options_requested.emit(
            self._exp_id,
            self.name_edit.text().strip(),
            source,
            self.copy_check.isChecked(),
        )

    def clear_import_form(self) -> None:
        """导入成功后清空单个/分段导入表单的名称与路径(0.2.112)。"""
        self.name_edit.clear()
        self.source_edit.clear()
        self.segmented_source_edit.clear()

    def _on_segmented_browse(self) -> None:
        """选择分段采集容器目录(0.2.199-补29gg:空输入起点=数据总目录)。"""
        from gui.settings import data_root_path

        path = QFileDialog.getExistingDirectory(
            self,
            "选择分段/重复实验容器目录",
            self.segmented_source_edit.text().strip()
            or str(data_root_path()),
        )
        if path:
            self.segmented_source_edit.setText(path)

    def _on_segmented_import(self) -> None:
        """分段采集导入:容器目录(合并 FID)直接发请求(带当前实验,0.2.122)。"""
        source = self.segmented_source_edit.text().strip()
        if not source:
            InfoDialog.show_info(
                self, "分段采集导入", "请先选择分段采集容器目录"
            )
            return
        self.segmented_import_requested.emit(self._exp_id, source)


def _dropdown_geometry(
    anchor: QWidget,
    host: QWidget,
    natural_h: int,
    width: int,
    margin: int = 8,
) -> tuple[QPoint, int]:
    """计算下拉在 host(父窗口)内的位置与最大高度:优先放按钮正下方,
    下方不够则放上方,保证不遮住触发按钮;高度超过可用空间时截断
    (由滚动条承载)。全部使用相对坐标,任何平台一致(0.2.194 修订:
    保持主窗口子部件方案,不回到有位置问题的顶层窗口)。

    返回 (pos, max_height)。
    """
    anchor_top = anchor.mapTo(host, QPoint(0, 0)).y()
    anchor_bottom = anchor.mapTo(host, QPoint(0, anchor.height())).y()
    host_w = max(host.width(), 1)
    host_h = max(host.height(), 1)
    below = host_h - anchor_bottom - margin
    above = anchor_top - margin
    target_h = min(natural_h, max(below, above, margin))
    if below >= target_h:
        y = anchor_bottom
    else:
        y = anchor_top - target_h
    x = anchor.mapTo(host, QPoint(0, 0)).x()
    x = min(max(x, 0), max(0, host_w - width))
    y = min(max(y, 0), max(0, host_h - target_h))
    return QPoint(x, y), target_h


class ImportDataDropdown(QWidget):
    """「导入数据」下拉面板:向下弹出,内含完整导入表单(0.2.162-补11)。"""

    import_options_requested = Signal(str, str, str, bool)
    segmented_import_requested = Signal(str, str)
    batch_import_requested = Signal(str, list, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # WA_AlwaysStackOnTop:子部件方案下保证绘制在中央部件之上,
        # 修复 Windows 首次弹出不可见(0.2.194 修订,不回到顶层窗口)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self._anchor: QWidget | None = None
        self._host_window: QWidget | None = None
        self._app = QApplication.instance()
        if self._app is not None:
            self.destroyed.connect(self._remove_event_filter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("导入样品数据")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)
        self.panel = ExperimentImportPanel(self)
        self.panel.import_options_requested.connect(self.import_options_requested.emit)
        self.panel.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self.panel.batch_import_requested.connect(self.batch_import_requested.emit)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self.panel)
        layout.addWidget(self._scroll, 1)
        self.setMinimumWidth(400)
        # 构造即隐藏:子部件方案下父页面显示会连带显示子部件,不隐藏会以
        # (0,0) 残影出现在页面顶部(0.2.194-补2 实测),且 isVisible 为真
        # 导致 open_below 从不执行
        self.hide()

    def open_below(self, anchor: QWidget, exp_id: str) -> None:
        """在 anchor 按钮正下方弹出(主窗口覆盖子部件),过长加滚动条。

        挂到 anchor 所在顶层窗口,用相对坐标定位(Qt 自己控制,不依赖
        Wayland 窗口协议);WA_AlwaysStackOnTop 保证绘制在中央部件之上,
        修复 Windows 首次弹出不可见(0.2.194 修订;不回到顶层窗口——
        顶层窗口存在无法解决的位置问题,0.2.163-补4 因此弃用)。
        """
        self._anchor = anchor
        self._host_window = anchor.window()
        if self._app is not None:
            self._app.installEventFilter(self)
        self.panel.set_context(exp_id)
        # 保持为实验页子部件、相对本页定位,不 reparent 到主窗口——
        # 首次打开 reparent 会触发位置重算,下拉跑到页面顶部只露滚动条
        # (0.2.194-补2 实测;子部件方案任何平台一致,无顶层窗口位置问题)
        host = self.parentWidget() or anchor.parentWidget()
        self.setMaximumHeight(16777215)  # 重置上次限制,重新取自然高度
        # 隐藏状态先放好位置(子部件 move 相对父窗口,正是所需语义);
        # show 在事件循环才真正生效,紧随的同步 move 会被丢——首个事件
        # 循环后再校正一次,保证首次打开也在按钮正下方(0.2.194-补2)
        self._place_below(anchor, host)
        self.show()
        QTimer.singleShot(0, self._deferred_place)
        self.activateWindow()

    def _place_below(self, anchor: QWidget, host: QWidget) -> None:
        """计算并应用按钮正下方的几何(隐藏/显示状态均可,0.2.194-补2)。"""
        self.adjustSize()
        pos, max_h = _dropdown_geometry(
            anchor, host, self.sizeHint().height(), self.width()
        )
        self.setMaximumHeight(max_h)
        self.adjustSize()
        self.move(pos)
        self.raise_()

    def _deferred_place(self) -> None:
        """show 生效后的位置校正:按当前锚点重放一次(0.2.194-补2)。"""
        if self._anchor is None or not self.isVisible():
            return
        host = self.parentWidget()
        if host is not None:
            self._place_below(self._anchor, host)

    def eventFilter(self, obj, event) -> bool:
        """非抓取窗口:点其它按钮/外部时先关掉本下拉,点击继续落到目标。"""
        try:
            visible = self.isVisible()
        except RuntimeError:  # pragma: no cover - 销毁竞态
            return False
        # 宿主顶层窗口关闭时同步收起下拉并移除应用过滤器,避免残留过滤器
        # 在进程收尾时悬挂(0.2.194 生命周期加固)
        if (
            visible
            and getattr(self, "_host_window", None) is obj
            and event.type() == QEvent.Type.Close
        ):
            self.close()
            return False
        if visible and event.type() == QEvent.Type.MouseButtonPress:
            if hasattr(event, "globalPosition"):
                pos = event.globalPosition().toPoint()
            else:  # pragma: no cover - Qt5 兼容
                pos = event.globalPos()
            if self._anchor is not None and self._anchor.rect().contains(
                self._anchor.mapFromGlobal(pos)
            ):
                return False  # 锚点按钮:交给按钮处理(开关/切换)
            if not self.rect().contains(self.mapFromGlobal(pos)):
                self.close()
        return False

    def hideEvent(self, event) -> None:
        if self._app is not None:
            self._app.removeEventFilter(self)
        super().hideEvent(event)

    def _remove_event_filter(self) -> None:
        if self._app is not None:
            try:
                self._app.removeEventFilter(self)
            except RuntimeError:  # pragma: no cover - 应用已销毁
                pass


class ExperimentDashboard(QWidget):
    """实验概览:样品数据列表(状态,名称可改);导入块已移入「导入数据」下拉。"""

    import_options_requested = Signal(str, str, str, bool)  # (exp_id, name, source, copy)
    data_rename_requested = Signal(str, str, str)  # (exp_id, data_id, new_name)
    segmented_import_requested = Signal(str, str)  # (exp_id, 分段采集容器目录)
    batch_import_requested = Signal(str, list, bool)  # (exp_id, folders, group)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("实验")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(8)

        data_title = QLabel("样品数据")
        data_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(data_title)
        self.data_table = QTableWidget(0, 3)
        self.data_table.setHorizontalHeaderLabels(["样品数据", "名称", "状态"])
        self.data_table.horizontalHeader().setStretchLastSection(True)
        self.data_table.setMaximumHeight(160)
        # 0.2.162-补13:名称列可编辑(双击/选中点击/F2),改名走 manager.rename_data
        self.data_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.data_table.itemChanged.connect(self._on_data_name_edited)
        self._loading_table = False
        layout.addWidget(self.data_table)
        layout.addSpacing(10)

        # 0.2.162-补11:导入块移入「导入数据」下拉面板,实验页不再内联展示
        self.import_panel = ExperimentImportPanel(self)
        self.import_panel.hide()
        self.import_panel.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self.import_panel.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self.import_panel.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        # 兼容旧测试/旧调用:表单控件与处理器转发到导入面板
        self.copy_check = self.import_panel.copy_check
        self.single_group = self.import_panel.single_group
        self.name_edit = self.import_panel.name_edit
        self.source_edit = self.import_panel.source_edit
        self.import_button = self.import_panel.import_button
        self.segmented_group = self.import_panel.segmented_group
        self.segmented_source_edit = self.import_panel.segmented_source_edit
        self.segmented_import_button = self.import_panel.segmented_import_button
        self.batch_group = self.import_panel.batch_group
        self.batch_list = self.import_panel.batch_list
        self.batch_add_button = self.import_panel.batch_add_button
        self.batch_clear_button = self.import_panel.batch_clear_button
        self.batch_import_button = self.import_panel.batch_import_button
        # 0.2.162-补12:「导入数据」按钮(原导入块位置);「数据组间分析」已隐藏(2026-09-03)
        action_row = QHBoxLayout()
        self.import_dropdown_button = QPushButton("导入数据")
        self.import_dropdown_button.clicked.connect(self._open_import_dropdown)
        action_row.addWidget(self.import_dropdown_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self._import_dropdown = ImportDataDropdown(self)
        self._import_dropdown.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self._import_dropdown.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self._import_dropdown.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        layout.addStretch(1)

    def _open_import_dropdown(self) -> None:
        """实验页「导入数据」:点击总是有明确反馈。

        下拉未打开 → 在按钮下方弹出;已打开 → 置顶聚焦(不重复 setParent,
        避免重复安装事件过滤器)。关闭通过点击外部(EventFilter)触发。
        """
        if self._import_dropdown.isVisible():
            self._import_dropdown.raise_()
            self._import_dropdown.activateWindow()
            return
        self._import_dropdown.open_below(self.import_dropdown_button, self._exp_id)

    def set_context(self, manager: ProjectManager, exp_id: str, label: str) -> None:
        self.manager = manager
        self._exp_id = exp_id
        self.import_panel.set_context(exp_id)
        self.context_label.setText(f"{label} ({exp_id})" if exp_id else "")
        self.refresh()

    def _on_data_name_edited(self, item) -> None:
        """数据表「名称」列编辑后重命名样品数据(0.2.162-补13)。"""
        if item.column() != 1 or self._loading_table:
            return
        if self.manager is None or self.manager.project is None or not self._exp_id:
            return
        data_item = self.data_table.item(item.row(), 0)
        if data_item is None:
            return
        data_id = data_item.text()
        new_name = item.text().strip()
        entry = self.manager.project.experiment(self._exp_id)
        data_entry = (
            next((d for d in entry.data if d.id == data_id), None) if entry else None
        )
        if data_entry is None or new_name == (getattr(data_entry, "title", "") or ""):
            return
        self.data_rename_requested.emit(self._exp_id, data_id, new_name)
        self.refresh()

    def refresh(self) -> None:
        self.data_table.setRowCount(0)
        if self.manager is None or self.manager.project is None or not self._exp_id:
            return
        exp = self.manager.project.experiment(self._exp_id)
        if exp is None:
            return
        self._loading_table = True
        try:
            for data in _active_data_of(exp):
                row = self.data_table.rowCount()
                self.data_table.insertRow(row)
                self.data_table.setItem(row, 0, QTableWidgetItem(data.id))
                self.data_table.setItem(
                    row, 1, QTableWidgetItem(getattr(data, "title", "") or "")
                )
                self.data_table.setItem(
                    row, 2, QTableWidgetItem(getattr(data, "status", "") or "")
                )
        finally:
            self._loading_table = False

    def _browse(self) -> None:
        self.import_panel.browse_single()

    def _on_batch_add_folder(self) -> None:
        self.import_panel.add_batch_folder()

    @staticmethod
    def _bruker_datasets_under(root: Path) -> list[Path]:
        return ExperimentImportPanel._bruker_datasets_under(root)

    def _on_batch_clear(self) -> None:
        self.import_panel.clear_batch_list()

    def _on_batch_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel.import_batch()

    def _on_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel.import_single()

    def clear_import_form(self) -> None:
        """导入成功后清空导入表单(含下拉面板,0.2.162-补12)。"""
        self.import_panel.clear_import_form()
        if self._import_dropdown is not None:
            self._import_dropdown.panel.clear_import_form()

    def _on_segmented_browse(self) -> None:
        self.import_panel.browse_segmented()

    def _on_segmented_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel.import_segmented()
