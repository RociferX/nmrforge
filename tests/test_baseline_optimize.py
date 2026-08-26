"""逐维基线优化测试(G2B-007)。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset
from workflow.baseline_optimize import optimize_baseline


def _write_ft2(path: Path, data: np.ndarray) -> None:
    from nmrglue.fileio import pipe

    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2"):
        dic[prefix + "SW"] = "6000.0"
        dic[prefix + "OBS"] = "600.0"
        dic[prefix + "CAR"] = "4.7"
        dic[prefix + "ORIG"] = "1000.0"
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def test_optimize_baseline_direct_axis_slope(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """直接维(F2)线性漂移:优化选校正(off 分数低于最优)。"""
    spec = np.zeros((32, 64))
    spec += np.linspace(-50.0, 50.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    assert "F2" in result.baseline and "F1" in result.baseline
    assert "off:0" in result.scores["F2"]
    # 直接维存在校正,且 off 不是最优
    assert result.baseline["F2"]["enabled"] is True
    assert result.scores["F2"]["off:0"] < max(result.scores["F2"].values())
    joined = "\n".join(result.logs)
    assert "F2" in joined and "基线已优化" in joined
    assert "F1" in joined and "未优化" in joined


def test_optimize_baseline_grid_contains_off_and_orders(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    # 中等质量谱(off 分 <95):候选网格需真正运行
    spec = np.full((32, 64), 100.0)
    spec += np.linspace(-1.0, 1.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "mid.ft2"
    _write_ft2(ft2, spec)
    result = optimize_baseline(experiment, ft2)
    assert result.scores["F2"]["off:0"] < 95.0
    assert result.scores["F2"]["off:0"] >= max(result.scores["F2"].values()) - 1e-9
    assert "auto:1" in result.scores["F2"]
    assert "order:3" in result.scores["F2"]


def test_optimize_baseline_good_baseline_skips_grid(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-补8:off 评分≥95 的轴跳过候选网格,直接保持 off。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    spec = np.zeros((32, 64))
    ft2 = tmp_path / "flat.ft2"
    _write_ft2(ft2, spec)
    result = optimize_baseline(experiment, ft2)
    assert set(result.scores["F2"]) == {"off:0"}
    assert result.scores["F2"]["off:0"] == 100.0
    assert result.baseline["F2"]["enabled"] is False
    assert any("基线已良好" in line for line in result.logs)


def test_optimize_baseline_reports_progress(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-补7:基线优化逐轴/候选输出进度(off 分 <95 才跑候选)。"""
    spec = np.full((32, 64), 100.0)
    spec += np.linspace(-1.0, 1.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    messages: list[str] = []
    optimize_baseline(experiment, ft2, progress=messages.append)
    assert any("轴 F1" in m for m in messages)
    assert any("score=" in m for m in messages)


def test_optimize_baseline_cancelled_raises(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-补7:取消标志置位时基线优化立即抛异常退出(off 分 <95)。"""
    import pytest

    spec = np.full((32, 64), 100.0)
    spec += np.linspace(-1.0, 1.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    with pytest.raises(RuntimeError, match="任务已取消"):
        optimize_baseline(experiment, ft2, cancel=lambda: True)


def test_decimated_reduces_traces() -> None:
    """0.2.199-补8:迹线子采样把候选评分迹数压到上限内。"""
    from workflow.baseline_optimize import _decimated

    arr = np.zeros((120, 80, 200))
    for axis in range(3):
        dec = _decimated(arr, axis, max_traces=4096)
        n = dec.shape[axis]
        assert dec.size // n <= 4096
        assert dec.ndim == 3


def test_optimize_baseline_small_max_traces_works(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-补8:小 max_traces 下基线优化仍返回有效配置。"""
    spec = np.zeros((32, 64))
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    result = optimize_baseline(experiment, ft2, max_traces=8)
    assert "F2" in result.baseline and "F1" in result.baseline
    assert "off:0" in result.scores["F2"]
