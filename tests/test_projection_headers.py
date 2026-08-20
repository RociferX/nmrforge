"""3D 投影输出头重写与投影命名测试(0.2.133-B)。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.projection_headers import (
    fixed_nucleus_for,
    rewrite_projection_headers,
    source_axis_table,
)
from workflow.stepwise import projection_filename


def _src_dic(
    n1: int = 256, n2: int = 750, n3: int = 64
) -> dict:
    """源 3D 头:FDSPECNUM↔FDF1、FDSIZE↔FDF2、FDF3SIZE↔FDF3(实测约定)。"""
    import nmrglue as ng

    dic = {k: "0" for k in ng.fileio.pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDSPECNUM"] = float(n1)
    dic["FDSIZE"] = float(n2)
    dic["FDF3SIZE"] = float(n3)
    for pre, label, obs, car, orig, sw in (
        ("FDF1", "15N", 60.818, 117.986, 6115.307, 2189.142),
        ("FDF2", "1H", 600.133, 4.696, 3602.677, 3001.729),
        ("FDF3", "13C", 150.909, 38.996, 272.927, 11312.218),
    ):
        dic[pre + "LABEL"] = label
        dic[pre + "OBS"] = obs
        dic[pre + "CAR"] = car
        dic[pre + "ORIG"] = orig
        dic[pre + "SW"] = sw
    return dic


def _write_ft2(path: Path, nrow: int, ncol: int, src: dict) -> tuple[dict, np.ndarray]:
    """写一个 2D 输出文件(头故意用源平面 15N/1H 的错误标签)。"""
    import nmrglue as ng

    dic = {k: "0" for k in ng.fileio.pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSPECNUM"] = float(nrow)
    dic["FDSIZE"] = float(ncol)
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF1LABEL"] = str(src.get("FDF1LABEL"))
    dic["FDF1OBS"] = src.get("FDF1OBS")
    dic["FDF1CAR"] = src.get("FDF1CAR")
    dic["FDF1ORIG"] = src.get("FDF1ORIG")
    dic["FDF1SW"] = src.get("FDF1SW")
    dic["FDF2LABEL"] = str(src.get("FDF2LABEL"))
    dic["FDF2OBS"] = src.get("FDF2OBS")
    dic["FDF2CAR"] = src.get("FDF2CAR")
    dic["FDF2ORIG"] = src.get("FDF2ORIG")
    dic["FDF2SW"] = src.get("FDF2SW")
    data = np.zeros((nrow, ncol), dtype=np.float32)
    data[1, 2] = 1.0
    ng.pipe.write(str(path), dic, data, overwrite=True)
    return dic, data


def test_source_axis_table_maps_sizes() -> None:
    table = source_axis_table(_src_dic(256, 750, 64))
    assert set(table) == {256, 750, 64}
    assert table[256]["prefix"] == "FDF1"
    assert table[750]["prefix"] == "FDF2"
    assert table[64]["prefix"] == "FDF3"
    assert table[256]["params"]["LABEL"] == "15N"
    assert table[750]["params"]["SW"] == 3001.729


def test_source_axis_table_missing_size_ignored() -> None:
    dic = _src_dic()
    dic["FDF3SIZE"] = 0
    table = source_axis_table(dic)
    assert set(table) == {256, 750}


def test_rewrite_stream_layout(tmp_path: Path) -> None:
    """生产终谱流切分几何:xy=(F3,F1), xz=(F2,F1), yz=(F2,F3)(实测)。"""
    src = _src_dic(256, 750, 64)
    outputs = {
        "xy": tmp_path / "xy.ft2",
        "xz": tmp_path / "xz.ft2",
        "yz": tmp_path / "yz.ft2",
    }
    for tag, (r, c) in {"xy": (64, 256), "xz": (750, 256), "yz": (750, 64)}.items():
        _write_ft2(outputs[tag], r, c, src)
    nuclei = rewrite_projection_headers(outputs, src)
    import nmrglue as ng

    shapes = {"xy": (64, 256), "xz": (750, 256), "yz": (750, 64)}
    expect = {
        "xy": ("13C", "15N"),
        "xz": ("1H", "15N"),
        "yz": ("1H", "13C"),
    }
    src_axis = {"xy": "FDF3", "xz": "FDF2", "yz": "FDF2"}
    for tag, (f1, f2) in expect.items():
        dic, data = ng.pipe.read(str(outputs[tag]))
        assert nuclei[tag] == [f1, f2]
        assert str(dic["FDF1LABEL"]) == f1
        assert str(dic["FDF2LABEL"]) == f2
        assert float(dic["FDSPECNUM"]) == data.shape[0]
        assert float(dic["FDSIZE"]) == data.shape[1]
        assert float(dic["FDDIMCOUNT"]) == 2.0
        assert float(dic["FDF3SIZE"]) == 0.0
        assert np.asarray(data).shape == shapes[tag]
        # 各轴参数来自源谱对应轴(float32 精度内)
        assert float(dic["FDF1SW"]) == pytest.approx(
            float(src[src_axis[tag] + "SW"]), abs=1e-3
        )


def test_rewrite_legacy_plane_layout(tmp_path: Path) -> None:
    """旧式平面集几何:xy=(F1,F2), xz=(F3,F2), yz=(F3,F1)。"""
    src = _src_dic(256, 676, 64)
    src["FDF2SW"] = 2705.558
    outputs = {
        "xy": tmp_path / "xy.ft2",
        "xz": tmp_path / "xz.ft2",
        "yz": tmp_path / "yz.ft2",
    }
    for tag, (r, c) in {"xy": (256, 676), "xz": (64, 676), "yz": (64, 256)}.items():
        _write_ft2(outputs[tag], r, c, src)
    nuclei = rewrite_projection_headers(outputs, src)
    import nmrglue as ng

    expect = {
        "xy": ("15N", "1H"),
        "xz": ("13C", "1H"),
        "yz": ("13C", "15N"),
    }
    for tag, (f1, f2) in expect.items():
        dic, _data = ng.pipe.read(str(outputs[tag]))
        assert nuclei[tag] == [f1, f2]
        assert str(dic["FDF1LABEL"]) == f1
        assert str(dic["FDF2LABEL"]) == f2


def test_rewrite_ambiguous_sizes_keeps_layout(tmp_path: Path) -> None:
    """退化尺寸(两轴点数相同)无法唯一匹配:保持原头,仅修尺寸计数。"""
    src = _src_dic(64, 128, 64)
    out = tmp_path / "xy.ft2"
    _write_ft2(out, 64, 128, src)
    nuclei = rewrite_projection_headers({"xy": out}, src)
    import nmrglue as ng

    dic, data = ng.pipe.read(str(out))
    assert str(dic["FDF1LABEL"]) == "15N"  # 原头未动
    assert float(dic["FDSPECNUM"]) == 64.0
    assert float(dic["FDSIZE"]) == 128.0
    assert nuclei["xy"] == ["15N", "1H"]


def test_fixed_nucleus_for() -> None:
    src_labels = ["15N", "1H", "13C"]
    nuclei = {"xy": ["13C", "15N"], "xz": ["1H", "15N"], "yz": ["1H", "13C"]}
    assert fixed_nucleus_for("xy", nuclei, src_labels) == "1H"
    assert fixed_nucleus_for("xz", nuclei, src_labels) == "13C"
    assert fixed_nucleus_for("yz", nuclei, src_labels) == "15N"
    assert fixed_nucleus_for("xy", {"xy": []}, src_labels) == ""


@pytest.mark.parametrize(
    ("nuclei", "logical", "tag", "expected"),
    [
        (["15N", "1H"], "F2", "xy", "d_001_15N-1H.ft2"),
        (["13C", "15N"], "F1", "yz", "d_001_13C-15N.ft2"),
        (["C13", "HN"], "F3", "xz", "d_001_C13-HN.ft2"),
        (None, "F2", "xy", "d_001_proj_F2.ft2"),
        (["15N"], "F3", "xz", "d_001_proj_F3.ft2"),
        (["1H", ""], "F2", "yz", "d_001_proj_F2.ft2"),
    ],
)
def test_projection_filename(
    nuclei: list[str] | None, logical: str, tag: str, expected: str
) -> None:
    assert projection_filename("d_001", nuclei, logical, tag) == expected
