"""1D 处理回归:预设/分类/计划/脚本/路由(补29gj)。"""

from __future__ import annotations

from pathlib import Path

from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    Sampling,
    SamplingMode,
)


def _experiment_1d(nucleus: str = "1H") -> Experiment:
    return Experiment(
        dataset_id="d_001",
        source_path=Path("/fake/bruker/1d"),
        ndim=1,
        dimensions=[
            Dimension(
                logical_axis="F2",
                nucleus=nucleus,
                td=1024,
                role=AxisRole.DIRECT,
            )
        ],
        sampling=Sampling(mode=SamplingMode.UNIFORM),
    )


def test_preset_options_include_1d() -> None:
    """1D 预设已注册并进入类型选项(Generic1D 不下拉)。"""
    from gui.notes import experiment_type_options

    options = experiment_type_options(1)
    assert {"1H-1D", "13C-1D", "31P-1D", "19F-1D"} <= set(options)
    assert "Generic1D" not in options
    assert not experiment_type_options(0)


def test_classify_1d_1h() -> None:
    """1D 1H 数据唯一命中 1H-1D(置信 0.95)。"""
    from core.experiment.experiment_classifier import classify

    result = classify(_experiment_1d("1H"))
    assert result.name == "1H-1D"
    assert result.confidence >= 0.9


def test_classify_1d_unknown_uses_generic_1d() -> None:
    """1D 未知核组合回退 generic_1d(不再误入 generic_2d)。"""
    from core.experiment.experiment_classifier import classify

    result = classify(_experiment_1d("29Si"))
    assert result.name == "generic_1d"
    assert result.confidence <= 0.4


def test_plan_1d_only_direct_axis() -> None:
    """1D 处理计划只含 F2(直接维)节点,无 F1/TP。"""
    from core.planning.method_selector import select_method

    plan = select_method(_experiment_1d())
    ids = plan.dag.execution_order()
    assert ids
    assert all(node_id.endswith("_F2") for node_id in ids)


def test_process_script_1d() -> None:
    """1D NMRPipe 脚本:单链 FT/PS/POLY,无 TP 转置,输出 .ft1。"""
    from backend.script_generator import generate_process_script
    from core.planning.method_selector import select_method

    experiment = _experiment_1d()
    plan = select_method(experiment)
    script = generate_process_script(
        experiment,
        plan,
        in_file="d_001.fid",
        out_file="d_001.ft1",
        extract=False,
    )
    lines = [ln for ln in script.splitlines() if ln.strip()]
    head = next(ln for ln in lines if ln.startswith("xyz2pipe"))
    assert head.startswith("xyz2pipe -in d_001.fid -x")
    assert any("FT" in ln for ln in lines)
    assert any("PS -p0" in ln for ln in lines)
    assert any("POLY -auto" in ln for ln in lines)
    assert not any("TP" in ln for ln in lines)
    assert lines[-1].strip() == "| pipe2xyz -out d_001.ft1 -x"


def test_default_phase_route_1d_is_none() -> None:
    """1D 默认 phase_route=none(直连 process);2D 仍 unified。"""
    from workflow.stepwise import _default_phase_route

    assert _default_phase_route(_experiment_1d()) == "none"
    two_d = Experiment(
        dataset_id="d_002",
        source_path=Path("/fake/bruker/2d"),
        ndim=2,
        dimensions=[
            Dimension(
                logical_axis="F1",
                nucleus="15N",
                td=128,
                role=AxisRole.INDIRECT,
            ),
            Dimension(
                logical_axis="F2",
                nucleus="1H",
                td=1024,
                role=AxisRole.DIRECT,
            ),
        ],
        sampling=Sampling(mode=SamplingMode.UNIFORM),
    )
    assert _default_phase_route(two_d) == "unified"
