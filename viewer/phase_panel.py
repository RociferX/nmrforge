"""交互式相位校正面板(P0/P1 滑块,0.2.87)。

像 nmrDraw 一样查看一维谱后拖 P0/P1 肉眼看相——仅显示,不改变数据,
实数谱也可用;值可一键复制,供回写人工相位参数或脚本 PS 行。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class PhasePanel(QWidget):
    """P0/P1 相位滑块;``phase_changed(final)`` 调整时发出(final=False 实时,True 松手)。"""

    phase_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._p0 = 0.0
        self._p1 = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        # 0.2.147:P0/P1 一行并排,间隔显明
        row = QHBoxLayout()
        row.setSpacing(12)

        # 0.2.148:P0/P1 数值直接显示在标题后且可输入,与滑块双向同步
        self.p0_label = QDoubleSpinBox()
        self.p0_label.setPrefix("P0: ")
        self.p0_label.setSuffix("°")
        self.p0_label.setRange(-180.0, 180.0)
        self.p0_label.setDecimals(0)
        self.p0_label.setValue(0.0)
        self.p0_label.setFixedWidth(110)
        self.p0_label.valueChanged.connect(self._on_p0_spin_changed)
        self.p0_slider = QSlider(Qt.Orientation.Horizontal)
        self.p0_slider.setRange(-180, 180)
        self.p0_slider.setValue(0)
        self.p0_slider.valueChanged.connect(self._on_p0_changed)
        self.p0_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        row.addWidget(self.p0_label)
        row.addWidget(self.p0_slider, 1)

        self.p1_label = QDoubleSpinBox()
        self.p1_label.setPrefix("P1: ")
        self.p1_label.setSuffix("°")
        self.p1_label.setRange(-180.0, 180.0)
        self.p1_label.setDecimals(0)
        self.p1_label.setValue(0.0)
        self.p1_label.setFixedWidth(110)
        self.p1_label.valueChanged.connect(self._on_p1_spin_changed)
        self.p1_slider = QSlider(Qt.Orientation.Horizontal)
        self.p1_slider.setRange(-180, 180)
        self.p1_slider.setValue(0)
        self.p1_slider.valueChanged.connect(self._on_p1_changed)
        self.p1_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        row.addWidget(self.p1_label)
        row.addWidget(self.p1_slider, 1)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setToolTip("P0/P1 归零")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip(
            "复制 P0/P1 值,可粘贴到人工相位参数或脚本 PS 行"
        )
        self.copy_button.clicked.connect(self._copy_values)
        row.addWidget(self.copy_button)
        outer.addLayout(row)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #888;")
        outer.addWidget(self.hint_label)
        self.set_available(False)



    # ------------------------------------------------------------- API
    def values(self) -> tuple[float, float]:
        """当前 (P0, P1) 度。"""
        return self._p0, self._p1

    def set_values(self, p0: float, p1: float) -> None:
        self.p0_slider.setValue(max(-180, min(180, int(round(p0)))))
        self.p1_slider.setValue(max(-180, min(180, int(round(p1)))))

    def reset(self) -> None:
        self.set_values(0.0, 0.0)
        self.phase_changed.emit(True)

    def set_visible_1d_mode(self, visible: bool) -> None:
        """1D 模式显示相位面板(条带模式亦显示);隐藏时不可交互。"""
        self.setVisible(visible)
        self.set_available(visible)

    def set_available(self, available: bool, hint: str = "") -> None:
        """复型数据可用时启用;实型谱等禁用并提示。"""
        for widget in (
            self.p0_label,
            self.p0_slider,
            self.p1_label,
            self.p1_slider,
            self.reset_button,
            self.copy_button,
        ):
            widget.setEnabled(available)
        self.hint_label.setText(
            ""
            if available
            else (hint or "仅显示调相(不改数据):查看一维谱或开启 1D 条带后可用")
        )

    # ------------------------------------------------------------- slots
    def _on_p0_changed(self, value: int) -> None:
        self._p0 = float(value)
        self.p0_label.setValue(value)
        self.phase_changed.emit(False)

    def _on_p1_changed(self, value: int) -> None:
        self._p1 = float(value)
        self.p1_label.setValue(value)
        self.phase_changed.emit(False)

    def _on_p0_spin_changed(self, value: float) -> None:
        self.p0_slider.setValue(int(round(value)))

    def _on_p1_spin_changed(self, value: float) -> None:
        self.p1_slider.setValue(int(round(value)))

    def _copy_values(self) -> None:
        QApplication.clipboard().setText(f"P0={self._p0:.0f} P1={self._p1:.0f}")


__all__ = ["PhasePanel"]
