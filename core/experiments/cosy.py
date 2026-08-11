"""COSY 实验模板。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE = ExperimentTemplate(
    name="COSY",
    direct_nucleus="1H",
    indirect_nuclei=["1H"],
    expected_peak_mode="absorption",
    display_orientation="1H/1H",
    priors={"1H": (0.0, 12.0)},
    constraints={"ndim": 2},
)

register(TEMPLATE)
