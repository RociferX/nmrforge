"""峰定位方法的 API 侧回归:测量 refine='gaussian'、非 2D 拒绝、config 缺省。

这些用例复用 ``tests/test_nmrforge_api.py`` 的合成谱/峰表/假后端构造(同一 ppm
口径),验证下游研究接口能通过 API 选择定位算法——即「比较两种算法带来的峰位
差距」的入口。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_nmrforge_api import (  # noqa: E402
    _FakeSweepBackend,
    _h1_step,
    _n15_step,
    _write_ft2,
    _write_peak_table,
)

import nmrforge_api.peaks as api_peaks  # noqa: E402
import workflow.pick_peaks as wf  # noqa: E402
from core.peaks import localize as lz  # noqa: E402
from core.peaks.peak_table import load_peaks  # noqa: E402
from nmrforge_api import (  # noqa: E402
    add_dataset,
    build_reference,
    measure_peak_positions,
    open_study,
)
from nmrforge_api.errors import MeasurementError  # noqa: E402
from nmrforge_api.peaks import pick_reference_peaks  # noqa: E402


def test_localization_defaults_come_from_config() -> None:
    """ROI 不写死在核心函数里:config ``peaks.localization`` 可覆盖。"""
    defaults = lz.load_localization_defaults()
    assert defaults["method"] == "parabolic"
    assert defaults["gaussian_roi_f1_ppm"] > 0
    assert defaults["gaussian_roi_f2_ppm"] > 0
    custom = lz.load_localization_defaults(
        {
            "peaks": {
                "localization": {
                    "method": "gaussian",
                    "gaussian_roi_f1_ppm": 0.8,
                    "gaussian_roi_f2_ppm": 0.1,
                }
            }
        }
    )
    assert custom["method"] == "gaussian"
    assert custom["gaussian_roi_f1_ppm"] == pytest.approx(0.8)
    assert custom["gaussian_roi_f2_ppm"] == pytest.approx(0.1)
    # 非法值回退默认,不抛异常
    broken = lz.load_localization_defaults(
        {"peaks": {"localization": {"method": "nope", "gaussian_roi_f1_ppm": -3}}}
    )
    assert broken["method"] == "parabolic"
    assert broken["gaussian_roi_f1_ppm"] == pytest.approx(
        lz.DEFAULT_GAUSSIAN_ROI_F1_PPM
    )


def test_measure_peak_positions_gaussian_refine(tmp_path: Path) -> None:
    """refine='gaussian':同一张谱/同一批峰换算法,逐峰留方法/QC。"""
    rows = load_peaks(_write_peak_table(tmp_path / "ref.list"))
    spectrum = _write_ft2(tmp_path / "shift.ft2", shift_y=1.25, shift_x=0.625)
    parabolic = measure_peak_positions(
        spectrum, rows, window_ppm=1.0, refine="parabolic"
    )
    gaussian = measure_peak_positions(
        spectrum,
        rows,
        window_ppm=1.0,
        refine="gaussian",
        roi_f1_ppm=1.0,
        roi_f2_ppm=0.2,
    )
    assert len(gaussian) == len(parabolic) == 2
    for fine, coarse in zip(gaussian, parabolic):
        record = fine.localization
        assert record["requested_method"] == "gaussian"
        assert record["actual_method"] in ("gaussian", "parabolic")
        assert record["fit_success"] is (record["actual_method"] == "gaussian")
        if record["actual_method"] == "gaussian":
            assert record["fwhm_f1"] > 0 and record["fwhm_f2"] > 0
            assert record["amplitude"] > 0
        # 同一 candidate:两种方法不会跳到别的峰
        assert abs(fine.positions["15N"] - coarse.positions["15N"]) < 0.5 * _n15_step()
        assert abs(fine.positions["1H"] - coarse.positions["1H"]) < 0.5 * _h1_step()
    # 至少一个峰真的走了高斯拟合(否则本用例没覆盖到拟合路径)
    assert (
        sum(1 for m in gaussian if m.localization["actual_method"] == "gaussian") >= 1
    )


class _Fake3D:
    """最小 3D 谱替身:只提供 read_spectrum_axes 会被用到的属性。"""

    data = np.zeros((4, 4, 4))
    ppm = [np.linspace(0.0, 1.0, 4)] * 3
    nuclei = ["15N", "13C", "1H"]
    logical_to_storage = [0, 1, 2]
    obs = [60.8, 151.0, 600.0]

    @property
    def ndim(self) -> int:
        return 3


def test_measure_peak_positions_gaussian_rejects_non_2d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """refine='gaussian' 只支持 2D:非 2D 谱必须明确拒绝,不静默跑错算法。"""
    spectrum = _write_ft2(tmp_path / "ref.ft2")
    rows = [{"N_shift": 119.0, "H_shift": 5.0, "label": "G1"}]
    monkeypatch.setattr(api_peaks, "read_spectrum_axes", lambda path: _Fake3D())
    with pytest.raises(MeasurementError) as exc:
        measure_peak_positions(spectrum, rows, refine="gaussian")
    assert "only for 2D spectra" in str(exc.value)
    # 未知 refine 仍是明确报错(不会静默按默认跑)
    with pytest.raises(MeasurementError):
        measure_peak_positions(spectrum, rows, refine="lorentzian")


def test_pick_reference_peaks_passes_localization_method(
    tmp_path: Path,
    bruker_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """参考峰表可选高斯定位:方法/ROI 透传到 workflow.pick_peaks 并留档。"""
    captured: dict = {}

    def fake_pick_peaks(manager, exp_id, data_id, *args, **kwargs):
        captured.update(kwargs)
        peak_path = tmp_path / "fake.list"
        peak_path.write_text(
            "Assignment w1 w2 Data Height Volume\nG1  119.000  8.000  0  100  0\n",
            encoding="utf-8",
        )
        return {
            "status": "success",
            "peak_path": str(peak_path),
            "peak_count": 1,
            "detection": {"edge_margin_points": 1},
            "localization": {"peak_localization_method": "gaussian"},
        }

    monkeypatch.setattr(wf, "pick_peaks", fake_pick_peaks)
    session = open_study(tmp_path / "study", name="gauss", backend=_FakeSweepBackend())
    add_dataset(session, bruker_dir / "hsqc_2d")
    build_reference(session)
    details: dict = {}
    peak_path = pick_reference_peaks(
        session,
        out_path=tmp_path / "reference.list",
        details=details,
        localization_method="gaussian",
        gaussian_roi_f1_ppm=1.2,
        gaussian_roi_f2_ppm=0.3,
    )
    assert captured["localization_method"] == "gaussian"
    assert captured["gaussian_roi_f1_ppm"] == pytest.approx(1.2)
    assert captured["gaussian_roi_f2_ppm"] == pytest.approx(0.3)
    assert details["localization"]["peak_localization_method"] == "gaussian"
    assert Path(peak_path).is_file()
