"""报告页面板:展示 data_dir(..., "report") 下的 html/pdf/json 产物。

与 core/reporting 产物命名对齐(API_CONTRACT §9.2 report/ 目录);存在产物时
内嵌预览(html/json)或经外部打开(pdf),缺失时提示先完成分析。Pipeline
「分析」步骤状态由本目录产物驱动(产物存在 → SUCCESS)。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager

REPORT_EXTS = (".html", ".pdf", ".json")


def report_products(manager: ProjectManager, exp_id: str, data_id: str) -> list[Path]:
    """扫描当前样品数据的 report 目录,返回 html/pdf/json 产物(排序)。"""
    if manager.project is None or not exp_id or not data_id:
        return []
    try:
        report_dir = manager.data_dir(exp_id, data_id, "report")
    except Exception:  # noqa: BLE001 - 布局不可用时按无产物处理
        return []
    if not report_dir.is_dir():
        return []
    return sorted(
        path
        for path in report_dir.iterdir()
        if path.is_file() and path.suffix.lower() in REPORT_EXTS
    )


class ReportPanel(QWidget):
    """报告页:产物列表 + 内嵌预览/外部打开。"""

    def __init__(
        self,
        manager: ProjectManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager or ProjectManager()
        self._exp_id = ""
        self._data_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("报告")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #666;")
        layout.addWidget(self.hint_label)

        self.file_list = QListWidget()
        self.file_list.itemClicked.connect(self._on_select)
        layout.addWidget(self.file_list, 1)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        layout.addWidget(self.preview, 2)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("打开外部")
        self.open_button.setEnabled(False)
        self.open_button.setToolTip("用系统默认应用打开所选报告")
        self.open_button.clicked.connect(self._open_external)
        buttons.addWidget(self.open_button)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._current: Path | None = None

    @property
    def manager(self) -> ProjectManager:
        return self._manager

    @manager.setter
    def manager(self, value: ProjectManager) -> None:
        self._manager = value

    def set_context(self, exp_id: str, data_id: str = "") -> None:
        self._exp_id = exp_id or ""
        self._data_id = data_id or ""
        self.refresh()

    def refresh(self) -> None:
        self.file_list.clear()
        self.preview.clear()
        self._current = None
        self.open_button.setEnabled(False)
        if self._manager.project is None or not self._exp_id or not self._data_id:
            self.hint_label.setText("未选中样品数据——从左侧选择样品数据节点后查看报告")
            self.file_list.setVisible(False)
            self.preview.setVisible(False)
            return
        products = report_products(self._manager, self._exp_id, self._data_id)
        self.file_list.setVisible(True)
        self.preview.setVisible(True)
        if not products:
            self.hint_label.setText(
                "未生成报告\n请先在 Pipeline 中完成「分析」步骤(report/ 目录下"
                "暂无 html/pdf/json 产物)"
            )
            self.file_list.setVisible(False)
            self.preview.setVisible(False)
            return
        self.hint_label.setText(
            f"{self._exp_id} / {self._data_id} — 共 {len(products)} 个报告产物"
        )
        for path in products:
            self.file_list.addItem(path.name)
        self.file_list.setCurrentRow(0)
        self._preview_path(products[0])

    def _on_select(self, item) -> None:
        products = report_products(self._manager, self._exp_id, self._data_id)
        for path in products:
            if path.name == item.text():
                self._preview_path(path)
                return

    def _preview_path(self, path: Path) -> None:
        self._current = path
        self.open_button.setEnabled(True)
        suffix = path.suffix.lower()
        try:
            if suffix == ".html":
                self.preview.setHtml(path.read_text(encoding="utf-8", errors="replace"))
            elif suffix == ".json":
                text = path.read_text(encoding="utf-8", errors="replace")
                self.preview.setPlainText(text)
            else:  # .pdf 等二进制产物:提示外部打开
                self.preview.setPlainText(
                    f"PDF 报告: {path.name}\n\n请点「打开外部」用系统默认应用查看。"
                )
        except OSError as exc:
            self.preview.setPlainText(f"读取失败: {exc}")

    def _open_external(self) -> None:
        if self._current is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current)))

    def current_report_path(self) -> Path | None:
        return self._current
