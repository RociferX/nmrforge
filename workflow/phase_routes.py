"""Unified phase-optimisation route (replaces the simple/advanced dispatch,
2026-08-17).

The unified plan: first a per-dimension complex preview (only the search axis PS
omits -di while the other axes add -di with their fixed phases, and no zero
fill) -> in-memory phase tuning (the old algorithm's criterion: median net
absorption of fixed traces, no extra backend) -> processing-parameter
optimisation (baseline/zero fill/window) -> the full final run (the joint review
is skipped since 0.2.199-patch29fj):
the final phase of every dimension is fed back into the initial script to
produce a new full script (NUS no longer writes a rotated nus3d_rc_ph copy; the
direct-dimension phase goes into the step1 PS after EXT and the
indirect-dimension phases into the step3 PS).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from backend.memory_disk import INTERMEDIATE_SUBDIR
from backend.runtime import cancel_requested
from core.data.internal_data_model import Experiment, SamplingMode
from core.experiment.acquisition_mode_detector import sign_sampling_flags
from core.planning.method_selector import select_method
from core.project.manager import atomic_write_text
from ui_support.i18n import tr
from workflow.optimization_report import format_phase_degrees, phase_angle_value


def reference_optimize_switches(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reference-mode optimisation switches (**for tests/reproduction/audit only;
    real experiments keep the defaults**).

    - ``baseline``: ``"auto"`` (default) / ``False`` / ``"off"`` (no
      optimisation; the final run uses the ``baseline`` given by the caller) /
      ``{"grid": [[mode, order], ...]}``;
    - ``window``: ``"auto"`` (default) / ``False`` / ``"off"`` (no optimisation;
      the final run uses the ``window`` given by the caller) /
      ``{"direct_candidates": [...], "indirect_candidates": [...]}``.

    With optimisation switched off the reference is no longer "optimised
    automatically", which must be stated in the records or the paper (the API
    guide requires that real experiments do not use these switches).
    """
    return dict((params or {}).get("reference_optimize") or {})


def baseline_grid_from_switch(switch: Any) -> list[tuple[str, int]] | None:
    """``reference_optimize.baseline`` → optimize_baseline(grid=…)."""
    if isinstance(switch, Mapping) and switch.get("grid"):
        grid: list[tuple[str, int]] = []
        for item in switch["grid"]:
            grid.append((str(item[0]), int(item[1])))
        return grid
    return None


def window_candidates_from_switch(
    switch: Any,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """``reference_optimize.window`` → (direct-dimension candidates,
    indirect-dimension candidates).
    """
    if isinstance(switch, Mapping):
        direct = switch.get("direct_candidates")
        indirect = switch.get("indirect_candidates")
        return (
            [dict(c) for c in direct] if direct else None,
            [dict(c) for c in indirect] if indirect else None,
        )
    return None, None


def _unlink_quiet(path) -> None:
    """Delete an intermediate spectrum once it has been consumed
    (0.2.199-patch29fk-fix: deleted as soon as it is used instead of waiting for
    the whole finalize to finish; on the ramdisk this frees tmpfs space right
    away).
    """
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _rmtree_quiet(path: Path) -> None:
    """Delete an intermediate directory once it has been consumed (NUS
    reconstruction planes; the final run rebuilds them).
    """
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _phase_delta(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Phase difference (circular p0 difference plus p1 difference), used for the
    iterative convergence test (0.2.199-patch29do).
    """
    p0 = abs((a[0] - b[0] + 180.0) % 360.0 - 180.0)
    return float(p0 + abs(a[1] - b[1]))


def _axis_index(axis: str, ndim: int = 2) -> int:
    """Logical axis name -> index into the production-layout spectrum array.

    Measured NMRPipe output layout: 2D is (F1, F2); 3D (including the finalize ZTP
    chain) is (F2, F1, F3) -- the FDF header labels are unreliable in 3D output,
    so the sizes and complex-axis positions are authoritative.
    """
    if ndim >= 3:
        return {"F2": 0, "F1": 1, "F3": 2}.get(axis, 0)
    return {"F1": 0, "F2": 1}.get(axis, 0)


def _template(experiment: Experiment) -> Any:
    """Look up a template by experiment type name (exact match first, then a
    case-insensitive fallback).
    """
    import core.experiments  # noqa: F401  Import and register the built-in template.
    from core.experiments.registry import get as get_template

    name = experiment.experiment_type.name if experiment.experiment_type else ""
    tpl = get_template(name)
    if tpl is None:
        for tname in (name.upper(), name.lower()):
            tpl = get_template(tname)
            if tpl is not None:
                break
    return tpl


def _sign_mode(experiment: Experiment) -> str:
    """Return the score sign constraint from the experiment template's peak_sign
    (mixed = positive and negative peaks coexist, uniform = same sign; uniform by
    default).
    """
    tpl = _template(experiment)
    if tpl is not None and tpl.peak_sign == "mixed":
        return "mixed"
    return "uniform"


def _disambiguate_180_mixed(
    complex_arr: np.ndarray,
    axis: int,
    phase: tuple[float, float],
    experiment: Experiment,
    searched_axis: str,
) -> tuple[float, float]:
    """+-180 deg absolute sign disambiguation for mixed experiments (a
    chemical-shift region prior).

    If the preset gives peak_sign_regions for the nucleus on the search axis (e.g.
    the C-alpha/C-beta of HNCACB 13C), the dominant sign of the strong peaks in
    each region is counted at the current phase: when the two regions have
    opposite signs and each is clean (dominance >= 0.7 with at least 4 strong
    peaks), p0 += 180 flips the whole spectrum if the absolute convention
    disagrees with the preset; an unclean region, too few peaks or a nucleus
    mismatch means no flip (conservative).
    """
    tpl = _template(experiment)
    if tpl is None:
        return phase
    dim = next(
        (d for d in experiment.dimensions if d.logical_axis == searched_axis),
        None,
    )
    if dim is None or not dim.nucleus:
        return phase
    regions = (tpl.peak_sign_regions or {}).get(dim.nucleus)
    if not regions or len(regions) < 2:
        return phase
    from workflow.memory_phase_search import rotate_real

    n = complex_arr.shape[axis]
    ppm = dim.o1p + (n / 2.0 - np.arange(n)) * (float(dim.sw) / (n * float(dim.sf)))
    real = rotate_real(complex_arr, axis, phase[0], phase[1])
    moved = np.moveaxis(real, axis, -1)
    flat = moved.reshape(-1, n)
    mag = np.abs(flat)
    peak_val = flat[np.argmax(mag, axis=0), np.arange(n)]
    global_max = float(np.max(np.abs(peak_val)))
    if global_max <= 0:
        return phase
    observed: list[int] = []
    for cfg in regions.values():
        lo, hi = cfg["ppm"]
        idx = np.where((ppm >= lo) & (ppm <= hi))[0]
        if idx.size == 0:
            return phase
        vals = peak_val[idx]
        strong = vals[np.abs(vals) > 0.2 * global_max]
        if strong.size < 4:
            return phase
        pos = int((strong > 0).sum())
        neg = int((strong < 0).sum())
        if max(pos, neg) / strong.size < 0.7:
            return phase
        observed.append(1 if pos > neg else -1)
    expected = [int(cfg["sign"]) for cfg in regions.values()]
    if len(set(observed)) < 2 or observed == expected:
        return phase
    return ((phase[0] + 180.0) % 360.0, phase[1])


def _read_complex_preview(path: Path | str, unpack_axis: int | None = None) -> np.ndarray:
    """Read a complex preview file: use nmrglue's result when it reads as complex,
    otherwise unpack the interleaved real array along unpack_axis (the complex
    axis of 3D output is not fixed: preview_F2 sits on axis 0 and preview_F1 on
    axis 1, so read_pipe_complex, which unpacks only axis 0, would unpack the
    wrong one).
    """
    import nmrglue as ng

    from core.data.pipe_io import read_pipe_complex

    path = Path(path)
    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        return arr.astype(np.complex128)
    if unpack_axis is not None:
        moved = np.moveaxis(arr, unpack_axis, -1)
        even = moved[..., 0::2]
        odd = moved[..., 1::2]
        return np.moveaxis(even + 1j * odd, -1, unpack_axis).astype(np.complex128)
    return read_pipe_complex(path)


def _preview_memory_warning(path, *, axis="", progress=None, logs=None) -> None:
    """In-memory warning about the phase-search preview array
    (0.2.199-patch29ec): estimate a complex128 array from the file size and warn
    when it exceeds 85% of available memory (non-blocking; a failure raises an
    explicit MemoryError separately).
    """
    try:
        size_bytes = Path(path).stat().st_size * 2
        est_mb = size_bytes / (1024.0 * 1024.0)
        from backend.memory_guard import available_memory_mb

        avail = available_memory_mb()
        if est_mb > avail * 0.85:
            msg = tr(
                "Memory tips: {p0} Replica preview {p1:.0f}MB,about {p2} MB free, memory may "
                "be insufficient (on failure, free memory or use a smaller data "
                "set)",
                p0=axis,
                p1=est_mb,
                p2=avail,
            )
            if progress is not None:
                progress(msg)
            if logs is not None:
                logs.append(msg)
    except OSError:
        pass


def _load_preview_with_memory_guard(
    path, *, axis, unpack_axis=None, progress=None, logs=None
) -> np.ndarray:
    """Read a complex preview with an in-memory warning (0.2.199-patch29ec): warn
    when the estimate exceeds the limit before reading, and turn MemoryError into
    an explicit RuntimeError so a crash or an out-of-memory kill is not silent.
    """
    _preview_memory_warning(path, axis=axis, progress=progress, logs=logs)
    try:
        return _read_complex_preview(path, unpack_axis=unpack_axis)
    except MemoryError:
        size_mb = 0.0
        try:
            size_mb = Path(path).stat().st_size * 2 / (1024.0 * 1024.0)
        except OSError:
            pass
        suffix = tr("(about {p0:.0f}MB)", p0=size_mb) if size_mb else ""
        raise RuntimeError(
            tr(
                "Out of memory: Unable to load {p0} Replica preview{p1},free memory or use a "
                "smaller data "
                "set",
                p0=axis,
                p1=suffix,
            )
        ) from None


def _read_complex_ft3(path: Path | str) -> np.ndarray:
    """Read a fully complex 3D final spectrum (the 0.2.199-patch18 keep_complex
    mode).

    When no finalize PS adds -di, the indirect dimensions (F2/F1) store
    real/imaginary interleaved on axes 0/1 and the direct dimension is the last
    axis (a plane stream, not complex). Returns a (F2, F1, F3) complex array.
    """
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        return arr.astype(np.complex128)
    cplx = arr[0::2] + 1j * arr[1::2]
    if cplx.ndim >= 2:
        cplx = cplx[:, 0::2] + 1j * cplx[:, 1::2]
    return cplx.astype(np.complex128)


def _read_real_ft3(path: Path | str) -> np.ndarray:
    """Read a real 3D/2D final spectrum (0.2.199-patch29q).

    The real finalize preview used for the direct-dimension search (every axis PS
    carries -di) does not use the complex interleaved layout, so _read_complex_ft3
    cannot be used (it would pair adjacent real points as real/imaginary;
    measured on 100/101 the shape (512,512,670) was misread as (256,256,670),
    skewing results by about 6-9 deg). The direct dimension is the last axis.
    """
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    return np.asarray(arr, dtype=float)


def _cleanup_unified_intermediates(
    work: Path,
    dataset_id: str,
    *,
    experiment: Any | None = None,
    backend: Any | None = None,
) -> None:
    """Clean up the intermediate products of the unified flow (phase previews and
    window/zero-fill score spectra plus the NUS reconstruction-plane
    directories).

    Cleaned up (inside the process working directory only):
    - the .com/.ft2/.ft3/.fdf of {dataset_id}_preview_*
    - the .com/.ft3/.fdf of {dataset_id}_joint*
    - the .com/.ft3/.fdf of {dataset_id}_win1* / _win2* / _win3* / _winzf_*
      (_winzf_* is 0.2.199-patch29em: a leftover of the old zero-fill/window
      candidates, no longer generated but still present in historical data
      directories)
    - 0.2.199-patch29dy (user): the NUS reconstruction intermediate directories
      nus3d_1 / nus3d_rc_ph / nus2d are not left behind (re-optimising afterwards
      re-runs SMILE, which is the normal price). **Exception (2026-09-25, user):
      the 3D NUS ``nus3d_rc`` is kept** -- it is the "input file (directory) for
      indirect-dimension processing", so keeping it means an "indirect-dimension
      flip (FT -neg)" only re-runs the final script instead of SMILE;
    - 0.2.199-patch29em (user): uniform window/phase-optimisation intermediate
      files were not deleted -- on some call paths backend.work_dir is unset, so
      the backend writes preview/joint and so on into the default working
      directory raw.parent/{dataset_id}.nmrpipe, which was never cleaned; those
      are deleted here too (the same-named directory under the workspace data
      directory and the fallback directory next to the raw data are pure
      temporary products and hold no final spectrum or final script).

    The kept items (final spectra, final scripts, phase.json, fid/, nuslist and
    smile.log) are unaffected.
    """
    if not work.is_dir():
        return
    # 0.2.199-patch29ez: the intermediates all live in work/_intermediate (in disk
    # mode the whole directory is cleaned; in ramdisk mode it is a symbolic link
    # torn down when generate_spectrum finishes)
    _intermediate = work / INTERMEDIATE_SUBDIR
    if _intermediate.is_dir() and not _intermediate.is_symlink():
        shutil.rmtree(_intermediate, ignore_errors=True)
    exts = (".com", ".ft2", ".ft3", ".fdf")
    for base_pattern in (
        f"{dataset_id}_preview_*",
        f"{dataset_id}_direct_*",
        f"{dataset_id}_joint*",
        f"{dataset_id}_win1*",
        f"{dataset_id}_win2*",
        f"{dataset_id}_win3*",
        f"{dataset_id}_winzf_*",
    ):
        for p in work.glob(base_pattern):
            if p.suffix in exts:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    for _dir in ("nus3d_1", "nus3d_rc_ph", "nus2d"):
        # 2026-09-25 (user): **the 3D NUS reconstruction-plane directory nus3d_rc is
        # kept** -- it is the "indirect-dimension processing input"; keeping it lets an
        # indirect-dimension flip (FT -neg) re-run only the final script (reading
        # nus3d_rc/test*.ft1) instead of SMILE. 2D (including 2D NUS) and 3D uniform
        # have no such intermediate directory and can simply re-run the full script,
        # so nus2d/nus3d_1/nus3d_rc_ph are still cleaned up.
        _target = work / _dir
        if _target.is_dir():
            shutil.rmtree(_target, ignore_errors=True)
    # 0.2.199-patch29em: clean up the backend's default-working-directory
    # fallback (when work_dir is unset, intermediate products such as
    # preview/joint land in raw.parent/{dataset_id}.nmrpipe)
    fallbacks: list[Path] = [work.parent / f"{dataset_id}.nmrpipe"]
    if experiment is not None:
        try:
            fallbacks.append(Path(experiment.source_path).parent / f"{dataset_id}.nmrpipe")
        except (AttributeError, TypeError, ValueError):
            # no source_path (test stubs or older objects): no fallback directory
            # is appended
            pass
    for _fb in fallbacks:
        if _fb != work and _fb.is_dir():
            shutil.rmtree(_fb, ignore_errors=True)


def _append_final_summary(
    logs: list[str],
    spectrum_path: str,
    *,
    direct_axis: str = "",
    direct_phase: tuple[float, float] | None = None,
    phases: dict[str, tuple[float, float]] | None = None,
    backend_runs: int = 0,
    baseline: Any = None,
    baseline_scores: dict[str, float] | None = None,
    zero_fill: Any = None,
    window: Any = None,
    optimization_logs: list[str] | None = None,
    peak_sign: str = "uniform",
    progress: Callable[[str], None] | None = None,
) -> None:
    """Closing report: spectrum quality and data-quality diagnostics
    (0.2.169-patch, written for the user to read).

    Structure:
      ◆ Final spectrum quality -- overall verdict plus SNR/phase/baseline/artefact
        sub-grades and scores; when the baseline is not flat and baseline
        optimisation is available, the reason is reported (the optimisation
        baseline and the threshold for keeping it off).
      ◆ Processing parameters and optimisation -- shared with the pipeline
        parameter report through format_optimization_report.
    (The data-quality diagnosis belongs to the Generate-FID step and has been
    moved out of this report, 2026-09-23 user request.)
    Every line also goes through progress into the GUI log panel.
    """
    from workflow.optimization_report import (
        format_optimization_report,
        spectrum_quality_report_lines,
        spectrum_report_title,
    )

    lines: list[str] = [spectrum_report_title()]
    # 2D (F1,F2);3D (F2,F1,F3)
    storage_axes = ["F1", "F2"] if direct_axis == "F2" else ["F2", "F1", "F3"]
    lines.append(tr("◆ processing settings"))
    lines += format_optimization_report(
        {
            "phase_route": "unified",
            "direct_phase": direct_phase,
            "phases": {k: v for k, v in (phases or {}).items()},
            "baseline": baseline,
            "window": window,
            "zero_fill": zero_fill,
            "backend_runs": backend_runs,
        }
    )
    lines += spectrum_quality_report_lines(
        str(spectrum_path),
        optimization_logs=optimization_logs,
        axis_names=storage_axes,
        sign_mode=peak_sign,
        baseline_scores=baseline_scores,
        progress=progress,
    )
    logs += lines
    if progress is not None:
        for line in lines:
            progress(line)


def _template_auto_phase(experiment: Experiment) -> bool:
    """Experiment-type-level automatic phase switch (presets
    processing_hints.auto_phase).

    With False (e.g. HMBC magnitude spectra) the unified path skips the phase
    search on every axis and the phase stays (0,0); the default True lets
    phase-sensitive experiments optimise the direct- and indirect-dimension
    phases automatically. A missing template or a failed parse falls back to
    True and does not block processing.
    """
    try:
        from core.experiments.registry import REGISTRY

        tpl = REGISTRY.get(experiment.experiment_type.name)
        if tpl is not None:
            return bool(tpl.processing_hints.get("auto_phase", True))
    except Exception:  # noqa: BLE001 - a failed template lookup must not block
        pass
    return True


def _template_peak_sign(experiment: Experiment) -> str:
    """Experiment-type peak sign (presets peak_sign): uniform = same sign, mixed =
    positive and negative coexisting. The same source as _template_auto_phase; a
    missing template or a failed parse falls back to uniform.
    """
    # 0.2.199-patch29hc: an annotation on the data takes precedence.
    ov = getattr(experiment, "note_peak_sign", "")
    if ov in ("uniform", "mixed"):
        return ov
    try:
        from core.experiments.registry import REGISTRY

        tpl = REGISTRY.get(experiment.experiment_type.name)
        if tpl is not None:
            return str(getattr(tpl, "peak_sign", "uniform") or "uniform")
    except Exception:  # noqa: BLE001 - a failed template lookup must not block
        pass
    return "uniform"


#: Sampling flags that derived runs (phase preview / joint evaluation) must carry
#: over: they decide the sign and direction of the indirect/direct-dimension FT
#: (-neg/-alt) and are not something "the step may choose freely".
#: The key table and value conventions have a single source
#: (``core.experiment.acquisition_mode_detector``).
def _sign_sampling(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Take the sampling override that affects the FT sign/direction out of the
    processing parameters (carried over by derived runs).

    2026-09-25 (user, building the API reference): manual flips
    (``sampling.flip_f1``/``flip_f2``) must feed into **every step** of the
    reference build -- the per-axis phase preview, the joint evaluation spectrum
    and the final-run script need the same sign convention; those derived runs
    rebuild params (with only the keys that step needs), so leaving this part out
    misaligns them ("the preview/evaluation spectrum was not flipped, the final
    run was").
    Returns a ``{"sampling": {...}}`` ready for ``**`` expansion (an empty dict
    when there is none). Other sampling keys such as ``auto_phase`` are **not**
    carried over: they belong to each step's own switches.
    """
    flags = sign_sampling_flags(params)
    return {"sampling": flags} if flags else {}


def _apply_ext_opt_enabled(params: dict[str, Any]) -> bool:
    """The "apply this range to the optimisation" switch (0.2.199-patch3): on by
    default.
    """
    return str(params.get("apply_ext_to_opt", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _split_final_ext(
    params: dict[str, Any],
) -> tuple[dict[str, Any], Any, Any, bool]:
    """Take the direct-dimension range (final_ext_lo/final_ext_hi) and the
    apply-to-optimisation switch out of the parameters.

    Returns (remaining parameters, ext_lo, ext_hi, apply_to_opt); the first pass
    and the complex preview path use the remaining parameters (the wide default
    6.5-10.5 window) while the final-run path maps the extracted values back onto
    ext_lo/ext_hi (_apply_final_ext); with apply_to_opt on, the caller also writes
    the range into the optimisation (first-pass reconstruction/phase search and
    the baseline/zero-fill/window evaluation).
    """
    p = dict(params)
    final_lo = p.pop("final_ext_lo", None)
    final_hi = p.pop("final_ext_hi", None)
    apply_to_opt = _apply_ext_opt_enabled(p)
    p.pop("apply_ext_to_opt", None)
    return p, final_lo, final_hi, apply_to_opt


def _apply_final_ext(params: dict[str, Any], final_lo: Any, final_hi: Any) -> dict[str, Any]:
    """Write the user's final-run direct-dimension range into the parameters (an
    empty value keeps the existing configuration).
    """
    p = dict(params)
    if final_lo is not None and str(final_lo).strip():
        p["ext_lo"] = str(final_lo)
    if final_hi is not None and str(final_hi).strip():
        p["ext_hi"] = str(final_hi)
    return p


def _direct_phase_width(params: dict[str, Any]) -> float:
    """Direct-dimension extraction window width (ppm): the difference between the
    effective EXT -x1/-xn values, falling back to the configured default.
    """
    from backend.config import resolve_ext_hi, resolve_ext_lo

    try:
        lo = float(resolve_ext_lo(params.get("ext_lo")))
        hi = float(resolve_ext_hi(params.get("ext_hi")))
    except (TypeError, ValueError):
        return 0.0
    return abs(lo - hi)


def _renormalize_direct_p1(
    direct_phase: tuple[float, float],
    first_params: dict[str, Any],
    final_params: dict[str, Any],
) -> tuple[float, float]:
    """Renormalise p1 by the window-width ratio when the final-run
    direct-dimension range differs from the first pass.

    p1 is the total linear phase in degrees across the whole extraction window;
    with a narrower or wider range the same p1 degrees spread over a different
    frequency range and the physical slope changes accordingly. Scaling by
    (final window width / first-pass window width) keeps the physical correction
    consistent with the in-memory search.
    """
    p0, p1 = direct_phase
    w_first = _direct_phase_width(first_params)
    w_final = _direct_phase_width(final_params)
    if w_first <= 0 or w_final <= 0:
        return (p0, p1)
    ratio = w_final / w_first
    if abs(ratio - 1.0) < 1e-9:
        return (p0, p1)
    return (p0, p1 * ratio)


def unified_route(
    experiment: Experiment,
    backend: Any,
    *,
    plan: Any | None = None,
    work_dir: Path | str | None = None,
    base_params: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Unified plan (replacing the simple/advanced dispatch): per-axis complex
    preview -> in-memory phase tuning (the old algorithm's criterion, no extra
    backend) -> full final run.

    uniform (0.2.199-patch29dr): an initial per-axis search (indirect dimensions
    first), then one more indirect-dimension search once the direct dimension is
    settled (no iteration; the joint review is skipped since
    0.2.199-patch29fj); the preview adds -di only outside the search axis (which
    stays at 0,0), the other axes use their fixed phases, and zero filling is the
    full auto one;
    NUS: a single SMILE pass produces the complex recon planes, the direct
    dimension is searched in memory on the real final spectrum and its projection
    traces (HT), the indirect dimensions replay the whole finalize chain in memory
    for a full search, and once the direct dimension is settled the indirect
    dimensions are searched once more; finally the final phase of every dimension
    is fed into the full script and re-run to produce a good spectrum (no rotated
    plane copies are written).
    """
    from workflow.memory_phase_search import search_axis_memory

    plan = plan or select_method(experiment)
    if experiment.sampling.mode is SamplingMode.NUS:
        return _unified_nus(
            experiment,
            backend,
            plan=plan,
            work_dir=work_dir,
            base_params=base_params,
            progress=progress,
        )
    work = Path(work_dir) if work_dir else backend._work_path(experiment)
    params = dict(base_params or {})
    params.pop("preview_axis", None)
    # 2026-09-23 (user request): the direct-dimension data quality diagnosis now runs at the end
    # of the Generate-FID step and lands in process/diagnostics.json; here it is only read back
    # and reused -- the spectrum step no longer re-runs or announces it. When the record is
    # missing or does not match the current fid (old working directory / manual route) the helper
    # falls back to a real run.
    diagnostics: dict[str, Any] = {}
    diag_logs: list[str] = []
    try:
        from workflow.direct_diagnostics import load_or_run_direct_diagnostics

        diag_result = load_or_run_direct_diagnostics(work, experiment, progress=progress)
        diagnostics = {
            "reports": list(diag_result.reports),
            "metrics": dict(diag_result.metrics),
            "apply_poly_time": diag_result.apply_poly_time,
            "repaired_badpoints": diag_result.repaired_badpoints,
            "backup_dir": diag_result.backup_dir,
            "clean": bool(getattr(diag_result, "clean", False)),
        }
    except Exception as exc:  # noqa: BLE001 - a failed diagnostic must not block
        diag_logs = [tr("data quality diagnosis failed: {p0}", p0=exc)]
    # 0.2.162-patch15: the user's final-run direct-dimension range
    # (final_ext_lo/final_ext_hi); 0.2.199-patch3: with "apply this range to the
    # optimisation" on it also enters the complex preview/optimisation evaluation,
    # otherwise the first complex preview keeps the wide default range and only the
    # final run uses it
    params, final_ext_lo, final_ext_hi, apply_ext_opt = _split_final_ext(params)
    if apply_ext_opt:
        params = _apply_final_ext(params, final_ext_lo, final_ext_hi)
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    sign_mode = _sign_mode(experiment)
    axes = [dim.logical_axis for dim in experiment.dimensions]
    # 0.2.75: the uniform path goes indirect first and direct last (the old
    # algorithm's order: the direct dimension locks onto peaks in the spectrum
    # corrected by the indirect dimensions); 0.2.199-patch29ad: uniform
    # direct-dimension complex data has a real imaginary part, so it is searched
    # directly on the complex preview (the NUS scheme of forcing real data + HT is
    # not applied)
    search_axes = [a for a in axes if a != direct_axis] + [direct_axis]
    fixed: dict[str, tuple[float, float]] = {}
    # 0.2.169: the data-quality diagnostic runs at the very start of the flow, so
    # its log must precede the preview/optimisation lines (previously diag_logs was
    # appended after the optimisation, putting the sequence out of order; the NUS
    # branch already did it at the start)
    logs: list[str] = list(diag_logs)
    # 0.2.167: experiment-type-level auto_phase (presets processing_hints) --
    # magnitude spectra (HMBC and the like) skip the phase search on every axis and
    # stay at (0,0), while phase-sensitive experiments filter the magnitude
    # indirect dimensions (QF has no notion of PS) out of the plan's phase nodes and
    # search only the axes that really have a phase step
    auto_phase = _template_auto_phase(experiment)
    if not auto_phase:
        search_axes = []
        logs.append(
            tr(
                "experiment type {p0}: magnitude spectra are not phase-optimised, so the all-axis "
                "phase search is skipped (keeping "
                "0,0)",
                p0=experiment.experiment_type.name,
            )
        )
    else:
        search_axes = [a for a in search_axes if f"phase_{a}" in plan.dag.nodes]
    # 0.2.199-patch29du (user, measured on the VM with sampleI and sampleH): the
    # phase-search preview never zero fills on any axis. Zero filling is data
    # dependent: with auto zero filling sampleI F1=90 deg was right but sampleH
    # F1=70 deg was wrong (should be 85); with no zero fill sampleI F1=90 deg
    # (guaranteed by the re-search) and sampleH F1=87.5 deg (2.5 deg off,
    # acceptable), with the direct dimension at 310 deg in both. The preview
    # therefore never zero fills, avoiding "zero filling suits some data and not
    # others".
    zf_phase = {"zero_fill": {a: {"mode": "none"} for a in axes}}
    backend_runs = 0
    # 0.2.199-patch29dr (user): no iteration -- after the initial per-axis search
    # (indirect dimensions first) the direct dimension is settled and the indirect
    # dimensions are searched once more (with the direct-dimension phase fixed in
    # the preview); there is no alternating iteration.
    # Indirect first: locking the direct-dimension traces depends on the spectrum
    # corrected by the indirect dimensions, whereas starting with the direct
    # dimension diverged in practice (sampleI F2 gave 172 deg with F1=(0,0)).
    for axis in search_axes:
        out_file = f"{experiment.dataset_id}_preview_{axis}.{ext}"
        t_axis = time.time()
        if progress is not None:
            progress(tr("In phase optimisation: {p0} Replica preview in progress", p0=axis))
        resp = backend.process(
            experiment,
            plan,
            direct_phase_override=dict(fixed) if fixed else None,
            params={**params, **zf_phase, "preview_axis": axis},
            out_file=out_file,
            script_name=f"{experiment.dataset_id}_preview_{axis}.com",
            progress=progress,
        )
        backend_runs += 1
        if not resp.get("success") or not resp.get("spectrum_path"):
            raise RuntimeError(
                tr(
                    "Replica preview ({p0}) failed: {p1}",
                    p0=axis,
                    p1=resp.get("message"),
                )
            )
        if progress is not None:
            progress(tr("In phase optimisation: {p0} Replica preview completed", p0=axis))
        ax = _axis_index(axis, experiment.ndim)
        arr = _load_preview_with_memory_guard(
            str(resp["spectrum_path"]),
            axis=axis,
            unpack_axis=ax,
            progress=progress,
            logs=logs,
        )
        _unlink_quiet(resp["spectrum_path"])
        est = search_axis_memory(arr, ax, sign_mode=sign_mode, cancel=cancel_requested)
        if est is None:
            raise RuntimeError(tr("memory phase search({p0}) No trace available", p0=axis))
        if sign_mode == "mixed":
            resolved = _disambiguate_180_mixed(arr, ax, est.phase, experiment, axis)
            if resolved != est.phase:
                logs.append(
                    tr(
                        "{p0}: +/-180° chemical shift partitioning disambiguation {p1} → {p2}",
                        p0=axis,
                        p1=f"({format_phase_degrees(est.phase[0])}°, "
                        f"{format_phase_degrees(est.phase[1])}°)",
                        p2=f"({format_phase_degrees(resolved[0])}°, "
                        f"{format_phase_degrees(resolved[1])}°)",
                    )
                )
            phase = resolved
        else:
            phase = est.phase
        fixed[axis] = phase
        logs += est.logs
        logs.append(
            tr(
                "{p0}: memory phase = ({p1:g}°, {p2:g}°) score={p3:.2f}",
                p0=axis,
                p1=phase_angle_value(phase[0]),
                p2=phase_angle_value(phase[1]),
                p3=est.score,
            )
        )
        logs.append(
            tr(
                "{p0} Phase search is completed, time-consuming {p1:.1f} Second",
                p0=axis,
                p1=time.time() - t_axis,
            )
        )
    # 0.2.199-patch29fj (user): skip the joint review -- neither the history nor the
    # measurements (900/102/101) crossed the 0.05 gate to correct the phase order
    # (patch29dn fixed sampleI through zero filling plus a re-search once the direct
    # dimension was settled, not through the review); after the direct dimension is
    # settled the r2 re-search already covers it. The joint_recheck_memory code and
    # its test are kept (not called, for reference / restoring later if needed).
    # 0.2.199-patch29dr (user): once the direct dimension is settled the indirect
    # dimensions are optimised for one more round (preview with the direct-dimension
    # phase fixed; this axis' own phase is excluded, unified/memory semantics,
    # sampleI F1 returns to the coarse-grid optimum of 90 deg)
    indirect_axes = [a for a in axes if a != direct_axis]
    if len(search_axes) >= 2 and direct_axis in fixed:
        for axis in indirect_axes:
            out_file = f"{experiment.dataset_id}_preview_{axis}_r2.{ext}"
            t_axis = time.time()
            if progress is not None:
                progress(
                    tr(
                        "In phase optimisation: {p0} Replica preview (direct dimension has been "
                        "determined)",
                        p0=axis,
                    )
                )
            resp = backend.process(
                experiment,
                plan,
                direct_phase_override={k: v for k, v in fixed.items() if k != axis},
                params={**params, **zf_phase, "preview_axis": axis},
                out_file=out_file,
                script_name=f"{experiment.dataset_id}_preview_{axis}_r2.com",
                progress=progress,
            )
            backend_runs += 1
            if not resp.get("success") or not resp.get("spectrum_path"):
                raise RuntimeError(
                    tr(
                        "Replica preview search ({p0}) failed: {p1}",
                        p0=axis,
                        p1=resp.get("message"),
                    )
                )
            ax = _axis_index(axis, experiment.ndim)
            arr = _load_preview_with_memory_guard(
                str(resp["spectrum_path"]),
                axis=axis,
                unpack_axis=ax,
                progress=progress,
                logs=logs,
            )
            _unlink_quiet(resp["spectrum_path"])
            est = search_axis_memory(arr, ax, sign_mode=sign_mode, cancel=cancel_requested)
            if est is None:
                raise RuntimeError(tr("Memory phase research({p0}) No trace available", p0=axis))
            if sign_mode == "mixed":
                resolved = _disambiguate_180_mixed(arr, ax, est.phase, experiment, axis)
                phase = resolved
            else:
                phase = est.phase
            fixed[axis] = phase
            logs += est.logs
            logs.append(
                tr(
                    "{p0}: in-memory phase re-search (direct dimension fixed) = ({p1:g}°, {p2:g}°) "
                    "score={p3:.2f}",
                    p0=axis,
                    p1=phase_angle_value(phase[0]),
                    p2=phase_angle_value(phase[1]),
                    p3=est.score,
                )
            )
            logs.append(
                tr(
                    "{p0} Phase re-search is completed, time-consuming {p1:.1f} Second",
                    p0=axis,
                    p1=time.time() - t_axis,
                )
            )
    # 0.2.166: with auto_phase=False the direct dimension took no part in the
    # search, so keep (0,0)
    fixed.setdefault(direct_axis, (0.0, 0.0))
    # 0.2.163-patch6: processing-parameter optimisation (baseline / direct-dimension
    # window / zero fill + indirect windows), symmetric with NUS; uniform has no
    # reconstruction, so re-running the full process per candidate is faster
    if progress is not None:
        progress(
            tr(
                "The phase search is completed and processing of parameter optimisation "
                "(baseline/zero "
                "filling/window function) "
                "begins",
            )
        )
    t_opt = time.time()
    proc = _optimize_uniform_processing(
        experiment,
        backend,
        work,
        fixed,
        params,
        plan=plan,
        progress=progress,
    )
    logs += proc["logs"]
    logs.append(
        tr(
            "Processing parameter optimisation (baseline/zero filling/window function) is "
            "completed, time-consuming {p0:.1f} "
            "Second",
            p0=time.time() - t_opt,
        )
    )
    # 0.2.166: uniform retains the complete script before optimisation (joint script, including the
    # final phase and automatic zero filling, but does not include the optimisation baseline/window)
    # as {dataset_id}_before_optimize.com, which is symmetrical with the NUS initial run script to
    # facilitate comparison of the scripts before and after optimisation.
    joint_script = work / f"{experiment.dataset_id}_joint.com"
    no_opt_script = work / f"{experiment.dataset_id}_before_optimize.com"
    try:
        if joint_script.is_file():
            no_opt_script.write_text(
                joint_script.read_text(encoding="utf-8"),
                encoding="utf-8",
                newline="\n",
            )
            logs.append(tr("first-run script kept as: {p0}", p0=no_opt_script.name))
    except OSError as exc:
        logs.append(tr("Initial script retention failed: {p0}", p0=exc))
    if progress is not None:
        progress(tr("Final run (complete rerun) in progress"))
    t_final = time.time()
    params_final = _apply_final_ext(dict(params), final_ext_lo, final_ext_hi)
    # 0.2.162-patch16: when the final-run direct-dimension range changes, the
    # direct-dimension p1 is renormalised by the window-width ratio
    fixed_final = dict(fixed)
    if direct_axis in fixed_final:
        renormed = _renormalize_direct_p1(fixed[direct_axis], params, params_final)
        if renormed != fixed[direct_axis]:
            logs.append(
                tr(
                    "direct dimension phase renormalized by final run window: {p0} p1={p1:g}° → "
                    "{p2:g}°",
                    p0=direct_axis,
                    p1=phase_angle_value(fixed[direct_axis][1]),
                    p2=phase_angle_value(renormed[1]),
                )
            )
        fixed_final[direct_axis] = renormed
    params_final.update(
        {
            "direct_phase_search": False,
            "display_phase_search": False,
            "direct_poly_time": bool(diagnostics.get("apply_poly_time")),
            "baseline": proc["baseline"],
            "zero_fill": proc["zero_fill"],
            "window": proc["window"],
        }
    )
    resp = backend.process(
        experiment,
        plan,
        direct_phase_override=fixed_final,
        params=params_final,
        progress=progress,
    )
    backend_runs += 1
    if not resp.get("success") or not resp.get("spectrum_path"):
        raise RuntimeError(tr("Final run failed: {p0}", p0=resp.get("message")))
    if progress is not None:
        progress(tr("Final run completed"))
    logs += list(resp.get("logs", []))
    logs.append(
        tr(
            "The final run is completed and takes time {p0:.1f} Second",
            p0=time.time() - t_final,
        )
    )
    _append_final_summary(
        logs,
        str(resp["spectrum_path"]),
        direct_axis=direct_axis,
        direct_phase=fixed_final.get(direct_axis),
        phases=fixed_final,
        backend_runs=backend_runs,
        baseline=proc["baseline"],
        baseline_scores=proc.get("baseline_scores"),
        zero_fill=proc["zero_fill"],
        window=proc["window"],
        optimization_logs=proc["logs"],
        peak_sign=_template_peak_sign(experiment),
        progress=progress,
    )
    _cleanup_unified_intermediates(
        work, experiment.dataset_id, experiment=experiment, backend=backend
    )
    return {
        "phases": fixed_final,
        "spectrum_path": str(resp["spectrum_path"]),
        "backend_runs": backend_runs,
        "logs": logs,
        "direct_phase": fixed_final.get(direct_axis),
        "baseline": proc["baseline"],
        "zero_fill": proc["zero_fill"],
        "window": proc["window"],
        "diagnostics": diagnostics,
    }


_DIRECT_PHASE_FP_KEYS = (
    "extract",
    "ext_lo",
    "ext_hi",
    "nsigma",
    "thresh",
    "smile_xq3",
    "smile_scaling",
    "zero_fill",
    "linewidth_hz",
    "points_per_line",
    "segment_shift_hz",
    "sampling",
    "window",  # 0.2.166: the direct-dim window enters the SMILE step1 recon plane
    "direct_poly_time",  # 0.2.160: the first pass drops POLY -time; the search uses the raw plane
)


def _direct_phase_params_fp(experiment: Experiment, params: dict) -> str:
    """Direct-dimension phase cache fingerprint: the parameters that affect the
    reconstruction plane plus the dataset identity.
    """
    import hashlib
    import json as _json

    payload = {
        "dataset_id": experiment.dataset_id,
        "ndim": experiment.ndim,
        "segments": [str(s) for s in (experiment.segments or [])],
        "params": {k: params.get(k) for k in _DIRECT_PHASE_FP_KEYS},
    }
    return hashlib.sha256(
        _json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _direct_phase_cache_path(work: Path) -> Path:
    return work / "phase.json"


def _estimate_direct_phase_seconds(work: Path) -> float | None:
    """Read the last unified_direct phase search time (used for progress estimation)."""
    import json as _json

    path = _direct_phase_cache_path(work)
    if not path.is_file():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if data.get("source") != "unified_direct":
        return None
    dur = data.get("duration_s")
    return float(dur) if dur else None


def _load_direct_phase_cache(
    work: Path, experiment: Experiment, params: dict, shape
) -> dict | None:
    """Reuse phase.json cache when parameter and spectrum remain unchanged (skipping repeated
    searches).
    """
    import json as _json

    path = _direct_phase_cache_path(work)
    if not path.is_file():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if data.get("version") != 2 or data.get("source") != "unified_direct":
        return None
    if list(data.get("shape") or []) != list(shape):
        return None
    if data.get("params_fp") != _direct_phase_params_fp(experiment, params):
        return None
    return data


def _save_direct_phase_cache(
    work: Path,
    experiment: Experiment,
    params: dict,
    shape,
    p0: float,
    p1: float,
    score: float,
    duration_s: float,
) -> None:
    import json as _json

    try:
        atomic_write_text(
            _direct_phase_cache_path(work),
            _json.dumps(
                {
                    "version": 2,
                    "source": "unified_direct",
                    "p0": p0,
                    "p1": p1,
                    "score": score,
                    "shape": list(shape),
                    "params_fp": _direct_phase_params_fp(experiment, params),
                    "duration_s": round(float(duration_s), 2),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except OSError:
        pass


def _load_recon_planes(experiment: Experiment, work: Path) -> np.ndarray:
    """Read the 2D SMILE reconstruction plane recon.ft1 (layout (F2 frequency,
    F1 time), direct dimension on axis 0).

    0.2.199-patch29: only 2D uses this (as the base for the direct-dimension phase
    search); the 3D direct-dimension search moved to the keep_complex complex final
    spectrum (patch18) and no longer loads 3D planes -- the old code loaded every
    test*.ft1 unconditionally and unpacked them with the naive axis-0 interleaved
    step, which unpacked real SMILE output (13C axis stored hypercomplex 4 x 75)
    wrongly and wasted hundreds of MB of memory for nothing.
    """
    if experiment.ndim >= 3:
        # 3D is not loaded (the direct-dimension search uses the keep_complex final
        # spectrum), keeping the 2D semantics compatible
        return np.zeros((0, 0, 0))
    recon = work / "nus2d" / "recon.ft1"
    if not recon.is_file():
        raise RuntimeError(tr("Missing 2D reconstruction plane: {p0}", p0=recon))
    return _read_complex_preview(recon)


def _chosen_baseline_scores(
    baseline_cfg: dict[str, dict[str, Any]],
    scores: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Score of the configuration selected by each axis' baseline optimisation
    (0.2.199-patch29z).

    The baseline score in the final spectrum-quality report matches the one from
    the optimisation: the score of the selected configuration (mode/order) is
    taken straight from the optimisation grid instead of evaluating the final
    spectrum separately (two different benchmarks produced the confusing
    "66.8 at the optimisation, 50 in the report").
    """
    out: dict[str, float] = {}
    for axis, cfg in baseline_cfg.items():
        mode = str(cfg.get("mode", "auto"))
        order = int(cfg.get("order", 0) or 0)
        axis_scores = scores.get(axis) or {}
        if mode == "off":
            key = "off:0"
        elif mode == "order":
            key = f"order:{max(order, 1)}"
        else:
            key = "auto:1"
        val = axis_scores.get(key)
        out[axis] = float(val) if val is not None else 0.0
    return out


def _optimize_uniform_processing(
    experiment: Experiment,
    backend: Any,
    work: Path,
    fixed: dict[str, tuple[float, float]],
    base_params: dict[str, Any] | None,
    plan: Any = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Processing-parameter optimisation after the per-axis phase search (uniform
    2D/3D): baseline (in-memory score) + direct-dimension window function (FID
    in-memory score) + indirect-dimension window functions (FID in-memory score,
    the candidates include no window).

    Ordering constraint (user request, 0.2.190): baseline correction comes before
    window optimisation -- 2) baseline -> 2.5) direct-dimension window ->
    3) indirect-dimension windows; the window candidates
    must not feed back into the baseline choice (in 0.2.189 the spectrum_quality
    phase/baseline coupling pushed the indirect dimensions away from no window).
    uniform has no SMILE reconstruction and any evaluation failure degrades
    gracefully: the existing base_params configuration or the default is kept and
    the final run is not blocked.
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    zf_params = {a: {"mode": "auto"} for a in axes}
    base = dict(base_params or {})
    out_logs: list[str] = []
    baseline_cfg = dict(base.get("baseline") or {})
    window_cfg = base.get("window")
    opt_switches = reference_optimize_switches(base)
    # 1) Joint review spectrum: final phase + full zero filling, used as the
    #    baseline scoring basis (one process run); the window functions use FID
    #    in-memory scoring and do not consume it (0.2.199-patch29fg removed the 2.1
    #    re-render); with "apply this range to the optimisation" on
    #    (0.2.199-patch3) the evaluation spectrum uses the user's direct-dimension
    #    range
    opt_ext = {k: base[k] for k in ("ext_lo", "ext_hi") if k in base}
    joint_file = f"{experiment.dataset_id}_joint.{ext}"
    resp = backend.process(
        experiment,
        plan,
        direct_phase_override=dict(fixed) if fixed else None,
        params={**_sign_sampling(base), "zero_fill": zf_params, **opt_ext},
        out_file=joint_file,
        script_name=f"{experiment.dataset_id}_joint.com",
        progress=progress,
    )
    if not resp.get("success") or not resp.get("spectrum_path"):
        out_logs.append(
            tr("Handling parameter optimisation: joint review spectrum generation failed (")
            + str(resp.get("message"))
            + tr("), maintain existing baseline/zero filling/window configuration")
        )
        return {
            "baseline": baseline_cfg,
            "zero_fill": zf_params,
            "window": window_cfg,
            "logs": out_logs,
        }
    base_path = Path(resp["spectrum_path"])
    # 2) baseline: in-memory scoring per axis (off/auto/order1-3), written back to
    #    the final run; params.reference_optimize.baseline can switch it off or
    #    restrict the candidates
    #    (**for tests/reproduction/audit only; real experiments keep auto**)
    opt = None
    baseline_switch = opt_switches.get("baseline", "auto")
    if baseline_switch in (False, "off"):
        out_logs.append(
            tr(
                "baseline optimisation: switched off externally "
                "(params.reference_optimize.baseline=off), reusing the baseline configuration "
                "supplied by the caller (tests / reproduction "
                "only)",
            )
        )
    else:
        try:
            from workflow.baseline_optimize import optimize_baseline

            if progress is not None:
                progress(tr("Baseline optimisation(memory score)"))
            opt = optimize_baseline(
                experiment,
                base_path,
                grid=baseline_grid_from_switch(baseline_switch),
                progress=progress,
                cancel=cancel_requested,
            )
            baseline_cfg = dict(opt.baseline)
            out_logs += opt.logs
        except Exception as exc:  # noqa: BLE001 - a failed baseline run is harmless
            out_logs.append(tr("Baseline optimisation (embedding) failed: {p0}", p0=exc))
    # 0.2.199-patch29fk-fix: the joint/base spectrum is only the baseline scoring
    # basis; it is deleted once scored
    _unlink_quiet(base_path)
    # 2.5) direct-dimension window function: FID direct-dimension trace in-memory
    #      score (no process re-run), written back to the final run;
    #      params.reference_optimize.window can switch it off or restrict the
    #      candidates (**for tests/reproduction/audit only; real experiments keep
    #      the default automatic optimisation**)
    window_switch = opt_switches.get("window", "auto")
    direct_candidates, indirect_candidates = window_candidates_from_switch(window_switch)
    if window_switch in (False, "off"):
        out_logs.append(
            tr(
                "window optimisation: switched off externally "
                "(params.reference_optimize.window=off), reusing the window configuration supplied "
                "by the caller (tests / reproduction "
                "only)",
            )
        )
    else:
        try:
            from workflow.window_optimize import optimize_direct_window_from_work

            if progress is not None:
                progress(tr("direct dimension window function optimisation(FID memory score)"))
            win_kwargs: dict[str, Any] = {"current": (window_cfg or {}).get(direct_axis)}
            if direct_candidates:
                win_kwargs["candidates"] = direct_candidates
            wres = optimize_direct_window_from_work(work, experiment, **win_kwargs)
            if wres.changed:
                win = dict(window_cfg or {})
                win[direct_axis] = wres.choice
                window_cfg = win
            out_logs += wres.logs
        except Exception as exc:  # noqa: BLE001 - a failed window run is harmless
            out_logs.append(tr("direct dimension window optimisation failed: {p0}", p0=exc))
    # 3) indirect-dimension window functions: FID indirect-dimension time-axis
    #    in-memory score (candidates include no window; a resolution-limited
    #    indirect dimension adds a resolution-retention factor), written back per
    #    axis; the direct-dimension window was already optimised separately in 2.5.
    #    0.2.190: restore the real window selection (the hard-coded windowless
    #    choice of 0.2.189 was a misreading of the requirement).
    if window_switch not in (False, "off"):
        try:
            from workflow.window_optimize import (
                optimize_indirect_windows_from_work,
            )

            if progress is not None:
                progress(
                    tr(
                        "Indirect dimension window function optimisation (FID memory score, do not "
                        "rerun the "
                        "process)",
                    )
                )
            win_kwargs = {"current": window_cfg}
            if indirect_candidates:
                win_kwargs["candidates"] = indirect_candidates
            ires = optimize_indirect_windows_from_work(work, experiment, **win_kwargs)
            if ires.changed:
                win = dict(window_cfg or {})
                win.update(ires.choice)
                window_cfg = win
            out_logs += ires.logs
        except Exception as exc:  # noqa: BLE001 - a failed window run is harmless
            out_logs.append(tr("indirect dimension window optimisation failed: {p0}", p0=exc))

    return {
        "baseline": baseline_cfg,
        # 0.2.199-patch29z: score of each axis' selected baseline configuration --
        # the report's baseline score matches the optimisation (no separate
        # evaluation of the final spectrum producing two sets of numbers)
        "baseline_scores": _chosen_baseline_scores(
            baseline_cfg, opt.scores if opt is not None else {}
        ),
        "zero_fill": zf_params,
        "window": window_cfg,
        "logs": out_logs,
    }


def _optimize_nus_processing(
    experiment: Experiment,
    backend: Any,
    work: Path,
    fixed: dict[str, tuple[float, float]],
    base_params: dict[str, Any] | None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Processing-parameter optimisation after the per-axis phase search (NUS):
    baseline (in-memory score) + direct-dimension window (FID in-memory score) +
    indirect-dimension window functions (reconstruction-plane in-memory score,
    candidates include no window, SMILE is not re-run).

    Ordering constraint (user request, 0.2.190): baseline correction comes before
    window optimisation -- 2) baseline -> 2.5) direct-dimension window ->
    3) indirect-dimension windows. Returns
    {"baseline", "zero_fill", "window", "logs"}; the optimisation result is written
    back into the final-run full script, and the direct-dimension baseline is still
    scored per axis and written back (applied by the final run's step1 POLY). Any
    evaluation failure degrades gracefully: the existing base_params configuration
    or the default is kept and the final run is not blocked.
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    zf_params = {a: {"mode": "auto"} for a in axes}
    base = dict(base_params or {})
    out_logs: list[str] = []
    baseline_cfg = dict(base.get("baseline") or {})
    window_cfg = base.get("window")
    opt_switches = reference_optimize_switches(base)
    # 1) Joint review spectrum: indirect-dimension final phase + full zero filling,
    #    used as the baseline scoring basis (the window functions use recon/FID
    #    in-memory scoring and do not consume it; 0.2.199-patch29fg removed the 2.1
    #    re-render)
    joint_file = f"{experiment.dataset_id}_joint.{ext}"
    resp = backend.finalize_nus(
        experiment,
        phases=fixed,
        work_dir=work,
        params={**_sign_sampling(base), "zero_fill": zf_params},
        out_file=joint_file,
        script_name=f"{experiment.dataset_id}_joint_finalize.com",
        progress=progress,
    )
    if not resp.get("success") or not resp.get("spectrum_path"):
        out_logs.append(
            tr("Handling parameter optimisation: joint review spectrum generation failed (")
            + str(resp.get("message"))
            + tr("), maintain existing baseline/zero filling/window configuration")
        )
        return {
            "baseline": baseline_cfg,
            "zero_fill": zf_params,
            "window": window_cfg,
            "logs": out_logs,
        }
    base_path = Path(resp["spectrum_path"])
    # 2) baseline: in-memory scoring per axis (off/auto/order1-3), written back to
    #    the final-run full script; params.reference_optimize.baseline can switch it
    #    off or restrict the candidates
    #    (**for tests/reproduction/audit only; real experiments keep auto**)
    opt = None
    baseline_switch = opt_switches.get("baseline", "auto")
    if baseline_switch in (False, "off"):
        out_logs.append(
            tr(
                "baseline optimisation: switched off externally "
                "(params.reference_optimize.baseline=off), reusing the baseline configuration "
                "supplied by the caller (tests / reproduction "
                "only)",
            )
        )
    else:
        try:
            from workflow.baseline_optimize import optimize_baseline

            if progress is not None:
                progress(tr("Baseline optimisation(memory score)"))
            opt = optimize_baseline(
                experiment,
                base_path,
                grid=baseline_grid_from_switch(baseline_switch),
                progress=progress,
                cancel=cancel_requested,
            )
            baseline_cfg = dict(opt.baseline)
            out_logs += opt.logs
        except Exception as exc:  # noqa: BLE001 - a failed baseline run is harmless
            out_logs.append(tr("Baseline optimisation (embedding) failed: {p0}", p0=exc))
    # 0.2.199-patch29fk-fix: the joint/base spectrum is only the baseline scoring
    # basis; it is deleted once scored
    _unlink_quiet(base_path)
    # 2.5) direct-dimension window function: FID direct-dimension trace in-memory
    #      score (no SMILE reconstruction re-run), resolution first plus an
    #      SNR/lineshape balance, written back to the final run's step1 SP;
    #      params.reference_optimize.window can switch it off or restrict the
    #      candidates (**for tests/reproduction/audit only; real experiments keep
    #      the default automatic optimisation**)
    window_switch = opt_switches.get("window", "auto")
    direct_candidates, indirect_candidates = window_candidates_from_switch(window_switch)
    if window_switch in (False, "off"):
        out_logs.append(
            tr(
                "window optimisation: switched off externally "
                "(params.reference_optimize.window=off), reusing the window configuration supplied "
                "by the caller (tests / reproduction "
                "only)",
            )
        )
    else:
        try:
            from workflow.window_optimize import (
                optimize_direct_window_from_work,
            )

            if progress is not None:
                progress(
                    tr(
                        "direct dimension window function optimisation (FID memory score, no rerun "
                        "SMILE)",
                    )
                )
            win_kwargs: dict[str, Any] = {"current": (window_cfg or {}).get(direct_axis)}
            if direct_candidates:
                win_kwargs["candidates"] = direct_candidates
            wres = optimize_direct_window_from_work(work, experiment, **win_kwargs)
            out_logs += wres.logs
            if wres.changed:
                # 0.2.199-patch11: the NUS direct-dimension window is fixed at SP
                # (SMILE requires a direct-dimension window with tail attenuation),
                # so the window candidates do not cover the direct dimension; the
                # result is used by the uniform path only
                out_logs.append(
                    tr(
                        "direct-dimension window: NUS SMILE requires a direct-dimension window "
                        "(SP) and the candidates do not cover the direct dimension, keeping the "
                        "default "
                        "SP",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - a failed window run is harmless
            out_logs.append(tr("direct dimension window optimisation failed: {p0}", p0=exc))
    # 3) indirect-dimension window functions: SMILE reconstruction plane (F1/F2
    #    time domain) in-memory score, candidates include no window, SMILE is not
    #    re-run; the direct-dimension window was already optimised in 2.5.
    #    0.2.190: restore the real window selection (the hard-coded windowless
    #    choice of 0.2.189 was a misreading of the requirement).
    if window_switch not in (False, "off"):
        try:
            from workflow.window_optimize import (
                optimize_indirect_windows_from_recon,
            )

            if progress is not None:
                progress(
                    tr(
                        "indirect dimension window function optimisation (reconstruct plane memory "
                        "score, do not rerun "
                        "SMILE)",
                    )
                )
            win_kwargs = {"current": window_cfg}
            if indirect_candidates:
                win_kwargs["candidates"] = indirect_candidates
            ires = optimize_indirect_windows_from_recon(work, experiment, **win_kwargs)
            if ires.changed:
                win = dict(window_cfg or {})
                win.update(ires.choice)
                window_cfg = win
            out_logs += ires.logs
        except Exception as exc:  # noqa: BLE001 - a failed window run is harmless
            out_logs.append(tr("indirect dimension window optimisation failed: {p0}", p0=exc))

    return {
        "baseline": baseline_cfg,
        # 0.2.199-patch29z: score of each axis' selected baseline configuration --
        # the report's baseline score matches the optimisation (no separate
        # evaluation of the final spectrum producing two sets of numbers)
        "baseline_scores": _chosen_baseline_scores(
            baseline_cfg, opt.scores if opt is not None else {}
        ),
        "zero_fill": zf_params,
        "window": window_cfg,
        "logs": out_logs,
    }


def _unified_nus(
    experiment: Experiment,
    backend: Any,
    *,
    plan: Any,
    work_dir: Path | str | None,
    base_params: dict[str, Any] | None,
    progress: Callable[[str], None] | None,
) -> dict[str, Any]:
    """NUS unified flow: one SMILE pass (direct-dimension PS at (0,0)) -> the direct
    dimension is searched in memory on the recon complex planes -> the indirect
    dimensions replay the finalize chain in memory for a full per-dimension search
    (the joint review is skipped since 0.2.199-patch29fj) -> processing-parameter
    optimisation (baseline/zero fill/window) -> final run: the final phase of every
    dimension is filled into the initial script together with the optimised
    processing parameters to produce a new full script (the direct-dimension phase
    goes into the step1 PS after EXT and the indirect-dimension phases into the
    step3 PS), and no rotated nus3d_rc_ph copy is written.
    """
    from core.data.internal_data_model import AxisRole
    from workflow.memory_phase_search import search_axis_memory

    work = Path(work_dir) if work_dir else backend._work_path(experiment)
    # 2026-09-23 (user request): the diagnosis already ran at the end of the Generate-FID step
    # and landed in process/diagnostics.json (DC offset -> POLY -time, bad-point replacement
    # results included); here it is only read back and reused -- the spectrum step no longer
    # re-runs or announces it. When the record is missing or does not match the current fid the
    # helper falls back to a real run, so old working directories and the manual route behave as
    # before.
    diagnostics: dict[str, Any] = {}
    diag_logs: list[str] = []
    try:
        from workflow.direct_diagnostics import load_or_run_direct_diagnostics

        diag_result = load_or_run_direct_diagnostics(work, experiment, progress=progress)
        diagnostics = {
            "reports": list(diag_result.reports),
            "metrics": dict(diag_result.metrics),
            "apply_poly_time": diag_result.apply_poly_time,
            "repaired_badpoints": diag_result.repaired_badpoints,
            "backup_dir": diag_result.backup_dir,
            "clean": bool(getattr(diag_result, "clean", False)),
        }
    except Exception as exc:  # noqa: BLE001 - a failed diagnostic must not block
        diag_logs = [tr("data quality diagnosis failed: {p0}", p0=exc)]
    params_first = dict(base_params or {})
    # 0.2.162-patch15: the final-run direct-dimension range
    # (final_ext_lo/final_ext_hi) only enters the final run by default;
    # 0.2.199-patch3: with "apply this range to the optimisation" on it also enters
    # the first-pass reconstruction / phase search and the optimisation evaluation
    # (the reconstruction-plane window is the evaluation window, and a narrower
    # direct-dimension window lowers SMILE memory)
    params_first, final_ext_lo, final_ext_hi, apply_ext_opt = _split_final_ext(params_first)
    if apply_ext_opt:
        params_first = _apply_final_ext(params_first, final_ext_lo, final_ext_hi)
    params_first.update(
        {
            "direct_phase_search": False,
            "display_phase_search": False,
            # 0.2.160: the first script does not add POLY -time -- the direct-dimension
            # phase search uses the original recon plane as input (POLY changes the
            # symmetry score and once biased sampleC's direct-dimension phase from
            # (0,0) to (40,-15) with a lower score); POLY -time only enters the
            # final-run full script (params_final's direct_poly_time).
        }
    )
    first = backend.reconstruct_nus(experiment, params_first, progress=progress)
    if not first.get("success") or not first.get("spectrum_path"):
        raise RuntimeError(
            tr(
                "The first pass SMILE reconstruction failed: {p0}",
                p0=first.get("message"),
            )
        )
    logs: list[str] = list(diag_logs) + [
        tr("The first pass SMILE reconstruction is completed: {p0}", p0=first.get("spectrum_path"))
    ]
    if progress is not None:
        progress(tr("First pass SMILE completed"))
    # 0.2.156: the initial run script is kept (the complete SMILE script without the
    # optimisation phase) and named {dataset_id}_before_optimize.com, to facilitate
    # comparison with the optimised final-run script.
    first_script = work / f"{experiment.dataset_id}_nus.com"
    no_opt_script = work / f"{experiment.dataset_id}_before_optimize.com"
    try:
        if first_script.is_file():
            no_opt_script.write_text(
                first_script.read_text(encoding="utf-8"),
                encoding="utf-8",
                newline="\n",
            )
            logs.append(tr("first-run script kept as: {p0}", p0=no_opt_script.name))
    except OSError as exc:  # noqa: BLE001 - failure to keep it does not affect the flow
        logs.append(tr("Initial script retention failed: {p0}", p0=exc))
    backend_runs = 1
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    sign_mode = _sign_mode(experiment)
    indirect_axes = [
        dim.logical_axis for dim in experiment.dimensions if dim.role is not AxisRole.DIRECT
    ]
    # 0.2.167: magnitude spectra (HMBC and the like) skip the indirect-dimension
    # search too -- the finalize complex preview is skipped and the phase stays
    # (0,0) (no phase node in the plan, so the search is pointless and pollutes the
    # log)
    auto_phase = _template_auto_phase(experiment)
    if not auto_phase:
        indirect_axes = []
        logs.append(
            tr(
                "indirect dimension: the amplitude spectrum does not automatically adjust the "
                "phase, skip the finalize replica preview and "
                "search",
            )
        )
    # Indirect dimensions: the finalize complex preview (this axis' PS omits -di,
    # the other axes add -di with their fixed phases, no zero fill) provides the
    # base, then a full in-memory per-dimension search -- the FT/-alt/ZTP
    # conventions are guaranteed by the real backend
    fixed: dict[str, tuple[float, float]] = {}
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    # 0.2.199-patch29dn (option A, user): zero filling is settled first -- the
    # indirect-dimension phase-search preview uses the same full auto zero filling
    # as the final run, avoiding the mismatch between the best score and the final
    # spectrum at low resolution;
    # 0.2.199-patch29dt: the NUS direct dimension is searched on the finalize real
    # final spectrum (direct dimension = plane index, no FT and no zero fill), so
    # the preview zero fill only acts on the indirect dimensions and NUS keeps full
    # auto
    zf_phase = {
        "zero_fill": {
            a: {"mode": "auto"} for a in (dim.logical_axis for dim in experiment.dimensions)
        }
    }
    for axis in indirect_axes:
        out_file = f"{experiment.dataset_id}_preview_{axis}.{ext}"
        t_axis = time.time()
        if progress is not None:
            progress(tr("In phase optimisation: {p0} Replica preview in progress", p0=axis))
        resp = backend.finalize_nus(
            experiment,
            phases=fixed,
            work_dir=work,
            params={
                **_sign_sampling(base_params),
                **zf_phase,
                "preview_axis": axis,
            },
            out_file=out_file,
            script_name=f"{experiment.dataset_id}_preview_{axis}_finalize.com",
            progress=progress,
        )
        backend_runs += 1
        if not resp.get("success") or not resp.get("spectrum_path"):
            raise RuntimeError(
                tr(
                    "NUS Replica preview ({p0}) failed: {p1}",
                    p0=axis,
                    p1=resp.get("message"),
                )
            )
        if progress is not None:
            progress(tr("In phase optimisation: {p0} Replica preview completed", p0=axis))
        ax = _axis_index(axis, experiment.ndim)
        arr = _load_preview_with_memory_guard(
            str(resp["spectrum_path"]),
            axis=axis,
            unpack_axis=ax,
            progress=progress,
            logs=logs,
        )
        _unlink_quiet(resp["spectrum_path"])
        est = search_axis_memory(arr, ax, sign_mode=sign_mode, cancel=cancel_requested)
        if est is None:
            raise RuntimeError(tr("memory phase search({p0}) No trace available", p0=axis))
        if sign_mode == "mixed":
            resolved = _disambiguate_180_mixed(arr, ax, est.phase, experiment, axis)
            if resolved != est.phase:
                logs.append(
                    tr(
                        "{p0}: +/-180° chemical shift partitioning disambiguation {p1} → {p2}",
                        p0=axis,
                        p1=f"({format_phase_degrees(est.phase[0])}°, "
                        f"{format_phase_degrees(est.phase[1])}°)",
                        p2=f"({format_phase_degrees(resolved[0])}°, "
                        f"{format_phase_degrees(resolved[1])}°)",
                    )
                )
            fixed[axis] = resolved
        else:
            fixed[axis] = est.phase
        logs += est.logs
        logs.append(
            tr(
                "{p0}: memory phase = ({p1:g}°, {p2:g}°) score={p3:.2f}",
                p0=axis,
                p1=phase_angle_value(est.phase[0]),
                p2=phase_angle_value(est.phase[1]),
                p3=est.score,
            )
        )
        logs.append(
            tr(
                "{p0} Phase search is completed, time-consuming {p1:.1f} Second",
                p0=axis,
                p1=time.time() - t_axis,
            )
        )
    # 0.2.199-patch29fj (user): skip the joint review (same as uniform, the code is
    # kept).
    # Direct dimension: 0.2.199-patch29l moved the search onto the "purely real final
    # spectrum + projection traces + HT".
    # The indirect dimensions are already corrected by the search above
    # (phases=fixed), so a real (real-valued) finalize final spectrum is generated
    # (with two same-named 15N indirect nuclei such as in HNN the header labels
    # repeat and proj3D cannot pick an axis by label, so the numpy projection path
    # must be used, choosing the plane by direct-dimension axis role/size)
    # -- that is the final spectrum the user sees while phase-tuning by hand (the
    # direct dimension is still uncorrected); equivalent to proj3D.tcl -sum over the
    # two planes that contain the direct dimension (XZ/YZ, summing the indirect
    # dimension), extracting the direct-dimension projection traces, Hilbert-filling
    # the imaginary part of each real trace (nmrPipe sign convention: Im=-H_scipy)
    # and taking the statistically best phase
    # (0.2.199-patch29l/patch29m: trace by trace plus consensus; ringing sidelobe
    # filtering plus gentle p1 protection).
    # Keep (0,0) when score<30.
    import time as _time

    from core.optimization.phase_consensus import search_direct_phase_real_ht

    if not auto_phase:
        direct_phase = (0.0, 0.0)
        logs.append(
            tr(
                "experiment type {p0}: magnitude spectra are not phase-optimised, the "
                "direct-dimension phase stays at (0,0) (the search is "
                "skipped)",
                p0=experiment.experiment_type.name,
            )
        )
    else:
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        preview_out = f"{experiment.dataset_id}_direct_final.{ext}"
        # 0.2.199-patch29fj-fix: the direct-dimension real preview renders for a few
        # seconds with no progress before it (the whole stretch is silent on a cache
        # hit), so progress is added to match the indirect-dimension complex preview
        # and avoid the impression of a swallowed log
        if progress is not None:
            progress(tr("phase optimisation: direct dimension real data final spectrum preview"))
        resp_direct = backend.finalize_nus(
            experiment,
            phases=fixed,
            work_dir=work,
            # 0.2.199-patch29dn (option A, user): the direct-dimension search preview
            # also uses the same full auto zero filling as the final run -- phase
            # search and final spectrum share the same resolution, avoiding a
            # mismatch between the best score and the final spectrum at low
            # resolution (it used the minimum zero filling before: direct dimension
            # 2048 vs 4096 in the final run)
            params={**params_first, **zf_phase},
            out_file=preview_out,
            script_name=(f"{experiment.dataset_id}_direct_final_finalize.com"),
            progress=progress,
        )
        backend_runs += 1
        if not resp_direct.get("success") or not resp_direct.get("spectrum_path"):
            raise RuntimeError(
                tr(
                    "direct-dimension real-data final spectrum preview failed: {p0}",
                    p0=resp_direct.get("message"),
                )
            )
        search_arr = _read_real_ft3(str(resp_direct["spectrum_path"]))
        _unlink_quiet(resp_direct["spectrum_path"])
        logs.append(
            tr(
                "direct dimension phase search basis: real data final spectrum {p0}(indirect "
                "dimension corrected, direct dimension = last axis, projection trace = sum of "
                "indirect dimension "
                "points)",
                p0=search_arr.shape,
            )
        )
        if progress is not None:
            progress(
                tr(
                    "phase optimisation: direct dimension real data final spectrum preview "
                    "completed",
                )
            )
        cache = _load_direct_phase_cache(work, experiment, params_first, search_arr.shape)
        if cache is not None:
            direct_phase = (float(cache["p0"]), float(cache["p1"]))
            _cache_msg = tr(
                "direct dimension phase reuse cache phase.json: {p0}=({p1:g}°, {p2:g}°)",
                p0=direct_axis,
                p1=phase_angle_value(direct_phase[0]),
                p2=phase_angle_value(direct_phase[1]),
            )
            logs.append(_cache_msg)
            if progress is not None:
                progress(_cache_msg)
        else:
            last_s = _estimate_direct_phase_seconds(work)
            if progress is not None:
                if last_s:
                    progress(
                        tr(
                            "direct dimension phase is searching (last appointment {p0:.0f} "
                            "seconds), please "
                            "wait",
                            p0=last_s,
                        )
                    )
                else:
                    progress(
                        tr(
                            "Searching for the direct-dimension phase (first run, usually tens of "
                            "seconds), please "
                            "wait",
                        )
                    )
            t0 = _time.time()
            direct_est = search_direct_phase_real_ht(
                search_arr,
                axis=-1,
                sign_mode=sign_mode,
                progress=progress,
                cancel=cancel_requested,
            )
            elapsed = _time.time() - t0
            if progress is not None:
                progress(
                    tr(
                        "direct dimension phase search completed, time consuming {p0:.1f} Second",
                        p0=elapsed,
                    )
                )
            logs.append(
                tr(
                    "direct dimension phase search completed, time consuming {p0:.1f} Second",
                    p0=elapsed,
                )
            )
            direct_phase = (0.0, 0.0)
            if direct_est is not None and direct_est[2] >= 30.0:
                direct_phase = (float(direct_est[0]), float(direct_est[1]))
                # 0.2.199-patch22: relax the p1 reset guardrail (20 -> 170) -- the high
                # field (1200MHz) acquisition delay lets the true p1 reach 100 deg+; it
                # is only reset above 170 deg (a near full-scale flip, suspected
                # wrapping artefact); when the final-run window changes, p1 is
                # renormalised by 0.2.162-patch16 according to the window width
                if abs(direct_phase[1]) > 170.0:
                    logs.append(
                        tr(
                            "direct-dimension HT search p1={p0:g}° is out of range (>170°), reset "
                            "to zero",
                            p0=phase_angle_value(direct_phase[1]),
                        )
                    )
                    direct_phase = (direct_phase[0], 0.0)
                logs.append(
                    tr(
                        "direct dimension projection HT Search for: {p0}=({p1:g}°, {p2:g}°) "
                        "score={p3:.2f}",
                        p0=direct_axis,
                        p1=phase_angle_value(direct_phase[0]),
                        p2=phase_angle_value(direct_phase[1]),
                        p3=direct_est[2],
                    )
                )
                _save_direct_phase_cache(
                    work,
                    experiment,
                    params_first,
                    search_arr.shape,
                    direct_phase[0],
                    direct_phase[1],
                    float(direct_est[2]),
                    elapsed,
                )
            else:
                logs.append(
                    tr(
                        "direct dimension projection HT searches for no clean signal peaks or "
                        "insufficient confidence, keep "
                        "(0,0)",
                    )
                )
    logs.append(
        tr(
            "direct dimension memory phase: {p0}=({p1:g}°, {p2:g}°)",
            p0=direct_axis,
            p1=phase_angle_value(direct_phase[0]),
            p2=phase_angle_value(direct_phase[1]),
        )
    )
    # 0.2.199-patch29do (user): iterating -- the indirect dimensions are searched
    # once more after the direct dimension is settled (the direct dimension is an
    # independent real-valued HT method, cached in phase.json; re-searching the
    # indirect dimensions re-locks their traces and resolves the two-way
    # dependency. On 3D NUS repeated re-searches showed a +-180 deg sign swing and
    # took about 30 s each, so the convergence iteration is limited to one round)
    if indirect_axes and auto_phase:
        for _round in range(1):
            changed = False
            for axis in indirect_axes:
                out_file = f"{experiment.dataset_id}_preview_{axis}_r{_round + 2}.{ext}"
                t_axis = time.time()
                if progress is not None:
                    progress(
                        tr(
                            "In phase optimisation: {p0} Replicate preview (iteration) in progress",
                            p0=axis,
                        )
                    )
                # The preview axis itself must be excluded: the finalize preview
                # writes the preview axis' phase from phases straight into PS
                # (unlike the uniform preview, which filters), so generating with the
                # old phase would search a residual (about 0) and overwrite/lose the
                # absolute phase
                resp = backend.finalize_nus(
                    experiment,
                    phases={k: v for k, v in fixed.items() if k != axis},
                    work_dir=work,
                    params={
                        **_sign_sampling(base_params),
                        **zf_phase,
                        "preview_axis": axis,
                    },
                    out_file=out_file,
                    script_name=(
                        f"{experiment.dataset_id}_preview_{axis}_r{_round + 2}_finalize.com"
                    ),
                    progress=progress,
                )
                backend_runs += 1
                if not resp.get("success") or not resp.get("spectrum_path"):
                    raise RuntimeError(
                        tr(
                            "NUS Replica preview search ({p0}) failed: {p1}",
                            p0=axis,
                            p1=resp.get("message"),
                        )
                    )
                ax = _axis_index(axis, experiment.ndim)
                arr = _load_preview_with_memory_guard(
                    str(resp["spectrum_path"]),
                    axis=axis,
                    unpack_axis=ax,
                    progress=progress,
                    logs=logs,
                )
                _unlink_quiet(resp["spectrum_path"])
                est = search_axis_memory(arr, ax, sign_mode=sign_mode, cancel=cancel_requested)
                if est is None:
                    raise RuntimeError(
                        tr(
                            "Memory phase research({p0}) No trace available",
                            p0=axis,
                        )
                    )
                if sign_mode == "mixed":
                    resolved = _disambiguate_180_mixed(arr, ax, est.phase, experiment, axis)
                    phase = resolved
                else:
                    phase = est.phase
                prev = fixed.get(axis)
                fixed[axis] = phase
                logs += est.logs
                logs.append(
                    tr(
                        "{p0}: Memory phase re-search (iteration {p1}) = ({p2:g}°, {p3:g}°) "
                        "score={p4:.2f}",
                        p0=axis,
                        p1=_round + 2,
                        p2=phase_angle_value(phase[0]),
                        p3=phase_angle_value(phase[1]),
                        p4=est.score,
                    )
                )
                if prev is None or _phase_delta(prev, phase) >= 5.0:
                    changed = True
                logs.append(
                    tr(
                        "{p0} Phase re-search is completed, time-consuming {p1:.1f} Second",
                        p0=axis,
                        p1=time.time() - t_axis,
                    )
                )
            if not changed:
                logs.append(
                    tr(
                        "phase iteration: indirect dimension {p0} There is no change in the wheel, "
                        "convergence",
                        p0=_round + 2,
                    )
                )
                break
    # Processing-parameter optimisation (baseline / zero fill / window functions):
    # after the per-axis phase search and before the final run; the final phase of
    # every dimension goes into the initial script together with the optimised
    # processing parameters to produce a new full script for the final run -- the
    # direct-dimension phase enters the step1 PS (after EXT, normalised the same way
    # as the in-memory recon-plane rotation) and the indirect-dimension phases enter
    # the step3 PS; no rotated nus3d_rc_ph copy is written.
    if progress is not None:
        progress(
            tr(
                "The phase search is completed and processing of parameter optimisation "
                "(baseline/zero "
                "filling/window function) "
                "begins",
            )
        )
    t_opt = time.time()
    proc = _optimize_nus_processing(
        experiment,
        backend,
        work,
        fixed,
        base_params,
        progress=progress,
    )
    logs += proc["logs"]
    logs.append(
        tr(
            "Processing parameter optimisation (baseline/zero filling/window function) is "
            "completed, "
            "time-consuming {p0:.1f} "
            "Second",
            p0=time.time() - t_opt,
        )
    )
    # 0.2.199-patch29fk-fix: the reconstruction planes have been consumed by the
    # parameter optimisation (indirect windows scored from recon).
    # 2026-09-25 (user): **nus3d_rc is no longer released early** -- it is kept (the
    # "indirect-dimension processing input" of 3D NUS) so that an
    # indirect-dimension flip (FT -neg) can re-run only the final script instead of
    # SMILE; nus3d_1 / nus2d are still released (the final run's reconstruct
    # rebuilds them).
    for _sub in ("nus3d_1", "nus2d"):
        _rmtree_quiet(work / _sub)
    params_final = dict(base_params or {})
    params_final.pop("final_ext_lo", None)
    params_final.pop("final_ext_hi", None)
    params_final = _apply_final_ext(params_final, final_ext_lo, final_ext_hi)
    # 0.2.162-patch16: when the final-run direct-dimension range changes, p1 is
    # renormalised by the window-width ratio (p1 is the total degrees across the
    # whole extraction window; with a narrower range the same p1 gives a steeper
    # physical slope)
    direct_phase_final = _renormalize_direct_p1(direct_phase, params_first, params_final)
    if direct_phase_final != direct_phase:
        logs.append(
            tr(
                "direct dimension phase renormalized by final run window: {p0} p1={p1:g}° → "
                "{p2:g}°",
                p0=direct_axis,
                p1=phase_angle_value(direct_phase[1]),
                p2=phase_angle_value(direct_phase_final[1]),
            )
        )
    params_final.update(
        {
            "direct_phase_search": False,
            "display_phase_search": False,
            "direct_poly_time": bool(diagnostics.get("apply_poly_time")),
            "direct_phase_override": [
                float(direct_phase_final[0]),
                float(direct_phase_final[1]),
            ],
            "phases": {axis: [float(p0), float(p1)] for axis, (p0, p1) in fixed.items()},
            "baseline": proc["baseline"],
            "zero_fill": proc["zero_fill"],
            "window": proc["window"],
        }
    )
    if progress is not None:
        progress(
            tr(
                "In the final run (complete script, including the final phase of each dimension)",
            )
        )
    t_final = time.time()
    final = backend.reconstruct_nus(experiment, params_final, progress=progress)
    backend_runs += 1
    if not final.get("success") or not final.get("spectrum_path"):
        raise RuntimeError(tr("Final run (complete script) failed: {p0}", p0=final.get("message")))
    if progress is not None:
        progress(tr("Final run completed"))
    logs.append(
        tr(
            "final run: the final phase of every dimension is written into the full script (direct "
            "dimension {p0}=({p1:g}°, "
            "{p2:g}°)",
            p0=direct_axis,
            p1=phase_angle_value(direct_phase_final[0]),
            p2=phase_angle_value(direct_phase_final[1]),
        )
        + "".join(
            f" {axis}=({format_phase_degrees(p0)}°, {format_phase_degrees(p1)}°)"
            for axis, (p0, p1) in fixed.items()
        )
        + tr("), nus3d_rc_ph rotated copy not generated")
    )
    logs += list(final.get("logs", []))
    logs.append(
        tr(
            "The final run is completed and takes time {p0:.1f} Second",
            p0=time.time() - t_final,
        )
    )
    # 0.2.199-patch29u: cross-check the final script's baseline -- when the report
    # says order3 was selected, the final script should carry the matching POLY
    # (to catch a "report/script inconsistency")
    try:
        final_script = work / f"{experiment.dataset_id}_nus.com"
        if final_script.is_file():
            _text = final_script.read_text(encoding="utf-8", errors="replace")
            _polys = [ln.strip() for ln in _text.splitlines() if "POLY" in ln]
            logs.append(
                tr("Final script baseline check: ")
                + ("; ".join(_polys) if _polys else tr("None POLY"))
            )
    except OSError:
        pass
    _append_final_summary(
        logs,
        str(final["spectrum_path"]),
        direct_axis=direct_axis,
        direct_phase=direct_phase_final,
        phases=fixed,
        backend_runs=backend_runs,
        baseline=proc["baseline"],
        baseline_scores=proc.get("baseline_scores"),
        zero_fill=proc["zero_fill"],
        window=proc["window"],
        optimization_logs=proc["logs"],
        peak_sign=_template_peak_sign(experiment),
        progress=progress,
    )
    _cleanup_unified_intermediates(
        work, experiment.dataset_id, experiment=experiment, backend=backend
    )
    return {
        "phases": fixed,
        "direct_phase": direct_phase_final,
        "baseline": proc["baseline"],
        "zero_fill": proc["zero_fill"],
        "window": proc["window"],
        "diagnostics": diagnostics,
        "spectrum_path": str(final["spectrum_path"]),
        "backend_runs": backend_runs,
        "logs": logs,
    }
