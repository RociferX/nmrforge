"""Independent spectrum viewing module (decoupled from project management GUI and can be run
independently). ``python -m viewer [spectrum.ft2]`` can open the window. 0.2.199-patch29ht: The
package import itself remains lightweight (no longer associated with pyqtgraph/scipy) --
Scenarios (GUI annotation tags) that only use ``viewer.axis_labels`` are not affected;
``Spectrum`` / ``SpectrumAxis`` / ``SpectrumViewer`` are not actually imported until the first
visit."""

from __future__ import annotations

from typing import Any

__all__ = ["Spectrum", "SpectrumAxis", "SpectrumViewer"]


def __getattr__(name: str) -> Any:
    """Import public objects in the package on demand (PEP 562)."""
    if name in ("Spectrum", "SpectrumAxis"):
        from viewer.spectrum import Spectrum, SpectrumAxis

        return Spectrum if name == "Spectrum" else SpectrumAxis
    if name == "SpectrumViewer":
        from viewer.spectrum_viewer import SpectrumViewer

        return SpectrumViewer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
