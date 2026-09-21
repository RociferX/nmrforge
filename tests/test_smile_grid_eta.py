"""SMILE optimisation degree (2x2..5x5) and estimated time consumption (0.2.199-patch29hz-fix 4)."""

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
    """Fake backend: Each group has a "candidate spectrum", which is deleted after evaluation
    (consistent with the real semantics)."""

    def smile_scan(
        self, experiment, params, combos, *, work_dir, evaluate=None, progress=None,
        delete_spectra=True, holdout_ratio=0.0,
    ):
        self.holdout_ratio = holdout_ratio
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress(0, len(combos), "direct dimension processing (generating slices)..")
        candidates = []
        for index, combo in enumerate(combos, start=1):
            if progress is not None:
                progress(index, len(combos), f"scanning {index}/{len(combos)}")
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
    """2x2..5x5 corresponds to the 4/9/16/25 group, and the first and last gears are always in."""
    assert (SMILE_GRID_MIN, SMILE_GRID_MAX) == (2, 5)
    assert len(smile_grid(2)) == 4
    assert len(smile_grid(3)) == 9
    assert len(smile_grid(4)) == 16
    assert len(smile_grid(5)) == 25
    assert smile_grid(5)[0] == {"nsigma": 3.0, "thresh": 0.9}
    assert smile_grid(5)[-1] == {"nsigma": 7.0, "thresh": 0.99}
    # Out-of-bounds input converges to legal range.
    assert len(smile_grid(1)) == 4
    assert len(smile_grid(9)) == 25


def test_estimate_scan_seconds_scales() -> None:
    """Rough estimate based on data size: positive number, proportional to the number of groups."""
    exp = read_dataset(BRUKER / "nus_3d")
    per_group, total_25 = estimate_scan_seconds(exp, 25)
    assert per_group > 0
    assert total_25 > 0
    _per2, total_4 = estimate_scan_seconds(exp, 4)
    assert total_4 < total_25



def test_rank_modes_differ(tmp_path) -> None:
    """Jingzhen Peak Priority and Consistency Priority will give different rankings
    (0.2.199-patch29hz-Xiu 7)."""

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
                # Candidate 1: many peaks but good residuals; candidate 2: few peaks but good
                # residuals.
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
    assert peak_first["rows"][0]["index"] == 1   # The one with the most peaks ranks first.
    assert cons_first["rows"][0]["index"] == 2   # The best residuals rank first.

def test_eta_messages_and_single_combo_fallback() -> None:
    """Initial estimate -> measured update prompt; no cross-combination elimination is performed
    for a single group of grids (the stable peak is not 0)."""
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
    assert any("Estimate" in m for m in messages)      # Estimate based on data before running.
    # Update with measured after the first group.
    assert any("Measured approx." in m for m in messages)
    assert result["n_combos"] == 4
    assert all(row["stable_count"] > 0 for row in result["rows"])
    assert all("suspect_count" in row and "net_peaks" in row for row in result["rows"])
    # Two common peaks appear stably in the 4 sets of grids -> spurious peak is 0, net true peak =
    # stable peak.
    assert all(row["suspect_count"] == 0 for row in result["rows"])

    single = scan_smile_parameters(
        exp,
        _FakeBackend(),
        {},
        scan_dir=Path(__file__).resolve().parent / "_tmp_scan_one",
        grid=smile_grid(2)[:1],
    )
    assert single["rows"][0]["stable_count"] > 0   # Single group rollback (no longer constant 0).

# ---------------------------------------------------------------------------
# SMILE candidate evaluation threshold (0.2.199-patch29hz-fix 16).
# ---------------------------------------------------------------------------


def _two_peak_spectrum():
    """A composite two-dimensional spectrum of a strong peak + a weak true peak (about 5σ)."""
    import numpy as np

    rng = np.random.default_rng(3)
    arr = rng.standard_normal((64, 64)) * 1.0  # Noise σ ≈ 1.
    arr[20, 30] = 50.0  # Qiangfeng.
    arr[44, 12] = 5.5  # Weak true peak.
    return arr


def test_smile_scan_uses_low_threshold() -> None:
    """A low threshold (3σ) is used for candidate evaluation: weak true peaks must also be detected
    and cannot be missed according to the 35σ in the peak selection step."""
    import numpy as np

    from core.qc import peak_detection
    from workflow.smile_optimize import SMILE_SCAN_SIGMA, evaluate_candidate_peaks

    arr = _two_peak_spectrum()
    peaks = evaluate_candidate_peaks(arr, sign_mode="positive")
    positions = {(int(round(p.position[0])), int(round(p.position[1]))) for p in peaks}

    assert SMILE_SCAN_SIGMA <= 5.0  # Low threshold level.
    assert (20, 30) in positions  # Qiangfeng.
    assert (44, 12) in positions  # Weak true peak (about 5σ).

    # Control: According to the default threshold (35σ) of the peak selection step, this weak true
    # peak will be missed.
    strict = peak_detection.detect(
        np.asarray(arr),
        peak_detection.PeakDetectionParams(
            sigma_multiplier=35.0, min_snr=35.0, sign_mode="positive"
        ),
    )
    strict_positions = {
        (int(round(p.position[0])), int(round(p.position[1]))) for p in strict
    }
    assert (44, 12) not in strict_positions


def test_smile_scan_sign_mode_follows_preset(monkeypatch) -> None:
    """The symbolic mode has the same origin as the peak selection step: mixed preset ->
    both,uniform/unknown -> dominant."""
    from types import SimpleNamespace

    import core.experiments.registry as registry
    from workflow.smile_optimize import smile_scan_sign_mode

    exp = SimpleNamespace(experiment_type=SimpleNamespace(name="X"))

    monkeypatch.setattr(
        registry, "get", lambda name: SimpleNamespace(peak_sign="mixed")
    )
    assert smile_scan_sign_mode(exp) == "both"

    monkeypatch.setattr(
        registry, "get", lambda name: SimpleNamespace(peak_sign="uniform")
    )
    assert smile_scan_sign_mode(exp) == "dominant"

    monkeypatch.setattr(registry, "get", lambda name: None)
    assert smile_scan_sign_mode(exp) == "dominant"


def test_smile_scan_edge_margin_matches_pick_peaks() -> None:
    """The axial peak exclusion and peak selection steps have the same constant (do not write a
    number each)."""
    from workflow.pick_peaks import PICK_EDGE_MARGIN
    from workflow.smile_optimize import smile_scan_edge_margin

    assert smile_scan_edge_margin() == int(PICK_EDGE_MARGIN)


def test_smile_scan_uses_candidate_axis_and_reports_evaluated_margin(
    tmp_path, monkeypatch
) -> None:
    """The actual spectral axis after clipping/zero filling determines the margin, and log must
    write the actual number of points after evaluation."""
    from types import SimpleNamespace

    import numpy as np

    from workflow.smile_optimize import smile_scan_edge_margin

    axis0 = np.linspace(4.0, -3.75, 32)  # 0.25 ppm/Point.
    spectrum = SimpleNamespace(
        data=np.zeros((32, 64), dtype=float),
        ppm=[axis0, np.linspace(10.0, 0.0, 64)],
        nuclei=["15N", "1H"],
        obs=[60.0, 600.0],
    )
    monkeypatch.setattr(
        "workflow.pick_peaks.read_spectrum_axes", lambda _path: spectrum
    )
    monkeypatch.setattr(
        "workflow.smile_optimize.evaluate_candidate_peaks",
        lambda *_args, **_kwargs: [],
    )

    class _EvaluatingBackend:
        def smile_scan(
            self, experiment, params, combos, *, work_dir, evaluate=None,
            progress=None, delete_spectra=True, holdout_ratio=0.0,
        ):
            assert evaluate is not None
            metrics = evaluate("candidate.ft2")
            return {
                "success": True,
                "message": "ok",
                "logs": [],
                "candidates": [
                    {
                        "index": 1,
                        "params": dict(combos[0]),
                        "metrics": metrics,
                        "script": "# candidate\n",
                        "ok": True,
                    }
                ],
                "scan_dir": str(work_dir),
            }

    exp = read_dataset(BRUKER / "nus_2d")
    expected = smile_scan_edge_margin(
        axis_ppm=axis0, nucleus="15N", obs_mhz=60.0
    )
    assert expected == 3
    result = scan_smile_parameters(
        exp,
        _EvaluatingBackend(),
        {},
        scan_dir=tmp_path / "actual-axis",
        grid=[{"nsigma": 5.0, "thresh": 0.95}],
    )
    assert f"{expected}–{expected} point" in result["logs"][0]
    assert "Not evaluated" not in result["logs"][0]


def test_rank_mode_selects_run_mode(tmp_path) -> None:
    """Modification 23: The sorting caliber determines the operation mode (net true peak -> full
    sampling holdout=0; consistency -> hold out)."""
    seen: list[float] = []

    class _Backend:
        def smile_scan(
            self, experiment, params, combos, *, work_dir, evaluate=None,
            progress=None, delete_spectra=True, holdout_ratio=0.0,
        ):
            seen.append(float(holdout_ratio))
            Path(work_dir).mkdir(parents=True, exist_ok=True)
            return {
                "success": True,
                "message": "fake",
                "logs": [],
                "candidates": [
                    {
                        "index": 1,
                        "params": dict(combos[0]),
                        "metrics": {"peak_count": 1, "quality": 50.0, "peaks": []},
                        "script": "# s1" + chr(10),
                        "ok": True,
                    }
                ],
                "scan_dir": str(work_dir),
            }

    exp = read_dataset(BRUKER / "nus_3d")
    scan_smile_parameters(
        exp, _Backend(), {}, scan_dir=tmp_path / "p",
        grid=smile_grid(2)[:1], rank_mode="true_peaks",
    )
    scan_smile_parameters(
        exp, _Backend(), {"holdout_ratio": 0.25}, scan_dir=tmp_path / "c",
        grid=smile_grid(2)[:1], rank_mode="consistency",
    )
    assert seen == [0.0, 0.25]


def test_pick_edge_margin_is_public() -> None:
    """Fix 24: Peak selection/SMILE Make shared constants public (old private names remain
    compatible)."""
    from workflow.pick_peaks import _PICK_EDGE_MARGIN, PICK_EDGE_MARGIN

    assert PICK_EDGE_MARGIN == _PICK_EDGE_MARGIN
