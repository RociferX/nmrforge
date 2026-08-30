"""3D 谱查看控制面板(契约 §10):查看平面 / 第三轴切片滑块(ppm)。

独立控件,可挂到 SpectrumViewer 控制区(SpectrumWindow 与 GUI 谱图面板共用);
切片产物经 :func:`current_spectrum` 返回二维 Spectrum,复用
SpectrumViewer/ContourLayer 绘制(正黑负红、框选缩放/平移/滚轮均保留)。
0.2.133:只保留 slice 模式;投影文件(proj3D 产物 .ft2)由右侧谱图列表
直接点击查看,3D 面板不再提供投影模式。
"""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from viewer.spectrum import Spectrum, Spectrum3D

# (显示名, 被固定/切片的轴下标);查看平面为该轴之外的另两轴
_PLANES = (("F1-F2", 2), ("F1-F3", 1), ("F2-F3", 0))
# 切片轴下标 → 平面下拉项下标
_PLANE_INDEX_BY_SLICE_AXIS = {0: 2, 1: 1, 2: 0}


def _nucleus_of(label: str) -> str:
    """轴标签 → 核名(兼容 1H/15N/13C 与 H/N/C,去 x/y/z 下标)。"""
    text = str(label or "").strip()
    if text[-1:] in ("x", "y", "z") and len(text) > 1:
        text = text[:-1]
    return {"H": "1H", "N": "15N", "C": "13C"}.get(text, text)


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
        layout.setSpacing(4)
        layout.addWidget(QLabel("3D view"))
        # 0.2.148:平面切换按钮 / 切片条 / 切片具体值 一行并排
        row = QHBoxLayout()
        row.setSpacing(12)
        self.plane_combo = QComboBox()
        self.plane_combo.addItems([name for name, _ in _PLANES])
        self.plane_combo.setToolTip("选择查看平面(第三轴用于切片)")
        self.plane_combo.currentIndexChanged.connect(self._on_plane_changed)
        row.addWidget(self.plane_combo)
        self.slice_slider = QSlider(Qt.Orientation.Horizontal)
        self.slice_slider.setToolTip("第三轴切片位置(拖动后松开刷新)")
        # 0.2.199-补10:拖动条加长(最小宽度,细调切片更顺手)
        # 0.2.199-补29dg:最小宽度过大(与固定输入框合计 ~610px)会把右侧
        # 面板列/窗口撑宽,收紧到 140(有拉伸因子,可用宽度内仍可加长)
        self.slice_slider.setMinimumWidth(140)
        self.slice_slider.valueChanged.connect(self._on_slider_value)
        self.slice_slider.sliderReleased.connect(self._emit)
        self.slice_slider.setEnabled(False)
        row.addWidget(self.slice_slider, 1)
        # 0.2.149:切片位置 point / ppm 可直接输入,与滑块三向同步
        self.point_spin = QSpinBox()
        self.point_spin.setPrefix("pt: ")
        self.point_spin.setRange(0, 1)
        self.point_spin.setValue(0)
        self.point_spin.setFixedWidth(110)
        self.point_spin.valueChanged.connect(self._on_point_spin)
        self.point_spin.setEnabled(False)
        row.addWidget(self.point_spin)
        self.ppm_spin = QDoubleSpinBox()
        self.ppm_spin.setSuffix(" ppm")
        self.ppm_spin.setDecimals(3)
        self.ppm_spin.setRange(0.0, 1.0)
        self.ppm_spin.setValue(0.0)
        self.ppm_spin.setFixedWidth(150)
        self.ppm_spin.valueChanged.connect(self._on_ppm_spin)
        self.ppm_spin.setEnabled(False)
        row.addWidget(self.ppm_spin)
        layout.addLayout(row)

    # ------------------------------------------------------------- API
    def _preferred_slice_axis(self) -> int | None:
        """优先切片轴:固定非 1H/13C 的轴(如 15N),剩余两轴即 CH 平面。

        0.2.199-补29bh(用户):三维谱优先显示 CH 平面。轴标签无法构成
        CH(如 HNN 双 15N、泛型 F1/F2/F3)时返回 None 走默认。
        """
        if self._spectrum3d is None:
            return None
        nuclei = [_nucleus_of(a.label) for a in self._spectrum3d.axes]
        fixed = [i for i, n in enumerate(nuclei) if n not in ("1H", "13C")]
        remaining = {n for i, n in enumerate(nuclei) if i not in fixed}
        if len(fixed) == 1 and remaining == {"1H", "13C"}:
            return fixed[0]
        return None

    def set_spectrum3d(self, spectrum3d: Spectrum3D) -> None:
        """绑定 3D 谱并重置默认平面(优先 CH,回退 F1-F2);自动发出重绘。"""
        self._spectrum3d = spectrum3d
        self._slice_axis = self._preferred_slice_axis()
        if self._slice_axis is None:
            self._slice_axis = 2  # 回退默认 F1-F2 平面(固定 F3)
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
        # 0.2.199-补29bh:CH 平面优先(固定非 1H/13C 轴)
        self.plane_combo.setCurrentIndex(
            _PLANE_INDEX_BY_SLICE_AXIS.get(self._slice_axis, 0)
        )
        self._update_slider_range()
        self._update_position_controls()
        self._emit()

    def clear(self) -> None:
        """解除 3D 谱绑定并隐藏面板。"""
        self._spectrum3d = None
        self.slice_slider.setEnabled(False)
        self.slice_slider.setValue(0)
        self.point_spin.setEnabled(False)
        self.point_spin.setValue(0)
        self.ppm_spin.setEnabled(False)
        self.ppm_spin.setValue(0.0)
        self.setVisible(False)

    def current_spectrum(self) -> Spectrum | None:
        """当前切片产物(二维 Spectrum);未绑定 3D 谱返回 None。"""
        if self._spectrum3d is None:
            return None
        spectrum = self._spectrum3d.slice(
            self._slice_axis, self.slice_slider.value()
        )
        # 记录剩余两轴的原始维序(F1=0/F2=1/F3=2),供峰表 F*_shift 映射;
        # 0.2.153:切片可能按横坐标优先级转置,按轴对象回查 3D 维序
        dims: list[int] = []
        for axis in spectrum.axes:
            for i, axis3 in enumerate(self._spectrum3d.axes):
                if axis is axis3:
                    dims.append(i)
                    break
        spectrum.dim_indices = tuple(dims)
        # 0.2.199-补29da:记录切片固定轴/位置(ppm),viewer 只显示本平面峰
        # (避免所有层峰叠加,看起来像在投影上选峰)
        try:
            import numpy as np

            axis3 = self._spectrum3d.axes[self._slice_axis]
            spectrum.slice_axis = int(self._slice_axis)
            spectrum.slice_ppm = float(axis3.ppm_at(self.slice_slider.value()))
            ppm = np.asarray(axis3.ppm, dtype=float)
            diff = np.abs(np.diff(ppm))
            diff = diff[diff > 0]
            spectrum.slice_step_ppm = (
                float(np.median(diff)) if diff.size else 0.0
            )
            # 0.2.199-补29dj:固定轴整条范围(供 viewer 判断峰坐标是否越出
            # 轴范围;原「当前切片 ±10 步」会把几乎所有非本平面峰当越界)
            spectrum.slice_ppm_min = (
                float(np.min(ppm)) if ppm.size else None
            )
            spectrum.slice_ppm_max = (
                float(np.max(ppm)) if ppm.size else None
            )
        except Exception:  # noqa: BLE001 - 轴信息缺失不阻断切片
            spectrum.slice_axis = None
            spectrum.slice_ppm = None
            spectrum.slice_step_ppm = None
            spectrum.slice_ppm_min = None
            spectrum.slice_ppm_max = None
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
        self._update_position_controls()
        self._emit()

    # ------------------------------------------------------------- slots
    def _on_plane_changed(self, index: int) -> None:
        if 0 <= index < len(_PLANES):
            self._slice_axis = _PLANES[index][1]
            self._update_slider_range()
            self._update_position_controls()
            self._emit()

    def _on_slider_value(self, _value: int) -> None:
        self._update_position_controls()

    def _on_point_spin(self, value: int) -> None:
        """输入点序号 -> 同步滑块并刷新切片。"""
        if self._spectrum3d is None or value == self.slice_slider.value():
            return
        self.slice_slider.setValue(value)
        self._emit()

    def _on_ppm_spin(self, value: float) -> None:
        """输入 ppm -> 取最近像素点并刷新切片。"""
        if self._spectrum3d is None:
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        index = axis.index_at(value)
        if index == self.slice_slider.value():
            return
        self.slice_slider.setValue(index)
        self._emit()

    def _update_slider_range(self) -> None:
        if self._spectrum3d is None:
            self.slice_slider.setEnabled(False)
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        self.slice_slider.setRange(0, max(0, axis.size - 1))
        self.slice_slider.setValue(axis.size // 2)
        self.slice_slider.setEnabled(True)
        # 设置范围时可能把当前值钳制到范围边界,
        # 用 QSignalBlocker 避免回写触发滑块跳动
        with QSignalBlocker(self.point_spin), QSignalBlocker(self.ppm_spin):
            self.point_spin.setRange(0, max(0, axis.size - 1))
            self.point_spin.setEnabled(True)
            self.ppm_spin.setRange(
                float(min(axis.ppm)), float(max(axis.ppm))
            )
            self.ppm_spin.setEnabled(True)

    def _update_position_controls(self) -> None:
        """把当前滑块位置同步到 point/ppm 输入框(轴标签作前缀)。"""
        if self._spectrum3d is None:
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        value = self.slice_slider.value()
        with QSignalBlocker(self.point_spin), QSignalBlocker(self.ppm_spin):
            self.point_spin.setValue(value)
            ppm = axis.ppm_at(value) if axis.size else 0.0
            self.ppm_spin.setPrefix(
                f"{axis.label} " if axis.label else ""
            )
            self.ppm_spin.setValue(ppm)

    def _plane_name(self) -> str:
        index = self.plane_combo.currentIndex()
        if 0 <= index < len(_PLANES):
            return _PLANES[index][0]
        return "F1-F2"

    def _emit(self) -> None:
        self.slice_changed.emit()


__all__ = ["Spectrum3DPanel"]
