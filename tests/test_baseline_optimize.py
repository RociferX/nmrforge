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
    from scipy.ndimage import gaussian_filter

    spec = np.zeros((32, 64))
    spec += np.linspace(-50.0, 50.0, 64)[np.newaxis, :]
    spec[16, 30] = 500.0
    spec = gaussian_filter(spec, sigma=(0.8, 1.2))
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    assert "F2" in result.baseline and "F1" in result.baseline
    assert "off:0" in result.scores["F2"]
    # 直接维存在校正,且 off 不是最优
    assert result.baseline["F2"]["enabled"] is True
    assert result.scores["F2"]["off:0"] < max(result.scores["F2"].values())


def test_optimize_baseline_grid_contains_off_and_orders(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    spec = np.zeros((32, 64))
    ft2 = tmp_path / "flat.ft2"
    _write_ft2(ft2, spec)
    result = optimize_baseline(experiment, ft2)
    # 平谱:off 应最优(校正无增益)
    assert result.scores["F2"]["off:0"] >= max(result.scores["F2"].values()) - 1e-9
    assert "auto:1" in result.scores["F2"]
    assert "order:3" in result.scores["F2"]
