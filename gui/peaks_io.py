"""Peak table IO and Poky export (G2B-005). From 0.2.164 onwards, directly use
core.peaks.peak_table of Shared Contract (delete the local equivalent implementation and
_use_core_peaks rollback branch to avoid the two sets of formats drifting)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.peaks.peak_table import (
    export_peaks_poky as core_export,
)
from core.peaks.peak_table import (
    import_peaks_poky as core_import,
)
from core.peaks.peak_table import (
    load_peaks as core_load,
)
from core.peaks.peak_table import (
    normalize_poky_label as core_normalize_label,
)
from core.peaks.peak_table import (
    poky_label_is_valid as core_label_valid,
)
from core.peaks.peak_table import (
    save_peaks as core_save,
)


def load_peaks(path: Path | str) -> list[dict[str, Any]]:
    """Read the peak table (.list takes precedence, Poky; old CSV compatible; core.peaks
    implementation)."""
    return list(core_load(path))


def save_peaks(path: Path | str, peaks: list[dict[str, Any]]) -> Path:
    """Write back the peak table file (.list/Poky;core.peaks implementation)."""
    return Path(core_save(path, peaks))


def export_peaks_poky(
    path: Path | str,
    peaks: list[dict[str, Any]],
    ndim: int = 2,
    *,
    nuclei: list[str] | None = None,
) -> Path:
    """Export Poky/Sparky.list(core.peaks implementation; see core for nuclei)."""
    return Path(core_export(path, peaks, ndim=ndim, nuclei=nuclei))


def import_peaks_poky(
    path: Path | str, *, nuclei: list[str] | None = None
) -> list[dict[str, Any]]:
    """Import Poky/Sparky.list(core.peaks implementation; see core for nuclei)."""
    return list(core_import(path, nuclei=nuclei))


def normalize_poky_label(text: str | None, ndim: int = 2) -> str:
    """Poky assignment is segmented and normalized by dimension (core.peaks implementation, 2D two
    paragraphs/3D three segments)."""
    return core_normalize_label(text, ndim=ndim)


def poky_label_is_valid(text: str | None, ndim: int = 2) -> bool:
    """Determine whether the text is the Poky assignment of the current dimension (implemented by
    core.peaks)."""
    return core_label_valid(text, ndim=ndim)
