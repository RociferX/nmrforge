"""交互式相位校正面板(P0/P1 滑块,0.2.87)。

像 nmrDraw 一样查看一维谱后拖 P0/P1 肉眼看相——仅显示,不改变数据,
实数谱也可用;值可一键复制,供回写人工相位参数或脚本 PS 行。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
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

        self.p0_slider = QSlider(Qt.Orientation.Horizontal)
        self.p0_slider.setRange(-180, 180)
        self.p0_slider.setValue(0)
        self.p0_slider.valueChanged.connect(self._on_p0_changed)
        self.p0_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        self.p0_label = QLabel("P0: 0°")
        row.addWidget(QLabel("P0"))
        row.addWidget(self.p0_slider, 1)
        row.addWidget(self.p0_label)

        self.p1_slider = QSlider(Qt.Orientation.Horizontal)
        self.p1_slider.setRange(-180, 180)
        self.p1_slider.setValue(0)
        self.p1_slider.valueChanged.connect(self._on_p1_changed)
        self.p1_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        self.p1_label = QLabel("P1: 0°")
        row.addWidget(QLabel("P1"))
        row.addWidget(self.p1_slider, 1)
        row.addWidget(self.p1_label)

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
            self.p0_slider,
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
        self.p0_label.setText(f"P0: {value}°")
        self.phase_changed.emit(False)

    def _on_p1_changed(self, value: int) -> None:
        self._p1 = float(value)
        self.p1_label.setText(f"P1: {value}°")
        self.phase_changed.emit(False)

    def _copy_values(self) -> None:
        QApplication.clipboard().setText(f"P0={self._p0:.0f} P1={self._p1:.0f}")


__all__ = ["PhasePanel"]
