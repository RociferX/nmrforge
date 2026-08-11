"""内部数据模型基础行为。"""

from __future__ import annotations

from pathlib import Path

from core.data.internal_data_model import AxisRole, Experiment, SamplingMode


def test_direct_dimension_property(hsqc_experiment: Experiment) -> None:
    direct = hsqc_experiment.direct_dimension
    assert direct is not None
    assert direct.nucleus == "1H"
    assert direct.role is AxisRole.DIRECT


def test_experiment_defaults() -> None:
    exp = Experiment(dataset_id="x", source_path=Path("/tmp/x"))
    assert exp.ndim == 2
    assert exp.sampling.mode is SamplingMode.UNIFORM
    assert exp.experiment_type.name == "generic_2d"
