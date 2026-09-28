"""Direct-dimension window function optimisation tests: synthetic FID in-memory scoring and
explicit no-window support on the rendering side."""

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
    """Synthetic direct-dimension FID: an exponentially decaying single peak plus slight noise
    (different traces, different amplitudes)."""
    rng = np.random.default_rng(7)
    t = np.arange(n, dtype=float)
    sig = np.exp(-t / 120.0) * np.exp(2j * np.pi * 0.11 * t)
    fid = np.tile(sig, (n_traces, 1))
    fid *= rng.uniform(0.5, 2.0, (n_traces, 1))
    fid += rng.normal(0.0, 0.02, (n_traces, n))
    fid += 1j * rng.normal(0.0, 0.02, (n_traces, n))
    return fid


def test_optimize_direct_window_picks_best_candidate() -> None:
    """Every candidate is scored, the best one is the highest score; the returned structure matches
    the configuration."""
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
    """changed=False when the optimum matches the current configuration, which avoids a pointless
    write-back."""
    fid = _synth_fid()
    res = optimize_direct_window(fid)
    again = optimize_direct_window(fid, current=res.choice)
    assert again.changed is False
    assert again.choice == res.choice
    # A default current=None counts as differing from the optimum, unless the optimum is the first
    # candidate
    assert optimize_direct_window(fid, current={}).changed is True


def test_optimize_direct_window_no_signal_skips_gracefully() -> None:
    """A signal-free FID raises nothing and returns keep-current."""
    res = optimize_direct_window(np.zeros((8, 256), dtype=complex))
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_optimize_direct_window_noise_resolution_trend() -> None:
    """Resolution trend: no window (rectangular) has the narrowest main lobe, so FWHM must not
    exceed 1.5x that of the SP candidates."""
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
    """Skipped when the work directory has no converted .fid, without blocking the automatic
    path."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    res = optimize_direct_window_from_work(tmp_path, exp)
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_window_line_explicit_none_and_render(
    bruker_dir: Path,
) -> None:
    """With type=none the direct dimension still gets a fixed SP (a SMILE requirement); an explicit
    SP parameter takes effect."""
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
    # 0.2.199-patch11: the direct dimension keeps a fixed SP, type=none no longer makes step1
    # windowless
    assert "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 2 -c 0.5" in none_script
    custom = generate_3d_nus_script(
        exp,
        window={"F3": {"type": "sine_bell", "off": 0.30, "end": 0.98,
                       "pow": 2, "c": 0.5}},
        **base,
    )
    assert "| nmrPipe -fn SP -off 0.3 -end 0.98 -pow 2 -c 0.5" in custom


def test_window_candidates_include_none() -> None:
    """Both the direct and indirect candidate pools must contain no window (no window is a correctly
    optimisable target, 0.2.190)."""
    assert {"type": "none"} in DEFAULT_CANDIDATES
    assert {"type": "none"} in INDIRECT_CANDIDATES
    # The direct candidates keep the user's preferred 0.5-0.98 combination (0.2.189)
    assert (
        {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5}
        in DEFAULT_CANDIDATES
    )


def test_indirect_windows_selects_none_for_decayed_fid() -> None:
    """Indirect-dimension naturally decaying FID: the optimiser must correctly select no window
    (0.2.190).

    The resolution-limited indirect-dimension score carries a resolution-retention factor -- when
    apodisation only broadens the peak, no window wins; when the truncation artefact is obvious, a
    mild window is still chosen.
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
    """Indirect-dimension truncated FID: no window has the narrowest main lobe but rings, so the
    score should pick a mild window instead of no window."""
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
    """Indirect-dimension window optimisation is skipped when the work directory has no SMILE
    reconstructed plane, without blocking."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    res = optimize_indirect_windows_from_recon(tmp_path, exp)
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_indirect_windows_missing_fid_skips(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Uniform indirect-dimension window optimisation is skipped when the work directory has no
    converted fid, without blocking."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "hsqc_2d")
    res = optimize_indirect_windows_from_work(tmp_path, exp)
    assert res.changed is False
    assert any("跳过" in log for log in res.logs)


def test_gm_in_direct_pool_requires_sw() -> None:
    """GM joins the direct-dimension default candidates (0.2.192); it is skipped without SW and
    scored with SW.

    The indirect candidate pool gets no GM: the inflated SNR of an apodised resolution-limited
    indirect dimension would overturn the no-window choice of a naturally decaying axis (0.2.190
    requires keeping it).
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
    assert gm_scores[0]["fwhm"] < 200.0  # plausible value (no garbage from sw=1.0)
