"""直接维窗函数优化测试:合成 FID 内存评分、渲染端显式无窗支持。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from workflow.window_optimize import (
    DEFAULT_CANDIDATES,
    INDIRECT_CANDIDATES,
    WindowOptimizeResult,
    optimize_direct_window,
    optimize_direct_window_from_work,
    optimize_indirect_windows,
    optimize_indirect_windows_from_recon,
    optimize_indirect_windows_from_work,
)


def _synth_fid(n_traces: int = 16, n: int = 512) -> np.ndarray:
    """合成直接维 FID:指数衰减单峰 + 轻微噪声(不同迹不同幅度)。"""
    rng = np.random.default_rng(7)
    t = np.arange(n, dtype=float)
    sig = np.exp(-t / 120.0) * np.exp(2j * np.pi * 0.11 * t)
    fid = np.tile(sig, (n_traces, 1))
    fid *= rng.uniform(0.5, 2.0, (n_traces, 1))
    fid += rng.normal(0.0, 0.02, (n_traces, n))
    fid += 1j * rng.normal(0.0, 0.02, (n_traces, n))
    return fid


def test_optimize_direct_window_picks_best_candidate() -> None:
    """候选全部评分,最优为最高分;返回结构与配置一致。"""
    res = optimize_direct_window(_synth_fid(), sw=20000.0)
    assert isinstance(res, WindowOptimizeResult)
    assert res.choice in DEFAULT_CANDIDATES
    assert len(res.scores) == len(DEFAULT_CANDIDATES)
    assert res.optimal_label
    selected = [s for s in res.scores if s["selected"]]
    assert len(selected) == 1
    max_score = max(s["score"] for s in res.scores)
    assert selected[0]["score"] == max_score
    assert any(res.logs)


def test_optimize_direct_window_changed_flag() -> None:
    """最优与现有配置一致时 changed=False,避免无意义写回。"""
    fid = _synth_fid()
    res = optimize_direct_window(fid)
    again = optimize_direct_window(fid, current=res.choice)
    assert again.changed is False
    assert again.choice == res.choice
    # 默认 current=None 应视为与最优不一致(除非最优恰好是首个候选)
    assert optimize_direct_window(fid, current={}).changed is True


def test_optimize_direct_window_no_signal_skips_gracefully() -> None:
    """无信号 FID 不抛异常,返回 keep-current。"""
    res = optimize_direct_window(np.zeros((8, 256), dtype=complex))
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_optimize_direct_window_noise_resolution_trend() -> None:
    """分辨率趋势:无窗(矩形)主瓣最窄,FWHM 不应大于 SP 候选的 1.5x。"""
    res = optimize_direct_window(_synth_fid())
    fwhm_by_label = {s["label"]: s["fwhm"] for s in res.scores}
    none_fwhm = fwhm_by_label["无窗(线性)"]
    sp_fwhms = [
        v for k, v in fwhm_by_label.items() if k.startswith("SP ")
    ]
    assert sp_fwhms
    assert none_fwhm <= max(sp_fwhms) * 1.5


def test_optimize_direct_window_from_work_missing_fid(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """work 目录无转换后 .fid 时跳过,不阻断自动路径。"""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    res = optimize_direct_window_from_work(tmp_path, exp)
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_window_line_explicit_none_and_render(
    bruker_dir: Path,
) -> None:
    """type=none 直接维仍固定 SP(SMILE 要求);显式 SP 参数生效。"""
    from backend.script_generator import (
        _window_line,
        generate_3d_nus_script,
    )
    from core.data.bruker_reader import read_dataset

    assert _window_line({"type": "none"}) is None
    exp = read_dataset(bruker_dir / "nus_3d")
    base = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3",
                nuslist_count=4)
    default = generate_3d_nus_script(exp, **base)
    assert "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 2 -c 0.5" in default
    none_script = generate_3d_nus_script(
        exp, window={"F3": {"type": "none"}}, **base
    )
    # 0.2.199-补11:直接维固定 SP,type=none 不再使 step1 无窗
    assert "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 2 -c 0.5" in none_script
    custom = generate_3d_nus_script(
        exp,
        window={"F3": {"type": "sine_bell", "off": 0.30, "end": 0.98,
                       "pow": 2, "c": 0.5}},
        **base,
    )
    assert "| nmrPipe -fn SP -off 0.3 -end 0.98 -pow 2 -c 0.5" in custom


def test_window_candidates_include_none() -> None:
    """直接/间接维候选池都必须含无窗(无窗是可正确优化的目标,0.2.190)。"""
    assert {"type": "none"} in DEFAULT_CANDIDATES
    assert {"type": "none"} in INDIRECT_CANDIDATES
    # 直接维候选保留用户偏好的 0.5-0.98 组合(0.2.189)
    assert (
        {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5}
        in DEFAULT_CANDIDATES
    )


def test_indirect_windows_selects_none_for_decayed_fid() -> None:
    """间接维自然衰减 FID:优化器应能正确选出无窗(0.2.190)。

    分辨率受限的间接维评分带分辨率保留因子——加窗只展宽时无窗胜出,
    截断伪影明显时仍会选温和窗。
    """
    rng = np.random.default_rng(3)
    n_f1, n_f2 = 64, 256
    k0 = np.arange(n_f2, dtype=float)
    t1 = np.arange(n_f1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - n_f2 * 0.35) / 1.5)
    fid1 = np.exp(-t1 / 25.0) * np.exp(2j * np.pi * 0.13 * t1)
    planes = np.outer(direct, fid1)
    planes += rng.normal(0.0, 0.02, planes.shape)
    planes += 1j * rng.normal(0.0, 0.02, planes.shape)
    res = optimize_indirect_windows(planes, {"F1": 1})
    assert res.per_axis["F1"].choice.get("type") == "none"
    assert res.changed is True


def test_indirect_windows_picks_window_for_truncated_fid() -> None:
    """间接维截断 FID:无窗主瓣最窄但有振铃,评分应选温和窗而非无窗。"""
    rng = np.random.default_rng(4)
    n_f1, n_f2 = 64, 256
    k0 = np.arange(n_f2, dtype=float)
    t1 = np.arange(n_f1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - n_f2 * 0.35) / 1.5)
    fid1 = np.exp(-t1 / 1000.0) * np.exp(2j * np.pi * 0.13 * t1)
    planes = np.outer(direct, fid1)
    planes += rng.normal(0.0, 0.02, planes.shape)
    planes += 1j * rng.normal(0.0, 0.02, planes.shape)
    res = optimize_indirect_windows(planes, {"F1": 1})
    assert res.per_axis["F1"].choice.get("type") != "none"


def test_indirect_windows_missing_recon_skips(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """work 无 SMILE 重构平面时间接维窗优化跳过,不阻断。"""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    res = optimize_indirect_windows_from_recon(tmp_path, exp)
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_indirect_windows_missing_fid_skips(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """work 无转换后 fid 时 uniform 间接维窗优化跳过,不阻断。"""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "hsqc_2d")
    res = optimize_indirect_windows_from_work(tmp_path, exp)
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_gm_in_direct_pool_requires_sw() -> None:
    """GM 加入直接维缺省候选(0.2.192);无 SW 时跳过,有 SW 时参与评分。

    间接维候选池不加 GM:分辨率受限的间接维加窗信噪比虚高会翻盘自然
    衰减轴的无窗选择(0.2.190 要求保留)。
    """
    gm = {"type": "gaussian", "g1": 8.0, "g2": 15.0, "g3": 0.0, "c": 1.0}
    assert gm in DEFAULT_CANDIDATES
    assert not any(c.get("type") == "gaussian" for c in INDIRECT_CANDIDATES)

    fid = _synth_fid()
    no_sw = optimize_direct_window(fid)
    assert not any("GM" in s["label"] for s in no_sw.scores)
    with_sw = optimize_direct_window(fid, sw=20000.0)
    gm_scores = [s for s in with_sw.scores if "GM" in s["label"]]
    assert len(gm_scores) == 1
    assert gm_scores[0]["fwhm"] < 200.0  # 数值正常(未按 sw=1.0 产生垃圾)
