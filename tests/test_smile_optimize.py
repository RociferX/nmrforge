"""SMILE 优化模块测试:网格与报告渲染(修24 后的存活接口)。

0.2.199-补29hz-修24 删除了旧两阶段评分链(optimize_smile_parameters /
write_smile_optimized_output / _rank_by_true_peaks 及只给它们用的评分 helper),
依赖旧链的用例一并移除;新扫描链(排序表 + 前三脚本 + 候选谱删除)见
tests/test_smile_scan_workflow.py,优化程度与耗时估计见 tests/test_smile_grid_eta.py。
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
    assert len(grid) == 25  # 0.2.162-补:加密网格(5x5),调参更细
    assert grid[0] == {"nsigma": 3.0, "thresh": 0.90}
    assert all("smile_xq3" not in g for g in grid)  # 0.2.162:xQ3 死参数移除
    combos = {(g["nsigma"], g["thresh"]) for g in grid}
    assert len(combos) == 25


def test_save_report_and_format(tmp_path: Path) -> None:
    """报告 JSON 与表格渲染(param_optimize 复用同一实现)。"""
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
    """CLI --grid 解析(nSigma,thresh 分号分隔)。"""
    from scripts.smile_optimize import _parse_grid

    grid = _parse_grid("5.0,0.95;6.0,0.99")
    assert grid == [
        {"nsigma": 5.0, "thresh": 0.95},
        {"nsigma": 6.0, "thresh": 0.99},
    ]
