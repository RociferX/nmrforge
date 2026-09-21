"""Unified lookup rules for project data artifacts.

The difference between the "primary spectrum" and the 3D derived projections is handled here
centrally, so that the GUI, the state machine and the project status do not each scan for
``*.ft2`` and reach contradictory conclusions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def is_projection_spectrum_file(name: str, data_id: str) -> bool:
    """Whether a file name denotes a 3D projection rather than the active primary spectrum.

    Known projection names include ``d_001_proj_F1.ft2`` and ``d_001_15N-1H.ft2``. The old
    primary-spectrum name ``<experiment>-<data>.ft2`` must not be caught by the second rule.
    """
    stem = Path(str(name)).stem
    if stem == str(data_id):
        return False
    if "_proj_" in stem:
        return True
    head, separator, tail = stem.rpartition("-")
    if not separator:
        return False

    def _nucleus_tag(text: str) -> bool:
        digits = "".join(character for character in text if character.isdigit())
        letters = "".join(character for character in text if character.isalpha())
        return bool(digits) and bool(letters) and len(letters) <= 2

    return _nucleus_tag(head.rsplit("_", 1)[-1]) and _nucleus_tag(tail)


def find_primary_spectrum(manager: Any, exp_id: str, data_id: str) -> Path | None:
    """Return the active primary spectrum of a data entry; ``None`` when only projections exist.

    ``DataEntry.spectrum_path`` is the explicit registration: it wins and is authoritative. Only
    for compatibility with old projects does this scan the ``spectra`` directory of the data
    entry, and that fallback scan excludes 3D projection files.
    """
    try:
        entry = manager.data(exp_id, data_id)
    except Exception:  # noqa: BLE001 - a failed lookup counts as "no artifact"
        return None

    registered = str(getattr(entry, "spectrum_path", "") or "")
    if registered:
        path = Path(registered)
        if not path.is_absolute():
            path = manager.root / path
        if path.is_file():
            return path

    try:
        spectra = manager.data_dir(exp_id, data_id, "spectra")
    except Exception:  # noqa: BLE001 - a broken directory mapping counts as "no artifact"
        return None

    # the two deterministic namings (new and old) come before the wildcard fallback.
    for stem in (f"{exp_id}-{data_id}", data_id):
        for extension in ("ft2", "ft3"):
            path = spectra / f"{stem}.{extension}"
            if path.is_file():
                return path

    # the backend may also name files after the original dataset_id; only non-projections count.
    for extension in ("ft2", "ft3"):
        try:
            matches = sorted(spectra.glob(f"*.{extension}"))
        except OSError:
            matches = []
        for path in matches:
            if not is_projection_spectrum_file(path.name, data_id):
                return path
    return None
