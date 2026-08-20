"""3D 谱查看控制面板(契约 §10):查看平面 / 第三轴切片滑块(ppm)。

独立控件,可挂到 SpectrumViewer 控制区(SpectrumWindow 与 GUI 谱图面板共用);
切片产物经 :func:`current_spectrum` 返回二维 Spectrum,复用
SpectrumViewer/ContourLayer 绘制(正黑负红、框选缩放/平移/滚轮均保留)。
0.2.133:只保留 slice 模式;投影文件(proj3D 产物 .ft2)由右侧谱图列表
直接点击查看,3D 面板不再提供投影模式。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from viewer.spectrum import Spectrum, Spectrum3D

# (显示名, 被固定/切片的轴下标);查看平面为该轴之外的另两轴
_PLANES = (("F1-F2", 2), ("F1-F3", 1), ("F2-F3", 0))


class Spectrum3DPanel(QWidget):
    """3D 谱控制:平面/切片滑块,变化后发 slice_changed(调用方重绘)。"""

    slice_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spectrum3d: Spectrum3D | None = None
        self._slice_axis = 2  # 默认 F1-F2 平面(固定 F3)
        self._mode = "slice"  # 0.2.133:仅切片模式

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("3D view"))
        self.plane_combo = QComboBox()
        self.plane_combo.addItems([name for name, _ in _PLANES])
        self.plane_combo.setToolTip("选择查看平面(第三轴用于切片)")
        self.plane_combo.currentIndexChanged.connect(self._on_plane_changed)
        layout.addWidget(self.plane_combo)
        self.slice_slider = QSlider(Qt.Orientation.Horizontal)
        self.slice_slider.setToolTip("第三轴切片位置(拖动后松开刷新)")
        self.slice_slider.valueChanged.connect(self._on_slider_value)
        self.slice_slider.sliderReleased.connect(self._emit)
        self.slice_slider.setEnabled(False)
        layout.addWidget(self.slice_slider)
        self.position_label = QLabel("Slice: -")
        self.position_label.setWordWrap(True)
        layout.addWidget(self.position_label)

    # ------------------------------------------------------------- API
    def set_spectrum3d(self, spectrum3d: Spectrum3D) -> None:
        """绑定 3D 谱并重置到默认平面(F1-F2, 切片);自动发出重绘。"""
        self._spectrum3d = spectrum3d
        self._slice_axis = 2
        self._mode = "slice"
        noise = spectrum3d.estimate_noise()
        self._proj_thresh = (
            3.0 * noise
            if noise > 0
            else 0.01 * max(spectrum3d.max_intensity, 1.0)
        )
        # 平面下拉项用核名(如 N-H / N-C / H-C);下标由轴标签决定
        for index, (_, axis) in enumerate(_PLANES):
            remaining = [i for i in range(3) if i != axis]
            name = "-".join(self._spectrum3d.axes[i].label for i in remaining)
            self.plane_combo.setItemText(index, name)
        self.plane_combo.setCurrentIndex(0)
        self._update_slider_range()
        self._update_position_label()
        self._emit()

    def clear(self) -> None:
        """解除 3D 谱绑定并隐藏面板。"""
        self._spectrum3d = None
        self.slice_slider.setEnabled(False)
        self.position_label.setText("Slice: -")
        self.setVisible(False)

    def current_spectrum(self) -> Spectrum | None:
        """当前切片产物(二维 Spectrum);未绑定 3D 谱返回 None。"""
        if self._spectrum3d is None:
            return None
        spectrum = self._spectrum3d.slice(
            self._slice_axis, self.slice_slider.value()
        )
        # 记录剩余两轴的原始维序(F1=0/F2=1/F3=2),供峰表 F*_shift 映射
        spectrum.dim_indices = tuple(
            i for i in range(3) if i != self._slice_axis
        )
        return spectrum

    def current_name(self, base: str = "") -> str:
        """产物显示名:例如 'hsqc3d N-H 切片'。"""
        plane = self._plane_name()
        if not base and self._spectrum3d is not None and self._spectrum3d.source:
            base = self._spectrum3d.source.stem
        return f"{base or '3D'} {plane} 切片"

    def slice_axis_label(self) -> str:
        """当前被固定(切片)轴的标签。"""
        if self._spectrum3d is None:
            return ""
        return self._spectrum3d.axes[self._slice_axis].label

    def refresh(self) -> None:
        """重新计算产物并发出重绘(滑块拖动后/外部触发)。"""
        self._update_position_label()
        self._emit()

    # ------------------------------------------------------------- slots
    def _on_plane_changed(self, index: int) -> None:
        if 0 <= index < len(_PLANES):
            self._slice_axis = _PLANES[index][1]
            self._update_slider_range()
            self._update_position_label()
            self._emit()

    def _on_slider_value(self, _value: int) -> None:
        self._update_position_label()

    def _update_slider_range(self) -> None:
        if self._spectrum3d is None:
            self.slice_slider.setEnabled(False)
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        self.slice_slider.setRange(0, max(0, axis.size - 1))
        self.slice_slider.setValue(axis.size // 2)
        self.slice_slider.setEnabled(True)

    def _update_position_label(self) -> None:
        if self._spectrum3d is None:
            self.position_label.setText("Slice: -")
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        value = self.slice_slider.value()
        ppm = axis.ppm_at(value) if axis.size else 0.0
        self.position_label.setText(
            f"{axis.label} slice: {ppm:.3f} ppm (point {value}/{axis.size - 1})"
        )

    def _plane_name(self) -> str:
        index = self.plane_combo.currentIndex()
        if 0 <= index < len(_PLANES):
            return _PLANES[index][0]
        return "F1-F2"

    def _emit(self) -> None:
        self.slice_changed.emit()


__all__ = ["Spectrum3DPanel"]
