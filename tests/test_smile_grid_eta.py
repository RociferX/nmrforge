"""SMILE 优化程度(2x2..5x5)与耗时预计(0.2.199-补29hz-修4)。"""

from __future__ import annotations

from pathlib import Path

from core.data.bruker_reader import read_dataset
from workflow.smile_optimize import (
    SMILE_GRID_MAX,
    SMILE_GRID_MIN,
    estimate_scan_seconds,
    scan_smile_parameters,
    smile_grid,
)

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"


class _FakeBackend:
    """假后端:每组一个「候选谱」,评估后删除(与真实语义一致)。"""

    def smile_scan(
        self, experiment, params, combos, *, work_dir, evaluate=None, progress=None,
        delete_spectra=True,
    ):
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress(0, len(combos), "直接维处理(生成切片)…")
        candidates = []
        for index, combo in enumerate(combos, start=1):
            if progress is not None:
                progress(index, len(combos), f"扫描 {index}/{len(combos)}")
            spectrum = Path(work_dir) / f"cand{index:02d}.ft2"
            spectrum.write_bytes(b"x")
            metrics = {
                "peak_count": 3,
                "quality": 70.0,
                "holdout_rmse": 0.1 + index * 0.01,
                "holdout_corr": 0.9 - index * 0.01,
                "peaks": [
                    {"position": [1.0, 1.0], "height": 1.0, "snr": 5.0},
                    {"position": [2.0, 2.0], "height": 1.0, "snr": 4.0},
                ],
            }
            if delete_spectra:
                spectrum.unlink()
            candidates.append(
                {
                    "index": index,
                    "params": dict(combo),
                    "metrics": metrics,
                    "script": f"# script {combo}\n",
                    "ok": True,
                }
            )
        return {
            "success": True,
            "message": "fake",
            "logs": [],
            "candidates": candidates,
            "scan_dir": str(work_dir),
        }


def test_smile_grid_sizes() -> None:
    """2x2..5x5 对应 4/9/16/25 组,且首尾档位始终在。"""
    assert (SMILE_GRID_MIN, SMILE_GRID_MAX) == (2, 5)
    assert len(smile_grid(2)) == 4
    assert len(smile_grid(3)) == 9
    assert len(smile_grid(4)) == 16
    assert len(smile_grid(5)) == 25
    assert smile_grid(5)[0] == {"nsigma": 3.0, "thresh": 0.9}
    assert smile_grid(5)[-1] == {"nsigma": 7.0, "thresh": 0.99}
    # 越界输入收敛到合法范围
    assert len(smile_grid(1)) == 4
    assert len(smile_grid(9)) == 25


def test_estimate_scan_seconds_scales() -> None:
    """按数据规模的粗估:正数、与组数成正比。"""
    exp = read_dataset(BRUKER / "nus_3d")
    per_group, total_25 = estimate_scan_seconds(exp, 25)
    assert per_group > 0
    assert total_25 > 0
    _per2, total_4 = estimate_scan_seconds(exp, 4)
    assert total_4 < total_25



def test_rank_modes_differ(tmp_path) -> None:
    """净真峰优先 与 一致性优先 会给出不同名次(0.2.199-补29hz-修7)。"""

    class _Mode(tuple):
        pass

    class _Backend:
        def smile_scan(
            self, experiment, params, combos, *, work_dir, evaluate=None,
            progress=None, delete_spectra=True, holdout_ratio=0.0,
        ):
            Path(work_dir).mkdir(parents=True, exist_ok=True)
            cands = []
            for i, combo in enumerate(combos, start=1):
                # 候选1:峰多但留出残差差;候选2:峰少但残差好
                peaks = (
                    [
                        {"position": [1.0, 1.0], "height": 1.0, "snr": 5.0},
                        {"position": [2.0, 2.0], "height": 1.0, "snr": 4.0},
                    ]
                    if i == 1
                    else [{"position": [1.0, 1.0], "height": 1.0, "snr": 3.0}]
                )
                cands.append(
                    {
                        "index": i,
                        "params": dict(combo),
                        "metrics": {
                            "peak_count": len(peaks),
                            "quality": 70.0,
                            "holdout_rmse": 0.5 if i == 1 else 0.01,
                            "holdout_corr": 0.3 if i == 1 else 0.9,
                            "peaks": peaks,
                        },
                        "script": f"# s{i}\n",
                        "ok": True,
                    }
                )
            return {
                "success": True,
                "message": "fake",
                "logs": [],
                "candidates": cands,
                "scan_dir": str(work_dir),
            }

    exp = read_dataset(BRUKER / "nus_3d")
    peak_first = scan_smile_parameters(
        exp, _Backend(), {}, scan_dir=tmp_path / "a",
        grid=smile_grid(2)[:2], rank_mode="true_peaks",
    )
    cons_first = scan_smile_parameters(
        exp, _Backend(), {}, scan_dir=tmp_path / "b",
        grid=smile_grid(2)[:2], rank_mode="consistency",
    )
    assert peak_first["rank_mode"] == "true_peaks"
    assert cons_first["rank_mode"] == "consistency"
    assert peak_first["rows"][0]["index"] == 1   # 峰多的排第一
    assert cons_first["rows"][0]["index"] == 2   # 残差好的排第一

def test_eta_messages_and_single_combo_fallback() -> None:
    """初估→实测更新的提示;单组网格不做跨组合剔除(稳定峰不为 0)。"""
    exp = read_dataset(BRUKER / "nus_3d")
    messages: list[str] = []
    result = scan_smile_parameters(
        exp,
        _FakeBackend(),
        {},
        scan_dir=Path(__file__).resolve().parent / "_tmp_scan_eta",
        grid=smile_grid(2),
        progress=lambda index, total, msg: messages.append(msg),
    )
    assert any("估算" in m for m in messages)      # 跑之前的按数据估算
    assert any("实测" in m for m in messages)      # 第一组之后用实测更新
    assert result["n_combos"] == 4
    assert all(row["stable_count"] > 0 for row in result["rows"])
    assert all("suspect_count" in row and "net_peaks" in row for row in result["rows"])
    # 4 组网格里两个公共峰都稳定出现 → 疑伪峰为 0,净真峰=稳定峰
    assert all(row["suspect_count"] == 0 for row in result["rows"])

    single = scan_smile_parameters(
        exp,
        _FakeBackend(),
        {},
        scan_dir=Path(__file__).resolve().parent / "_tmp_scan_one",
        grid=smile_grid(2)[:1],
    )
    assert single["rows"][0]["stable_count"] > 0   # 单组回退(不再恒 0)
