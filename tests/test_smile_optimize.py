"""SMILE optimisation module test: Grid and report rendering (survival interface after revision
24). 0.2.199-patch29hz-Revision 24 deleted the old two-stage scoring chain
(optimize_smile_parameters / write_smile_optimized_output / _rank_by_true_peaks and only the
scoring helper they used), and the use cases that relied on the old chain were removed together;
the new scan chain (sorting list + top three script + candidate spectrum deleted) see
tests/test_smile_scan_workflow.py, optimisation For estimates of extent and time consumption,
see tests/test_smile_grid_eta.py."""

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
    assert len(grid) == 25  # 0.2.162-Supplement: Encrypted grid (5x5), finer parameter adjustment.
    assert grid[0] == {"nsigma": 3.0, "thresh": 0.90}
    assert all("smile_xq3" not in g for g in grid)  # 0.2.162:xQ3 Dead parameter removed.
    combos = {(g["nsigma"], g["thresh"]) for g in grid}
    assert len(combos) == 25


def test_save_report_and_format(tmp_path: Path) -> None:
    """Reporting JSON and table rendering (param_optimize reuses the same implementation)."""
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
    """CLI --grid parsing (nSigma,thresh semicolon separated)."""
    from scripts.smile_optimize import _parse_grid

    grid = _parse_grid("5.0,0.95;6.0,0.99")
    assert grid == [
        {"nsigma": 5.0, "thresh": 0.95},
        {"nsigma": 6.0, "thresh": 0.99},
    ]
