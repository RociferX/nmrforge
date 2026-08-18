"""HNCACB 实验模板。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE = ExperimentTemplate(
    name="HNCACB",
    direct_nucleus="1H",
    indirect_nuclei=["15N", "13C"],
    expected_peak_mode="absorption",
    peak_sign="mixed",
    peak_sign_regions={
        "13C": {
            "alpha": {"ppm": [40.0, 70.0], "sign": -1},
            "beta": {"ppm": [15.0, 45.0], "sign": 1},
        }
    },
    display_orientation="13C/15N/1H",
    priors={
        "1H": (6.0, 11.0),
        "15N": (90.0, 135.0),
        "13C_alpha": (40.0, 70.0),
        "13C_beta": (15.0, 45.0),
    },
    constraints={"ndim": 3},
)

register(TEMPLATE)
