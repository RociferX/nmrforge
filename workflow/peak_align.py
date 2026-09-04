"""Peak-file whole-spectrum alignment (0.2.199-补29fw).

Move the current peak file as a whole (all peaks together) in ppm space and
search for the offset that maximizes the number of matching peaks.  This is
NOT per-peak CSP matching:

- row -> {nucleus: ppm}: 2D rows use H_shift/N_shift/C_shift keys; 3D rows
  use F1/F2/F3_shift keys plus the nuclei list mapping each F axis.
- Different dimensionalities are handled by using the COMMON nuclei only
  (e.g. a 2D HSQC reference vs a 3D HNCA current spectrum is matched on
  1H/15N only; the 13C axis does not participate).
- Alignment ratio (user definition, 2026-09-03): matched / min(cur_count,
  ref_count).  Peaks with real displacement naturally do not match and count
  as unmatched.
- Matching: every nucleus must fall within tolerance (defaults 1H +-0.1,
  15N/13C +-0.5 ppm).  One reference peak is used at most once.
- Minimum acceptable ratio is 60%: below it the result is flagged "low" so
  the GUI can tell the user to check whether the reference is similar.
"""

from __future__ import annotations

from itertools import product
from typing import Any

import numpy as np

# --- tunables (edit here and rerun) ---------------------------------------
TOLERANCE_PPM: dict[str, float] = {
    "1H": 0.10,
    "2H": 0.10,
    "15N": 0.50,
    "13C": 0.50,
    "19F": 0.10,
    "31P": 0.10,
    "23Na": 0.50,
    "29Si": 0.50,
}
SEARCH_RANGE_PPM: dict[str, float] = {
    "1H": 1.0,
    "2H": 1.0,
    "15N": 5.0,
    "13C": 5.0,
    "19F": 1.0,
    "31P": 1.0,
    "23Na": 2.0,
    "29Si": 2.0,
}
MIN_ACCEPTABLE_RATIO: float = 0.60  # below this -> "low" (check similarity)
_MIN_STEP_PPM = 0.02


def row_coords(
    row: dict[str, Any], nuclei: list[str] | None = None
) -> dict[str, float]:
    """One peak-table row -> {nucleus: ppm}.

    2D rows: N_shift=15N / H_shift=1H / C_shift=13C.
    3D rows: F1/F2/F3_shift with nuclei = per-F nucleus names.
    """
    coords: dict[str, float] = {}
    # {nucleus: ppm} coordinate passthrough (caller already parsed, e.g.
    # {"15N": 118.5, "1H": 8.1}); this is what pick_peaks passes in.
    nuclei_keys = ("1H", "2H", "15N", "13C", "19F", "31P", "23Na", "29Si")
    if any(k in row for k in nuclei_keys):
        for nucleus in nuclei_keys:
            value = row.get(nucleus)
            if value is not None and str(value) not in ("", "?"):
                coords[nucleus] = float(value)
        return coords
    if "F1_shift" in row:
        if nuclei and len(nuclei) >= 3:
            for i, nucleus in enumerate(nuclei[:3]):
                value = row.get(f"F{i + 1}_shift")
                if value is not None and str(value) not in ("", "?"):
                    coords[nucleus] = float(value)
        return coords
    for key, nucleus in (
        ("H_shift", "1H"),
        ("N_shift", "15N"),
        ("C_shift", "13C"),
    ):
        value = row.get(key)
        if value is not None and str(value) not in ("", "?"):
            coords[nucleus] = float(value)
    return coords


def common_nuclei(
    cur_coords: list[dict[str, float]],
    ref_coords: list[dict[str, float]],
) -> list[str]:
    """Nuclei present in both sets (stable order: 1H, 2H, 15N, 13C, ...)."""
    order = {"1H": 0, "2H": 1, "15N": 2, "13C": 3, "19F": 4, "31P": 5,
             "23Na": 6, "29Si": 7}
    cur = set().union(*(c.keys() for c in cur_coords)) if cur_coords else set()
    ref = set().union(*(c.keys() for c in ref_coords)) if ref_coords else set()
    common = cur & ref
    return sorted(common, key=lambda n: (order.get(n, 99), n))


def _coord_matrix(
    coords: list[dict[str, float]], nuclei: list[str]
) -> np.ndarray:
    """{nucleus:ppm} list -> (N,K) matrix; rows missing any common nucleus
    are skipped (they cannot participate in a joint match)."""
    rows: list[np.ndarray] = []
    for c in coords:
        values = [c.get(n) for n in nuclei]
        if all(v is not None for v in values):
            rows.append(np.asarray(values, dtype=float))
    if not rows:
        return np.zeros((0, len(nuclei)), dtype=float)
    return np.vstack(rows)


def _existence_count(
    cur: np.ndarray, ref: np.ndarray, tol: np.ndarray
) -> int:
    """How many cur points sit inside at least one reference tolerance box.

    Existence only (multiple cur points may use the same ref point) -- this
    is the shift-search objective: a 2D reference keeps every 3D peak whose
    common nuclei land on the reference peak (e.g. HNCA CA/CB share N/H)."""
    if cur.size == 0 or ref.size == 0:
        return 0
    count = 0
    for i in range(cur.shape[0]):
        d = np.abs(ref - cur[i])
        if np.any(np.all(d <= tol, axis=1)):
            count += 1
    return count


def _score_shift(
    cur: np.ndarray, ref: np.ndarray, tol: np.ndarray
) -> tuple[int, float]:
    """(matched, normalized-distance cost) with greedy one-to-one pairing.

    matched never exceeds min(N_cur, N_ref).  cost is the sum of normalized
    squared distances of the matched pairs (smaller = tighter fit)."""
    if cur.size == 0 or ref.size == 0:
        return 0, 0.0
    used = np.zeros(ref.shape[0], dtype=bool)
    matched = 0
    cost = 0.0
    for i in range(cur.shape[0]):
        d = np.abs(ref - cur[i])
        inside = np.all(d <= tol, axis=1) & ~used
        if not inside.any():
            continue
        dist = np.sum((d[inside] / tol) ** 2, axis=1)
        j = int(np.argmin(dist))
        used_idx = int(np.flatnonzero(inside)[j])
        used[used_idx] = True
        matched += 1
        cost += float(dist[j])
    return matched, cost


def _best_shift(
    cur: np.ndarray,
    ref: np.ndarray,
    tol: np.ndarray,
    ranges: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Coarse then fine grid search over per-nucleus shifts.

    Objective: maximize matched pairs (one-to-one greedy, capped by the
    smaller list); ties are broken by the smallest normalized distance
    sum."""
    coarse = np.maximum(tol / 4.0, _MIN_STEP_PPM)
    best_shift = np.zeros(cur.shape[1])
    best_matched, best_cost = _score_shift(cur, ref, tol)
    for _round in range(2):
        step = coarse if _round == 0 else np.maximum(
            coarse / 2.0, _MIN_STEP_PPM
        )
        axes = [
            np.arange(-r, r + 0.5 * s, s) for r, s in zip(ranges, step)
        ]
        for trial in product(*axes):
            shifted = cur + np.asarray(trial)
            matched, cost = _score_shift(shifted, ref, tol)
            if (matched, -cost) > (best_matched, -best_cost):
                best_matched, best_cost = matched, cost
                best_shift = np.asarray(trial, dtype=float)
    return best_shift, best_matched


def align_peak_files(
    cur_rows: list[dict[str, Any]],
    ref_rows: list[dict[str, Any]],
    cur_nuclei: list[str] | None = None,
    ref_nuclei: list[str] | None = None,
    *,
    tol_ppm: dict[str, float] | None = None,
    range_ppm: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Whole-file alignment of cur peak rows against ref peak rows.

    Returns {status, shift, ratio, matched, total_min, nuclei, message}.
    shift maps nucleus -> ppm to ADD to the current peaks so that they land
    on the reference frame (cur + shift ~= ref).
    """
    cur_c = [row_coords(r, cur_nuclei) for r in cur_rows]
    ref_c = [row_coords(r, ref_nuclei) for r in ref_rows]
    nuclei = common_nuclei(cur_c, ref_c)
    if not nuclei or not cur_c or not ref_c:
        return {
            "status": "no_common",
            "message": "No common nuclei between current peaks and reference",
            "nuclei": nuclei,
        }
    cur_m = _coord_matrix(cur_c, nuclei)
    ref_m = _coord_matrix(ref_c, nuclei)
    if cur_m.shape[0] == 0 or ref_m.shape[0] == 0:
        return {
            "status": "no_common",
            "message": "No parseable common-nucleus coordinates",
            "nuclei": nuclei,
        }
    tol = TOLERANCE_PPM if tol_ppm is None else {**TOLERANCE_PPM, **tol_ppm}
    ranges = SEARCH_RANGE_PPM if range_ppm is None else {
        **SEARCH_RANGE_PPM, **range_ppm
    }
    tol_a = np.asarray([tol[n] for n in nuclei], dtype=float)
    range_a = np.asarray([ranges[n] for n in nuclei], dtype=float)
    shift_vec, matched = _best_shift(cur_m, ref_m, tol_a, range_a)
    shift = {n: float(v) for n, v in zip(nuclei, shift_vec)}
    total_min = min(cur_m.shape[0], ref_m.shape[0])
    ratio = matched / total_min if total_min else 0.0
    status = "ok" if ratio >= MIN_ACCEPTABLE_RATIO else "low"
    return {
        "status": status,
        "shift": shift,
        "ratio": ratio,
        "matched": int(matched),
        "total_min": int(total_min),
        "nuclei": nuclei,
        "message": (
            f"Aligned {matched}/{total_min} peaks (denominator = smaller "
            f"list), per-nucleus shift {shift}"
        ),
    }


def shifted_rows(
    rows: list[dict[str, Any]],
    shift: dict[str, float],
    nuclei: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Rows with coordinates + shift (for aligned export)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        new = dict(row)
        # {nucleus: ppm} passthrough rows (used by pick_peaks)
        nuclei_keys = ("1H", "2H", "15N", "13C", "19F", "31P", "23Na", "29Si")
        if any(k in new for k in nuclei_keys):
            for nucleus in nuclei_keys:
                if nucleus in new and nucleus in shift:
                    new[nucleus] = float(new[nucleus]) + shift[nucleus]
            out.append(new)
            continue
        if "F1_shift" in new:
            if nuclei and len(nuclei) >= 3:
                for i, nucleus in enumerate(nuclei[:3]):
                    key = f"F{i + 1}_shift"
                    if key in new and nucleus in shift:
                        new[key] = float(new[key]) + shift[nucleus]
        else:
            for key, nucleus in (
                ("H_shift", "1H"),
                ("N_shift", "15N"),
                ("C_shift", "13C"),
            ):
                if key in new and nucleus in shift:
                    new[key] = float(new[key]) + shift[nucleus]
        out.append(new)
    return out


def filter_by_reference(
    rows: list[dict[str, Any]],
    ref_rows: list[dict[str, Any]],
    shift: dict[str, float],
    nuclei: list[str] | None = None,
    ref_nuclei: list[str] | None = None,
    *,
    tol_ppm: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep current rows that, after applying shift, hit at least one
    reference peak inside its tolerance box.

    Existence semantics: one reference peak may keep several current peaks
    (2D reference vs 3D current, e.g. HNCA CA/CB).  Rows without any
    common-nucleus coordinate are dropped.
    """
    shifted = shifted_rows(rows, shift, nuclei)
    cur_c = [row_coords(r, nuclei) for r in shifted]
    ref_c = [row_coords(r, ref_nuclei) for r in ref_rows]
    common = common_nuclei(cur_c, ref_c)
    if not common:
        return [], {"kept": 0, "removed": len(rows), "nuclei": []}
    tol = TOLERANCE_PPM if tol_ppm is None else {**TOLERANCE_PPM, **tol_ppm}
    tol_a = np.asarray([tol[n] for n in common])
    ref_m = _coord_matrix(ref_c, common)
    keep_flags: list[bool] = []
    for c in cur_c:
        if not all(c.get(n) is not None for n in common):
            keep_flags.append(False)
            continue
        keep_flags.append(
            _existence_count(
                np.asarray([[c[n] for n in common]], dtype=float),
                ref_m,
                tol_a,
            )
            >= 1
        )
    kept = [r for r, keep in zip(rows, keep_flags) if keep]
    return kept, {
        "kept": len(kept),
        "removed": len(rows) - len(kept),
        "nuclei": common,
    }


__all__ = [
    "MIN_ACCEPTABLE_RATIO",
    "SEARCH_RANGE_PPM",
    "TOLERANCE_PPM",
    "align_peak_files",
    "_existence_count",
    "common_nuclei",
    "filter_by_reference",
    "row_coords",
    "shifted_rows",
]
