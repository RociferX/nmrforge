"""Regression tests for compact user-facing optimization reports."""


def test_phase_report_rounds_angles_to_at_most_two_decimals() -> None:
    from workflow.optimization_report import format_phase_pair, phase_angle_value

    assert format_phase_pair((-12.34567, 0.004)) == "p0=-12.35° p1=0.00°"
    assert format_phase_pair({"p0": 1.2, "p1": 3.4567}) == "p0=1.20° p1=3.46°"
    assert format(phase_angle_value(3.4567), "g") == "3.46"
