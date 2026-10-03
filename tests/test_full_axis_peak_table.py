from __future__ import annotations

import math

from nmrforge_api.peak_tables import (
    PEAK_TABLE_COLUMNS,
    mark_duplicate_localization,
    peak_table_row,
)
from nmrforge_api.peaks import PeakMeasurement


def test_full_axis_schema_and_row_fields() -> None:
    assert len(PEAK_TABLE_COLUMNS) == 38
    assert PEAK_TABLE_COLUMNS[-17:-8] == (
        "F1_ppm",
        "F1_nucleus",
        "FWHM_F1",
        "F2_ppm",
        "F2_nucleus",
        "FWHM_F2",
        "F3_ppm",
        "F3_nucleus",
        "FWHM_F3",
    )
    measurement = PeakMeasurement(
        peak_id=1,
        assignment="A1",
        positions={
            "F1": 55.0,
            "F2": 8.1,
            "F3": 120.2,
            "1H": 8.1,
            "15N": 120.2,
        },
        axis_nuclei={"F1": "13C", "F2": "1H", "F3": "15N"},
        localization={"fwhm_by_axis": {"F1": 0.2, "F2": 0.03}},
    )
    row = peak_table_row(measurement, workflow_id="wf")
    assert (row["F1_ppm"], row["F1_nucleus"], row["FWHM_F1"]) == (
        55.0,
        "13C",
        0.2,
    )
    assert (row["F2_ppm"], row["F2_nucleus"], row["FWHM_F2"]) == (
        8.1,
        "1H",
        0.03,
    )
    assert (row["F3_ppm"], row["F3_nucleus"]) == (120.2, "15N")
    assert math.isnan(row["FWHM_F3"])


def test_duplicate_key_uses_full_axes_and_requires_complete_marked_axes() -> None:
    rows = [
        {
            "F1_ppm": 55,
            "F1_nucleus": "13C",
            "F2_ppm": 8,
            "F2_nucleus": "1H",
            "F3_ppm": 120,
            "F3_nucleus": "15N",
            "H_ppm": 8,
            "N_ppm": 120,
        },
        {
            "F1_ppm": 55,
            "F1_nucleus": "13C",
            "F2_ppm": 8,
            "F2_nucleus": "1H",
            "F3_ppm": 121,
            "F3_nucleus": "15N",
            "H_ppm": 8,
            "N_ppm": 120,
        },
        {
            "F1_ppm": 55,
            "F1_nucleus": "13C",
            "F2_ppm": 8,
            "F2_nucleus": "1H",
            "F3_nucleus": "15N",
            "H_ppm": 8,
            "N_ppm": 120,
        },
        {"H_ppm": 8, "N_ppm": 120},
        {"H_ppm": 8, "N_ppm": 120, "detected": False},
    ]
    assert mark_duplicate_localization(rows) == 0
    assert [r["duplicate_localization"] for r in rows] == [False] * 5


def test_legacy_hn_and_repeated_nucleus_coordinates_remain_supported() -> None:
    legacy = [
        {"H_ppm": 8, "N_ppm": 120},
        {"H_ppm": 8, "N_ppm": 120},
    ]
    assert mark_duplicate_localization(legacy) == 1
    assert all(row["duplicate_localization"] for row in legacy)
    repeated = [
        {"F1_ppm": 8, "F1_nucleus": "1H", "F2_ppm": 8, "F2_nucleus": "1H"},
        {"F1_ppm": 8, "F1_nucleus": "1H", "F2_ppm": 8, "F2_nucleus": "1H"},
    ]
    assert mark_duplicate_localization(repeated) == 1
