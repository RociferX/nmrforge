"""Direct dimension window function optimisation test: synthesis FID memory score, explicit
windowless support on the render side."""

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
    """Synthesize direct dimension FID: exponential decay single peak + slight noise (different
    traces and different amplitudes)."""
    rng = np.random.default_rng(7)
    t = np.arange(n, dtype=float)
    sig = np.exp(-t / 120.0) * np.exp(2j * np.pi * 0.11 * t)
    fid = np.tile(sig, (n_traces, 1))
    fid *= rng.uniform(0.5, 2.0, (n_traces, 1))
    fid += rng.normal(0.0, 0.02, (n_traces, n))
    fid += 1j * rng.normal(0.0, 0.02, (n_traces, n))
    return fid


def test_optimize_direct_window_picks_best_candidate() -> None:
    """All candidate scores, the best is the highest score; the returned structure is consistent
    with the configuration."""
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
    """It is best to set changed=False when it is consistent with the existing configuration to
    avoid meaningless writeback."""
    fid = _synth_fid()
    res = optimize_direct_window(fid)
    again = optimize_direct_window(fid, current=res.choice)
    assert again.changed is False
    assert again.choice == res.choice
    # The default current=None should be considered inconsistent with the optimal (unless the
    # optimal happens to be the first candidate).
    assert optimize_direct_window(fid, current={}).changed is True


def test_optimize_direct_window_no_signal_skips_gracefully() -> None:
    """No signal FID does not throw an exception and returns keep-current."""
    res = optimize_direct_window(np.zeros((8, 256), dtype=complex))
    assert res.changed is False
    assert any("skip" in log for log in res.logs)


def test_optimize_direct_window_noise_resolution_trend() -> None:
    """Resolution trend: The windowless (rectangular) main lobe is the narrowest, FWHM should not
    be larger than SP candidate's 1.5x."""
    res = optimize_direct_window(_synth_fid())
    fwhm_by_label = {s["label"]: s["fwhm"] for s in res.scores}
    none_fwhm = fwhm_by_label["No window (linear)"]
    sp_fwhms = [
        v for k, v in fwhm_by_label.items() if k.startswith("SP ")
    ]
    assert sp_fwhms
    assert none_fwhm <= max(sp_fwhms) * 1.5


def test_optimize_direct_window_from_work_missing_fid(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """If the work directory does not have a converted.fid, it will be skipped and the automatic
    path will not be blocked."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    res = optimize_direct_window_from_work(tmp_path, exp)
    assert res.changed is False
    assert any("skip" in log for log in res.logs)


def test_window_line_explicit_none_and_render(
    bruker_dir: Path,
) -> None:
    """Type=none direct dimension remains fixed SP (required by SMILE); explicit SP parameter takes
    effect."""
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
    # 0.2.199-patch11: direct dimension fixed SP, type=none no longer makes step1 windowless.
    assert "| nmrPipe -fn SP -off 0.45 -end 0.98 -pow 2 -c 0.5" in none_script
    custom = generate_3d_nus_script(
        exp,
        window={"F3": {"type": "sine_bell", "off": 0.30, "end": 0.98,
                       "pow": 2, "c": 0.5}},
        **base,
    )
    assert "| nmrPipe -fn SP -off 0.3 -end 0.98 -pow 2 -c 0.5" in custom


def test_window_candidates_include_none() -> None:
    """Both direct/indirect dimension candidate pools must be windowless (windowless is the goal of
    correct optimisation, 0.2.190)."""
    assert {"type": "none"} in DEFAULT_CANDIDATES
    assert {"type": "none"} in INDIRECT_CANDIDATES
    # Direct dimension candidate 0.5-0.98 combination that retains user preference (0.2.189).
    assert (
        {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5}
        in DEFAULT_CANDIDATES
    )


def test_indirect_windows_selects_none_for_decayed_fid() -> None:
    """Indirect dimension natural attenuation FID: The optimiser should be able to correctly select
    no window (0.2.190). resolution restricted indirect dimension score band resolution
    retention factor -- apodisation No window wins when only broadening, mild window will still
    be selected when truncation artifacts are obvious."""
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
    """Indirect dimension truncation FID: The windowless main lobe is the narrowest but has
    ringing. The mild window should be selected for scoring instead of windowless."""
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
    """Work None SMILE reconstruction plane indirect dimension window optimisation skip, not
    blocked."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_3d")
    res = optimize_indirect_windows_from_recon(tmp_path, exp)
    assert res.changed is False
    assert any("skip" in log for log in res.logs)


def test_indirect_windows_missing_fid_skips(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """When there is no converted fid in work, uniform indirect dimension window optimisation is
    skipped and not blocked."""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "hsqc_2d")
    res = optimize_indirect_windows_from_work(tmp_path, exp)
    assert res.changed is False
    assert any("skip" in log for log in res.logs)


def test_gm_in_direct_pool_requires_sw() -> None:
    """GM Add direct dimension default candidate (0.2.192); skip when there is no SW, participate
    in scoring when there is SW. Indirect dimension candidate pool does not add GM: resolution
    is limited indirect dimension apodisation, the artificially high signal-to-noise ratio will
    overturn the windowless selection of the natural attenuation axis (0.2.190 is required to be
    retained)."""
    gm = {"type": "gaussian", "g1": 8.0, "g2": 15.0, "g3": 0.0, "c": 1.0}
    assert gm in DEFAULT_CANDIDATES
    assert not any(c.get("type") == "gaussian" for c in INDIRECT_CANDIDATES)

    fid = _synth_fid()
    no_sw = optimize_direct_window(fid)
    assert not any("GM" in s["label"] for s in no_sw.scores)
    with_sw = optimize_direct_window(fid, sw=20000.0)
    gm_scores = [s for s in with_sw.scores if "GM" in s["label"]]
    assert len(gm_scores) == 1
    # The value is normal (sw=1.0 is not pressed to generate garbage).
    assert gm_scores[0]["fwhm"] < 200.0
