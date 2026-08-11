"""Phase 1 数据理解：读取 / 采样检测 / 采集模式 / 分类 / 轴映射。"""

from __future__ import annotations

from pathlib import Path

from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import SamplingMode
from core.experiment.acquisition_mode_detector import detect_modes, ft_alt_for
from core.experiment.dimension_mapper import map_dimensions


def test_read_hsqc_2d_uniform(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    assert exp.ndim == 2
    assert exp.sampling.mode is SamplingMode.UNIFORM
    assert exp.experiment_type.name == "HSQC"
    assert exp.experiment_type.confidence >= 0.9
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.nucleus == "1H"
    assert direct.td == 2048
    f1 = next(d for d in exp.dimensions if d.logical_axis == "F1")
    assert f1.nucleus == "15N"
    assert f1.td == 256


def test_read_nus_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_2d")
    assert exp.sampling.mode is SamplingMode.NUS
    assert exp.sampling.sampling_fraction == 0.25
    assert exp.sampling.nus_list
    assert exp.sampling.confidence >= 0.9


def test_read_hnca_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    assert exp.ndim == 3
    assert exp.experiment_type.name == "HNCA"
    nuclei = {d.logical_axis: d.nucleus for d in exp.dimensions}
    assert nuclei == {"F1": "13C", "F2": "15N", "F3": "1H"}


def test_read_unknown_2d_generic(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "unknown_2d")
    assert exp.experiment_type.name == "generic_2d"
    assert exp.experiment_type.confidence < 0.6


def test_detect_modes_and_ft_alt(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    modes = detect_modes(exp)
    assert modes == {"F1": "Echo-Antiecho", "F2": "States-TPPI", "F3": "States"}
    assert ft_alt_for(5) is True
    assert ft_alt_for(4) is False


def test_map_dimensions_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    mappings = map_dimensions(exp)
    by_axis = {m.processing_axis: m for m in mappings}
    assert by_axis["F2"].acquisition_axis == 1
    assert by_axis["F1"].acquisition_axis == 2
    assert by_axis["F2"].display_axis == "X"
    assert by_axis["F1"].display_axis == "Y"


def test_map_dimensions_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    mappings = map_dimensions(exp)
    by_axis = {m.processing_axis: m for m in mappings}
    assert by_axis["F1"].display_axis == "X"  # 13C
    assert by_axis["F2"].display_axis == "Y"  # 15N
    assert by_axis["F3"].display_axis == "Z"  # 1H
