"""Bruker 数据读取：定位数据集、读取 ser/fid、结合参数生成内部数据模型。

参考 NMRFlow 的 Bruker 接入：
- 元数据：acqus/acqu2s/acqu3s 为权威参数源，PARMODE + 存在性启发式判断维数；
- 二进制：read_data 读取 ser/fid 为复 FID 矩阵（轴序 F1,F2[,F3]；
  间接维超复数分量作为该轴前导因子，由 core/processing/hypercomplex 合并）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    SamplingMode,
)
from core.experiment.bruker_parser import parse_dataset_params
from core.experiment.experiment_classifier import classify
from core.experiment.sampling_detector import detect


class BrukerDataError(Exception):
    """Bruker 数据读取/校验错误（携带可操作提示）。"""


@dataclass
class DimensionLayout:
    td: int
    mult: int
    fnmode: int
    n_fids: int


@dataclass
class BrukerData:
    matrix: np.ndarray
    layout: dict[str, DimensionLayout]
    data_file: str = ""
    byte_order: str = "little"

    @property
    def layout_summary(self) -> str:
        return ",".join(f"{axis}={d.td}(x{d.mult})" for axis, d in self.layout.items())


def _fnmode(experiment: Experiment, logical_axis: str) -> int:
    if experiment.ndim >= 3:
        mapping = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}
    else:
        mapping = {"F2": "acqus", "F1": "acqu2s"}
    block = experiment.acquisition_parameters.get(mapping.get(logical_axis, ""), {})
    try:
        return int(block.get("FnMODE", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _mult_for(fnmode: int) -> int:
    """超复数分量数：States/TPPI/States-TPPI/Echo-Antiecho 为 2，QF 为 1。"""
    return 2 if fnmode in (0, 1, 2, 4, 5, 6) else 1


def _dim(experiment: Experiment, logical_axis: str) -> Dimension:
    for dim in experiment.dimensions:
        if dim.logical_axis == logical_axis:
            return dim
    raise BrukerDataError(f"数据模型缺少 {logical_axis} 维度")


def _read_complex(path: Path, byterda: int) -> np.ndarray:
    """读取 int32 实虚交错数据为复数组（Bruker DQD 存储）。"""
    dt = np.dtype(("<" if byterda == 0 else ">") + "i4")
    raw = np.fromfile(path, dtype=dt)
    pairs = raw.reshape(-1, 2)
    return pairs[:, 0] + 1j * pairs[:, 1]



def _check_size(path: Path, expected_bytes: int, is_nus: bool) -> None:
    actual = path.stat().st_size
    if actual == expected_bytes:
        return
    if is_nus:
        return  # NUS：FID 数由采样表决定，Phase 3 严格校验
    raise BrukerDataError(
        f"{path.name} 大小不符：实际 {actual} 字节，期望 {expected_bytes} 字节"
        "（TD/维数可能不匹配）"
    )


def read_data(experiment: Experiment) -> BrukerData:
    """读取 Bruker 二进制数据（ser/fid）为复 FID 矩阵。"""
    ndim = experiment.ndim
    data_file = experiment.source_path / ("ser" if ndim >= 2 else "fid")
    if not data_file.is_file():
        raise BrukerDataError(f"缺少数据文件 {data_file.name}: {experiment.source_path}")
    acqus = experiment.acquisition_parameters.get("acqus", {})
    try:
        byterda = int(acqus.get("BYTORDA", 0) or 0)
    except (TypeError, ValueError):
        byterda = 0
    byte_order = "little" if byterda == 0 else "big"
    is_nus = experiment.sampling.mode is SamplingMode.NUS

    if ndim == 1:
        f2 = _dim(experiment, "F2")
        _check_size(data_file, f2.td * 8, is_nus)
        matrix = _read_complex(data_file, byterda).reshape(-1)
        layout = {"F2": DimensionLayout(td=f2.td, mult=1, fnmode=0, n_fids=1)}
        return BrukerData(
            matrix=matrix, layout=layout, data_file=data_file.name, byte_order=byte_order
        )

    if ndim == 2:
        f1 = _dim(experiment, "F1")
        f2 = _dim(experiment, "F2")
        m1 = _mult_for(_fnmode(experiment, "F1"))
        n_fids = f1.td * m1
        points = f2.td
        _check_size(data_file, n_fids * points * 8, is_nus)
        matrix = _read_complex(data_file, byterda).reshape(n_fids, points)
        layout = {
            "F1": DimensionLayout(
                td=f1.td, mult=m1, fnmode=_fnmode(experiment, "F1"), n_fids=n_fids
            ),
            "F2": DimensionLayout(
                td=f2.td, mult=1, fnmode=_fnmode(experiment, "F2"), n_fids=n_fids
            ),
        }
        return BrukerData(
            matrix=matrix, layout=layout, data_file=data_file.name, byte_order=byte_order
        )

    f1 = _dim(experiment, "F1")
    f2 = _dim(experiment, "F2")
    f3 = _dim(experiment, "F3")
    m1 = _mult_for(_fnmode(experiment, "F1"))
    m2 = _mult_for(_fnmode(experiment, "F2"))
    n_f1 = f1.td * m1
    n_f2 = f2.td * m2
    n_fids = n_f1 * n_f2
    points = f3.td
    _check_size(data_file, n_fids * points * 8, is_nus)
    matrix = np.transpose(
        _read_complex(data_file, byterda).reshape(n_f2, n_f1, points),
        (1, 0, 2),
    )
    layout = {
        "F1": DimensionLayout(td=f1.td, mult=m1, fnmode=_fnmode(experiment, "F1"), n_fids=n_f1),
        "F2": DimensionLayout(td=f2.td, mult=m2, fnmode=_fnmode(experiment, "F2"), n_fids=n_f2),
        "F3": DimensionLayout(td=f3.td, mult=1, fnmode=_fnmode(experiment, "F3"), n_fids=n_fids),
    }
    return BrukerData(
        matrix=matrix, layout=layout, data_file=data_file.name, byte_order=byte_order
    )


def _param_float(block: dict, *keys: str, default: float = 0.0) -> float:
    """按顺序取第一个非空数值参数。"""
    for key in keys:
        value = block.get(key)
        if value in (None, "", 0, 0.0):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _detect_ndim(params: dict, acqus: dict) -> int:
    """PARMODE + 存在性启发式：0=1D，1=2D，2=3D。"""
    try:
        parmode = int(acqus.get("PARMODE", 0) or 0)
    except (TypeError, ValueError):
        parmode = 0
    if parmode > 0:
        return parmode + 1
    if "acqu3s" in params:
        return 3
    if "acqu2s" in params:
        return 2
    return 1


def _build_dimensions(params: dict, ndim: int) -> list[Dimension]:
    if ndim >= 3:
        entries = [
            ("F3", "acqus", AxisRole.DIRECT),
            ("F2", "acqu2s", AxisRole.INDIRECT),
            ("F1", "acqu3s", AxisRole.INDIRECT),
        ]
    elif ndim == 2:
        entries = [
            ("F2", "acqus", AxisRole.DIRECT),
            ("F1", "acqu2s", AxisRole.INDIRECT),
        ]
    else:
        entries = [("F2", "acqus", AxisRole.DIRECT)]

    dims: list[Dimension] = []
    for logical, filename, role in entries:
        block = params.get(filename)
        if block is None:
            continue
        sf = _param_float(block, "SFO1")
        o1 = _param_float(block, "O1")
        o1p = _param_float(block, "O1P")
        if not o1p and sf:
            o1p = o1 / sf
        dims.append(
            Dimension(
                logical_axis=logical,
                nucleus=str(block.get("NUC1", "")),
                sf=sf,
                sw=_param_float(block, "SW_h", "SW"),
                o1=o1,
                o1p=o1p,
                td=int(block.get("TD", 0) or 0),
                acquisition_mode=str(block.get("FnMODE", "")),
                axis_direction="increasing",
                role=role,
            )
        )
    return dims


def read_segments(paths: list[Path | str]) -> Experiment:
    """把多个同实验数据集读取为一个多段 Experiment（参数必须一致）。"""
    if len(paths) < 2:
        raise ValueError("多段实验至少需要 2 个数据集目录")
    dirs = [Path(p).resolve() for p in paths]
    base = read_dataset(dirs[0])
    base.segments = dirs

    def _key(exp: Experiment) -> tuple:
        return (
            exp.ndim,
            [d.nucleus for d in exp.dimensions],
            [d.td for d in exp.dimensions],
            [round(d.sw, 6) for d in exp.dimensions],
        )

    for extra in dirs[1:]:
        other = read_dataset(extra)
        if _key(other) != _key(base):
            raise ValueError(
                f"数据段参数不一致：{dirs[0]} vs {extra}（维数/核/TD/谱宽必须一致）"
            )
    if base.sampling.mode is SamplingMode.NUS:
        base.sampling.evidence.append(f"多段：{len(dirs)} 个数据集，采样点合并后由后端生成")
    return base


def read_dataset(path: Path) -> Experiment:
    """读取一个 Bruker 数据集目录并生成 Experiment（元数据，不做语义判断）。"""
    dataset_dir = path
    if not (dataset_dir / "acqus").is_file():
        raise ValueError(f"不是 Bruker 数据集目录（缺少 acqus）：{dataset_dir}")
    params = parse_dataset_params(dataset_dir)
    acqus = params.get("acqus", {})
    ndim = _detect_ndim(params, acqus)
    experiment = Experiment(
        dataset_id=dataset_dir.name,
        source_path=dataset_dir,
        ndim=ndim,
        acquisition_order=[name for name in ("acqus", "acqu2s", "acqu3s") if name in params],
        dimensions=_build_dimensions(params, ndim),
        acquisition_parameters=params,
    )
    experiment.sampling = detect(experiment)
    experiment.experiment_type = classify(experiment)
    return experiment
