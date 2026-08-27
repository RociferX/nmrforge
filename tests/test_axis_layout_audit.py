"""相位优化维度解析审计回归测试(0.2.199-补29)。

实证基准(sampleB + sampleJ 手工切片):
- NMRPipe 单文件 3D 输出布局 = (F2, F1, F3),2D = (F1, F2);
- 基线优化读的是输出布局谱文件,必须用 file_axis_index(旧代码用内部
  约定 axis_index,3D 下 F1/F2 互换,会拿 F2 数据给 F1 选配置);
- NUS 重构平面 3D 堆叠 = (F1 时, F2 时, F3),F1 窗作用轴 0;
- 头部 SW 取轴须按核标签匹配(头部 FDF1=15N/FDF2=1H/FDF3=13C,
  与逻辑轴号 F2/F3/F1 不同)。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    Sampling,
    SamplingMode,
)
from core.processing.axes import axis_index, file_axis_index
from workflow.baseline_optimize import optimize_baseline
from workflow.window_optimize import _axis_sw, _load_recon_planes, _nus_axis_map


def _exp_3d() -> Experiment:
    return Experiment(
        dataset_id="exp3d",
        source_path=Path("/fake/3d"),
        ndim=3,
        dimensions=[
            Dimension(logical_axis="F3", nucleus="1H", td=600, role=AxisRole.DIRECT),
            Dimension(logical_axis="F2", nucleus="15N", td=30, role=AxisRole.INDIRECT),
            Dimension(logical_axis="F1", nucleus="13C", td=75, role=AxisRole.INDIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.NUS),
    )


def _write_ft3(path: Path, data: np.ndarray) -> None:
    from nmrglue.fileio import pipe

    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDPIPEFLAG"] = 1  # 3D 数据流单文件:find_shape 需 PIPE 标记才返回 3D
    dic["FDSIZE"] = float(data.shape[2])  # x(最快)
    dic["FDSPECNUM"] = float(data.shape[1])  # y
    dic["FDF3SIZE"] = float(data.shape[0])  # z(最慢)
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDF3QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2", "FDF3"):
        dic[prefix + "SW"] = "6000.0"
        dic[prefix + "OBS"] = "600.0"
        dic[prefix + "CAR"] = "4.7"
        dic[prefix + "ORIG"] = "1000.0"
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def test_file_axis_index_matches_output_layout() -> None:
    """生产布局映射:2D (F1,F2);3D (F2,F1,F3)。"""
    assert file_axis_index("F1", 2) == 0
    assert file_axis_index("F2", 2) == 1
    assert file_axis_index("F2", 3) == 0
    assert file_axis_index("F1", 3) == 1
    assert file_axis_index("F3", 3) == 2
    # 内部约定 3D 与生产布局 F1/F2 相反(这正是旧基线优化评错轴的根源)
    assert axis_index("F1", 3) == 0
    assert axis_index("F2", 3) == 1


def test_baseline_optimize_3d_uses_output_layout(tmp_path: Path) -> None:
    """3D 输出布局 (F2,F1,F3):漂移沿 F2(轴 0)时 F2 被选校正,F1 保持 off。

    旧代码用内部约定 axis_index(F1→0,F2→1),会把 F2 的漂移算到 F1 头上
    (F2 键拿到轴 1 的平坦数据 → 误保持 off)。
    """
    rng = np.random.default_rng(7)
    # 生产布局 (F2=16, F1=20, F3=3):F3 短轴视为无基线问题;
    # 漂移只沿 F2(轴 0);F1(轴 1)无漂移
    spec = np.zeros((16, 20, 3))
    spec += np.linspace(-40.0, 40.0, 16)[:, np.newaxis, np.newaxis]
    for i, j in ((3, 5), (9, 12), (12, 4), (5, 15)):
        spec[i, j, 0] += 3000.0
        spec[i, j + 1, 0] += 1500.0
    spec += rng.normal(0.0, 0.3, spec.shape)
    ft3 = tmp_path / "spec.ft3"
    _write_ft3(ft3, spec)
    result = optimize_baseline(_exp_3d(), ft3)
    assert result.baseline["F2"]["enabled"] is True, result.logs
    assert result.baseline["F1"]["enabled"] is False, result.logs
    assert result.baseline["F3"]["enabled"] is False, result.logs


def test_nus_axis_map_3d_matches_recon_stack() -> None:
    """3D 重构平面堆叠 (F1 时, F2 时, F3) → F1=0,F2=1。"""
    assert _nus_axis_map(_exp_3d()) == {"F1": 0, "F2": 1}


def test_axis_sw_matches_header_label(tmp_path: Path) -> None:
    """头部 FDF1=15N/FDF2=1H/FDF3=13C:按核标签取 SW,不按逻辑轴号。"""
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic["FDF1LABEL"] = "15N"
    dic["FDF1SW"] = 2189.14
    dic["FDF2LABEL"] = "1H"
    dic["FDF2SW"] = 8196.72
    dic["FDF3LABEL"] = "13C"
    dic["FDF3SW"] = 11312.22
    exp = _exp_3d()
    assert abs(_axis_sw(dic, "F2", exp) - 2189.14) < 1e-2  # 15N(float32)
    assert abs(_axis_sw(dic, "F1", exp) - 11312.22) < 1e-2  # 13C
    assert abs(_axis_sw(dic, "F3", exp) - 8196.72) < 1e-2  # 1H


def _write_2d_recon(path: Path, data: np.ndarray) -> None:
    """写真实 2D recon.ft1 布局文件(F2 频, F1 时复型在最后轴)。

    与 VM 实测(sampleF 制造的 recon.ft1)一致:FDF2QUADFLAG=1、
    FDTRANSPOSED=1、FDQUADFLAG=0,实型存储为 (F2, 2×F1)
    (实部块+虚部块),nmrglue 读回 (F2, F1) complex。
    """
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic.update(
        {
            "FDDIMCOUNT": 2.0,
            "FDPIPEFLAG": 0.0,
            "FDSIZE": float(data.shape[1]),
            "FDSPECNUM": float(data.shape[0]),
            "FDQUADFLAG": 0.0,
            "FDF1QUADFLAG": 0.0,
            "FDF2QUADFLAG": 1.0,
            "FDTRANSPOSED": 1.0,
            "FDF1LABEL": "15N",
            "FDF1SW": 2920.0,
            "FDF2LABEL": "1H",
            "FDF2SW": 19230.77,
        }
    )
    ng.pipe.write(str(path), dic, data.astype(np.complex64), overwrite=True)


def test_load_recon_planes_2d_keeps_complex_shape(tmp_path: Path) -> None:
    """2D recon.ft1 布局 (F2 频, F1 时) 复型:两个读取器都不砍直接维。

    0.2.199-补29b 实证:nmrglue 对真实 2D recon 直接返回复型,
    read_pipe_complex 无条件拆轴 0 会把直接维砍半(旧 bug)。
    """
    rng = np.random.default_rng(9)
    data = rng.normal(size=(16, 8)) + 1j * rng.normal(size=(16, 8))
    recon = tmp_path / "nus2d"
    recon.mkdir()
    _write_2d_recon(recon / "recon.ft1", data)
    exp = _exp_3d()
    exp.ndim = 2
    exp.dimensions = exp.dimensions[1:]
    planes_w, _dic = _load_recon_planes(tmp_path, exp)
    assert planes_w.shape == (16, 8)
    assert np.iscomplexobj(planes_w)
    from workflow.phase_routes import _load_recon_planes as routes_load

    planes_r = routes_load(exp, tmp_path)
    assert planes_r.shape == (16, 8)
    # 内容与写出的复型数据一致(不是被拆错的 (8, 8))
    assert np.allclose(np.abs(planes_w), np.abs(data), atol=1e-5)


def test_load_recon_planes_3d_raw_and_count(tmp_path: Path) -> None:
    """3D 平面:原样实型堆叠(不拆包 hypercomplex)+ 按 FDFILECOUNT 截断陈旧文件。"""
    import nmrglue as ng

    work = tmp_path
    plane_dir = work / "nus3d_rc"
    plane_dir.mkdir()
    rng = np.random.default_rng(3)
    n0, n1 = 8, 5
    for i in range(1, 7):  # 4 个有效 + 2 个陈旧
        arr = rng.normal(size=(n0, n1)).astype(np.float32)
        dic = ng.pipe.create_empty_dic()
        dic.update(
            {
                "FDDIMCOUNT": 3.0,
                "FDPIPEFLAG": 0.0,
                "FDSIZE": float(n1),
                "FDSPECNUM": float(n0),
                "FDQUADFLAG": 1.0,
                "FDF1QUADFLAG": 1.0,
                "FDF2QUADFLAG": 1.0,
                "FDFILECOUNT": 4.0,
                "FDF1LABEL": "15N",
                "FDF1SW": 2189.14,
                "FDF2LABEL": "1H",
                "FDF2SW": 8196.72,
                "FDF3LABEL": "13C",
                "FDF3SW": 11312.22,
            }
        )
        ng.pipe.write(
            str(plane_dir / f"test{i:04d}.ft1"), dic, arr, overwrite=True
        )
    loaded, dic = _load_recon_planes(work, _exp_3d())
    assert loaded is not None
    assert loaded.shape == (n0, n1, 4), loaded.shape  # 只读 4 个,不拆包
    assert not np.iscomplexobj(loaded)
    assert abs(float(dic["FDF3SW"]) - 11312.22) < 1e-2
