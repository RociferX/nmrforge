"""主窗口:建立在 ProjectManager 之上。

所有项目数据一律经 core.project 访问(GUI 不直接读写 project.json):
新建/打开/保存/最近项目、实验列表(状态推断)、样本管理入口。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)

from core.project import (
    JsonRecentProjectsStore,
    ProjectError,
    ProjectManager,
)


class MainWindow(QMainWindow):
    """NMRForge 主窗口;未打开项目时实验树为空。"""

    def __init__(
        self,
        manager: ProjectManager | None = None,
        recent: JsonRecentProjectsStore | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager or ProjectManager()
        self.recent = recent or JsonRecentProjectsStore()
        self.setWindowTitle("NMRForge")
        self.resize(980, 620)
        self._build_menus()
        self._build_central()
        self.refresh()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_menus(self) -> None:
        bar = self.menuBar()

        project_menu = bar.addMenu("项目(&P)")
        project_menu.addAction("新建项目...", self.new_project)
        project_menu.addAction("打开项目...", self.open_project)
        save_action = QAction("保存项目", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_project)
        project_menu.addAction(save_action)
        self.recent_menu = QMenu("最近项目", self)
        project_menu.addMenu(self.recent_menu)
        project_menu.addSeparator()
        project_menu.addAction("退出", self.close)

        exp_menu = bar.addMenu("实验(&E)")
        exp_menu.addAction("添加实验...", self.add_experiment)
        exp_menu.addAction("重命名实验...", self.rename_experiment)
        exp_menu.addAction("删除实验", self.delete_experiment)

        sample_menu = bar.addMenu("样本(&S)")
        sample_menu.addAction("添加样本...", self.add_sample)
        sample_menu.addAction("删除样本...", self.delete_sample)

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self.about)

    def _build_central(self) -> None:
        self.experiment_tree = QTreeWidget()
        self.experiment_tree.setColumnCount(5)
        self.experiment_tree.setHeaderLabels(["ID", "标题", "状态", "样本", "来源"])
        self.experiment_tree.setRootIsDecorated(False)
        self.experiment_tree.setAlternatingRowColors(True)
        self.setCentralWidget(self.experiment_tree)

    # ------------------------------------------------------------------
    # 项目动作
    # ------------------------------------------------------------------
    def new_project(self) -> None:
        start = str(Path.home())
        root = QFileDialog.getExistingDirectory(self, "选择新项目目录", start)
        if not root:
            return
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:", text="unnamed")
        if not ok:
            return
        try:
            self.manager = ProjectManager.create_project(root, name.strip() or "unnamed")
        except ProjectError as exc:
            QMessageBox.critical(self, "新建项目失败", str(exc))
            return
        self.recent.push(str(self.manager.root))
        self.refresh()

    def open_project(self) -> None:
        root = QFileDialog.getExistingDirectory(self, "选择项目目录", str(Path.home()))
        if not root:
            return
        self._open_root(Path(root))

    def save_project(self) -> None:
        try:
            self.manager.save()
        except ProjectError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.statusBar().showMessage("项目已保存")

    def _open_root(self, root: Path) -> None:
        try:
            self.manager = ProjectManager.open_project(root)
        except ProjectError as exc:
            QMessageBox.critical(self, "打开项目失败", str(exc))
            return
        self.recent.push(str(self.manager.root))
        self.refresh()

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.recent.list():
            action = self.recent_menu.addAction(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self._open_root(Path(p))
            )

    # ------------------------------------------------------------------
    # 实验/样本动作
    # ------------------------------------------------------------------
    def add_experiment(self) -> None:
        if self.manager.project is None:
            QMessageBox.information(self, "提示", "请先新建或打开项目")
            return
        source, ok = QInputDialog.getText(self, "添加实验", "Bruker 数据集路径:")
        if not ok or not source.strip():
            return
        title, ok = QInputDialog.getText(self, "添加实验", "标题(可留空):", text="")
        if not ok:
            return
        try:
            self.manager.add_experiment(source.strip(), title=title.strip())
            self.manager.save()
        except ProjectError as exc:
            QMessageBox.critical(self, "添加实验失败", str(exc))
            return
        self.refresh()

    def rename_experiment(self) -> None:
        exp_id = self._current_experiment_id()
        if exp_id is None:
            return
        title, ok = QInputDialog.getText(self, "重命名实验", "新标题:")
        if ok:
            self.manager.rename_experiment(exp_id, title)
            self.manager.save()
            self.refresh()

    def delete_experiment(self) -> None:
        exp_id = self._current_experiment_id()
        if exp_id is None:
            return
        answer = QMessageBox.question(
            self,
            "删除实验",
            f"删除实验 {exp_id} 及其产物文件?\n(WorkflowRun 审计记录将保留)",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.delete_experiment(exp_id)
            self.manager.save()
            self.refresh()

    def add_sample(self) -> None:
        if self.manager.project is None:
            QMessageBox.information(self, "提示", "请先新建或打开项目")
            return
        name, ok = QInputDialog.getText(self, "添加样本", "样本名称:")
        if not ok:
            return
        sample = self.manager.add_sample(name=name)
        self.manager.save()
        self.statusBar().showMessage(f"已添加样本 {sample.sample_id}")

    def delete_sample(self) -> None:
        if self.manager.project is None:
            return
        sample_ids = [s.sample_id for s in self.manager.project.samples]
        if not sample_ids:
            QMessageBox.information(self, "提示", "项目中没有样本")
            return
        sample_id, ok = QInputDialog.getItem(
            self, "删除样本", "选择样本:", sample_ids, editable=False
        )
        if not ok:
            return
        try:
            self.manager.delete_sample(sample_id)
            self.manager.save()
        except ProjectError as exc:
            QMessageBox.critical(self, "删除样本失败", str(exc))
            return
        self.refresh()

    def about(self) -> None:
        QMessageBox.about(
            self,
            "关于 NMRForge",
            "NMRForge:面向 Bruker 2D/3D NMR 的自动化处理、参数优化与质量控制平台。\n"
            "项目管理模块(core/project)是 GUI 的地基。",
        )

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def _current_experiment_id(self) -> str | None:
        items = self.experiment_tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def refresh(self) -> None:
        """用 ProjectManager 数据刷新窗口标题、状态栏、实验树与最近项目菜单。"""
        self._refresh_recent_menu()
        tree = self.experiment_tree
        tree.clear()
        project = self.manager.project
        if project is None:
            self.setWindowTitle("NMRForge - 未打开项目")
            self.statusBar().showMessage("新建或打开项目开始工作")
            return
        for exp in project.experiments:
            status = self.manager.infer_status(exp.id).value
            item = QTreeWidgetItem([exp.id, exp.title, status, exp.sample_id, exp.source])
            item.setData(0, Qt.ItemDataRole.UserRole, exp.id)
            tree.addTopLevelItem(item)
        self.setWindowTitle(f"NMRForge - {project.name}")
        self.statusBar().showMessage(f"项目: {self.manager.root}")

    @staticmethod
    def run() -> int:
        """启动 Qt 应用(供 main.py 调用)。"""
        import sys

        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        return app.exec()
