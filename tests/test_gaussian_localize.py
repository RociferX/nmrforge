"""2D 高斯峰定位(peak localization)回归。

覆盖用户需求 §15 的六项测试(非整数中心、零填零/网格密度、与抛物线一致、
失败路径、非 2D 拒绝、向后兼容)+ 选峰集成(峰表附件 / 运行参数留档)。

约定:合成谱的 ppm 轴与选峰同源(``CAR + (size/2 - i)*SW/(size*OBS)``);
数据轴 0 = F1(间接,15N)、轴 1 = F2(直接,1H)。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.peaks import localize as lz
from core.peaks.gaussian_fit import REASON_FLAT_REGION, REASON_NON_FINITE
from core.peaks.peak_table import import_peaks_poky
from core.project import ProjectManager
from workflow.pick_peaks import pick_peaks

_N15 = {"obs": 60.8, "sw": 2000.0, "car": 118.0}
_H1 = {"obs": 600.0, "sw": 6000.0, "car": 4.7}
# 非整数真实中心(用户需求:不能落在整数 grid 上)
_TRUE_CENTER = (30.37, 60.62)
_TRUE_SIGMA = (2.2, 2.6)
# 合成峰比 config 默认线宽宽(≈0.48 点/0.78 点),ROI 显式给足
_ROI_F1_PPM = 2.0
_ROI_F2_PPM = 0.4


def _synth(
    shape: tuple[int, int],
    center: tuple[float, float],
    sigma: tuple[float, float],
    *,
    amp: float = 100.0,
    base: float = 3.0,
    noise: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    row, col = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = base + amp * np.exp(
        -((row - center[0]) ** 2) / (2 * sigma[0] ** 2)
        - ((col - center[1]) ** 2) / (2 * sigma[1] ** 2)
    )
    if noise:
        arr = arr + np.random.default_rng(seed).normal(0.0, noise, shape)
    return arr


def _ppm_axes(shape: tuple[int, int]) -> list[np.ndarray]:
    axes: list[np.ndarray] = []
    for spec, size in ((_N15, shape[0]), (_H1, shape[1])):
        idx = np.arange(size, dtype=float)
        axes.append(
            spec["car"] + (size / 2.0 - idx) * spec["sw"] / (size * spec["obs"])
        )
    return axes


def _argmax(arr: np.ndarray) -> tuple[int, int]:
    row, col = np.unravel_index(int(np.argmax(arr)), arr.shape)
    return int(row), int(col)


def _err(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


# --------------------------------------------------------------- Test 1
def test_gaussian_recovers_subpoint_center_2d() -> None:
    """Test 1:非整数中心的孤立高斯峰——拟合中心优于整数格,且更准。"""
    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA)
    axes = _ppm_axes(shape)
    index = _argmax(arr)
    grid_err = _err(index, _TRUE_CENTER)
    assert grid_err > 0.25  # 整数格确实偏了(否则测试没有意义)

    parabolic = lz.localize_peak(arr, index, method="parabolic")
    # ROI 用 ppm 给(合成峰的宽度比 config 线宽宽,给足 ROI 才良态)
    gaussian = lz.localize_peak(
        arr,
        index,
        method="gaussian",
        ppm_axes=axes,
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
    )

    assert gaussian.success and gaussian.actual_method == "gaussian"
    fit_err = _err(gaussian.position, _TRUE_CENTER)
    assert fit_err < 0.02                       # 亚格点:远好于整数格
    assert fit_err < grid_err / 10              # 明显优于 integer-grid maximum
    # 两种方法在同一量级(noiseless 时抛物线对 Gaussian 也近乎精确)
    assert fit_err <= max(0.02, _err(parabolic.position, _TRUE_CENTER) * 3)

    record = gaussian.to_dict(axes)
    step0 = _N15["sw"] / (shape[0] * _N15["obs"])
    step1 = _H1["sw"] / (shape[1] * _H1["obs"])
    assert record["amplitude"] == pytest.approx(100.0, rel=0.01)
    assert record["baseline"] == pytest.approx(3.0, rel=0.05)
    assert record["fit_rmse"] < 0.1
    assert not record["boundary_hit"]
    # FWHM = 2*sqrt(2 ln 2) * sigma(ppm)
    assert record["fwhm_f1"] == pytest.approx(
        _TRUE_SIGMA[0] * lz.FWHM_FACTOR * step0, rel=0.03
    )
    assert record["fwhm_f2"] == pytest.approx(
        _TRUE_SIGMA[1] * lz.FWHM_FACTOR * step1, rel=0.03
    )
    # 中心 ppm 与真实 ppm 一致(轴映射口径)
    assert record["center_f1"] == pytest.approx(
        lz.ppm_from_point(axes[0], _TRUE_CENTER[0]), abs=0.02 * step0
    )
    assert record["center_f2"] == pytest.approx(
        lz.ppm_from_point(axes[1], _TRUE_CENTER[1]), abs=0.02 * step1
    )


# --------------------------------------------------------------- Test 2
def test_gaussian_center_is_grid_density_independent() -> None:
    """Test 2:同一物理峰在 1×/4× 网格(填零)上给出同一 ppm 中心与宽度。"""
    results: dict[int, tuple[dict, float]] = {}
    for factor in (1, 4):
        shape = (64 * factor, 128 * factor)
        center = (_TRUE_CENTER[0] * factor, _TRUE_CENTER[1] * factor)
        sigma = (_TRUE_SIGMA[0] * factor, _TRUE_SIGMA[1] * factor)
        arr = _synth(shape, center, sigma)
        axes = _ppm_axes(shape)
        loc = lz.localize_peak(
            arr,
            _argmax(arr),
            method="gaussian",
            ppm_axes=axes,
            roi_f1_ppm=_ROI_F1_PPM,
            roi_f2_ppm=_ROI_F2_PPM,
        )
        assert loc.success, loc.reason
        record = loc.to_dict(axes)
        results[factor] = (record, _N15["sw"] / (shape[0] * _N15["obs"]))

    coarse, coarse_step = results[1]
    fine, _ = results[4]
    # 中心 ppm:两种网格差 << 粗网格一个点距(否则就是「随填零漂移」)
    assert abs(coarse["center_f1"] - fine["center_f1"]) < 0.1 * coarse_step
    assert abs(coarse["center_f2"] - fine["center_f2"]) < 0.1 * coarse_step
    # 宽度也一致(物理量,不随点距变)
    assert coarse["fwhm_f1"] == pytest.approx(fine["fwhm_f1"], rel=0.05)
    assert coarse["fwhm_f2"] == pytest.approx(fine["fwhm_f2"], rel=0.05)
    # 但点数口径的 sigma 确实不同(填零只改点距)
    assert fine["sigma_points_f1"] == pytest.approx(
        coarse["sigma_points_f1"] * 4, rel=0.05
    )


# --------------------------------------------------------------- Test 3
def test_gaussian_and_parabolic_agree_on_strong_isolated_peak() -> None:
    """Test 3:高 S/N 孤立峰下抛物线与高斯结果非常接近。"""
    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA, noise=0.2)
    axes = _ppm_axes(shape)
    index = _argmax(arr)
    parabolic = lz.localize_peak(arr, index, method="parabolic")
    gaussian = lz.localize_peak(
        arr,
        index,
        method="gaussian",
        ppm_axes=axes,
        roi_f1_ppm=_ROI_F1_PPM,
        roi_f2_ppm=_ROI_F2_PPM,
    )
    assert gaussian.success
    assert _err(gaussian.position, parabolic.position) < 0.05
    assert _err(parabolic.position, _TRUE_CENTER) < 0.05
    assert _err(gaussian.position, _TRUE_CENTER) < 0.05


# --------------------------------------------------------------- Test 4
def test_gaussian_failure_paths_are_recorded_and_fall_back() -> None:
    """Test 4:失败不崩、fit_success=False、回退抛物线、原因被记录。"""
    axes = _ppm_axes((32, 32))
    cases: list[tuple[str, np.ndarray, float, float, str]] = []
    flat = np.full((32, 32), 7.0)
    cases.append(("flat", flat, 0.5, 0.5, REASON_FLAT_REGION))
    nan_arr = _synth((32, 32), (16.2, 16.3), (2.0, 2.0))
    nan_arr[16, 16] = np.nan
    cases.append(("nan", nan_arr, 0.5, 0.5, REASON_NON_FINITE))
    # ROI 半径过小(1 个 ppm 步长 < 3 点)→ roi_too_small
    cases.append(
        ("tiny_roi", _synth((32, 32), (16.2, 16.3), (2.0, 2.0)), 0.2, 0.2, "roi_too_small")
    )
    for name, arr, roi1, roi2, expected in cases:
        loc = lz.localize_peak(
            arr,
            _argmax(arr) if name != "flat" else (16, 16),
            method="gaussian",
            ppm_axes=axes,
            roi_f1_ppm=roi1,
            roi_f2_ppm=roi2,
        )
        assert loc.success is False, name
        assert loc.fallback is True, name
        assert loc.actual_method == "parabolic", name
        assert loc.requested_method == "gaussian", name
        assert expected in loc.reason, f"{name}: {loc.reason}"
        # 回退位置必须有限:谱含 NaN 时抛物线会给 NaN,高斯出口要挡住
        assert all(np.isfinite(v) for v in loc.position), name
        reference = lz.localize_peak(
            arr, _argmax(arr) if name != "flat" else (16, 16), method="parabolic"
        )
        if all(np.isfinite(v) for v in reference.position):
            assert loc.position == pytest.approx(reference.position)
        record = loc.to_dict(axes)
        assert record["gaussian_fit_success"] is False
        assert record["fallback"] is True
        assert record["fit_success"] is False
        assert record["fallback_reason"]
        assert record["fit_failure_reason"]
        assert record["requested_method"] == "gaussian"


# --------------------------------------------------------------- Test 5
def test_gaussian_is_rejected_for_non_2d_spectra() -> None:
    """Test 5:1D/3D 明确拒绝(消息与用户需求一致),不静默跑错算法。"""
    for ndim in (1, 3):
        data = np.zeros((8,) * ndim)
        with pytest.raises(lz.LocalizationError) as exc:
            lz.localize_peak(data, (4,) * ndim, method="gaussian")
        assert "only for 2D spectra" in str(exc.value)
    # 维数判定工具本身也要一致
    assert lz.localization_supported("gaussian", 2) is True
    assert lz.localization_supported("gaussian", 1) is False
    assert lz.localization_supported("gaussian", 3) is False
    assert lz.localization_supported("parabolic", 3) is True


# --------------------------------------------------------------- Test 6
def test_parabolic_default_matches_legacy_implementation() -> None:
    """Test 6:默认方法=抛物线,结果与既有 ``_refined_index`` 逐位一致。"""
    from core.qc.peak_detection import _refined_index

    shape = (64, 128)
    arr = _synth(shape, _TRUE_CENTER, _TRUE_SIGMA, noise=0.3)
    index = _argmax(arr)
    default = lz.localize_peak(arr, index)
    assert default.requested_method == "parabolic"
    assert default.actual_method == "parabolic"
    assert lz.DEFAULT_LOCALIZATION_METHOD == "parabolic"
    value = np.asarray(arr, dtype=float)
    expected = tuple(
        _refined_index(value, list(index), axis) for axis in range(value.ndim)
    )
    assert default.position == pytest.approx(expected)
    # 高斯请求下 also 用同一 candidate:抛物线的位置就是高斯的初值(§4)
    gaussian = lz.localize_peak(
        arr,
        index,
        method="gaussian",
        ppm_axes=_ppm_axes(shape),
    )
    assert gaussian.gaussian is not None
    assert gaussian.gaussian.seed == pytest.approx(default.position)


# --------------------------------------------------------- 选峰集成(2D)
def _write_ft2(path: Path, data: np.ndarray) -> None:
    """写带 N15/H1 标签与 CAR 的合成 NMRPipe 2D 谱(ppm 口径与选峰同源)。"""
    from nmrglue.fileio import pipe

    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = data.shape[1]
    dic["FDSPECNUM"] = data.shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDDIMORDER"] = [2, 1]
    dic["FDF1LABEL"] = "N15"
    dic["FDF2LABEL"] = "H1"
    for prefix, spec in (("FDF1", _N15), ("FDF2", _H1)):
        dic[prefix + "SW"] = str(spec["sw"])
        dic[prefix + "OBS"] = str(spec["obs"])
        dic[prefix + "CAR"] = str(spec["car"])
        dic[prefix + "ORIG"] = "0"
    path.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(path), dic, data.astype(np.float32), overwrite=True)


def _manager_with_spectrum(
    tmp_path: Path, spec_path: Path
) -> tuple[ProjectManager, str, str]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    manager.set_data_spectrum(entry.id, data.id, str(spec_path))
    return manager, entry.id, data.id


def _two_peak_spectrum() -> np.ndarray:
    arr = _synth((64, 128), _TRUE_CENTER, _TRUE_SIGMA, noise=0.5)
    arr = arr + _synth((64, 128), (45.28, 90.71), (2.4, 2.1), amp=60.0, noise=0.0)
    return arr + np.random.default_rng(7).normal(0.0, 0.5, (64, 128))


def test_pick_peaks_gaussian_writes_sidecar_and_provenance(
    tmp_path: Path,
) -> None:
    """选峰:高斯定位写峰表附件 + 运行参数留档,失败/回退计数可查。"""
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, _two_peak_spectrum())
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)

    baseline = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    parabolic_rows = import_peaks_poky(baseline["peak_path"])
    assert baseline["localization"]["peak_localization_method"] == "parabolic"
    assert baseline["localization"]["gaussian_fit_success_count"] == 0

    result = pick_peaks(
        manager,
        exp_id,
        data_id,
        sigma_multiplier=25.0,
        localization_method="gaussian",
    )
    assert result["status"] == "success"
    summary = result["localization"]
    assert summary["peak_localization_method"] == "gaussian"
    assert summary["n_peaks"] == result["peak_count"]
    assert summary["gaussian_fit_success_count"] + summary["gaussian_fallback_count"] == (
        result["peak_count"]
    )
    assert summary["records_path"].endswith(".localization.json")
    assert Path(summary["records_path"]).is_file()
    assert any("峰定位" in line and "高斯" in line for line in result["logs"])

    records = lz.read_localization_records(result["peak_path"])
    assert len(records) == result["peak_count"]
    for record in records:
        assert record["requested_method"] == "gaussian"
        assert record["actual_method"] in ("gaussian", "parabolic")
        assert record["fit_success"] is (record["actual_method"] == "gaussian")
        if record["actual_method"] == "gaussian":
            assert record["fwhm_f1"] > 0 and record["fwhm_f2"] > 0
            assert record["amplitude"] > 0

    # 运行参数留档(WorkflowRun.params)
    runs = [
        r for r in manager.project.workflow_runs if r.workflow_ref == "pick_peaks"
    ]
    assert runs[-1].params["localization"]["peak_localization_method"] == "gaussian"
    assert runs[-1].params["localization"]["gaussian_roi_f1_ppm"] > 0

    # 两种方法对同一批峰:峰位差很小,但高斯更接近真值(亚格点)
    gaussian_rows = import_peaks_poky(result["peak_path"])
    assert len(gaussian_rows) == len(parabolic_rows)
    step0 = _N15["sw"] / (64 * _N15["obs"])
    step1 = _H1["sw"] / (128 * _H1["obs"])
    diffs = [
        max(
            abs(float(a["N_shift"]) - float(b["N_shift"])) / step0,
            abs(float(a["H_shift"]) - float(b["H_shift"])) / step1,
        )
        for a, b in zip(parabolic_rows, gaussian_rows)
    ]
    assert max(diffs) < 1.0  # 同一 candidate,不会跳到别的峰
    truth = (_N15["car"] + (32 - _TRUE_CENTER[0]) * step0,
             _H1["car"] + (64 - _TRUE_CENTER[1]) * step1)
    best = max(
        zip(parabolic_rows, gaussian_rows),
        key=lambda pair: abs(float(pair[0]["H_shift"]) - truth[1]),
    )
    assert abs(float(best[1]["N_shift"]) - truth[0]) <= abs(
        float(best[0]["N_shift"]) - truth[0]
    ) + 1e-9


def test_pick_peaks_default_and_explicit_parabolic_identical(
    tmp_path: Path,
) -> None:
    """向后兼容:不指定方法(默认)与显式 parabolic 的峰表逐字一致。"""
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, _two_peak_spectrum())
    manager, exp_id, data_id = _manager_with_spectrum(tmp_path, ft2)
    default = pick_peaks(manager, exp_id, data_id, sigma_multiplier=25.0)
    text_default = Path(default["peak_path"]).read_text(encoding="utf-8")
    explicit = pick_peaks(
        manager,
        exp_id,
        data_id,
        sigma_multiplier=25.0,
        localization_method="parabolic",
    )
    text_explicit = Path(explicit["peak_path"]).read_text(encoding="utf-8")
    assert text_default == text_explicit
    assert default["peak_count"] == explicit["peak_count"]
