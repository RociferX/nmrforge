"""CCH 固体核磁模板:1H 检测的 13C-13C 相关(固体核磁,液体核磁无此类型)。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE = ExperimentTemplate(
    name="CCH",
    direct_nucleus="1H",
    indirect_nuclei=["13C", "13C"],
    expected_peak_mode="absorption",
    peak_sign="uniform",
    display_orientation="13C/13C/1H",
    priors={"1H": (-10.0, 20.0), "13C": (-50.0, 250.0)},
    constraints={"ndim": 3},
)

register(TEMPLATE)
