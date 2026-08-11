"""Generic 2D/3D 模板：未知实验的安全兜底（框架 §68）。"""

from core.experiments.registry import ExperimentTemplate, register

TEMPLATE_2D = ExperimentTemplate(
    name="Generic2D",
    phase_sensitive=False,
    direct_nucleus="",
    indirect_nuclei=[],
    expected_peak_mode="unknown",
    display_orientation="",
    constraints={"ndim": 2},
)

TEMPLATE_3D = ExperimentTemplate(
    name="Generic3D",
    phase_sensitive=False,
    direct_nucleus="",
    indirect_nuclei=[],
    expected_peak_mode="unknown",
    display_orientation="",
    constraints={"ndim": 3},
)

register(TEMPLATE_2D)
register(TEMPLATE_3D)
