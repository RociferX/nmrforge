"""3D 谱查看测试(契约 §10):Spectrum3D 模型 + 独立窗口/面板 3D 模式。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from viewer.spectrum import Spectrum3D, SpectrumAxis


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _axis3(label: str, size: int, carrier: float) -> SpectrumAxis:
    return SpectrumAxis(
        label=label,
        size=size,
        sw_hz=6000.0,
        obs_mhz=600.0,
        carrier_ppm=carrier,
        orig_hz=carrier * 600.0,
    )


def _synthetic3d(shape: tuple[int, int, int] = (4, 6, 8)) -> Spectrum3D:
    """合成 3D 谱:峰位于 (1, 2, 3),三个轴标签 F1/F2/F3。"""
    data = np.zeros(shape, dtype=np.float32)
    data[1, 2, 3] = 500.0
    from scipy.ndimage import gaussian_filter

    data = gaussian_filter(data, sigma=(0.7, 0.7, 0.7))
    axes = [
        _axis3("F1", shape[0], 60.0),
        _axis3("F2", shape[1], 118.0),
        _axis3("F3", shape[2], 4.7),
    ]
    return Spectrum3D(data, axes)


def _write_ft3(path: Path, spectrum3d: Spectrum3D, stream: bool = True) -> None:
    """写 NMRPipe 3D 谱:流文件(FDPIPEFLAG=1)或非流单文件(FDPIPEFLAG=0)。"""
    from nmrglue.fileio import pipe

    axes = spectrum3d.axes
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1 if stream else 0
    dic["FDSIZE"] = axes[2].size
    # 流文件 FDSPECNUM = F2(planes 数由 FDF3SIZE 给定);非流存储为 F1*F2
    dic["FDSPECNUM"] = axes[1].size if stream else axes[0].size * axes[1].size
    dic["FDF3SIZE"] = axes[0].size
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    for index, prefix in enumerate(("FDF1", "FDF2", "FDF3")):
        axis = axes[index]
        dic[prefix + "T"] = axis.size
        dic[prefix + "SW"] = axis.sw_hz
        dic[prefix + "OBS"] = axis.obs_mhz
        dic[prefix + "CAR"] = axis.carrier_ppm
        dic[prefix + "ORIG"] = axis.orig_hz
    pipe.write(
        str(path), dic, spectrum3d.data.astype(np.float32), overwrite=True
    )


def _write_ft3_ordered(
    path: Path, data: np.ndarray, fddimorder: list[float]
) -> None:
    """写带 FDDIMORDER 的 3D 流文件。

    data 为 nmrglue 读回形状 (FDF3SIZE, FDSPECNUM, FDSIZE) 的自然数组
    (轴序 = 存储序反转;FDDIMORDER 记录每轴对应的逻辑维号)。FDF1/FDF2/
    FDF3 分别为逻辑维 1/2/3 的参数块。
    """
    from nmrglue.fileio import pipe

    nz, ny, nx = data.shape
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1
    dic["FDSIZE"] = nx
    dic["FDSPECNUM"] = ny
    dic["FDF3SIZE"] = nz
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    dic["FDDIMORDER"] = [float(v) for v in fddimorder] + [4.0]
    for i, v in enumerate(fddimorder, start=1):
        dic[f"FDDIMORDER{i}"] = float(v)
    blocks = {  # 逻辑维号 -> (核, 点数, SW, OBS, CAR, ORIG)
        1: ("15N", nz, 2189.0, 60.8, 118.0, 100.0 * 60.8),
        2: ("1H", nx, 3000.0, 600.0, 4.7, 6.0 * 600.0),
        3: ("13C", ny, 11300.0, 150.9, 45.0, 40.0 * 150.9),
    }
    for dim in (1, 2, 3):
        prefix = f"FDF{dim}"
        lab, size, sw, obs, car, orig = blocks[dim]
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = sw
        dic[prefix + "OBS"] = obs
        dic[prefix + "CAR"] = car
        dic[prefix + "ORIG"] = orig
        dic[prefix + "LABEL"] = lab
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def test_load_from_ft3_honors_fddimorder_order_231(tmp_path: Path) -> None:
    """0.2.151:ORDER 2 3 1(存储 F2,F3,F1)文件正确映射到逻辑序。

    28.ft3/61.ft3 等真实 NMRPipe 3D 输出即 ORDER 2 3 1(自然数组轴序
    (F1,F3,F2));修复前 viewer 按位置配 FDF1/FDF2/FDF3,数据-轴对应
    错位(轴 1/2 参数块互换且无告警)。
    """
    # 自然数组 (F1=15N, F3=13C, F2=1H):P[z,y,x] = z*100 + y*10 + x
    nz, ny, nx = 2, 4, 6
    P = np.zeros((nz, ny, nx), dtype=np.float32)
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                P[z, y, x] = z * 100 + y * 10 + x
    path = tmp_path / "order231.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(
        path, labels=("N", "H", "C"), nuclei=["15N", "1H", "13C"]
    )
    # 逻辑序 (F1=15N, F2=1H, F3=13C):形状 (2, 6, 4),data[i,j,k]=P[i,k,j]
    assert loaded.data.shape == (nz, nx, ny)
    assert loaded.data[1, 2, 3] == P[1, 3, 2]
    assert [ax.label for ax in loaded.axes] == ["N", "H", "C"]
    assert [ax.size for ax in loaded.axes] == [nz, nx, ny]
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)  # 15N
    assert loaded.axes[1].obs_mhz == pytest.approx(600.0)  # 1H
    assert loaded.axes[2].obs_mhz == pytest.approx(150.9)  # 13C
    # 切片:固定 F3(13C) → 平面 (15N, 1H),数据与逻辑序一致
    sl = loaded.slice(2, 1)
    assert sl.data.shape == (nz, nx)
    assert sl.y_axis.label == "N" and sl.x_axis.label == "H"
    np.testing.assert_allclose(sl.data, loaded.data[:, :, 1])


def test_load_from_ft3_without_metadata_keeps_storage_axes_but_params_correct(
    tmp_path: Path,
) -> None:
    """0.2.151:无 metadata 直接打开时,每数据轴参数按 FDDIMORDER 正确配对。"""
    nz, ny, nx = 2, 4, 6
    P = np.arange(nz * ny * nx, dtype=np.float32).reshape(nz, ny, nx)
    path = tmp_path / "order231_nomd.ft3"
    _write_ft3_ordered(path, P, [2.0, 3.0, 1.0])
    loaded = Spectrum3D.load_from_ft3(path)
    # nmrglue 读回自然数组 (15N, 13C, 1H)
    assert loaded.data.shape == (nz, ny, nx)
    # 轴参数按 FDDIMORDER 配对:轴0=15N(FDF1)、轴1=13C(FDF3)、轴2=1H(FDF2)
    assert loaded.axes[0].obs_mhz == pytest.approx(60.8)
    assert loaded.axes[1].obs_mhz == pytest.approx(150.9)
    assert loaded.axes[2].obs_mhz == pytest.approx(600.0)
