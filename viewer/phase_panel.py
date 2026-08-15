"""交互式相位校正面板(P0/P1 滑块,0.2.86)。

对复型数据(1D FID / 二维时域 FID)在频率域做 P0/P1 相位旋转后显示实部,
等价 nmrDraw 的交互调相;实型终谱(ft2/ft3)无法再事后调相,面板提示禁用。
值可一键复制,供回写人工相位参数或脚本 PS 行。
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addWidget(QLabel("Phase (P0/P1)"))

        self.p0_slider = QSlider(Qt.Orientation.Horizontal)
        self.p0_slider.setRange(-180, 180)
        self.p0_slider.setValue(0)
        self.p0_slider.valueChanged.connect(self._on_p0_changed)
        self.p0_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        self.p0_label = QLabel("P0: 0°")
        layout.addWidget(self.p0_slider)
        layout.addWidget(self.p0_label)

        self.p1_slider = QSlider(Qt.Orientation.Horizontal)
        self.p1_slider.setRange(-180, 180)
        self.p1_slider.setValue(0)
        self.p1_slider.valueChanged.connect(self._on_p1_changed)
        self.p1_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        self.p1_label = QLabel("P1: 0°")
        layout.addWidget(self.p1_slider)
        layout.addWidget(self.p1_label)

        row = QHBoxLayout()
        self.reset_button = QPushButton("Reset")
        self.reset_button.setToolTip("P0/P1 归零")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("复制 P0/P1 值,可粘贴到人工相位参数或脚本 PS 行")
        self.copy_button.clicked.connect(self._copy_values)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #888;")
        layout.addWidget(self.hint_label)
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
            "" if available else (hint or "当前谱图无复型数据,仅 FID/复型谱可交互调相")
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
