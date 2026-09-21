"""Clipping consistency of peak positioning attachments: ``max_peaks`` The number of rows in the
appendix must be consistent with the peak table."""

from __future__ import annotations

from pathlib import Path

from core.peaks import localize as lz
from core.peaks.peak_table import export_peaks_poky, import_peaks_poky


def _records(n: int) -> list[dict]:
    return [
        {
            "Peak_ID": i,
            "label": "",
            "requested_method": "gaussian",
            "actual_method": "gaussian",
            "gaussian_fit_success": True,
            "fallback": False,
        }
        for i in range(1, n + 1)
    ]


def test_trim_localization_records_keeps_rows_aligned(tmp_path: Path) -> None:
    peak_path = tmp_path / "peaks.list"
    export_peaks_poky(
        peak_path,
        [
            {"label": "?", "N_shift": 110.0 + i, "H_shift": 8.0, "Intensity": 100 - i}
            for i in range(6)
        ],
    )
    lz.write_localization_records(peak_path, _records(6), meta={"method": "gaussian"})
    assert len(lz.read_localization_records(peak_path)) == 6

    # Same sequence as _keep_top_peaks: first cut the peak table (max_peaks), and then truncate the
    # attachment synchronously.
    rows = import_peaks_poky(peak_path)
    export_peaks_poky(peak_path, rows[:4])
    assert lz.trim_localization_records(peak_path, 4) is True
    records = lz.read_localization_records(peak_path)
    assert len(records) == 4 == len(import_peaks_poky(peak_path))
    assert [r["Peak_ID"] for r in records] == [1, 2, 3, 4]
    # Idempotent + No accessories/No exception is thrown when cropping is not required.
    assert lz.trim_localization_records(peak_path, 4) is False
    assert lz.trim_localization_records(tmp_path / "missing.list", 3) is False
