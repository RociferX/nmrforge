"""Step -> possible workflow run refs (single source of truth).

0.2.199-patch29hz-fix1: the pipeline and the project tree each kept a copy; both now
come from gui/pipeline_state. fix24: moved further down into core, because the
workflow layer (batch reference parameters) needs it too and workflow must not depend
on gui in reverse. gui/pipeline_state keeps re-exporting the same names, so external
imports are unchanged.
"""

from __future__ import annotations

STEP_RUN_REFS: dict[str, tuple[str, ...]] = {
    "fid": ("convert_to_fid", "manual_fid"),
    "spectrum": (
        "process",
        "reconstruct_nus",
        "manual_process",
        "manual_nus",
        "phase_optimize_unified",
        "smile_optimize_rank1",
    ),
    "smile": ("smile_optimize",),
    "peaks": ("pick_peaks", "manual_peaks"),
}

# Spectrum refs that belong to the manual path only (registered when a script is run by hand)
MANUAL_SPECTRUM_RUN_REFS: tuple[str, ...] = ("manual_process", "manual_nus")

ALL_STEP_RUN_REFS: tuple[str, ...] = tuple(
    sorted({ref for refs in STEP_RUN_REFS.values() for ref in refs})
)
