"""Golden vector (conformance vector): prove "same behaviour" in under a minute.

A fixed **tiny, deterministic** 2D synthetic spectrum plus fixed parameters, publishing the
expected spectrum/peak-table SHA-256. Given the ``golden`` block from the manifest,
downstream can run the same recipe locally and confirm the behaviour has not moved, without
re-running their own data.

The recipe (**deliberately free of machine-dependent inputs**):

- pure numpy generates three Gaussian peaks plus fixed-seed noise, and nmrglue
  ``pipe.write`` writes a float32 ft2;
- detection/localization get explicit threshold and margin (``sigma_multiplier=20``,
  ``edge_margin_ppm=0.30``) and **read no local config**; the method is fixed to parabolic
  (no dependence on the fitter version);
- the peak table is written under the unified schema, so **a column-order change shows up
  in the hash as well**.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from core.project.manager import sha256_file

#: golden-vector name (the declaration's ``golden.name`` must match it)
GOLDEN_NAME = "conformance_v1"
#: fixed geometry: data axis 0 = indirect (15N, 64 points), axis 1 = direct (1H, 128 points)
_N15_OBS, _N15_SW, _N15_CAR, _N15_SIZE = 60.8, 2000.0, 118.0, 64
_H1_OBS, _H1_SW, _H1_CAR, _H1_SIZE = 600.0, 6000.0, 4.7, 128
#: (axis-0 index, axis-1 index, amplitude)
_PEAKS = ((30, 60, 140.0), (45, 90, 120.0), (18, 100, 100.0))
_NOISE_SEED = 20260920
_NOISE_SIGMA = 0.4
#: explicit parameters (no config is read, so machines agree)
GOLDEN_SIGMA_MULTIPLIER = 20.0
GOLDEN_EDGE_MARGIN_PPM = 0.30


def build_golden_spectrum(path: Path | str) -> Path:
    """Write the golden synthetic spectrum (deterministic: same bytes on any platform).

    Parameters
    ----------
    path : Path | str
         target ``.ft2`` path; parent directories are created.

    Returns
    -------
    Path
        the written spectrum path.

    Side effects
    ------------
     Overwrites the target file.
    """
    from nmrglue.fileio import pipe

    target = Path(path)
    shape = (_N15_SIZE, _H1_SIZE)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = np.zeros(shape, dtype=float)
    for cy, cx, amplitude in _PEAKS:
        arr += amplitude * np.exp(
            -(((yy - cy) ** 2) / (2 * 1.2**2) + ((xx - cx) ** 2) / (2 * 1.4**2))
        )
    rng = np.random.default_rng(_NOISE_SEED)
    arr += rng.normal(0.0, _NOISE_SIGMA, shape)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = shape[1]
    dic["FDSPECNUM"] = shape[0]
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDDIMORDER"] = [2, 1]
    dic["FDF1LABEL"] = "N15"
    dic["FDF2LABEL"] = "H1"
    dic["FDF1SW"] = str(_N15_SW)
    dic["FDF1OBS"] = str(_N15_OBS)
    dic["FDF1CAR"] = str(_N15_CAR)
    dic["FDF1ORIG"] = "0"
    dic["FDF2SW"] = str(_H1_SW)
    dic["FDF2OBS"] = str(_H1_OBS)
    dic["FDF2CAR"] = str(_H1_CAR)
    dic["FDF2ORIG"] = "0"
    target.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(target), dic, arr.astype(np.float32), overwrite=True)
    return target


def golden_hashes(workdir: Path | str | None = None) -> dict[str, Any]:
    """Run the golden recipe -> ``{name, spectrum_sha256, peak_table_sha256, n_peaks}``.

    Parameters
    ----------
    workdir : Path | str, optional
        artefact directory; a temporary directory by default (discarded, not kept in the repo).

    Returns
    -------
    dict[str, Any]
        the vector name and both SHA-256 values, plus the number of detected peaks.

    Side effects
    ------------
     Writes ``conformance.ft2`` and ``conformance_peak_table.csv`` under ``workdir``.
    """
    from nmrforge_api.peak_tables import write_peak_table
    from nmrforge_api.peaks import detect_and_localize

    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="nmrforge_golden_") as tmp:
            return golden_hashes(tmp)
    base = Path(workdir)
    spectrum = build_golden_spectrum(base / "conformance.ft2")
    rows, _meta = detect_and_localize(
        spectrum,
        method="parabolic",
        sigma_multiplier=GOLDEN_SIGMA_MULTIPLIER,
        edge_margin_ppm=GOLDEN_EDGE_MARGIN_PPM,
    )
    table = write_peak_table(base / "conformance_peak_table.csv", rows)
    return {
        "name": GOLDEN_NAME,
        "spectrum_sha256": sha256_file(spectrum),
        "peak_table_sha256": sha256_file(table),
        "n_peaks": len(rows),
    }


def check_conformance(
    expected: dict[str, Any] | None = None,
    *,
    workdir: Path | str | None = None,
) -> dict[str, Any]:
    """Run the golden recipe and compare it item by item with the expected hashes.

    Parameters
    ----------
    expected : dict, optional
        the expected vector (the declaration's ``golden`` by default).
    workdir : Path | str, optional
        artefact directory; a temporary directory by default.

    Returns
    -------
    dict[str, Any]
        ``{name, match, items, expected, actual}``: ``items`` holds one boolean per item
        (``spectrum_sha256`` / ``peak_table_sha256`` / ``n_peaks``).
    """
    if expected is None:
        from nmrforge_api.compat import declaration

        declared = declaration().get("golden") or {}
        expected = dict(declared) if isinstance(declared, dict) else {}
    actual = golden_hashes(workdir)
    keys = ("spectrum_sha256", "peak_table_sha256", "n_peaks")
    items = {
        key: bool(str(expected.get(key, "")) or expected.get(key) is not None)
        and expected.get(key) == actual.get(key)
        for key in keys
    }
    return {
        "name": actual["name"],
        "match": all(items.values()),
        "items": items,
        "expected": {key: expected.get(key) for key in keys},
        "actual": actual,
    }


__all__ = [
    "GOLDEN_EDGE_MARGIN_PPM",
    "GOLDEN_NAME",
    "GOLDEN_SIGMA_MULTIPLIER",
    "build_golden_spectrum",
    "check_conformance",
    "golden_hashes",
]
