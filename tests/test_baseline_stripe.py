"""基线条纹伪影回归测试(0.2.132)。

复现:逐迹多项式拟合被强峰拉偏 → 相邻迹拟合系数跳变 → 竖线条纹;
旧评分只测两端/中部均值,对条纹不敏感,导致 0.2.130 全轴写回把
POLY 写进终谱出现竖线。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset
from core.processing import baseline as baseline_proc
from core.qc.baseline_quality import stripe_penalty
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


def _strong_peak_spectrum() -> np.ndarray:
    """漂移 + 噪声 + 多个散布强峰:逐迹多项式拟合被强峰拉偏 → 明显竖线。"""
    rng = np.random.default_rng(11)
    n1, n2 = 64, 128
    spec = np.zeros((n1, n2))
    spec += np.linspace(-30.0, 30.0, n2)[np.newaxis, :]
    spec += np.linspace(-5.0, 5.0, n1)[:, np.newaxis]
    for i, row in enumerate((6, 7, 22, 23, 40, 55, 56)):
        spec[row, 50 + 8 * i] += 3500.0
    spec += rng.normal(0.0, 3.0, spec.shape)
    return spec


def test_stripe_penalty_flags_peak_pulled_correction() -> None:
    """条纹罚项:被强峰拉偏的逐迹校正显著增大,平滑漂移校正不增大。"""
    spec = _strong_peak_spectrum()
    base = float(stripe_penalty(spec))
    corrected = baseline_proc.apply(
        spec.copy(),
        baseline_proc.BaselineParams(method="polynomial", axis="F2", order=2),
    )
    after = float(stripe_penalty(corrected))
    # 稀疏强峰拉偏 → 端部均值跳变 >> 中位(条纹),罚项显著
    assert after > 0.2
    assert after > base + 0.2

    # 对照:无强峰时同样校正不引入条纹
    rng = np.random.default_rng(11)
    clean = np.zeros(spec.shape)
    clean += np.linspace(-30.0, 30.0, spec.shape[1])[np.newaxis, :]
    clean += np.linspace(-5.0, 5.0, spec.shape[0])[:, np.newaxis]
    clean += rng.normal(0.0, 3.0, clean.shape)
    corrected_clean = baseline_proc.apply(
        clean.copy(),
        baseline_proc.BaselineParams(method="polynomial", axis="F2", order=2),
    )
    assert float(stripe_penalty(corrected_clean)) < 0.2


def test_optimize_baseline_keeps_off_for_peak_stripes(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """强峰+漂移谱:逐迹校正候选被硬性条纹否决,该轴保持 off。"""
    spec = _strong_peak_spectrum()
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    # 两个轴的校正候选都因条纹被否决 → off(不写 POLY,终谱无竖线)
    assert result.baseline["F2"]["enabled"] is False
    assert result.baseline["F1"]["enabled"] is False
    # 应用选择后的配置(off)不改变谱,条纹水平与原始一致
    chosen = result.baseline["F2"]
    applied = spec.copy()
    if chosen["enabled"]:
        applied = baseline_proc.apply(
            applied,
            baseline_proc.BaselineParams(method="polynomial", axis="F2", order=2),
        )
    assert stripe_penalty(applied) <= stripe_penalty(spec) + 0.2


def test_optimize_baseline_axis_mapping(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """评分轴与校正轴一致(dimensions 直接维在前时不再评错轴)。"""
    spec = np.zeros((32, 64))
    spec += np.linspace(-50.0, 50.0, 64)[np.newaxis, :]  # 沿 F2(numpy 轴 1)
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    # F2 检测到漂移并校正;F1 沿行恒定,无增益保持 off
    assert result.baseline["F2"]["enabled"] is True
    assert result.baseline["F1"]["enabled"] is False
