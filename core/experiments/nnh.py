"""NNH 固体核磁模板:1H 检测的 15N-15N 相关(固体核磁,液体核磁无此类型)。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE = ExperimentTemplate(
    name="NNH",
    direct_nucleus="1H",
    indirect_nuclei=["15N", "15N"],
    expected_peak_mode="absorption",
    peak_sign="uniform",
    display_orientation="15N/15N/1H",
    priors={"1H": (-10.0, 20.0), "15N": (-50.0, 250.0)},
    constraints={"ndim": 3},
)

register(TEMPLATE)
