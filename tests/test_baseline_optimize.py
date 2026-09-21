"""Dimension-by-dimensional baseline optimisation test (G2B-007)."""

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
    """Direct dimension (F2) linear drift: optimisation, choose correction (off score is lower than
    optimal)."""
    spec = np.zeros((32, 64))
    spec += np.linspace(-50.0, 50.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")

    result = optimize_baseline(experiment, ft2)
    assert "F2" in result.baseline and "F1" in result.baseline
    assert "off:0" in result.scores["F2"]
    # Direct dimension exists correction, and off is not optimal.
    assert result.baseline["F2"]["enabled"] is True
    assert result.scores["F2"]["off:0"] < max(result.scores["F2"].values())
    joined = "\n".join(result.logs)
    assert "F2" in joined and "Baseline has been optimised" in joined
    assert "F1" in joined and "Not optimisation" in joined


def test_optimize_baseline_grid_contains_off_and_orders(
    tmp_path: Path, bruker_dir: Path
) -> None:
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    # Medium quality spectrum (off score <95): candidate grid needs to be actually run.
    spec = np.full((32, 64), 100.0)
    spec += np.linspace(-1.0, 1.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "mid.ft2"
    _write_ft2(ft2, spec)
    result = optimize_baseline(experiment, ft2)
    assert result.scores["F2"]["off:0"] < 95.0
    assert result.scores["F2"]["off:0"] >= max(result.scores["F2"].values()) - 1e-9
    assert "auto:1" in result.scores["F2"]
    assert "order:3" in result.scores["F2"]


def test_optimize_baseline_good_baseline_skips_grid(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch8: Axis with off score >= 95 skips the candidate grid and remains off
    directly."""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    spec = np.zeros((32, 64))
    ft2 = tmp_path / "flat.ft2"
    _write_ft2(ft2, spec)
    result = optimize_baseline(experiment, ft2)
    assert set(result.scores["F2"]) == {"off:0"}
    assert result.scores["F2"]["off:0"] == 100.0
    assert result.baseline["F2"]["enabled"] is False
    assert any("The baseline is good" in line for line in result.logs)


def test_optimize_baseline_reports_progress(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch7: baseline optimisation outputs progress axis-by-axis/candidate (candidate is
    run only when off score <95)."""
    spec = np.full((32, 64), 100.0)
    spec += np.linspace(-1.0, 1.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    messages: list[str] = []
    optimize_baseline(experiment, ft2, progress=messages.append)
    assert any("axis F1" in m for m in messages)
    assert any("score=" in m for m in messages)


def test_optimize_baseline_cancelled_raises(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch7: When the cancellation flag is set, baseline optimisation immediately throws
    an exception and exits (off points <95)."""
    import pytest

    spec = np.full((32, 64), 100.0)
    spec += np.linspace(-1.0, 1.0, 64)[np.newaxis, :]
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    with pytest.raises(RuntimeError, match="Task cancelled"):
        optimize_baseline(experiment, ft2, cancel=lambda: True)


def test_decimated_reduces_traces() -> None:
    """0.2.199-patch8: Trace subsampling reduces the number of candidate scoring traces to the
    upper limit."""
    from workflow.baseline_optimize import _decimated

    arr = np.zeros((120, 80, 200))
    for axis in range(3):
        dec = _decimated(arr, axis, max_traces=4096)
        n = dec.shape[axis]
        assert dec.size // n <= 4096
        assert dec.ndim == 3


def test_optimize_baseline_small_max_traces_works(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.199-patch8: Baseline optimisation under max_traces still returns a valid
    configuration."""
    spec = np.zeros((32, 64))
    ft2 = tmp_path / "spec.ft2"
    _write_ft2(ft2, spec)
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    result = optimize_baseline(experiment, ft2, max_traces=8)
    assert "F2" in result.baseline and "F1" in result.baseline
    assert "off:0" in result.scores["F2"]
