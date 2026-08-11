"""Bruker 数据读取：定位数据集、读取 ser/fid、结合参数生成内部数据模型。"""

from __future__ import annotations

from pathlib import Path

from core.data.internal_data_model import AxisRole, Dimension, Experiment
from core.experiment.bruker_parser import parse_dataset_params
from core.experiment.experiment_classifier import classify
from core.experiment.sampling_detector import detect


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
        dims.append(
            Dimension(
                logical_axis=logical,
                nucleus=str(block.get("NUC1", "")),
                sf=float(block.get("SFO1", 0.0) or 0.0),
                sw=float(block.get("SW", 0.0) or block.get("SW_h", 0.0) or 0.0),
                o1=float(block.get("O1", 0.0) or 0.0),
                o1p=float(block.get("O1P", 0.0) or 0.0),
                td=int(block.get("TD", 0) or 0),
                acquisition_mode=str(block.get("FnMODE", "")),
                axis_direction="increasing",
                role=role,
            )
        )
    return dims


def read_dataset(path: Path) -> Experiment:
    """读取一个 Bruker 数据集目录并生成 Experiment（不做语义判断）。"""
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
