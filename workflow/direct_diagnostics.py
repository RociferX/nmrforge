"""Direct dimension data quality diagnosis gate (0.2.140). Run at the very beginning of the
spectrum generation step (based on converted fid, memory evaluation, no re-run SMILE): detect
correctable problems (DC offset -> POLY -time, peak bad point -> automatic replacement) and
automatically process; problems that are difficult to eliminate through data processing
(frequency drift, broadband solvent residue, serious uneven energy at sampling point) Clearly
report and give suggestions (Check temperature control/solvent pressing/Gain or reacquire
spectrum). The diagnostic report is presented to the user along with the processing log. The
format is: 1. There is a DC offset in the direct dimension (DC peak is X.X times the strongest
signal), and POLY -time correction has been enabled. 2. N peak bad points have been detected and
have been automatically replaced (see fid_diag_bak/ for backup) 3. Before sampling/There is a
frequency drift in the later period, data processing cannot be completely eliminated, it is
recommended to check the temperature control and consider re-acquisition fid byte layout
(nmrPipe standard): 512 bytes parameter header + each trace [real part block (fdsize number of
complex points x 4B), imaginary part block (fdsize x 4B)], complex64. Reading and writing are
performed directly according to this layout, without relying on nmrglue's writeback path (its
complex writeback has shape parsing problems)."""

from __future__ import annotations

import json
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.audit.qc_audit import QcAction, QcAuditLog
from core.data.internal_data_model import Experiment, SamplingMode
from core.project.manager import atomic_write_text
from ui_support.i18n import tr

# Threshold (first version, empirical value; 0.2.199-patch29cw calibration) time domain indicator
# |FID mean|/|FID peak|:VM measured conventional spectrum 0.07-0.20(100/102/3/28/101), true DC
# (sampleC)≈0.49; threshold 0.25 distinguishes between the two, so that every spectrum no longer
# gives false positives.
# Enabled when the mean exceeds 25% of the strongest amplitude POLY -time.
DC_RATIO_THRESHOLD = 0.25
BADPOINT_MAD = 12.0  # Isolated spike = amplitude 12 x MAD outside the neighborhood median.
# First dot/Sub-point margin exceeds 1.6 x Prompt group delayed reconstruction.
FIRST_POINT_RATIO = 1.6
# The strongest peak FWHM exceeds 8% of the spectral width and is considered a broadband package
# (suspected solvent).
BROAD_PEAK_FRACTION = 0.08
# The peak position drift of the first and last sampling period exceeds 1 FWHM, prompting re-
# sampling.
DRIFT_FWHM_MULT = 1.0
# : The maximum number of change points listed in a single QC audit record (exceeding the number of
# records only, to avoid writing huge JSONL for pathological data).
AUDIT_DETAIL_LIMIT = 32
# The top 20% trace energy accounts for more than 92%, indicating uneven distribution.
ENERGY_TOP20_THRESHOLD = 0.92

# The nmrPipe fid header length varies depending on 2D (2048B)/3D stream (512B), etc., and is
# determined dynamically during parsing.
COMPLEX_BYTES = 8  # complex64 Every complex point.
HEADER_CANDIDATES = (512, 1024, 2048)


@dataclass
class DirectDiagnosticsResult:
    """Direct dimension diagnosis results."""

    reports: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    apply_poly_time: bool = False
    repaired_badpoints: int = 0
    backup_dir: str = ""


def _collect_fid_paths(
    work: Path, experiment: Experiment
) -> list[Path]:
    """After positioning and conversion, fid: single file (dataset.fid) or multi-segment
    fid/testNNN.fid."""
    if experiment.segments:
        for base in (work / "merged", work):
            d = base / "fid"
            if d.is_dir():
                fs = sorted(d.glob("test*.fid"))
                if fs:
                    return fs
    d2 = work / "fid"
    if d2.is_dir():
        fs = sorted(d2.glob("test*.fid"))
        if fs:
            return fs
    single = work / f"{experiment.dataset_id}.fid"
    if single.is_file():
        return [single]
    return sorted(work.glob("test*.fid"))


def _read_fid_raw(
    path: Path,
) -> tuple[np.ndarray, int, int, int] | None:
    """Read fid according to byte layout -> (complex64 (nrows, fdsize), nrows, fdsize, header).
    Based on the complex array of nmrglue read, try the candidate header length (512/1024/2048),
    and the one element-wise congruent is the real layout of the file. Support: - Old sliced 2D
    complex plane: (specnum, fdsize), real and virtual interleaved along the second axis; -
    0.2.199-patch16 Single file aq2D pseudo 3D: (a, b, c) 3D replica, direct dimension in the
    last axis, reshape to (a*b, c)(0.2.199-patch25)."""
    raw = path.read_bytes()
    if len(raw) <= 2048:
        return None
    try:
        import nmrglue as ng

        dic, d = ng.pipe.read(str(path))
        arr = np.asarray(d)
        fdsize = int(float(dic["FDSIZE"]))
        specnum = int(float(dic["FDSPECNUM"]))
    except Exception:  # noqa: BLE001
        return None
    # Single file aq2D:nmrglue reads as 3D replica (a, b, c), direct dimension = last axis.
    if arr.ndim == 3:
        nrows = int(arr.shape[0] * arr.shape[1])
        fdsize3 = int(arr.shape[2])
        target = arr.reshape(nrows, fdsize3).astype(np.complex64)
        for header in HEADER_CANDIDATES + (0,):
            expect = header + nrows * fdsize3 * COMPLEX_BYTES
            if len(raw) != expect:
                continue
            flat = np.frombuffer(
                raw, dtype="<f4", count=nrows * fdsize3 * 2, offset=header
            ).astype(np.float32)
            rows = flat.reshape(nrows, fdsize3 * 2)
            cand = rows[:, :fdsize3] + 1j * rows[:, fdsize3:]
            if cand.shape == target.shape and np.array_equal(
                cand, target, equal_nan=True
            ):
                return target, fdsize3, nrows, header
        # The header length is not within candidate: inverse according to file size, still returns
        # nmrglue reading value.
        for header in HEADER_CANDIDATES:
            if len(raw) >= header + nrows * fdsize3 * COMPLEX_BYTES:
                return target, fdsize3, nrows, header
        return target, fdsize3, nrows, 0
    if arr.ndim != 2 or arr.shape != (specnum, fdsize):
        return None
    target = arr.astype(np.complex64)
    for header in HEADER_CANDIDATES:
        expect = header + specnum * fdsize * COMPLEX_BYTES
        if len(raw) != expect:
            continue
        flat = np.frombuffer(
            raw, dtype="<f4", count=specnum * fdsize * 2, offset=header
        ).astype(np.float32)
        rows = flat.reshape(specnum, fdsize * 2)
        cand = rows[:, :fdsize] + 1j * rows[:, fdsize:]
        if cand.shape == target.shape and np.array_equal(cand, target, equal_nan=True):
            return cand, fdsize, specnum, header
    return None


def _dc_ratio_time(traces: np.ndarray) -> float:
    """Time domain DC bias estimation: top trace |FID mean| / |FID median of peak|.
    0.2.199-patch29cw: The original frequency domain bin0 indicator is affected by FID
    Truncate/Envelope leakage effects, and almost all real spectra exceed the 3% threshold (user
    feedback reports DC bias for each spectrum). The time domain mean value directly corresponds
    to POLY -time The eliminated constant component has clear physical meaning; VM measured
    conventional spectrum 0.07-0.20, real DC (sampleC)≈0.49."""
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(len(order) * 0.05)), 4), 12)
    top = traces[order[:keep]]
    means = np.abs(np.mean(top, axis=-1))
    peaks = np.max(np.abs(top), axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = means / np.maximum(peaks, 1e-12)
    return float(np.median(ratios))


def _trace_metrics(
    traces: np.ndarray, n: int
) -> dict[str, float]:
    """Top Calculate DC on the trace/first point/Broadband peak/Drift indicator."""
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(len(order) * 0.05)), 4), 12)
    top = traces[order[:keep]]
    n_pad = n
    work = np.zeros((len(top), n_pad), dtype=complex)
    work[:, : min(n, n_pad)] = top[:, : min(n, n_pad)]
    spec = np.fft.fft(work, axis=-1)
    amp = np.abs(spec)
    dc_ratio = _dc_ratio_time(traces)
    p_side = np.argmax(amp[:, 3 : n_pad // 2], axis=1) + 3
    avg = np.median(amp, axis=0)
    pk = int(np.median(p_side))
    peak = avg[pk]
    half = peak / 2
    left = pk
    while left > 0 and avg[left] > half:
        left -= 1
    r = pk
    while r < n_pad - 1 and avg[r] > half:
        r += 1
    fwhm_pts = max(float(r - left), 1.0)
    broad = fwhm_pts / n_pad > BROAD_PEAK_FRACTION
    med_amp = np.median(np.abs(top), axis=0)
    first_ratio = (
        float(med_amp[0] / max(med_amp[1], 1e-12))
        if n > 2 and med_amp[1] > 0
        else 0.0
    )
    split = max(int(len(order) * 0.2), 2)
    head = traces[order[:split]]
    tail = traces[order[-split:]]
    pos_head, pos_tail = [], []
    for arr2 in (head, tail):
        w2 = np.zeros((len(arr2), n_pad), dtype=complex)
        w2[:, : min(n, n_pad)] = arr2[:, : min(n, n_pad)]
        a2 = np.abs(np.fft.fft(w2, axis=-1))
        a2[:, :3] = 0.0
        pos = np.argmax(a2[:, 3 : n_pad // 2], axis=1)
        (pos_head if arr2 is head else pos_tail).append(float(np.median(pos)))
    drift_pts = abs(float(np.median(pos_head)) - float(np.median(pos_tail)))
    return {
        "dc_ratio": dc_ratio,
        "first_point_ratio": first_ratio,
        "broad_peak": broad,
        "fwhm_pts": fwhm_pts,
        "drift_pts": drift_pts,
        "n_direct": n,
    }


def _find_bad_points(row: np.ndarray) -> np.ndarray:
    """Isolated spike mask: Amplitude >> neighborhood median with steep dips on both sides.
    0.2.199-patch29u: Pointwise Python loop vectorization -- 3D NUS The original implementation
    of thousands of traces x 2048 points was the main time consuming for data quality
    diagnostics."""
    amp = np.abs(row)
    med = float(np.median(amp))
    mad = float(np.median(np.abs(amp - med)))
    n = amp.size
    cand = np.zeros(n, dtype=bool)
    if mad <= 0 or n < 5:
        return cand
    thr = med + BADPOINT_MAD * 1.4826 * mad
    center = amp[2:-2]
    left = np.maximum(amp[1:-3], amp[0:-4])
    right = np.maximum(amp[3:-1], amp[4:])
    cand[2:-2] = (
        (center > thr)
        & (center > 3.0 * np.maximum(left, 1e-12))
        & (center > 3.0 * np.maximum(right, 1e-12))
    )
    return cand


def _patch_point(
    raw: bytearray, fdsize: int, header: int, row: int, col: int, value: complex
) -> None:
    """Write back a single point (complex value) to the byte buffer: real part block/Imaginary
    block layout."""
    re_off = header + row * fdsize * COMPLEX_BYTES + col * 4
    struct.pack_into("<f", raw, re_off, float(value.real))
    struct.pack_into("<f", raw, re_off + fdsize * 4, float(value.imag))


def run_direct_diagnostics(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    repair: bool = True,
) -> DirectDiagnosticsResult:
    """Generate-spectrum direct-dimension diagnostic gate.

    Parameters
    ----------
    work_dir: Path | str processing working directory (fid is under it after conversion, single
    file or ``fid/test*.fid`` slice stream). experiment: Experiment data understanding results;
    used to locate fid and distinguish uniform/NUS gate. repair: bool, default True Whether to
    automatically repair correctable problems (bad point replacement, backup to
    ``fid_diag_bak/`` before repair).

    Returns
    -------
    DirectDiagnosticsResult ``reports``(diagnostic line for user), ``metrics``(DC/first
    point/broadband/drift/ bad point count), ``apply_poly_time``(whether direct dimension is
    enabled POLY -time), ``repaired_badpoints``, ``backup_dir``.

    Raises
    ------
    - No exception thrown: When the layout cannot be parsed or there is no fid, ``reports`` will
    be used to explain and skip (does not block processing).

    Side effects
    ------------
    ``repair=True`` will rewrite the fid in the working directory (back up to ``fid_diag_bak/``
    first), and write each change into ``qc_audit.jsonl``; write another ``diagnostics.json``
    summary.

    Examples
    --------
    result = run_direct_diagnostics(work_dir, experiment) if result.apply_poly_time:... # The
    final script needs to be inserted POLY -time."""
    work = Path(work_dir)
    paths = _collect_fid_paths(work, experiment)
    return _diagnose_paths(
        paths,
        work,
        repair=repair,
        is_uniform=experiment.sampling.mode is SamplingMode.UNIFORM,
    )


def run_fid_diagnostics_paths(
    paths: list[Path | str],
    *,
    repair: bool = False,
) -> DirectDiagnosticsResult:
    """Standalone FID diagnostics for arbitrary fid files/folders (no Experiment needed; detect-
    only by default).

    Parameters
    ----------
    paths : list[Path | str] Fid file or directory to diagnose (a directory is collected
    automatically as ``*.fid`` / ``test*.fid``). repair : bool, default False Whether bad points
    are repaired automatically; this standalone entry point **only detects and never modifies**
    by default.

    Returns
    -------
    DirectDiagnosticsResult Same structure as :func:`run_direct_diagnostics`
    (``reports``/``metrics``/``apply_poly_time``...).

    Raises
    ------
    Nothing is raised: a missing path is reported as "not found" inside ``reports``.

    Side effects
    ------------
    Directories and files are read-only (``repair=False``); with repair enabled the behaviour
    matches :func:`run_direct_diagnostics` (backup plus audit record).

    Examples
    --------
    result = run_fid_diagnostics_paths(["process/exp_001.fid"])"""
    files: list[Path] = []
    for _p in paths:
        _pp = Path(_p)
        if _pp.is_dir():
            _fs = sorted(_pp.glob("*.fid")) or sorted(_pp.glob("test*.fid"))
            files.extend(_fs)
        elif _pp.is_file():
            files.append(_pp)
    _work = files[0].parent if files else Path(".")
    return _diagnose_paths(files, _work, repair=repair, is_uniform=False)


def _record_bad_point_repair(
    audit: QcAuditLog,
    *,
    file_name: str,
    row: int,
    changed: list[tuple[int, complex, complex]],
    backup_dir: str = "",
) -> None:
    """Write a "bad point has been replaced" structured audit record (Phase 10). Extract an
    independent function for direct single testing: the record content must include detection
    rules, actions, before change/The value after, as well as line numbers and backup locations;
    when it exceeds ``AUDIT_DETAIL_LIMIT``, only the number is recorded and marked as truncated."""
    shown = changed[:AUDIT_DETAIL_LIMIT]
    audit.record(
        QcAction(
            issue_detected=tr("direct dimension peak bad point"),
            location=f"{file_name} row={row}",
            detection_rule=(
                tr(
                "_find_bad_points (single row amplitude distribution): peak determination compared "
                "with adjacent "
                "points",
            )
            ),
            action_taken="neighbour_interpolation",
            before_state={
                "columns": [col for col, _b, _a in shown],
                "real": [round(b.real, 6) for _c, b, _a in shown],
                "imag": [round(b.imag, 6) for _c, b, _a in shown],
                "count": len(changed),
            },
            after_state={
                "columns": [col for col, _b, _a in shown],
                "real": [round(a.real, 6) for _c, _b, a in shown],
                "imag": [round(a.imag, 6) for _c, _b, a in shown],
                "count": len(changed),
            },
            extra={
                "file": file_name,
                "row": row,
                "truncated": len(changed) > len(shown),
                "backup_dir": backup_dir,
            },
        )
    )


def _diagnose_paths(
    paths: list[Path],
    work: Path,
    *,
    repair: bool = True,
    is_uniform: bool = False,
) -> DirectDiagnosticsResult:
    res = DirectDiagnosticsResult()
    if not paths:
        res.reports = [(
            tr(
            "Data quality diagnosis: converted fid not found, skipped (diagnosis does not block "
            "processing)",
        )
        )]
        return res
    blocks: list[np.ndarray] = []
    parsed: list[tuple[Path, int, int, int]] = []
    for path in paths:
        got = _read_fid_raw(path)
        if got is None:
            continue
        data, fdsize, specnum, header = got
        blocks.append(data)
        parsed.append((path, fdsize, specnum, header))
    if not blocks:
        res.reports = [(
            tr(
            "Data quality diagnosis: fid layout cannot be parsed after conversion, "
            "skipped",
        )
        )]
        return res
    traces = np.concatenate(blocks, axis=0)
    n = int(traces.shape[-1])
    m = _trace_metrics(traces, n)
    res.metrics = {
        k: (float(v) if isinstance(v, (int, float, np.floating)) else bool(v))
        for k, v in m.items()
    }
    reports: list[str] = []

    if m["dc_ratio"] > DC_RATIO_THRESHOLD:
        res.apply_poly_time = True
        reports.append(
            tr(
                "A DC bias is present in the direct dimension (the FID mean is about {p0:.0f}% of "
                "the strongest amplitude); POLY -time auto-correction has been "
                "enabled",
                p0=m['dc_ratio']*100,
            )
        )

    repaired = 0
    backup = work / "fid_diag_bak"
    made_backup = False
    audit = QcAuditLog(work)
    if repair:
        repaired = 0
        for path, fdsize, specnum, header in parsed:
            got = _read_fid_raw(path)
            if got is None:
                continue
            data, _fs, _sn, _hdr = got
            raw = bytearray(path.read_bytes())
            for row in range(specnum):
                energy = float(np.sum(np.abs(data[row]) ** 2))
                if energy <= 0:  # NUS No plane collected.
                    continue
                mask = _find_bad_points(data[row])
                if not np.any(mask):
                    continue
                if not made_backup:
                    backup.mkdir(parents=True, exist_ok=True)
                    for p in paths:
                        try:
                            shutil.copy2(p, backup / p.name)
                        except OSError:
                            pass
                    made_backup = True
                changed: list[tuple[int, complex, complex]] = []
                for col in np.where(mask)[0]:
                    if 0 < col < fdsize - 1:
                        before_value = complex(data[row, col])
                        val = 0.5 * (data[row, col - 1] + data[row, col + 1])
                        data[row, col] = val
                        _patch_point(raw, fdsize, header, row, col, val)
                        changed.append((int(col), before_value, complex(val)))
                        repaired += 1
                if changed:
                    # Phase 10: Every step of automatically changing intermediate data must have
                    # structured records (not just log lines).
                    _record_bad_point_repair(
                        audit,
                        file_name=path.name,
                        row=int(row),
                        changed=changed,
                        backup_dir=str(backup) if made_backup else "",
                    )
            if made_backup:
                path.write_bytes(bytes(raw))
        res.repaired_badpoints = repaired
    if repaired:
        reports.append(
            tr(
                "{p0} spike bad point(s) detected and replaced automatically (the original fid is "
                "backed up in fid_diag_bak/; re-running fid.com can restore "
                "it)",
                p0=repaired,
            )
        )
        audit_summary = audit.summary()
        if audit_summary:
            reports.append(audit_summary)
        nonzero = int(np.sum(np.sum(np.abs(traces) ** 2, axis=-1) > 0))
        if repaired / max(nonzero * n, 1) > 0.005:
            reports.append(
                tr(
                "the bad-point fraction is high; also check ADC / gain stability on the "
                "acquisition "
                "side",
            ))

    if m["first_point_ratio"] > FIRST_POINT_RATIO:
        reports.append(
            tr(
                "The amplitude of the first sampling point is relatively high ({p0:.2f}× the "
                "second point); group delay / first-point reconstruction is normally handled by "
                "the conversion parameters; if the baseline at high field still tilts, check the "
                "GRPDLY/DSPFVS parameters in "
                "acqus",
                p0=m['first_point_ratio'],
            )
        )
    if m["broad_peak"]:
        reports.append(
            tr(
                "broad envelope peak detected (FWHM over 8% of the spectral width); likely solvent "
                "/ chemical-exchange residue, which processing cannot remove completely; check the "
                "solvent-suppression "
                "conditions",
            )
        )
    if m["drift_pts"] > DRIFT_FWHM_MULT * m["fwhm_pts"]:
        reports.append(
            tr(
                "direct-dimension frequency drift between the pre- and post-sampling periods is "
                "about {p0:.1f} points ({p1:.1f} linewidths), processing cannot remove it "
                "completely; check temperature control / lock and consider "
                "re-acquiring",
                p0=m['drift_pts'],
                p1=m['drift_pts'] * m['fwhm_pts'],
            )
        )
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    # 0.2.199-patch29z:NUS Most of the traces are empty (not collected), and the first 20% of the
    # traces must account for 100% of the energy, causing each spectrum to be falsely reported --
    # only the energy distribution of non-empty traces is counted.
    nz = energy[energy > 0]
    frac = 0.0
    if nz.size >= 8:
        order = np.argsort(nz)[::-1]
        top20 = max(int(np.ceil(nz.size * 0.2)), 1)
        frac = float(np.sum(nz[order[:top20]]) / max(np.sum(nz), 1e-12))
    res.metrics["energy_top20_frac"] = frac
    if frac > ENERGY_TOP20_THRESHOLD:
        reports.append(
            tr(
                "Sampling point energy distribution is uneven (the first 20% of non-empty traces "
                "account for {p0:.0f}% of the energy), this may indicate gain-step / pulse "
                "instability; if the reconstruction is unaffected you may continue, otherwise "
                "check the acquisition",
                p0=frac * 100,
            )
        )
    # 0.2.196: Potential problems are only reported and not automatically handled -- NaN/Inf, all
    # zero traces, continuous abnormal energy.
    n_nan_inf = int(np.isnan(traces).sum()) + int(np.isinf(traces).sum())
    res.metrics["nan_inf_count"] = n_nan_inf
    if n_nan_inf:
        reports.append(
            tr(
                "{p0} NaN/Inf value(s) detected and left untouched (check the acquisition side and "
                "the conversion "
                "parameters)",
                p0=n_nan_inf,
            )
        )
    zero_traces = int(np.sum(energy == 0))
    res.metrics["zero_traces"] = zero_traces
    if is_uniform and zero_traces:
        reports.append(
            tr(
                "{p0} all-zero trace(s) detected and left untouched (uniform sampling should have "
                "no all-zero traces; the acquisition may be "
                "incomplete)",
                p0=zero_traces,
            )
        )
    nz_energy = energy[energy > 0]
    high_energy = 0
    if nz_energy.size >= 4:
        med_nz = float(np.median(nz_energy))
        if med_nz > 0:
            high_energy = int(np.sum(nz_energy > 100.0 * med_nz))
            res.metrics["high_energy_traces"] = high_energy
    if high_energy:
        reports.append(
            tr(
                "{p0} trace(s) have abnormally high energy (>100× the median) and are not isolated "
                "spikes; left untouched (check gain / pulse "
                "stability)",
                p0=high_energy,
            )
        )
    if not reports:
        reports = [(
            tr(
            "Data quality diagnosis: DC offset not detected, peak bad point, first point "
            "abnormality, broadband peak or "
            "drift",
        )
        )]
    res.reports = reports
    if made_backup:
        res.backup_dir = str(backup)
    try:
        atomic_write_text(
            (work / "diagnostics.json"),
            json.dumps(
                {
                    "reports": reports,
                    "metrics": res.metrics,
                    "apply_poly_time": res.apply_poly_time,
                    "repaired_badpoints": repaired,
                    "backup_dir": res.backup_dir or str(backup),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except OSError:
        pass
    return res


__all__ = [
    "DirectDiagnosticsResult",
    "run_direct_diagnostics",
]
