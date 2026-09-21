"""Peak table model and persistence (Shared, contract §6 / G2B-005; ported from the
older NMRFlow project).

- ``.list``: the internal peak file *is* Poky/Sparky format (0.2.199-patch29ar, user:
  peak files stay Poky from start to finish, never CSV); "Assignment w1 w2 [w3] Data
  Height Volume", 2D w1=15N (N_shift) w2=1H (H_shift), 3D follows the external
  convention w1=15N/w2=13C/w3=1H (0.2.199-patch29dk, user: .list and the peak-table
  display both match the outside, while internally the axes are read as F1/F2/F3 --
  export/import permutes external w columns <-> internal F columns through ``nuclei``);
  unnamed peaks are ?-? (2D) / ?-?-? (3D), Height=Intensity %.3g, Data/Volume=0,
  double spaces;
- import compatibility (0.2.199-patch29fx, user: a reference .list may carry extra
  trailing columns, a trimmed column set or a lowercase header): the header is
  case-insensitive, 2D needs at least 3 columns / 3D at least 4, Data/Height/Volume may
  be omitted and default to 0, surplus trailing columns are ignored, and without a
  header the dimension is inferred from the column count;
- the old CSV format is read-only (``load_peaks`` detects it) and is never written.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PEAK_COLUMNS = ("Peak_ID", "H_shift", "N_shift", "Intensity", "SN", "label")
PEAK_3D_COLUMNS = (
    "Peak_ID",
    "F1_shift",
    "F2_shift",
    "F3_shift",
    "Intensity",
    "SN",
    "label",
)

_NUMERIC_KEYS = {
    "Peak_ID",
    "H_shift",
    "N_shift",
    "F1_shift",
    "F2_shift",
    "F3_shift",
    "Intensity",
    "SN",
}

# Poky assignment single-letter amino acids (0.2.199-patch29cn/patch29co, user: the
# format follows Poky). Verified 2026-08-29 against ADAPT-NMR/PINE/relax peak-table
# samples: an assignment is split per dimension and joined with hyphens -- 2D has two
# parts (unassigned ?-?), 3D three (unassigned ?-?-?); every part is single-letter
# amino acid + residue number + nucleus name, e.g. C16H-K15CB-C16N or G1H-G1N.
# Poky/Sparky external .list convention (0.2.199-patch29dk, user): 2D w1=15N/w2=1H;
# 3D w1=15N/w2=13C/w3=1H. Internal row keys (F1/F2/F3_shift) follow the spectrum axis
# order; export/import permutes external <-> internal through ``nuclei`` (the nucleus
# of every F axis, e.g. ["15N","1H","13C"]).
_EXTERNAL_2D_NUCLEI = ("15N", "1H")
_EXTERNAL_3D_NUCLEI = ("15N", "13C", "1H")
# Poky .list header (0.2.199-patch29fx: real files may use a lowercase assignment)
_POKY_HEADER_RE = re.compile(r"^assignment\b", re.IGNORECASE)


def _external_w_to_internal_axes(
    nuclei: list[str] | None, external: tuple[str, ...], ndim: int
) -> list[int]:
    """External w columns -> internal F axis indices (0-based) as a permutation.

    When the nucleus information is complete, the dimensionality matches and the
    nuclei match the external set, columns are matched by nucleus (e.g.
    nuclei=["15N","1H","13C"] -> [0, 2, 1], w1=F1/w2=F3/w3=F2); otherwise this falls
    back to the positional mapping (F{j} <-> w{j+1}), which keeps the old behaviour.
    """
    if (
        nuclei
        and len(nuclei) == len(external)
        and len(nuclei) == ndim
        and all(nuclei)
        and set(nuclei) == set(external)
    ):
        try:
            return [nuclei.index(n) for n in external]
        except ValueError:
            return list(range(ndim))
    return list(range(ndim))


_AA_LETTERS = "ACDEFGHIKLMNPQRSTVWY"


def _poky_part(part: str) -> str | None:
    """Normalise one assignment part to single-letter amino acid + residue number +
    nucleus name, upper-cased; returns None when the part does not match."""
    m = re.fullmatch(r"([A-Za-z])(\d+)([A-Za-z]+)", part)
    if m and m.group(1).upper() in _AA_LETTERS:
        return f"{m.group(1).upper()}{m.group(2)}{m.group(3).upper()}"
    return None


def normalize_poky_label(text: str | None, ndim: int = 2) -> str:
    """Normalise an assignment into Poky format (split per dimension, hyphen-joined).

    - ndim: spectrum dimensionality -- 2D has two parts (e.g. G1H-G1N), 3D three
      (e.g. G1H-G1N-G1CA);
    - None/empty string -> "" (export then writes ?-?/?-?-?);
    - every part is upper-cased (e.g. c16h-k15cb-c16n -> C16H-K15CB-C16N) and a
      literal ? inside a part is kept;
    - when the number of parts differs from ndim, or for other content, the text comes
      back unchanged (the caller decides whether to warn).
    """
    if text is None:
        return ""
    raw = str(text).strip()
    if not raw:
        return ""
    parts: list[str] = []
    for part in (p.strip() for p in raw.split("-")):
        if part == "?":
            parts.append("?")
            continue
        norm = _poky_part(part)
        parts.append(norm if norm is not None else part)
    return "-".join(parts)


def poky_label_is_valid(text: str | None, ndim: int = 2) -> bool:
    """Whether the text is a valid Poky assignment for this dimension
    (part count = ndim and every part well formed)."""
    if text is None:
        return True
    raw = str(text).strip()
    if not raw:
        return True
    parts = [p.strip() for p in raw.split("-")]
    if len(parts) != ndim:
        return False
    for part in parts:
        if part == "?":
            continue
        if _poky_part(part) is None:
            return False
    return True


@dataclass
class PeakTable:
    """In-memory peak table: experiment_id + peak-picking parameters + peak rows."""

    experiment_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, peak: dict[str, Any]) -> int:
        """Append a row; a numeric Peak_ID is assigned automatically
        (``len(rows)+1`` when none is given)."""
        peak_id = int(peak.get("Peak_ID", 0) or 0) or (len(self.rows) + 1)
        row = dict(peak)
        row["Peak_ID"] = peak_id
        self.rows.append(row)
        return peak_id

    def remove(self, peak_id: int) -> None:
        """Remove one row by Peak_ID."""
        self.rows = [
            r for r in self.rows if int(r.get("Peak_ID", -1)) != peak_id
        ]


def save_peaks(
    path: Path | str,
    peaks: list[dict[str, Any]],
    extra_columns: tuple[str, ...] = (),
    *,
    nuclei: list[str] | None = None,
) -> Path:
    """Write the peak list as a Poky/Sparky ``.list`` (contract §6: the peak file is
    itself .list).

    The Poky format has no extra columns, so ``extra_columns`` survives only for old
    callers (and is ignored); ``nuclei`` names the nucleus of every F axis (F1/F2/F3
    order) and 3D export orders the w columns by the external convention
    (0.2.199-patch29dk); returns the path actually written (.list suffix added).
    """
    path = Path(path)
    if path.suffix.lower() != ".list":
        path = path.with_suffix(".list")
    is_3d = bool(peaks) and "F1_shift" in peaks[0]
    return export_peaks_poky(
        path, peaks, ndim=3 if is_3d else 2, nuclei=nuclei
    )


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    """Read a legacy CSV peak table (produced before 0.2.199-patch29ar)."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if key in _NUMERIC_KEYS:
                    try:
                        if key == "Peak_ID" and value != "":
                            row[key] = int(value)
                        elif value != "":
                            row[key] = float(value)
                        else:
                            row[key] = 0.0
                    except (TypeError, ValueError):
                        row[key] = 0
                else:
                    row[key] = value
            rows.append(row)
    return rows


def load_peaks(path: Path | str) -> list[dict[str, Any]]:
    """Read a peak table: ``.list`` (Poky) is parsed, the legacy CSV accepted.

    Returned rows carry a numeric Peak_ID (1..n in file order), the label,
    N_shift/H_shift or F1/F2/F3_shift, Intensity (Height) and Data/Volume; numeric
    columns are converted back to float.
    """
    path = Path(path)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    # .list is always parsed as Poky (including trimmed files without a header); other
    # suffixes are decided by the header (0.2.199-patch29fx: case-insensitive).
    is_poky = path.suffix.lower() == ".list" or bool(
        _POKY_HEADER_RE.match(text.lstrip())
    )
    rows = import_peaks_poky(path) if is_poky else _load_csv_rows(path)
    for i, row in enumerate(rows, start=1):
        if not (row.get("Peak_ID") or ""):
            row["Peak_ID"] = i
        for key in _NUMERIC_KEYS:
            if key not in row:
                continue
            try:
                row[key] = int(row[key]) if key == "Peak_ID" else float(row[key])
            except (TypeError, ValueError):
                pass
    return rows


def _poky_num(value: Any) -> float:
    """Lenient parsing of exported Poky numbers: empty/None/invalid falls back to 0.0
    (peak-table cells may be left blank)."""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def export_peaks_poky(
    path: Path | str,
    peaks: list[dict[str, Any]],
    ndim: int = 2,
    *,
    nuclei: list[str] | None = None,
) -> Path:
    """Export a Poky/Sparky peak table (.list): "Assignment w1 w2 [w3] Data Height Volume".

    2D w1=15N (N_shift), w2=1H (H_shift); 3D follows the external convention
    w1=15N/w2=13C/w3=1H (0.2.199-patch29dk, user); ``nuclei`` names the nucleus of every
    F axis (F1/F2/F3 order), and when it is missing or does not form a permutation the
    export falls back to positional w1/w2/w3 = F1/F2/F3_shift; unnamed peaks are
    ?-? (2D) / ?-?-? (3D); Height=Intensity %.3g; Data/Volume=0; double spaces.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_3d = ndim >= 3 or (bool(peaks) and "F1_shift" in peaks[0])
    header = (
        "Assignment w1 w2 w3 Data Height Volume"
        if is_3d
        else "Assignment w1 w2 Data Height Volume"
    )
    lines = [header]
    order = (
        _external_w_to_internal_axes(nuclei, _EXTERNAL_3D_NUCLEI, 3)
        if is_3d
        else list(range(2))
    )
    for peak in peaks:
        label = str(peak.get("label", "") or "").strip().replace(" ", "_")
        if not label:
            label = "?-?-?" if is_3d else "?-?"
        if is_3d:
            shifts = [
                _poky_num(peak.get(f"F{order[j] + 1}_shift", 0.0))
                for j in range(3)
            ]
        else:
            shifts = [
                _poky_num(peak.get("N_shift", 0.0)),
                _poky_num(peak.get("H_shift", 0.0)),
            ]
        height = _poky_num(peak.get("Intensity", 0.0))
        row = [label] + [f"{s:.3f}" for s in shifts] + ["0", f"{height:.3g}", "0"]
        lines.append("  ".join(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _poky_header_ndim(line: str) -> int | None:
    """Number of coordinate columns in a Poky header (2 or 3 w columns); None when the
    line is not a header."""
    if not _POKY_HEADER_RE.match(line):
        return None
    cols = line.split()
    w = sum(1 for c in cols[1:] if c.lower().startswith("w"))
    return w if w in (2, 3) else None


def _infer_poky_ndim(tokens: list[str]) -> int | None:
    """Infer the dimensionality from the token count when there is no header
    (0.2.199-patch29fx).

    2D needs at least 3 columns (label+w1+w2) and has 6 in full; 3D needs at least 4
    and has 7 in full; 5 columns (rare: 2D plus two extra columns) is conservatively
    treated as 2D.
    """
    n = len(tokens)
    if n in (3, 6):
        return 2
    if n in (4, 7) or n > 7:
        return 3
    if n == 5:
        return 2
    return None


def _parse_poky_row(
    tokens: list[str], ndim: int, nuclei: list[str] | None
) -> dict[str, Any] | None:
    """One Poky data line -> peak dict; the coordinate columns must parse, Data/Height/
    Volume default to 0.0 and surplus trailing columns are ignored (0.2.199-patch29fx)."""
    if len(tokens) < ndim + 1:
        return None
    try:
        coords = [float(tokens[j + 1]) for j in range(ndim)]
    except (TypeError, ValueError):
        return None
    row: dict[str, Any] = {"label": tokens[0]}
    if ndim == 2:
        row.update({"N_shift": coords[0], "H_shift": coords[1]})
    else:
        order = _external_w_to_internal_axes(
            nuclei, _EXTERNAL_3D_NUCLEI, 3
        )
        for j in range(3):
            row[f"F{order[j] + 1}_shift"] = coords[j]
    data = _poky_num(tokens[ndim + 1]) if len(tokens) > ndim + 1 else 0.0
    height = _poky_num(tokens[ndim + 2]) if len(tokens) > ndim + 2 else 0.0
    volume = _poky_num(tokens[ndim + 3]) if len(tokens) > ndim + 3 else 0.0
    row.update(
        {
            "Data": data,
            "Height": height,
            "Volume": volume,
            "Intensity": height,
        }
    )
    return row


def import_peaks_poky(
    path: Path | str, *, nuclei: list[str] | None = None
) -> list[dict[str, Any]]:
    """Parse a Poky/Sparky .list back (header line + row parsing) into peak dicts.

    3D defaults to the external convention w1=15N/w2=13C/w3=1H (0.2.199-patch29dk,
    user); ``nuclei`` names the nucleus of every F axis (F1/F2/F3 order) and maps the w
    columns back to internal F1/F2/F3_shift; when it is missing or does not form a
    permutation the mapping falls back to positional (w1->F1 and so on).
    0.2.199-patch29fx: the header is case-insensitive; 2D >= 3 columns / 3D >= 4 suffice;
    Data/Height/Volume default to 0; surplus trailing columns are ignored; without a
    header the dimension is inferred from the column count.
    """
    path = Path(path)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    header_ndim: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if header_ndim is None:
            guessed = _poky_header_ndim(stripped)
            if guessed is not None:
                header_ndim = guessed
                continue
        tokens = stripped.split()
        if len(tokens) < 3:
            continue
        ndim = (
            header_ndim
            if header_ndim is not None
            else _infer_poky_ndim(tokens)
        )
        if ndim is None:
            continue
        row = _parse_poky_row(tokens, ndim, nuclei)
        if row is not None:
            rows.append(row)
    return rows


__all__ = [
    "PEAK_3D_COLUMNS",
    "PEAK_COLUMNS",
    "PeakTable",
    "export_peaks_poky",
    "import_peaks_poky",
    "load_peaks",
    "save_peaks",
]
