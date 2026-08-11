"""HMQC 实验模板。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE = ExperimentTemplate(
    name="HMQC",
    direct_nucleus="1H",
    indirect_nuclei=["15N"],
    expected_peak_mode="absorption",
    display_orientation="1H/15N",
    priors={"1H": (6.0, 11.0), "15N": (90.0, 135.0)},
    constraints={"ndim": 2},
)

register(TEMPLATE)
