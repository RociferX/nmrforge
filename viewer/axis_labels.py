"""Spectral axis display name: replace F1/F2/F3 with the real nuclear symbol (H/N/C...), and add
x/y/z index to homonuclear. Data source: import the generated metadata.json of
dataset.dimensions[].logical_axis/nucleus (such as logical_axis=F1, nucleus=15N). After mapping,
HSQC displays N-H, and homonuclear 2D COSY displays Hx-Hy,3D displays as C-N-H. The caller falls
back when metadata is missing F1/F2/F3."""

from __future__ import annotations

_NUCLEUS_ALIASES = {
    "1H": "H",
    "2H": "D",
    "13C": "C",
    "15N": "N",
    "19F": "F",
    "31P": "P",
    "23Na": "Na",
    "29Si": "Si",
}
_SUBSCRIPT = "xyz"

# The gyromagnetic ratio of the nucleus (relative to 1H), used to infer the nuclear type according
# to the observation frequency sf (0.2.89).
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


def infer_nucleus(sf: float) -> str:
    """Infer the nuclear type (corresponding to chemical shift) according to the observed frequency
    (sf, MHz). sf/gyromagnetic ratio = 1H frequency corresponding to this dimension, the one
    closest to the common magnetic field (300-2000 MHz) is the core; if it cannot be confidently
    determined, an empty string is returned."""
    if not sf or sf <= 0:
        return ""
    best, best_err = "", float("inf")
    for nucleus, ratio in _NUCLEUS_RATIOS.items():
        implied_1h = sf / ratio
        if not (300.0 <= implied_1h <= 2100.0):
            continue
        err = min(abs(implied_1h - b0) for b0 in _COMMON_B0_H1) / implied_1h
        if err < best_err:
            best, best_err = nucleus, err
    return best if best_err < 0.05 else ""


def nucleus_symbol(nucleus: str) -> str:
    """Kernel string -> Display symbols: 15N -> N, 1H -> H; remove leading digits when unknown."""
    n = (nucleus or "").strip()
    if n in _NUCLEUS_ALIASES:
        return _NUCLEUS_ALIASES[n]
    body = n
    while body and body[0].isdigit():
        body = body[1:]
    return body or n


def axis_labels_from_nuclei(nuclei: list[str]) -> tuple[str, ...]:
    """Nuclei[i] is the core of the i-th dimension (F1/F2/F3); when the same core appears multiple
    times, all add x/y/z index. The index priority is based on the role of the logical axis:
    direct dimension (the F axis with the largest number) > acqu2 > acqu3, that is, the rearward
    index in the logical order is reviewed: 2D double 1H:F2 -> Hx, F1 -> Hy; 3D three identical
    cores: F3 -> Hx, F2 -> Hy, F1 -> Hz; HNN Double 15N:F2 -> Nx, F1 -> Ny."""
    symbols = [nucleus_symbol(n) for n in nuclei]
    counts = {s: symbols.count(s) for s in set(symbols)}
    labels = list(symbols)
    for symbol, cnt in counts.items():
        if cnt <= 1:
            continue
        positions = [i for i, s in enumerate(symbols) if s == symbol]
        for rank, pos in enumerate(sorted(positions, reverse=True)):
            if rank < len(_SUBSCRIPT):
                labels[pos] = f"{symbol}{_SUBSCRIPT[rank]}"
            else:
                labels[pos] = f"{symbol}{rank + 1}"
    return tuple(labels)


def nuclei_from_metadata(metadata: dict | None) -> list[str] | None:
    """Extract the nucleus list from the imported metadata in the order of F1/F2/F3; if the
    information is missing, None is returned. 0.2.89: Give priority to inferring the nucleus
    according to the observation frequency sf (corresponding to chemical shift), and fall back
    to the stored nucleus field if the inference fails."""
    dims = ((metadata or {}).get("dataset") or {}).get("dimensions") or []
    by_axis: dict[int, str] = {}
    for dim in dims:
        axis = str((dim or {}).get("logical_axis", "") or "")
        if axis[:1] != "F" or not axis[1:].isdigit():
            continue
        try:
            sf = float((dim or {}).get("sf", 0) or 0)
        except (TypeError, ValueError):
            sf = 0.0
        nucleus = infer_nucleus(sf) or str((dim or {}).get("nucleus", "") or "")
        if nucleus:
            by_axis[int(axis[1:])] = nucleus
    if not by_axis:
        return None
    return [by_axis[i] for i in sorted(by_axis)]
