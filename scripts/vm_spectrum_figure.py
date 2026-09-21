"""Automatic vs manual: a **spectrum comparison figure** (the 2D spectra plus the 3D HN projection).

Why a separate figure: the similarities and offsets ``--manual`` reports are debugging aids; whether
a spectrum is usable is settled by manual inspection and the QC scores, and this figure is the raw
evidence a reader can look at.

Usage (on the machine; data paths appear on the command line only)::

    nmrforge/bin/python scripts/vm_spectrum_figure.py \
        --software-2d <automatic 2D .ft2> --manual-2d <manual 2D .ft2> \
        --software-3d <automatic 3D .ft3> --manual-3d <manual 3D .ft3> \
        --tag-2d <2D data set tag> --tag-3d <3D data set tag> --out figure.png

The first row holds the two 2D spectra, the second the **HN projections** of the two 3D spectra
(summed over 13C). Both panels of a row use the same ppm window and the same contour convention
(each normalised to its own maximum), so peak shapes and peak counts can be compared by eye. The
axes come from the product's own spectrum reader (shared with peak picking and the viewer) rather
than from header fields rebuilt here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Contour levels as fractions of each spectrum's own maximum (relative convention).
LEVELS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9)
#: A single contour colour (no black; still clear when four panels sit together).
COLOR = "#1f4e79"
#: Figure text is ASCII only: the machine has no CJK fonts and titles would show boxes.


def _nucleus_family(label: str) -> str:
    """Map ``15N`` / ``HN`` / ``1H`` / ``13C`` to ``N`` / ``H`` / ``C``.

    The isotope prefix is stripped first (``1H`` starts with a digit).
    """
    text = str(label or "").upper().replace(" ", "")
    core = text.lstrip("0123456789") or text
    if core.startswith("H"):
        return "H"
    if "N" in core:
        return "N"
    if "C" in core:
        return "C"
    return core


def _load(path: Path) -> dict:
    """Read one spectrum; returns ``(data, {nucleus family: ppm axis})``."""
    from workflow.pick_peaks import read_spectrum_axes

    spectrum = read_spectrum_axes(path)
    axes: dict[str, np.ndarray] = {}
    for position, ppm_axis in enumerate(spectrum.ppm):
        label = (
            str(spectrum.nuclei[position])
            if position < len(spectrum.nuclei)
            else f"F{position + 1}"
        )
        axes[_nucleus_family(label)] = np.asarray(ppm_axis, dtype=float)
    return {"data": np.asarray(spectrum.data, dtype=float), "axes": axes}


def _hn_plane(spectrum: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The HN plane: 2D is used as is, 3D is summed over 13C (no 13C axis -> error, no guessing)."""
    data = spectrum["data"]
    axes = spectrum["axes"]
    families = list(axes)
    if data.ndim == 2:
        if not {"H", "N"} <= set(families):
            raise SystemExit(f"2D spectrum is missing the H/N axes (found: {families})")
        return data, axes["H"], axes["N"]
    if data.ndim != 3:
        raise SystemExit(f"only 2D/3D spectra are supported, got {data.ndim}D")
    if "C" not in families:
        raise SystemExit(f"3D spectrum has no 13C axis to sum over (found: {families})")
    carbon = families.index("C")
    remaining = [index for index in range(3) if index != carbon]
    plane = data.sum(axis=carbon)
    first, second = (families[index] for index in remaining)
    if {first, second} != {"H", "N"}:
        raise SystemExit(f"an HN projection needs the H and N axes, got: {first}/{second}")
    return plane, axes["H"], axes["N"]


def _window(ppm_a: np.ndarray, ppm_b: np.ndarray) -> tuple[float, float]:
    """Shared ppm window of the two spectra on that axis (intersection)."""
    low = max(float(ppm_a.min()), float(ppm_b.min()))
    high = min(float(ppm_a.max()), float(ppm_b.max()))
    if not high > low:
        raise SystemExit("the two spectra share no ppm window on that axis")
    return low, high


def _crop(
    plane: np.ndarray, ppm_h: np.ndarray, ppm_n: np.ndarray, low_h: float, high_h: float,
    low_n: float, high_n: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Crop to the shared window and turn it into increasing ppm (flipped back when plotting)."""
    rows = np.where((ppm_n >= low_n) & (ppm_n <= high_n))[0]
    cols = np.where((ppm_h >= low_h) & (ppm_h <= high_h))[0]
    block = np.abs(plane)[np.ix_(rows, cols)]
    return block, ppm_h[cols], ppm_n[rows]


def _draw(ax, block: np.ndarray, ppm_h: np.ndarray, ppm_n: np.ndarray, title: str) -> None:
    peak = float(block.max()) if block.size else 0.0
    if not np.isfinite(peak) or peak <= 0.0:
        ax.text(0.5, 0.5, "no signal", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        return
    # Matplotlib wants increasing coordinates; the display range is inverted below (NMR convention).
    h_axis = ppm_h[::-1] if ppm_h[0] < ppm_h[-1] else ppm_h
    n_axis = ppm_n[::-1] if ppm_n[0] < ppm_n[-1] else ppm_n
    values = block
    if ppm_h[0] < ppm_h[-1]:
        values = values[:, ::-1]
    if ppm_n[0] < ppm_n[-1]:
        values = values[::-1, :]
    ax.contour(
        h_axis,
        n_axis,
        values,
        levels=[peak * level for level in LEVELS],
        colors=COLOR,
        linewidths=0.6,
    )
    ax.set_xlabel("1H (ppm)")
    ax.set_ylabel("15N (ppm)")
    ax.set_title(title)


def main(argv: list[str] | None = None) -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser(
        description="automatic vs manual spectrum comparison figure (2D + HN projection)"
    )
    parser.add_argument("--software-2d", type=Path, required=True)
    parser.add_argument("--manual-2d", type=Path, required=True)
    parser.add_argument("--software-3d", type=Path, default=None)
    parser.add_argument("--manual-3d", type=Path, default=None)
    parser.add_argument("--tag-2d", default="2D")
    parser.add_argument("--tag-3d", default="3D HN")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=160)
    args = parser.parse_args(argv)

    for label, path in (
        ("--software-2d", args.software_2d),
        ("--manual-2d", args.manual_2d),
        ("--software-3d", args.software_3d),
        ("--manual-3d", args.manual_3d),
    ):
        if path is not None and not path.is_file():
            parser.error(f"{label} is not a file")
    if (args.software_3d is None) != (args.manual_3d is None):
        parser.error("give both 3D spectra or neither")

    rows: list[tuple[str, dict, dict]] = []
    auto_2d = _load(args.software_2d)
    hand_2d = _load(args.manual_2d)
    rows.append((args.tag_2d, auto_2d, hand_2d))
    if args.software_3d is not None and args.manual_3d is not None:
        rows.append((args.tag_3d, _load(args.software_3d), _load(args.manual_3d)))

    figure, grid = plt.subplots(len(rows), 2, figsize=(11.0, 4.6 * len(rows)), squeeze=False)
    for index, (tag, auto, hand) in enumerate(rows):
        auto_plane, auto_h, auto_n = _hn_plane(auto)
        hand_plane, hand_h, hand_n = _hn_plane(hand)
        low_h, high_h = _window(auto_h, hand_h)
        low_n, high_n = _window(auto_n, hand_n)
        panels = (
            (grid[index][0], auto_plane, auto_h, auto_n, f"{tag} - automatic"),
            (grid[index][1], hand_plane, hand_h, hand_n, f"{tag} - manual"),
        )
        for ax, plane, ppm_h, ppm_n, title in panels:
            block, block_h, block_n = _crop(
                plane, ppm_h, ppm_n, low_h, high_h, low_n, high_n
            )
            shape = "x".join(str(int(v)) for v in plane.shape)
            _draw(ax, block, block_h, block_n, f"{title} ({shape})")
            ax.set_xlim(high_h, low_h)
            # 15N runs large-at-the-bottom (user convention, 2026-09-21), matching how the
            # laboratory displays its own spectra.
            ax.set_ylim(high_n, low_n)
            ax.set_aspect("auto")
    figure.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.out, dpi=args.dpi)
    print(f"figure written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
