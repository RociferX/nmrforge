"""HNCO 实验模板。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE = ExperimentTemplate(
    name="HNCO",
    direct_nucleus="1H",
    indirect_nuclei=["15N", "13C"],
    expected_peak_mode="absorption",
    display_orientation="13C/15N/1H",
    priors={"1H": (6.0, 11.0), "15N": (90.0, 135.0), "13C": (165.0, 180.0)},
    constraints={"ndim": 3},
)

register(TEMPLATE)
