"""HMBC 实验模板。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE = ExperimentTemplate(
    name="HMBC",
    direct_nucleus="1H",
    indirect_nuclei=["13C"],
    expected_peak_mode="magnitude",
    display_orientation="13C/1H",
    priors={"1H": (0.0, 12.0), "13C": (0.0, 220.0)},
    constraints={"ndim": 2},
)

register(TEMPLATE)
