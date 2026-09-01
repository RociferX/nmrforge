"""基线条纹伪影回归测试(0.2.132)+ 稳健校正(0.2.190)。

复现:逐迹多项式拟合被强峰拉偏 → 相邻迹拟合系数跳变 → 竖线条纹;
0.2.190 起 baseline.apply 使用迭代峰值屏蔽的稳健拟合,强峰不再拉偏,
校正后无条纹且真实基线被安全校正。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.data.bruker_reader import read_dataset
from core.processing import baseline as baseline_proc
from core.qc import baseline_quality as bq
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


def test_t1_ridge_not_flagged_as_stripe() -> None:
    """t1 噪声带/首增量偏置(前几根迹整迹 DC 偏移)不再误判为条纹
    (0.2.199-补29ek:中位数边带 + p95 跳变统计)。旧 max/均值组合
    几乎把所有真实 2D 谱打成 0.50 条纹。"""
    rng = np.random.default_rng(21)
    n1, n2 = 256, 512
    spec = rng.normal(0.0, 1.0, size=(n1, n2))
    spec[:8] += np.linspace(20.0, 80.0, 8)[:, None]
    for i, row in enumerate((50, 120, 200)):
        spec[row, 100 + 120 * i] += 200.0
    m = bq.evaluate(spec, axis=1)
    assert m.stripe <= 0.1
    assert m.score >= 80.0
    assert not m.needs_correction


def test_edge_peak_not_flagged_as_stripe() -> None:
    """轴边缘带内强峰不再把端部均值拉高误判为条纹(中位数边带稳健)。"""
    rng = np.random.default_rng(22)
    n1, n2 = 128, 512
    spec = rng.normal(0.0, 1.0, size=(n1, n2))
    for row in range(60, 66):
        spec[row, 500] += 150.0  # 峰落在最后 8% 边带内
    m = bq.evaluate(spec, axis=1)
    assert m.stripe <= 0.1
    assert m.score >= 80.0


def test_single_trace_stripe_still_caught() -> None:
    """真正的逐迹基线偏置(整迹 DC 偏移)仍被显著惩罚——p95 只放过
    少量伪影迹,不会放过真实条纹。"""
    rng = np.random.default_rng(0)
    spec = rng.normal(0.0, 1.0, size=(24, 40))
    spec[1, :] += 50.0
    assert float(stripe_penalty(spec)) >= 0.25


def test_robust_correction_removes_slope_without_stripes() -> None:
    """稳健校正:强峰+漂移谱校正后斜率归零且不引入条纹(0.2.190)。

    旧 plain polyfit 会被稀疏强峰拉偏 → 相邻迹拟合系数跳变 → 竖线条纹
    (0.2.132 回归);稳健拟合迭代屏蔽峰区,只基于基线点估计多项式。
    """
    spec = _strong_peak_spectrum()
    base = float(stripe_penalty(spec))
    corrected = baseline_proc.apply(
        spec.copy(),
        baseline_proc.BaselineParams(method="polynomial", axis="F2", order=2),
    )
    after = float(stripe_penalty(corrected))
    # 稳健校正不引入条纹(旧 plain polyfit 校正后 after > 0.2)
    assert after <= base + 0.05
    assert after < 0.1
    # 斜率被校正(两端均值差明显减小),基线分提高
    before = bq.evaluate(spec, axis=1)
    after_q = bq.evaluate(corrected, axis=1)
    assert abs(after_q.slope) < abs(before.slope) * 0.2
    assert after_q.score > before.score + 1.0

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


def test_has_stripe_artifact_relative_veto() -> None:
    """0.2.199-补9:相对否决只拦比原谱明显更差的候选。"""
    from workflow.baseline_optimize import _has_stripe_artifact, _stripe_ratio

    rng = np.random.default_rng(0)

    def striped(offset: float) -> np.ndarray:
        arr = rng.normal(0.0, 1.0, size=(24, 40))
        arr[1, :] += offset
        return arr

    clean = rng.normal(0.0, 1.0, size=(24, 40))
    orig = striped(50.0)   # 原谱强条纹
    improved = striped(20.0)  # 改善但仍超阈值
    worse = striped(200.0)  # 明显更差
    clean_r = _stripe_ratio(clean, 1)
    orig_r = _stripe_ratio(orig, 1)
    improved_r = _stripe_ratio(improved, 1)
    assert orig_r > 8.0 and improved_r > 8.0
    # 相对:改善的候选不拦,更差的候选拦
    assert not _has_stripe_artifact(improved, 1, baseline_ratio=orig_r)
    assert _has_stripe_artifact(worse, 1, baseline_ratio=orig_r)
    # 干净原谱 + 候选引入条纹 → 拦(相对否决也拦)
    assert _has_stripe_artifact(worse, 1, baseline_ratio=clean_r)
    # 缺省绝对阈值行为保持
    assert _has_stripe_artifact(worse, 1)


def test_optimize_baseline_corrects_peak_spectrum_safely(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """强峰+漂移谱:稳健校正候选通过(不产生条纹),该轴写回校正。

    0.2.190:稳健拟合屏蔽峰区后不再被强峰拉偏,硬性条纹否决不再把
    校正全部打成 off——真实基线被安全校正。
    """
    spec = _strong_peak_spectrum()
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    # F2 检出漂移并被安全校正(旧 plain polyfit 保持 off)
    assert result.baseline["F2"]["enabled"] is True
    # 应用选择后的配置不引入条纹,基线分提高
    chosen = result.baseline["F2"]
    applied = spec.copy()
    if chosen["enabled"]:
        applied = baseline_proc.apply(
            applied,
            baseline_proc.BaselineParams(
                method="polynomial",
                axis="F2",
                order=max(int(chosen.get("order", 0) or 0), 1),
            ),
        )
    assert stripe_penalty(applied) <= stripe_penalty(spec) + 0.2
    assert (
        bq.evaluate(applied, axis=1).score
        > bq.evaluate(spec, axis=1).score + 1.0
    )


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
