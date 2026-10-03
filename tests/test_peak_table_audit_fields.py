from __future__ import annotations

import math

from nmrforge_api.peak_tables import (
    PEAK_TABLE_COLUMNS,
    peak_table_row,
    read_peak_table,
    write_peak_table,
)
from nmrforge_api.peaks import PeakMeasurement


def test_missing_measurement_keeps_requested_actual_and_failure_separate() -> None:
    measurement = PeakMeasurement(
        peak_id=1,
        assignment="",
        found=False,
        localization={"failure_reason": "no_local_peak_above_threshold"},
    )

    row = peak_table_row(
        measurement,
        workflow_id="wf",
        method="none",
        requested_method="parabolic",
    )

    assert row["localization_requested"] == "parabolic"
    assert row["localization_method"] == "none"
    assert row["failure_reason"] == "no_local_peak_above_threshold"
    assert row["fallback"] is False


def test_fallback_reason_does_not_imply_fallback() -> None:
    measurement = PeakMeasurement(
        peak_id=1,
        assignment="A",
        localization={
            "actual_method": "parabolic",
            "requested_method": "parabolic",
            "fallback_reason": "informational only",
        },
    )

    row = peak_table_row(measurement, workflow_id="wf", method="caller-method")

    assert row["localization_method"] == "parabolic"
    assert row["fallback"] is False
    assert row["fallback_reason"] == "informational only"


def test_csv_roundtrip_preserves_empty_identity_none_cell_and_diagnostics(tmp_path) -> None:
    measurement = PeakMeasurement(
        peak_id=2,
        assignment="",
        reference={"1H": 8.0, "15N": 120.0},
        positions={"1H": 8.1, "15N": 120.2},
        deltas={"1H": 0.1, "15N": 0.2},
        intensity=50.0,
        found=True,
        cell_edge=None,
        intensity_ratio=0.5,
        localization={"actual_method": "parabolic"},
    )
    path = tmp_path / "peaks.csv"

    write_peak_table(path, [peak_table_row(measurement, workflow_id="wf")])
    row = read_peak_table(path)[0]

    assert len(PEAK_TABLE_COLUMNS) == 38
    assert row["assignment"] == ""
    assert row["cell_edge"] != row["cell_edge"]  # serialized as NaN
    assert math.isnan(row["cell_low_H"])
    assert row["intensity_ratio_vs_picked"] == 0.5
    assert row["shift_vs_picked_H"] == 0.1
    assert row["shift_vs_picked_N"] == 0.2
