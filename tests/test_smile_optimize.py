"""SMILE optimization module tests: grid and report rendering (the interfaces that
survived fix24).

0.2.199-patch29hz-fix24 removed the old two-stage scoring chain
(optimize_smile_parameters / write_smile_optimized_output / _rank_by_true_peaks plus the
scoring helpers used only by them), and the cases that depended on the old chain were
removed with it; the new scan chain (ranking table + top three scripts + candidate
spectrum deletion) is in tests/test_smile_scan_workflow.py, and optimization degree and
time estimation are in tests/test_smile_grid_eta.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from workflow.smile_optimize import (
    SmileParameterResult,
    default_smile_grid,
    format_results,
    save_report,
)


def test_default_smile_grid() -> None:
    grid = default_smile_grid()
    assert len(grid) == 25  # 0.2.162-patch: denser grid (5x5), finer parameter tuning
    assert grid[0] == {"nsigma": 3.0, "thresh": 0.90}
    assert all("smile_xq3" not in g for g in grid)  # 0.2.162: dead xQ3 parameter removed
    combos = {(g["nsigma"], g["thresh"]) for g in grid}
    assert len(combos) == 25


def test_save_report_and_format(tmp_path: Path) -> None:
    """Report JSON and table rendering (param_optimize reuses the same implementation)."""
    results = [
        SmileParameterResult(
            params={"nsigma": 5.0, "thresh": 0.95},
            decision="accept",
            overall=74.1,
            components={
                "stability": 0.8,
                "cross_support": 1.0,
                "snr": 12.0,
                "peak_count": 40,
                "artifact": 30.0,
            },
        )
    ]
    out = save_report(results, tmp_path / "report.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload[0]["params"]["nsigma"] == 5.0
    assert payload[0]["overall"] == 74.1
    table = format_results(results)
    assert "nSigma" in table and "stability" in table
    assert "5.0" in table and "74.1" in table


def test_cli_parse_grid() -> None:
    """CLI --grid parsing (nSigma,thresh separated by semicolons)."""
    from scripts.smile_optimize import _parse_grid

    grid = _parse_grid("5.0,0.95;6.0,0.99")
    assert grid == [
        {"nsigma": 5.0, "thresh": 0.95},
        {"nsigma": 6.0, "thresh": 0.99},
    ]
