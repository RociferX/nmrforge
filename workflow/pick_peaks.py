"""Peak selection step (Contract §6 peak table format / G2B-004). Input spectrum
(spectra/<exp_id>-<data_id>.ft2|ft3) -> core/qc/peak_detection Detection -> Write peak table
Poky.list(data_dir(..., "peaks")/<exp_id>-<data_id>.list) ->
WorkflowRun(workflow_ref="pick_peaks") registration; fails finish_run("failed") and throws an
exception with information. Peak symbol rules (0.2.199-patch29ap, user): experiment type single
symbol (uniform,presets peak_sign=uniform) only selects the peak occupying the main symbol (does
not care about the sign, the symbol with the highest candidate peak count shall prevail);
experiment type Both positive and negative (mixed) are selected. The experiment type name is
taken from the imported metadata experiment_type.name, and the template is missing and falls
back to uniform. Threshold (0.2.199-patch29aq/patch29ar/patch29cm/patch29gc, user): The default
is 35σ (5σ -> 6σ -> 15σ -> 25σ), which can be adjusted by the GUI threshold bar through the
sigma_multiplier parameter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.peaks import axis_units
from core.peaks.localize import (
    DEFAULT_LOCALIZATION_METHOD,
    load_localization_defaults,
    localization_label,
    localize_peak,
    normalize_localization_method,
    summarize_localization,
    write_localization_records,
)
from core.peaks.peak_table import save_peaks
from core.project import ProjectManager
from core.qc import peak_detection
from ui_support.i18n import tr

# 0.2.199-patch29df: Core name collection (used for axis mapping, homologous to viewer/spectrum).
_NUCLEUS_SET = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}


class PickPeaksError(Exception):
    """Peak picking error (Spectrum missing/Read failed/Checkout failed)."""


# The default threshold for peak selection (0.2.199-patch29aq 5σ -> patch29ar 6σ -> patch29cm 15σ ->
# patch29gc 25σ -> patch29hn 35σ; the current default is 35σ). The default detection algorithm is 3σ
# for QC to use, and the peak selection step uses a stricter threshold.
_PICK_THRESHOLD_SIGMA = 35.0
# Number of edge points for axial peak exclusion (0.2.199-patch29at/patch29bf, user): Peaks within
# the upper and lower edge bars are not selected; patch29bf is increased from 2 to 5, and axial peak
# residues close to the edge are also excluded. Axial peak exclusion margin (points): peak selection
# is shared with SMILE candidate evaluation (revised as a public constant from 24).
PICK_EDGE_MARGIN = 5
_PICK_EDGE_MARGIN = PICK_EDGE_MARGIN  # Compatible with old names.
# 2026-09-13 (user plan A): The margin is defined by **physical width** by default (see
# core.peaks.axis_units: default = 3 x nuclide line width of this axis (Hz) converted ppm), and is
# converted into points according to the current spectrum point distance during runtime; the above
# PICK_EDGE_MARGIN (points) is only used as a fallback when conversion cannot be performed (missing
# OBS/point distance). spectrum mixed spectrum Evidence (0.2.199-patch29fc, user): Type low
# confidence/unknown, if the proportion of positive and negative peaks in the spectrum is high
# (minor symbols >= number of major symbols x 0.2 and >= 3, total number >= 6), press mixed to
# select both positive and negative; high-confidence uniform template (such as HSQC) still respects
# the template to avoid noise negative peak band bias.
_MIXED_MIN_MINOR = 3
_MIXED_COUNT_SHARE = 0.20   # Number of minority symbols >= Number of major symbols x 0.2.
# The sum of the absolute strengths of the minority symbols >= the main symbol x 0.15.
_MIXED_INTEN_SHARE = 0.15
_MIXED_MIN_TOTAL = 6
# Anti-pollution: A few symbols cannot be dominated by a single extremely strong peak -- the
# intensity of the second strongest peak >= the strongest x 0.25.
_MIXED_OUTLIER_RATIO = 0.25


def _ppm_axis(dic: dict[str, Any], prefix: str, size: int) -> np.ndarray:
    """NMRPipe head structure ppm axis (ORIG preferential fallback CAR, consistent with
    viewer/spectrum contract)."""
    obs = float(dic.get(prefix + "OBS", 0.0) or 0.0)
    sw = float(dic.get(prefix + "SW", 0.0) or 0.0)
    orig = float(dic.get(prefix + "ORIG", 0.0) or 0.0)
    carrier = float(dic.get(prefix + "CAR", 0.0) or 0.0)
    idx = np.arange(size)
    if obs and orig:
        return orig / obs + (size - 1 - idx) * (sw / (size * obs))
    if obs and sw:
        return carrier + (size / 2 - idx) * sw / (size * obs)
    return np.zeros(size)


def _ppm_at_fraction(axis_ppm: np.ndarray, value: float) -> float:
    """Sub-pixel index -> ppm linear interpolation (0.2.199-patch29eo: write interpolation ppm
    after peak sub-pixel correction, the same semantics as viewer/spectrum.ppm_at_f)."""
    n = axis_ppm.size
    if n < 2:
        return float(axis_ppm[0]) if n else 0.0
    i0 = max(0, int(np.floor(value)))
    i1 = min(i0 + 1, n - 1)
    i0 = min(i0, i1)
    frac = value - i0
    return float(axis_ppm[i0] * (1.0 - frac) + axis_ppm[i1] * frac)


def _fdf_prefix(dic: dict[str, Any], ndim: int, axis_idx: int) -> str:
    """Data axis axis_idx corresponds to the FDF block prefix ('FDF1'/'FDF2'/...). Same origin as
    viewer/spectrum._fdf_prefix_for_axis (0.2.151): nmrglue pipe.read The data axis order is
    opposite to the storage order, and the logical dimension number of each axis is given by
    FDDIMORDER (axis i ↔ FDF{FDDIMORDER[ndim-1-i]});Missing/Illegal rollback to old position."""
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        order = []
    if len(order) >= ndim:
        dim = order[ndim - 1 - axis_idx]
        if 1 <= dim <= 4:
            return f"FDF{dim}"
    return f"FDF{axis_idx + 1}"


def _logical_axis_indices(dic: dict[str, Any], ndim: int) -> list[int]:
    """The data axis index corresponding to the logical dimension F{k+1}(k=0..ndim-1);FDDIMORDER is
    an illegal fallback position formula. When 3D is common ORDER 2 3 1, the data axis sequence
    is (F1, F3, F2), and the peak table F1/F2/F3_shift must obtain the corresponding data axis
    ppm according to the logical dimension, otherwise F2/F3 interchange (0.2.199-patch29ap
    repair)."""
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        order = []
    if len(order) >= ndim and sorted(order[:ndim]) == list(range(1, ndim + 1)):
        indices: list[int] = [0] * ndim
        for axis_idx in range(ndim):
            dim = order[ndim - 1 - axis_idx]
            indices[dim - 1] = axis_idx
        return indices
    return list(range(ndim))

# NMRPipe/Sparky Common non-standard LABEL alias (0.2.199-patch29dh): Real data 1H axis is often
# written as "HN" (sampleC, cc, etc. 30.ft3/d_011.ft3 measured), single letters are common
# abbreviations.
_NMRPIPE_LABEL_ALIASES: dict[str, str] = {
    "HN": "1H",
    "H": "1H",
    "N": "15N",
    "C": "13C",
    "P": "31P",
    "F": "19F",
    "D": "2H",
    "NA": "23Na",
    "SI": "29Si",
}


def _parse_nmrpipe_label(label: str) -> str:
    """NMRPipe FDF*LABEL('N15'/'H1'/'C13', same core index '15Nx', alias 'HN') -> core name; return
    '' on failure."""
    text = str(label or "").strip().upper()
    if not text:
        return ""
    if text in _NUCLEUS_SET:
        return text
    if text in _NMRPIPE_LABEL_ALIASES:
        return _NMRPIPE_LABEL_ALIASES[text]
    if text[-1:] in ("X", "Y", "Z") and text[:-1] in _NUCLEUS_SET:
        return text[:-1]
    digits = "".join(ch for ch in text if ch.isdigit())
    letters = "".join(ch for ch in text if ch.isalpha())
    candidate = f"{digits}{letters}" if digits and letters else ""
    return candidate if candidate in _NUCLEUS_SET else ""


# Nuclear gyromagnetic ratio (relative to 1H) and common 1H frequency field strength, infer the
# nucleus according to the observed frequency (0.2.199-patch29dh: has the same origin as
# viewer.axis_labels.infer_nucleus; the old implementation ratio direction is inverted and only
# recognizes 600 MHz -- Non-600 MHz spectrum and 15N/13C's OBS inference all failed).
_NUCLEUS_RATIOS: dict[str, float] = {
    "1H": 1.0,
    "2H": 0.15351,
    "13C": 0.25145,
    "15N": 0.10137,
    "19F": 0.94077,
    "31P": 0.40481,
    "23Na": 0.26452,
    "29Si": 0.19837,
}
_COMMON_B0_H1 = (
    300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 850.0, 900.0, 950.0,
    1000.0, 1100.0, 1200.0, 1300.0, 1500.0, 2000.0,
)


def _infer_nucleus_obs(obs: float) -> str:
    """The nucleus is inferred based on the observation frequency OBS(MHz) and the nuclear
    gyromagnetic ratio (same origin as viewer.axis_labels)."""
    if not obs or obs <= 0:
        return ""
    best, best_err = "", float("inf")
    for nucleus, ratio in _NUCLEUS_RATIOS.items():
        implied_1h = obs / ratio
        if not (300.0 <= implied_1h <= 2100.0):
            continue
        err = min(abs(implied_1h - b0) for b0 in _COMMON_B0_H1) / implied_1h
        if err < best_err:
            best, best_err = nucleus, err
    return best if best_err < 0.05 else ""


def _storage_nuclei(dic: dict[str, Any], prefixes: tuple[str, ...]) -> list[str]:
    """Press the NMRPipe header to infer the core of each storage axis: LABEL gives priority, OBS
    takes the bottom."""
    nuclei: list[str] = []
    for prefix in prefixes:
        nucleus = _parse_nmrpipe_label(dic.get(prefix + "LABEL", ""))
        if not nucleus:
            try:
                obs = float(dic.get(prefix + "OBS", 0) or 0)
            except (TypeError, ValueError):
                obs = 0.0
            nucleus = _infer_nucleus_obs(obs)
        nuclei.append(nucleus)
    return nuclei


def _permutation_to_logical(
    storage_nuclei: list[str], logical_nuclei: list[str]
) -> list[int] | None:
    """Storage axis -> logical position arrangement; cannot form an arrangement and returns None
    (same origin as viewer)."""
    n = len(storage_nuclei)
    if n != len(logical_nuclei) or n == 0:
        return None
    if any(not s for s in storage_nuclei) or any(not t for t in logical_nuclei):
        return None
    perm: list[int | None] = [None] * n
    used = [False] * n
    for lpos, target in enumerate(logical_nuclei):
        for spos, source in enumerate(storage_nuclei):
            if source == target and not used[spos]:
                perm[spos] = lpos
                used[spos] = True
                break
        else:
            return None
    return [int(p) for p in perm]


def _logical_nuclei_from_order(
    dic: dict[str, Any], ndim: int, storage_nuclei: list[str]
) -> list[str] | None:
    """Press FDDIMORDER to infer the logical sequence core list (F1/F2/F3 sequence); cannot form an
    arrangement and return None. Same as viewer/spectrum (0.2.152): the logical dimension number
    of nmrglue data axis i = FDDIMORDER[ndim-1-i], based on which the storage sequence core is
    mapped back to the logical sequence."""
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        return None
    if len(order) < ndim:
        return None
    logical: list[str | None] = [None] * ndim
    for axis_idx, nucleus in enumerate(storage_nuclei):
        dim = order[ndim - 1 - axis_idx]
        if not (1 <= dim <= ndim) or logical[dim - 1] is not None:
            return None
        logical[dim - 1] = nucleus
    if any(n is None for n in logical):
        return None
    return [n for n in logical if n is not None]  # type: ignore[return-value]



def _axes_ppm(dic: dict[str, Any], data: np.ndarray) -> list[np.ndarray]:
    """Construct ppm axes in data axis order (each axis FDF block is positioned by FDDIMORDER)."""
    return [
        _ppm_axis(dic, _fdf_prefix(dic, data.ndim, i), data.shape[i])
        for i in range(data.ndim)
    ]


def _write_peaks_list(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    data: np.ndarray,
    dic: dict[str, Any],
    peaks: list[peak_detection.Peak],
) -> Path:
    """Write the detection peak as Poky/Sparky `.list` (Contract §6, peak file is.list)."""
    peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    path = peaks_dir / f"{exp_id}-{data_id}.list"
    axes = _axes_ppm(dic, data)
    # 0.2.199-patch29df/patch29dg/patch29dh: The logical order is completely consistent with the
    # viewer load_from_ft3. patch29dh(user): The axis order is only based on the.ft3 file header
    # (FDDIMORDER+LABEL/OBS), and no metadata is used to cover the bottom line. --- The software
    # processing flow has axis rearrangement. Metadata's Bruker F1/F2/F3 is the acquisition order,
    # which does not represent the final.ft3 axis order; the storage order is maintained when the
    # header cannot form an arrangement.
    logical_axes = list(range(data.ndim))
    prefixes = tuple(_fdf_prefix(dic, data.ndim, i) for i in range(data.ndim))
    storage_nuclei = _storage_nuclei(dic, prefixes)
    target = _logical_nuclei_from_order(dic, data.ndim, storage_nuclei)
    if target:
        perm = _permutation_to_logical(storage_nuclei, target)
        if perm is not None and perm != list(range(data.ndim)):
            logical_axes = [0] * data.ndim
            for spos, lpos in enumerate(perm):
                logical_axes[lpos] = spos
    rows: list[dict[str, Any]] = []
    for i, peak in enumerate(peaks, start=1):
        row: dict[str, Any] = {
            "Peak_ID": i,
            "Intensity": float(peak.height),
            "SN": float(peak.snr),
            "label": "",
        }
        if data.ndim == 2:
            # 0.2.199-patch29dh: 2D writes the N/H column according to core matching (external
            # (1H,15N) storage order file no longer writes the 1H value into N_shift); when both
            # cores are complete, it is positioned according to the core name, otherwise it is
            # logically F1 -> N, F2 -> H (keep the original behaviour of HSQC storage (15N, 1H)
            # unchanged).
            if "15N" in storage_nuclei and "1H" in storage_nuclei:
                n_axis = storage_nuclei.index("15N")
                h_axis = storage_nuclei.index("1H")
            else:
                n_axis = logical_axes[0]
                h_axis = logical_axes[1]
            row["H_shift"] = _ppm_at_fraction(axes[h_axis], peak.position[h_axis])
            row["N_shift"] = _ppm_at_fraction(axes[n_axis], peak.position[n_axis])
        else:
            for k in range(3):
                if k >= len(axes):
                    row[f"F{k + 1}_shift"] = 0.0
                else:
                    ax = logical_axes[k] if k < len(logical_axes) else k
                    row[f"F{k + 1}_shift"] = _ppm_at_fraction(
                        axes[ax], peak.position[ax]
                    )
        rows.append(row)
    # 0.2.199-patch29dk: 3D.list writes columns according to external convention
    # w1=15N/w2=13C/w3=1H; nuclei=target (F1/F2/F3 logical core derived from head FDDIMORDER),
    # missing fallback position formula.
    save_peaks(path, rows, nuclei=target)
    return path


def _localization_records(
    peaks: list[peak_detection.Peak],
) -> list[dict[str, Any]]:
    """Peak table row -> Peak-by-peak positioning diagnosis (JSON attachment content of
    Poky.list)."""
    rows: list[dict[str, Any]] = []
    for index, peak in enumerate(peaks, start=1):
        row: dict[str, Any] = {
            "Peak_ID": int(index),
            "label": str(getattr(peak, "label", "") or ""),
        }
        row.update(dict(peak.localization or {}))
        rows.append(row)
    return rows


def _metadata_experiment_type(
    manager: ProjectManager, exp_id: str, data_id: str
) -> tuple[str, float]:
    """Read from data metadata (experiment type name, confidence); missing returns ("", 0.0)."""
    data_entry = manager.data(exp_id, data_id)
    candidates: list[Path] = []
    try:
        candidates.append(manager.data_metadata_path(exp_id, data_id))
    except Exception:  # noqa: BLE001 - Peak picking is not blocked if path construction fails.
        pass
    if data_entry.metadata_path:
        p = Path(data_entry.metadata_path)
        candidates.append(p if p.is_absolute() else manager.root / p)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # 0.2.199-patch29fa(Fixed): The experiment type in the real metadata.json is in
        # dataset.experiment_type (_dataset_summary is written when importing); old/Compatible
        # structure may be at the top level experiment_type -- read from both places, dataset first
        # and then the top level.
        entry = (
            ((payload.get("dataset") or {}).get("experiment_type") or {})
            or (payload.get("experiment_type") or {})
        )
        name = str(entry.get("name", "") or "")
        try:
            confidence = float(entry.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if name:
            return name, confidence
    return "", 0.0


def _experiment_type_name(
    manager: ProjectManager, exp_id: str, data_id: str
) -> str:
    """Get the experiment type name from data metadata(experiment_type.name); if missing, return
    ''."""
    return _metadata_experiment_type(manager, exp_id, data_id)[0]


def _type_uncertain(
    manager: ProjectManager, exp_id: str, data_id: str
) -> bool:
    """Whether the type has low confidence/unknown (can be overridden by spectrum evidence): no
    type or confidence < 0.6."""
    name, confidence = _metadata_experiment_type(manager, exp_id, data_id)
    if not name:
        return True
    return confidence < 0.6


def _strong_two_sign(peaks: list[Any]) -> bool:
    """Spectrum Whether positive and negative coexist obviously (0.2.199-patch29fc/patch29fc-
    modified). Combination of quantity and intensity: the proportion of the number of minority
    symbols >= _MIXED_COUNT_SHARE and the proportion of the absolute sum of the intensity of the
    minority symbols >= _MIXED_INTEN_SHARE; anti-pollution -- the minority symbols cannot be
    dominated by an extremely strong peak (the second strongest peak >= the strongest peak x
    _MIXED_OUTLIER_RATIO), sporadic/Unimodal pollution will not be misjudged."""
    pos = [abs(p.height) for p in peaks if p.height > 0]
    neg = [abs(p.height) for p in peaks if p.height < 0]
    total = len(pos) + len(neg)
    if total < _MIXED_MIN_TOTAL:
        return False
    minor_abs, major_abs = (
        (pos, neg) if len(pos) <= len(neg) else (neg, pos)
    )
    minor = len(minor_abs)
    major = len(major_abs)
    if minor < _MIXED_MIN_MINOR or major <= 0:
        return False
    if minor / major < _MIXED_COUNT_SHARE:
        return False
    sum_minor = float(sum(minor_abs))
    sum_major = float(sum(major_abs))
    if sum_major <= 0 or sum_minor / sum_major < _MIXED_INTEN_SHARE:
        return False
    ordered = sorted(minor_abs, reverse=True)
    if len(ordered) >= 2 and ordered[1] < ordered[0] * _MIXED_OUTLIER_RATIO:
        # A few symbols are dominated by a single extremely strong peak (suspected contamination).
        return False
    return True


def _sign_mode_for(
    manager: ProjectManager, exp_id: str, data_id: str
) -> str:
    """Peak sign mode: presets peak_sign=mixed -> both;uniform/unknown -> dominant."""
    from core.experiments.registry import get as get_template

    name = _experiment_type_name(manager, exp_id, data_id)
    template = get_template(name) if name else None
    peak_sign = template.peak_sign if template else "uniform"
    return "both" if peak_sign == "mixed" else "dominant"


def _peak_nucleus_ppm(
    peak: peak_detection.Peak,
    axes: list[np.ndarray],
    storage_nuclei: list[str],
) -> dict[str, float]:
    """Detect peaks (data axis order) -> {core name: ppm} (only core known axes;
    0.2.199-patch29dl)."""
    coords: dict[str, float] = {}
    for ax, nucleus in enumerate(storage_nuclei):
        if not nucleus or ax >= len(axes):
            continue
        pos = float(peak.position[ax])
        if 0 <= pos < int(axes[ax].size):
            # 0.2.199-patch29fx: Same as _write_peaks_list -- sub-pixel interpolation instead of
            # int() truncation (truncation at 19.999... This type of floating point will differ by 1
            # data point, and reference matching will miss the true peak after tightening to the
            # Poky kr tolerance).
            coords[nucleus] = _ppm_at_fraction(axes[ax], pos)
    return coords


def _row_nucleus_ppm(
    row: dict[str, Any], nuclei: list[str] | None
) -> dict[str, float]:
    """Reference peak table row -> {Nucleus name: ppm}(0.2.199-patch29dl). nuclei is the reference
    axis nuclei name (F order, used for 3D row F1/F2/F3_shift); for 2D row, directly press the
    key name (N_shift/H_shift/C_shift) to get nuclei."""
    coords: dict[str, float] = {}
    if nuclei:
        for i, nucleus in enumerate(nuclei):
            value = row.get(f"F{i + 1}_shift")
            if value is not None and str(value) != "":
                coords[nucleus] = float(value)
    for key, nucleus in (
        ("H_shift", "1H"), ("N_shift", "15N"), ("C_shift", "13C"),
    ):
        value = row.get(key)
        if value is not None and str(value) != "":
            coords.setdefault(nucleus, float(value))
    return coords


def _safe_figure_token(name: str) -> str:
    """Reference display name -> single-segment safe file name token(0.2.199-patch29fx:exp/data
    When the display name contains '/', a nested directory will no longer be generated under
    figures/)."""
    token = "".join(
        c if c.isalnum() or c in "_-." else "_" for c in str(name)
    )
    return token.strip("_.") or "reference"


def _reference_match(
    peak_coords: dict[str, float],
    ref_coords: list[dict[str, float]],
    tol: dict[str, float],
) -> bool:
    """Whether the peak matches any reference peak: for each core of the reference, the current
    peak must have and fall within the tolerance. The cores with more current spectra than the
    reference (such as 3D vs. 2D reference) do not participate in the matching. The third
    dimension is free -- one reference peak can retain multiple peaks (such as HNCA and CA/CB)."""
    for rc in ref_coords:
        ok = True
        for nucleus, rv in rc.items():
            cv = peak_coords.get(nucleus)
            if cv is None or abs(cv - rv) > tol.get(nucleus, 1.0):
                ok = False
                break
        if ok:
            return True
    return False


def pick_peaks(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any | None = None,
    *,
    sigma_multiplier: float | None = None,
    ref_peaks: list[dict[str, Any]] | None = None,
    ref_nuclei: list[str] | None = None,
    tolerance_ppm: dict[str, float] | None = None,
    ref_name: str = "",
    edge_margin_ppm: float | None = None,
    edge_margin_points: int | None = None,
    localization_method: str = DEFAULT_LOCALIZATION_METHOD,
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
) -> dict[str, Any]:
    """Peak selection: detect spectral peaks and write to Poky.list, register WorkflowRun. backend
    is reserved as an interface occupancy; ``sigma_multiplier`` is the noise multiple threshold
    (default 35σ, min_snr synchronization) -- **External input**: Use the default if not given,
    select peaks according to the threshold if given, and the actual value is written into the
    returned ``detection``(``sigma_multiplier``/``threshold_source``).
    ref_peaks/ref_nuclei/tolerance_ppm is the reference peak table constraint
    (0.2.199-patch29dl, user): only retain peaks that match the reference peak table by core
    name -- 2D reference matches all cores; 3D current spectrum + 2D reference, the third
    dimension is free, and one reference peak can retain multiple peaks. The axis peak exclusion
    margin (above and below the 0th axis) is defined according to the **physical width**:
    ``edge_margin_ppm`` is given explicitly ppm, or use the default (3 ``"parabolic"`` (default,
    existing 3-point parabola, completely unchanged behaviour) or ``"gaussian"`` (2D Gaussian
    fitting, **only 2D**; 1D/3D reports an error directly, does not silently convert the
    algorithm). Gaussian ROI is defined by the physical width
    (``gaussian_roi_f1_ppm``/``gaussian_roi_f2_ppm``, the default reading is config
    ``peaks.localization``), and the number of points is converted according to the current
    spectral point distance; Fitting failed/Boundary retraction parabola,
    ``requested_method``/``actual_method``/``fallback_reason`` Write all into the peak table
    attachments ``<peak table>.localization.json`` and ``run.params['localization']``. Return
    {"status", "peak_path", "peak_count", "detection", "localization", "logs"};``detection``
    record the source of the margin, ppm/Points, and the point distance of each axis (for file
    recalculation)."""
    data_entry = manager.data(exp_id, data_id)
    spectrum_path = data_entry.spectrum_path
    if not spectrum_path or not Path(spectrum_path).is_file():
        run = manager.start_run(
            exp_id, workflow_ref="pick_peaks", inputs={"data_id": data_id}
        )
        manager.finish_run(
            run.run_id, "failed", message=tr(
                "spectrum is missing: "
                "{p0}/{p1}",
                p0=exp_id,
                p1=data_id,
            )
        )
        raise PickPeaksError(
            tr(
            "The spectrum is missing and cannot pick the peak: "
            "{p0}/{p1}",
            p0=exp_id,
            p1=data_id,
        ))

    run = manager.start_run(
        exp_id,
        workflow_ref="pick_peaks",
        inputs={"data_id": data_id, "spectrum_path": spectrum_path},
    )
    try:
        import nmrglue as ng

        dic, data = ng.pipe.read(str(spectrum_path))
        arr = np.asarray(data)
        if np.iscomplexobj(arr):
            arr = arr.real
        # 2026-09-13 (user plan A): The margin is converted into points according to the physical
        # width (ppm) -- The fixed number of points will change the coverage width with zero
        # filling, so that the peak set diameter will drift with the processing parameter.
        prefixes = tuple(
            _fdf_prefix(dict(dic), arr.ndim, i) for i in range(arr.ndim)
        )
        storage_nuclei = _storage_nuclei(dict(dic), prefixes)
        axes_ppm = _axes_ppm(dict(dic), arr)
        axis0_nucleus = storage_nuclei[0] if storage_nuclei else ""
        axis0_obs = (
            float(dic.get(prefixes[0] + "OBS", 0.0) or 0.0)
            if prefixes
            else 0.0
        )
        try:
            from backend.config import load_processing_defaults

            _linewidths = load_processing_defaults().get("linewidth_hz") or {}
        # Use the built-in default when the configuration is unreadable.
        except Exception:  # noqa: BLE001 -
            _linewidths = {}
        if edge_margin_points is not None:
            edge_points = max(0, int(edge_margin_points))
            edge_width_ppm = axis_units.ppm_for_points(axes_ppm[0], edge_points)
            edge_source = tr("points_explicit")
        else:
            edge_width_ppm = (
                float(edge_margin_ppm)
                if edge_margin_ppm is not None
                else axis_units.edge_margin_ppm(
                    axis0_nucleus,
                    axis0_obs,
                    linewidth_hz_by_nucleus=_linewidths,
                )
            )
            edge_points = axis_units.points_for_ppm(axes_ppm[0], edge_width_ppm)
            edge_source = tr("ppm_physical_width")
            if edge_points <= 0:
                edge_points = int(_PICK_EDGE_MARGIN)
                edge_width_ppm = axis_units.ppm_for_points(
                    axes_ppm[0], edge_points
                )
                edge_source = tr("points_fallback")
        detection = {
            "edge_margin_source": edge_source,
            "edge_margin_ppm": round(float(edge_width_ppm), 6),
            "edge_margin_points": int(edge_points),
            "axis0_nucleus": axis0_nucleus,
            "linewidth_hz_by_nucleus": {
                str(k): float(v) for k, v in (_linewidths or {}).items()
            },
            "axes": [
                axis_units.describe_axis(
                    axes_ppm[i],
                    nucleus=(storage_nuclei[i] if i < len(storage_nuclei) else ""),
                    width_ppm=(edge_width_ppm if i == 0 else None),
                )
                for i in range(arr.ndim)
            ],
        }
        sign_mode = _sign_mode_for(manager, exp_id, data_id)
        threshold = (
            float(sigma_multiplier)
            if sigma_multiplier and float(sigma_multiplier) > 0
            else _PICK_THRESHOLD_SIGMA
        )
        detection["sigma_multiplier_requested"] = (
            float(sigma_multiplier) if sigma_multiplier else None
        )
        detection["sigma_multiplier"] = float(threshold)
        detection["min_snr"] = float(threshold)
        detection["threshold_source"] = (
            "user"
            if sigma_multiplier and float(sigma_multiplier) > 0
            else "default(35sigma)"
        )
        # 0.2.199-patch29fc(user): spectrum backfill -- unknown/Use low confidence type first both
        # detection, if the proportion of positive and negative peaks is high, press mixed to select
        # both positive and negative; otherwise, follow template rules (dominant filtering).
        evidence_log: str | None = None
        peaks = peak_detection.detect(
            arr,
            peak_detection.PeakDetectionParams(
                sign_mode="both",
                sigma_multiplier=threshold,
                min_snr=threshold,
                edge_margin=edge_points,
            ),
        )
        if sign_mode == "both":
            pass  # Template is mixed.
        else:
            n_pos = sum(1 for p in peaks if p.height > 0)
            n_neg = sum(1 for p in peaks if p.height < 0)
            if (
                _type_uncertain(manager, exp_id, data_id)
                and _strong_two_sign(peaks)
            ):
                sign_mode = "both"
                evidence_log = (
                    tr(
                        "spectrum evidence: the type is low-confidence / unknown, but both peak "
                        "signs are common (positive {p0} / negative {p1}), so mixed mode keeps "
                        "both positive and negative "
                        "peaks",
                        p0=n_pos,
                        p1=n_neg,
                    )
                )
            else:
                peaks = peak_detection.keep_dominant(peaks)
        run.params["detection"] = detection
        # 2026-09-13 (user request): Peak positioning = subgrid point refinement after detection.
        # Parabola and 2D Gaussian run independently on the same batch of candidates (to facilitate
        # comparison of the peak position differences of the two algorithms); Gaussian is only 2D,
        # ROI converts the number of points according to the physical width (ppm), and returns to
        # the parabola if it fails and leaves the reason.
        method = normalize_localization_method(localization_method)
        loc_defaults = load_localization_defaults()
        roi_f1_ppm = (
            float(gaussian_roi_f1_ppm)
            if gaussian_roi_f1_ppm is not None
            else float(loc_defaults["gaussian_roi_f1_ppm"])
        )
        roi_f2_ppm = (
            float(gaussian_roi_f2_ppm)
            if gaussian_roi_f2_ppm is not None
            else float(loc_defaults["gaussian_roi_f2_ppm"])
        )
        logical_axes = _logical_axis_indices(dict(dic), arr.ndim)
        for peak in peaks:
            index = [int(round(float(v))) for v in peak.position]
            loc = localize_peak(
                arr,
                index,
                method=method,
                sign=int(peak.sign),
                ppm_axes=axes_ppm,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                logical_axes=logical_axes,
            )
            peak.position = tuple(loc.position)
            peak.localization = loc.to_dict(axes_ppm)
        localization_summary = summarize_localization(
            _localization_records(peaks),
            method=method,
            roi_f1_ppm=(roi_f1_ppm if method == "gaussian" else None),
            roi_f2_ppm=(roi_f2_ppm if method == "gaussian" else None),
        )
        # 0.2.199-patch29dl/patch29fw(user): Reference peak table constraints. When there are enough
        # reference peak files (>=5), the overall translation is first aligned (there may be offset
        # between different acquisitions, 2026-09-04 user ruling), and then the peaks whose
        # corresponding peaks cannot be found in the reference are eliminated; when there are few
        # reference peaks, the overall translation is unreliable and degenerates to zero translation
        # and is directly matched by coordinates (retaining patch29dl semantics).
        ref_log: str | None = None
        if ref_peaks:
            from workflow.peak_align import (
                MIN_ACCEPTABLE_RATIO,
                TOLERANCE_PPM,
                align_peak_files,
                filter_by_reference,
            )

            prefixes = tuple(
                _fdf_prefix(dict(dic), arr.ndim, i) for i in range(arr.ndim)
            )
            storage_nuclei = _storage_nuclei(dict(dic), prefixes)
            axes = _axes_ppm(dict(dic), arr)
            # Reference matching tolerance (0.2.199-patch29fx, user: press Poky kr default -- 1H
            # +/-0.02 ppm, other cores +/-0.2 ppm; explicit tolerance_ppm can still be overridden).
            ref_tol = (
                tolerance_ppm if tolerance_ppm else dict(TOLERANCE_PPM)
            )
            # Detect peaks -> {kernel: ppm} (data axis order; isomorphic to the reference row).
            cur_coords = [
                _peak_nucleus_ppm(peak, axes, storage_nuclei)
                for peak in peaks
            ]
            cur_rows = [c for c in cur_coords if c]
            ref_rows = [
                c for c in (_row_nucleus_ppm(r, ref_nuclei) for r in ref_peaks)
                if c
            ]
            before = len(peaks)
            if len(ref_rows) >= 5:
                align = align_peak_files(
                    cur_rows,
                    ref_peaks,
                    cur_nuclei=None,
                    ref_nuclei=ref_nuclei,
                    tol_ppm=ref_tol,
                )
                if align["status"] == "no_common":
                    ref_log = (
                        tr(
                            "Reference peak table constraint: The current spectrum and the "
                            "reference peak have no common core coordinates and are not "
                            "filtered",
                        )
                    )
                else:
                    kept_rows, _stats = filter_by_reference(
                        cur_rows,
                        ref_peaks,
                        align["shift"],
                        nuclei=None,
                        ref_nuclei=ref_nuclei,
                        tol_ppm=ref_tol,
                    )
                    kept_keys = {
                        tuple(
                            round(v, 6)
                            for _, v in sorted(row.items())
                        )
                        for row in kept_rows
                    }
                    kept_peaks: list = []
                    for peak, coord in zip(peaks, cur_coords):
                        if not coord:
                            continue
                        key = tuple(
                            round(v, 6)
                            for _, v in sorted(coord.items())
                        )
                        if key in kept_keys:
                            kept_peaks.append(peak)
                    ratio = align.get("ratio", 0.0)
                    ref_log = (
                        tr(
                            "Reference peak table constraints (after alignment): {p0} → {p1} Peak "
                            "(overall offset {p2}, alignment rate "
                            "{p3:.0%}",
                            p0=before,
                            p1=len(kept_peaks),
                            p2=align['shift'],
                            p3=ratio,
                        )
                    )
                    if ratio < MIN_ACCEPTABLE_RATIO:
                        ref_log += (
                            tr(
                            ", please check whether the reference spectrum is similar to this "
                            "spectrum",
                        )
                        )
                    ref_log += ")"
                    peaks = kept_peaks
                    try:
                        from workflow.peak_align import alignment_figure

                        figures_dir = manager.data_dir(
                            exp_id, data_id, "figures"
                        )
                        figures_dir.mkdir(parents=True, exist_ok=True)
                        ref_label = ref_name or "reference"
                        fig_name = (
                            f"{exp_id}-{data_id}_aligned_"
                            f"{_safe_figure_token(ref_label)}.png"
                        )
                        fig_path = figures_dir / fig_name
                        alignment_figure(
                            cur_rows,
                            ref_peaks,
                            align["shift"],
                            fig_path,
                            cur_nuclei=None,
                            ref_nuclei=ref_nuclei,
                            tol_ppm=ref_tol,
                            cur_label=f"{exp_id}-{data_id}",
                            ref_label=ref_label,
                        )
                        ref_log += (
                            tr(
                                "(Check the diagram {p0};SVG "
                                "{p1})",
                                p0=fig_path,
                                p1=fig_path.with_suffix('.svg'),
                            )
                        )
                    # Peak selection will not be blocked if the graph fails.
                    except Exception:  # noqa: BLE001 -
                        pass
            else:
                # Few reference peaks: overall translation unreliable -> zero translation direct
                # match (patch29dl).
                zero_shift = {}
                kept_rows, _stats = filter_by_reference(
                    cur_rows,
                    ref_peaks,
                    zero_shift,
                    nuclei=None,
                    ref_nuclei=ref_nuclei,
                    tol_ppm=ref_tol,
                )
                kept_keys = {
                    tuple(
                        round(v, 6)
                        for _, v in sorted(row.items())
                    )
                    for row in kept_rows
                }
                kept_peaks = [
                    peak
                    for peak, coord in zip(peaks, cur_coords)
                    if not coord
                    or tuple(
                        round(v, 6) for _, v in sorted(coord.items())
                    )
                    in kept_keys
                ]
                ref_log = (
                    tr(
                        "Reference peak table constraints (straight matching): {p0} → {p1} Peaks "
                        "(there are few reference peaks and no overall translation is "
                        "performed)",
                        p0=before,
                        p1=len(kept_peaks),
                    )
                )
                peaks = kept_peaks

        peak_path = _write_peaks_list(
            manager, exp_id, data_id, arr, dict(dic), peaks,
        )
        # Peak-by-peak positioning diagnosis, write peak table attachment (JSON; Poky.list format
        # remains unchanged) + run parameter file.
        records_path = write_localization_records(
            peak_path,
            _localization_records(peaks),
            meta=localization_summary,
        )
        localization_summary["records_path"] = str(records_path)
        localization_summary["peak_table_path"] = str(peak_path)
        run.params["localization"] = localization_summary
    except Exception as exc:  # noqa: BLE001 - Unified failure registration.
        manager.finish_run(run.run_id, "failed", message=str(exc))
        raise PickPeaksError(tr("Peak picking failed: {p0}", p0=exc)) from exc

    manager.finish_run(
        run.run_id,
        "success",
        outputs={"peak_path": str(peak_path)},
        message=tr("Peak selection completed ({p0} peak)", p0=len(peaks)),
    )
    sign_label = {
        "both": tr("Select both positive and negative peaks (mixed)"),
        "dominant": tr("Only the main symbol peak (uniform)"),
        "positive": tr("Only the main peak"),
        "negative": tr("Only negative peaks"),
    }.get(sign_mode, sign_mode)
    logs = [
        tr(
            "Peak pick: {p0} Peaks -> {p1}(symbol mode: {p2}, threshold: {p3:.1f}σ, source: "
            "{p4})",
            p0=len(peaks),
            p1=peak_path,
            p2=sign_label,
            p3=threshold,
            p4=detection.get('threshold_source', ''),
        )
    ]
    logs.append(
        tr(
            "Axial peak exclusion margin: {p0:.3f} ppm({p1}) = {p2} points(point spacing {p3:.4f} "
            "ppm/point, source "
            "{p4})",
            p0=detection['edge_margin_ppm'],
            p1=detection['axis0_nucleus'] or 'axis 0',
            p2=detection['edge_margin_points'],
            p3=detection['axes'][0]['ppm_per_point'],
            p4=detection['edge_margin_source'],
        )
    )
    if method == "gaussian":
        logs.append(
            tr(
                "peak localization: {p0}(ROI F1 ±{p1:g} ppm / F2 ±{p2:g} ppm; Success {p3}, "
                "fallback "
                "{p4})",
                p0=localization_label(method),
                p1=roi_f1_ppm,
                p2=roi_f2_ppm,
                p3=localization_summary['gaussian_fit_success_count'],
                p4=localization_summary['gaussian_fallback_count'],
            )
            + (
                tr("fallback reason: ")
                + ", ".join(
                    f"{key}×{value}"
                    for key, value in sorted(
                        localization_summary["fallback_reasons"].items(),
                        key=lambda item: -item[1],
                    )[:3]
                )
                if localization_summary["fallback_reasons"]
                else ""
            )
            + tr("(Peak-by-peak diagnosis {p0})", p0=localization_summary['records_path'])
        )
    else:
        logs.append(
            tr(
                "peak localization: {p0}(3-point parabolic line vertex, consistent with the "
                "existing algorithm)",
                p0=localization_label(method),
            )
        )
    if evidence_log:
        logs.append(evidence_log)
    if ref_log:
        logs.append(ref_log)
    return {
        "status": "success",
        "peak_path": str(peak_path),
        "peak_count": len(peaks),
        "detection": detection,
        "localization": localization_summary,
        "logs": logs,
    }


# ---------------------------------------------------------------------------
# Public spectrum reading entrance (2026-09-12, parameter sensitivity interface reuse) The caliber
# of "data axis ↔ logical dimension ↔ core name ↔ ppm" has already been converged here within the
# peak selection; the external interface (nmrforge_api) must locate the peak position according to
# the same caliber, so this layer is explicitly exposed to avoid the second set. Implemented in
# FDDIMORDER / ORIG-CAR It bifurcates again.


@dataclass(frozen=True)
class SpectrumAxes:
    """A NMRPipe spectrum reading result (data axis sequence) + axis mapping. - ``dic`` /
    ``data``:nmrglue head and array (replica takes real part); - ``ppm``: Data axis sequence ppm
    axis (ORIG preferential fallback CAR, same origin as viewer); - ``nuclei``: data axis
    sequence name (cannot be determined as ``""``); - ``logical_to_storage``: logical dimension
    F{k+1}(k=0..ndim-1) -> data axis index (FDDIMORDER fallback position when illegal)."""

    dic: dict[str, Any]
    data: np.ndarray
    ppm: list[np.ndarray]
    nuclei: list[str]
    logical_to_storage: list[int]
    # Data axis sequence observation frequency (MHz).
    obs: list[float] = field(default_factory=list)

    @property
    def ndim(self) -> int:
        return int(self.data.ndim)

    def storage_of(self, nucleus: str) -> int | None:
        """Core name -> Data axis index (repeat the first one for the same core; return None if
        unknown)."""
        for axis, name in enumerate(self.nuclei):
            if name and name == nucleus:
                return axis
        return None

    def ppm_at_fraction(self, axis: int, fraction: float) -> float:
        """Subpixel index of data axis ``axis`` -> ppm (same origin as peak selection writing
        table)."""
        return _ppm_at_fraction(self.ppm[axis], fraction)

    def fraction_from_ppm(self, axis: int, ppm: float) -> float:
        """Ppm -> Score index of data axis ``axis`` (axis increment/Supported in descending
        order)."""
        axis_ppm = np.asarray(self.ppm[axis], dtype=float)
        index = np.arange(axis_ppm.size, dtype=float)
        if axis_ppm.size < 2:
            return 0.0
        if axis_ppm[0] <= axis_ppm[-1]:
            return float(np.interp(float(ppm), axis_ppm, index))
        return float(np.interp(-float(ppm), -axis_ppm, index))


def read_spectrum_axes(path: Path | str) -> SpectrumAxes:
    """Read NMRPipe spectrum (ft1/ft2/ft3) and return the axis mapping with the same caliber as the
    peak selection. It is completely homologous to ``pick_peaks``: the same ORIG/CAR formula,
    the same FDDIMORDER analysis, the same core name alias table; the real part of the replica
    data. ``obs`` is the data axis sequence observation frequency (MHz), which is used for
    "physical width ↔ points" conversion (see core.peaks.axis_units)."""
    import nmrglue as ng

    dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    ndim = int(arr.ndim)
    prefixes = tuple(_fdf_prefix(dic, ndim, axis) for axis in range(ndim))
    logical_to_storage = _logical_axis_indices(dic, ndim)
    return SpectrumAxes(
        dic=dict(dic),
        data=arr,
        ppm=_axes_ppm(dic, arr),
        nuclei=_storage_nuclei(dic, prefixes),
        logical_to_storage=list(logical_to_storage),
        obs=[float(dic.get(prefix + "OBS", 0.0) or 0.0) for prefix in prefixes],
    )


__all__ = ["PickPeaksError", "SpectrumAxes", "pick_peaks", "read_spectrum_axes"]
