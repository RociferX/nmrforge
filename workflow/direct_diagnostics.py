"""Direct-dimension data quality diagnosis gate (0.2.140).

Since 2026-09-23 it runs at the very end of the Generate-FID step (on the fid that
was just converted/merged, evaluated in memory, without re-running SMILE); the
findings and what was done about them are written into that step log and saved to
process/diagnostics.json:
correctable problems (DC offset -> POLY -time, spike bad point -> automatic
replacement) are handled automatically; problems that data processing cannot remove
(frequency drift, broadband solvent residue, badly uneven sampling-point energy) are
reported explicitly with advice (check temperature control / solvent suppression /
gain, or re-acquire).
The Generate-Spectrum step no longer re-runs the diagnosis and reads
diagnostics.json back instead (only a missing record, or one whose fid signature
does not match the current products, falls back to a real run; old working
directories and the manual route behave exactly as before).

The diagnosis report is presented to the user together with the processing log, in
the form:
  1. a DC bias is present in the direct dimension (the DC peak is X.X times the
     strongest signal); POLY -time correction has been enabled
  2. N spike bad points were detected and replaced automatically (backup in
     fid_diag_bak/)
  3. frequency drift is present between the pre- and post-sampling periods and data
     processing cannot remove it completely; check temperature control and consider
     re-acquiring

fid byte layout (nmrPipe standard): a 512-byte parameter header + each trace [real
block (fdsize complex points x 4B), imaginary block (fdsize x 4B)], complex64.
Reading and writing follow that layout directly and do not rely on nmrglue's
writeback path (its complex writeback has shape-parsing problems).
"""

from __future__ import annotations

import json
import re
import shutil
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.audit.qc_audit import (
    SOURCE_CLEAN_ACTION,
    QcAction,
    QcAuditLog,
    audit_action_label,
    read_audit,
)
from core.data.internal_data_model import Experiment, SamplingMode
from core.experiment.pulse_pathways import is_neg_notice
from core.project.manager import atomic_write_text
from ui_support.i18n import tr

DC_RATIO_THRESHOLD = 0.25
BADPOINT_MAD = 12.0
FIRST_POINT_RATIO = 1.6
BROAD_PEAK_FRACTION = 0.08
DRIFT_LW_NEGLIGIBLE = 0.5
DRIFT_LW_LARGE = 3.0
DRIFT_LW_SEVERE = 10.0
AUDIT_DETAIL_LIMIT = 32
ENERGY_TOP20_THRESHOLD = 0.92  # top 20% of traces holding over 92% of the energy: uneven

# nmrPipe fid header length varies with 2D (2048B) / 3D stream (512B) etc.; it is
# determined dynamically while parsing
COMPLEX_BYTES = 8  # bytes per complex point (complex64)
HEADER_CANDIDATES = (512, 1024, 2048)
#: on-disk name of the diagnosis conclusion (written by Generate FID, read back by
#: Generate Spectrum)
DIAGNOSTICS_FILENAME = "diagnostics.json"


@dataclass
class DirectDiagnosticsResult:
    """Direct-dimension diagnosis result."""

    reports: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    apply_poly_time: bool = False
    repaired_badpoints: int = 0
    backup_dir: str = ""
    #: True when this scan detected nothing at all (``reports`` still carries one
    #: conclusion line for older callers; use this flag to tell "clean" apart)
    clean: bool = False
    #: Did this scan **really run**? (2026-09-24 review B13: "no fid found" / "layout
    #: cannot be parsed" mean "not executed", not "an issue detected" - the report has
    #: to say the two apart)
    ran: bool = True
    #: number of issues auto-processed in this run (DC bias 1 + spike bad point 1);
    #: deliberately **not** len(reports) - reports also carries note-style lines
    #: (2026-09-23 counting fix)
    auto_handled: int = 0
    #: note lines not counted as "detected issues" (audit summary, reasons why
    #: something could not be auto-processed, ...)
    notes: list[str] = field(default_factory=list)


def collect_fid_paths(work: Path, experiment: Experiment) -> list[Path]:
    """Locate the converted fid: the **single source** of "where the converted
    product is" (slice stream / single file).

    Every caller goes through here instead of guessing paths itself -- the manual
    route, the FID-layer diagnosis and the direct-dimension window optimisation share
    one implementation (each copy used to miss one landing place):

    - **slice stream**: ``fid/{dataset_id}NNN.fid`` / ``fid/testNNN.fid`` (the older
      slice form); the merged slices of segmented data live in ``merged/fid/...`` --
      segmented data prefers ``merged/fid``;
    - **single file**: ``{dataset_id}.fid`` (single dataset) and
      ``merged/{dataset_id}.fid`` (the multi-part merged product since
      0.2.199-patch28);
    - fallback: ``merged/*.fid`` (merged file name differing from dataset_id),
      ``merged/fid/*.fid`` and the legacy ``work/test*.fid``.

    2026-09-23 fix: ``merged/{dataset_id}.fid`` used to be missed -> the whole
    FID-layer diagnosis of d_016/d_017 was silently skipped (the log only kept
    "converted fid not found"). 2026-09-24 fix: the manual route reporting "fid not
    found" was the same kind of missed landing place (see
    ``workflow.manual._converted_fid_present``).
    """
    dataset_id = experiment.dataset_id
    merged = work / "merged"
    # (1) slice stream: segmented data prefers merged/fid (merged slices), single dataset work/fid
    for base in (merged, work) if experiment.segments else (work, merged):
        d = base / "fid"
        if d.is_dir():
            # 2026-09-24 review B11: slice names come in two generations -- the new
            # {dataset_id}NNN.fid and the old testNNN.fid (the same convention as backend's
            # _slice_candidates; globbing only test* turns the whole diagnosis into
            # "fid not found")
            found: list[Path] = []
            for pattern in (f"{dataset_id}*.fid", "test*.fid"):
                for path in sorted(d.glob(pattern)):
                    if path not in found:
                        found.append(path)
            if found:
                return found
    # (2) single file: the merged single file prefers merged/ (where segmented data really lands)
    for single in (merged / f"{dataset_id}.fid", work / f"{dataset_id}.fid"):
        if single.is_file():
            return [single]
    # (3) merged/ fallback: single files whose name differs from dataset_id, or
    # slices that are not named testNNN
    if merged.is_dir():
        fs = sorted(merged.glob("*.fid"))
        if fs:
            return fs
        d = merged / "fid"
        if d.is_dir():
            fs = sorted(d.glob("*.fid"))
            if fs:
                return fs
    return sorted(work.glob("test*.fid"))


def _read_fid_raw(
    path: Path,
) -> tuple[np.ndarray, int, int, int] | None:
    """Read the fid by byte layout -> (complex64 (nrows, fdsize), nrows, fdsize, header).

    Taking the complex array read by nmrglue as the reference, try the candidate
    header lengths (512/1024/2048); the one that is element-wise identical is the
    file's real layout. Supported:
    - the old slice-form 2D complex plane: (specnum, fdsize), real/imaginary
      interleaved along the second axis;
    - the 0.2.199-patch16 single-file aq2D pseudo 3D: an (a, b, c) 3D complex array
      with the direct dimension on the last axis, reshaped to (a*b, c)
      (0.2.199-patch25).
    """
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
    # Single-file aq2D: nmrglue reads it as a 3D complex array (a, b, c); direct dim = last axis
    if arr.ndim == 3:
        nrows = int(arr.shape[0] * arr.shape[1])
        fdsize3 = int(arr.shape[2])
        target = arr.reshape(nrows, fdsize3).astype(np.complex64)
        for header in HEADER_CANDIDATES + (0,):
            expect = header + nrows * fdsize3 * COMPLEX_BYTES
            if len(raw) != expect:
                continue
            flat = np.frombuffer(raw, dtype="<f4", count=nrows * fdsize3 * 2, offset=header).astype(
                np.float32
            )
            rows = flat.reshape(nrows, fdsize3 * 2)
            cand = rows[:, :fdsize3] + 1j * rows[:, fdsize3:]
            if cand.shape == target.shape and np.array_equal(cand, target, equal_nan=True):
                return target, fdsize3, nrows, header
        # Header length outside the candidates: infer from file size, keep the nmrglue values
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
        flat = np.frombuffer(raw, dtype="<f4", count=specnum * fdsize * 2, offset=header).astype(
            np.float32
        )
        rows = flat.reshape(specnum, fdsize * 2)
        cand = rows[:, :fdsize] + 1j * rows[:, fdsize:]
        if cand.shape == target.shape and np.array_equal(cand, target, equal_nan=True):
            return cand, fdsize, specnum, header
    return None


def _dc_ratio_time(traces: np.ndarray) -> float:
    """Time-domain DC bias estimate: the median over the top traces of
    |FID mean| / |FID peak|.

    0.2.199-patch29cw: the original frequency-domain bin0 indicator suffers from FID
    truncation / envelope leakage, so almost every real spectrum exceeded the 3%
    threshold (users reported a DC bias on every spectrum). The time-domain mean
    corresponds directly to the constant component removed by POLY -time, which is
    physically clear; VM measurements give 0.07-0.20 on conventional spectra and
    about 0.49 for a real DC bias (sampleC).
    """
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(len(order) * 0.05)), 4), 12)
    top = traces[order[:keep]]
    means = np.abs(np.mean(top, axis=-1))
    peaks = np.max(np.abs(top), axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = means / np.maximum(peaks, 1e-12)
    return float(np.median(ratios))


def _trace_metrics(traces: np.ndarray, n: int) -> dict[str, float]:
    """Compute the DC / first-point / broadband-peak / drift metrics on the top traces."""
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
    first_ratio = float(med_amp[0] / max(med_amp[1], 1e-12)) if n > 2 and med_amp[1] > 0 else 0.0
    nonzero = np.flatnonzero(energy > 0)
    if nonzero.size:
        split = max(int(np.ceil(nonzero.size * 0.2)), 2)
        split = min(split, max(nonzero.size // 2, 1))
        head = traces[nonzero[:split]]
        tail = traces[nonzero[-split:]]
    else:
        head = tail = traces[:1]
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
    """Isolated-spike mask: amplitude >> the local median with steep drops on both
    sides.

    0.2.199-patch29u: the point-by-point Python loop was vectorised -- on 3D NUS data
    (thousands of traces x 2048 points) the original implementation was the main cost
    of the data quality diagnosis.
    """
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
    """Write a single (complex) point back into the byte buffer: real/imag block layout."""
    re_off = header + row * fdsize * COMPLEX_BYTES + col * 4
    struct.pack_into("<f", raw, re_off, float(value.real))
    struct.pack_into("<f", raw, re_off + fdsize * 4, float(value.imag))


def run_direct_diagnostics(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    repair: bool = True,
) -> DirectDiagnosticsResult:
    """Direct-dimension diagnostic gate (run at the end of the Generate-FID step).

    Parameters
    ----------
    work_dir : Path | str
        Processing working directory (the converted fid is under it: a single file,
        ``merged/dataset.fid`` or a ``fid/test*.fid`` slice stream).
    experiment : Experiment
        Data understanding result; used to locate the fid and to tell the uniform/NUS
        gate apart.
    repair : bool, default True
        Whether correctable problems are repaired automatically (bad-point
        replacement, backed up to ``fid_diag_bak/`` before the repair).

    Returns
    -------
    DirectDiagnosticsResult
        ``reports`` (the diagnosis lines shown to the user), ``metrics`` (DC /
        first-point / broadband / drift / bad-point counts), ``apply_poly_time``
        (whether direct-dimension POLY -time is enabled), ``repaired_badpoints`` and
        ``backup_dir``.

    Raises
    ------
    - Never raises: when the layout cannot be parsed or there is no fid, ``reports``
      explains it and the diagnosis is skipped (processing is not blocked).

    Side effects
    ------------
    With ``repair=True`` the fid inside the working directory **is rewritten** (backed
    up to ``fid_diag_bak/`` first) and every change is appended to ``qc_audit.jsonl``;
    a ``diagnostics.json`` summary is written as well.

    Examples
    --------
        result = run_direct_diagnostics(work_dir, experiment)
        if result.apply_poly_time:
            ...  # the final-run script needs POLY -time inserted
    """
    work = Path(work_dir)
    paths = collect_fid_paths(work, experiment)
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
    """Standalone FID diagnostics for arbitrary fid files/folders
    (no Experiment needed; detect-only by default).

    Parameters
    ----------
    paths : list[Path | str]
        The fid files or directories to diagnose (a directory is collected
        automatically as ``*.fid`` / ``test*.fid``).
    repair : bool, default False
        Whether bad points are repaired automatically; this standalone entry point
        **only detects and never modifies** by default.

    Returns
    -------
    DirectDiagnosticsResult
        The same structure as :func:`run_direct_diagnostics`
        (``reports``/``metrics``/``apply_poly_time``...).

    Raises
    ------
    - Never raises: a missing path is reported as "not found" inside ``reports``.

    Side effects
    ------------
    Directories and files are read-only (``repair=False``); with repair enabled the
    behaviour matches :func:`run_direct_diagnostics` (backup + audit record).

    Examples
    --------
        result = run_fid_diagnostics_paths(["process/exp_001.fid"])
    """
    files: list[Path] = []
    seen: set[Path] = set()

    def slice_key(path: Path):
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", path.name)
        )

    for _p in paths:
        _pp = Path(_p)
        if _pp.is_dir():
            candidates = sorted(
                (p for p in _pp.iterdir() if p.is_file() and p.suffix.lower() == ".fid"),
                key=slice_key,
            )
        elif _pp.is_file():
            candidates = [_pp]
        else:
            candidates = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(candidate)
    _work = files[0].parent if files else Path(".")
    result = _diagnose_paths(
        files, _work, repair=repair, is_uniform=False, diagnostic_only=not repair
    )
    if files:
        result.reports.insert(0, tr("FID input: {p0} file(s)", p0=len(files)))
    if not repair:
        result.reports.append(
            tr("Detection only: FID files and processing records were not modified.")
        )
    return result


def _record_bad_point_repair(
    audit: QcAuditLog,
    *,
    file_name: str,
    row: int,
    changed: list[tuple[int, complex, complex]],
    backup_dir: str = "",
) -> None:
    """Write one structured "bad point replaced" audit record (Phase 10).

    Extracted into its own function so it can be unit-tested directly: the record must
    contain the detection rule, the action, the values before/after the change, and
    the row number plus backup location; beyond ``AUDIT_DETAIL_LIMIT`` only the count
    is recorded and the record is marked truncated.
    """
    shown = changed[:AUDIT_DETAIL_LIMIT]
    audit.record(
        QcAction(
            issue_detected=tr("direct dimension peak bad point"),
            location=f"{file_name} row={row}",
            detection_rule=tr(
                "_find_bad_points (single row amplitude distribution): peak determination compared "
                "with adjacent "
                "points",
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
    diagnostic_only: bool = False,
) -> DirectDiagnosticsResult:
    res = DirectDiagnosticsResult()
    if not paths:
        res.ran = False
        res.reports = [
            tr(
                "Data quality diagnosis: converted fid not found, skipped (diagnosis does not "
                "block "
                "processing)",
            )
        ]
        return res
    blocks: list[np.ndarray] = []
    parsed: list[tuple[Path, int, int, int, np.ndarray]] = []
    for path in paths:
        got = _read_fid_raw(path)
        if got is None:
            if diagnostic_only:
                res.notes.append(
                    tr("Could not parse FID, excluded from this check: {p0}", p0=path.name)
                )
            continue
        data, fdsize, specnum, header = got
        if diagnostic_only and blocks and data.shape[-1] != blocks[0].shape[-1]:
            res.ran = False
            res.reports = [
                tr(
                    "FID files have inconsistent direct-dimension lengths; "
                    "they cannot be checked as one slice group: {p0}",
                    p0=path.name,
                )
            ]
            return res
        blocks.append(data)
        parsed.append((path, fdsize, specnum, header, data))
    if not blocks:
        res.ran = False
        res.reports = [
            tr(
                "Data quality diagnosis: fid layout cannot be parsed after conversion, skipped",
            )
        ] + list(res.notes)
        return res
    traces = np.concatenate(blocks, axis=0)
    n = int(traces.shape[-1])
    m = _trace_metrics(traces, n)
    # 2026-09-23 fix: test bool first (isinstance(True, int) is true, so broad_peak
    # would otherwise be serialised as 1.0)
    res.metrics = {}
    for _key, _value in m.items():
        if isinstance(_value, bool):
            res.metrics[_key] = bool(_value)
        else:
            res.metrics[_key] = float(_value)
    reports: list[str] = []

    if m["dc_ratio"] > DC_RATIO_THRESHOLD:
        res.apply_poly_time = True
        reports.append(
            tr(
                "A DC bias is present in the direct dimension (about {p0:.0f}% of "
                "the strongest amplitude); consider POLY -time correction during processing",
                p0=m["dc_ratio"] * 100,
            )
            if diagnostic_only
            else tr(
                "A DC bias is present in the direct dimension (the FID mean is about {p0:.0f}% of "
                "the strongest amplitude); POLY -time auto-correction has been "
                "enabled",
                p0=m["dc_ratio"] * 100,
            )
        )

    repaired = 0
    backup = work / "fid_diag_bak"
    made_backup = False
    audit = QcAuditLog(work)
    if repair:
        repaired = 0
        for path, fdsize, specnum, header, data in parsed:
            # 2026-09-23: reuse the first-pass parse instead of reading every fid a
            # second time (traces is a concatenated copy, so editing data in place
            # cannot skew the metrics already computed)
            raw = bytearray(path.read_bytes())
            for row in range(specnum):
                energy = float(np.sum(np.abs(data[row]) ** 2))
                if energy <= 0:  # NUS plane that was not acquired
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
                    # Phase 10: every automatic change to intermediate data needs a structured
                    # record, not just a log line
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
    elif diagnostic_only:
        detected = sum(int(np.count_nonzero(_find_bad_points(row))) for row in traces)
        res.metrics["detected_badpoints"] = detected
        if detected:
            reports.append(
                tr(
                    "{p0} spike bad point(s) detected and left untouched "
                    "(detection only; consider repair during FID processing)",
                    p0=detected,
                )
            )
    if repaired:
        reports.append(
            tr(
                "{p0} spike bad point(s) detected and replaced automatically (the original fid is "
                "backed up in fid_diag_bak/; re-running fid.com can restore "
                "it)",
                p0=repaired,
            )
        )
        # 2026-09-23: the audit summary is not a "detected issue", so it goes into
        # notes -- the step report used to count it as one extra issue
        audit_summary = audit.summary()
        if audit_summary:
            res.notes.append(audit_summary)
        nonzero = int(np.sum(np.sum(np.abs(traces) ** 2, axis=-1) > 0))
        if repaired / max(nonzero * n, 1) > 0.005:
            reports.append(
                tr(
                    "the bad-point fraction is high; also check ADC / gain stability on the "
                    "acquisition "
                    "side",
                )
            )

    if m["first_point_ratio"] > FIRST_POINT_RATIO:
        reports.append(
            tr(
                "The amplitude of the first sampling point is relatively high ({p0:.2f}× the "
                "second point); group delay / first-point reconstruction is normally handled by "
                "the conversion parameters; if the baseline at high field still tilts, check the "
                "GRPDLY/DSPFVS parameters in "
                "acqus",
                p0=m["first_point_ratio"],
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
    if drift_linewidths(m["drift_pts"], m["fwhm_pts"]) >= DRIFT_LW_NEGLIGIBLE:
        reports.append(format_drift_report_line(m["drift_pts"], m["fwhm_pts"]))
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    # 0.2.199-patch29z: most NUS traces are empty (not acquired), so the top 20%
    # of all traces always hold 100% of the energy and every spectrum false-alarmed --
    # only the energy distribution of non-empty traces is counted
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
    # 0.2.196: potential problems are reported but never auto-processed -- NaN/Inf,
    # all-zero traces, persistently abnormal energy
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
    res.auto_handled = (0 if diagnostic_only else int(res.apply_poly_time)) + (1 if repaired else 0)
    res.clean = not reports
    if not reports:
        reports = [
            tr(
                "Data quality diagnosis: DC offset not detected, peak bad point, first point "
                "abnormality, broadband peak or "
                "drift",
            )
        ]
    res.reports = reports + list(res.notes)
    if made_backup:
        res.backup_dir = str(backup)
    if diagnostic_only:
        return res
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
                    "clean": res.clean,
                    "ran": res.ran,
                    "auto_handled": res.auto_handled,
                    "notes": list(res.notes),
                    # fid signature (name/size/mtime_ns): the Generate-Spectrum step uses it to
                    # decide whether the record still matches the current converted
                    # product; if not, it falls back to re-running the diagnosis
                    "fid_signature": _fid_signature(paths),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except OSError:
        pass
    return res


def _fid_signature(paths: list[Path]) -> list[list[str | int]]:
    """fid product signature (file name + byte size + mtime_ns); tells whether a record
    on disk still describes the current products.
    """
    signature: list[list[str | int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            return []
        signature.append([path.name, int(stat.st_size), int(stat.st_mtime_ns)])
    return signature


def _as_int(value: Any) -> int:
    """Lenient int conversion (a record may hold int/float/str/None)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def read_source_cleanup_history(
    work_dir: Path | str,
    raw_dirs: list[Path | str] | None = None,
) -> dict[str, Any]:
    """Traces of "source sampling bad points were already cleaned" from earlier runs
    (user request 2026-09-23).

    Source bad points are removed from the raw ser + nuslist only in the run where they
    are found (the originals are backed up as .bak); every later re-run (re-conversion
    or reuse of an already converted fid) therefore only sees "no new bad point in this
    step", and the report reads as if the data had never had any. This reads both kinds
    of trace back so the report can state "there were bad points originally, and they
    were removed in an earlier run":

    - records in ``qc_audit.jsonl`` whose ``action_taken`` starts with
      ``removed_from_source_ser_and_nuslist`` (with the bad-point count and a
      coordinate sample);
    - ``ser.bak`` / ``nuslist.bak`` still present in the raw directory (including its
      immediate subdirectories, for multi-segment ``raw/seg_00N/``) when the audit file
      has been cleared.

    Returns
    -------
    dict[str, Any]
        ``{"points": int, "events": int, "sample": list[list[int]],
        "backup_files": list[str]}``; an **empty dict when there is no trace** -- the
        caller uses that to tell "nothing new in this step" from "never had a bad
        point".

    Raises
    ------
    - Never raises: an unreadable record or a missing raw directory means "no trace".

    Side effects
    ------------
    Reads ``qc_audit.jsonl`` and file names under the raw directory only.

    Examples
    --------
        history = read_source_cleanup_history(work, [raw_dir])
    """
    points = 0
    events = 0
    sample: list[list[int]] = []
    try:
        actions = read_audit(work_dir)
    except OSError:
        actions = []
    for action in actions:
        action_text = str(action.action_taken)
        source_removed = action.extra.get("source_removed")
        if not (
            source_removed is True
            or action_text.startswith(SOURCE_CLEAN_ACTION)
            or action_text.startswith(audit_action_label(SOURCE_CLEAN_ACTION))
        ):
            continue
        if source_removed is False:
            continue
        events += 1
        points += max(_as_int(action.before_state.get("bad_points")), 1)
        for point in action.extra.get("bad_points_sample") or []:
            if isinstance(point, (list, tuple)) and len(sample) < 8:
                try:
                    sample.append([int(v) for v in point])
                except (TypeError, ValueError):
                    continue
    backups: list[str] = []
    for raw_dir in raw_dirs or []:
        base = Path(raw_dir)
        for name in ("ser.bak", "nuslist.bak"):
            try:
                candidates = sorted(path for path in base.rglob(name) if path.is_file())
            except OSError:
                candidates = []
            for candidate in candidates:
                try:
                    relative = candidate.relative_to(base).as_posix()
                except ValueError:
                    relative = candidate.name
                backups.append(f"{base.name}/{relative}")
    if not events and not backups:
        return {}
    return {
        "points": points,
        "events": events,
        "sample": sample,
        "backup_files": backups,
    }


def _source_history_line(history: dict[str, Any] | None) -> str:
    """History "source bad points cleaned" trace -> report line; empty when there is no trace."""
    if not isinstance(history, dict) or not history:
        return ""
    points = _as_int(history.get("points"))
    sample: list[str] = []
    for point in history.get("sample") or []:
        if isinstance(point, (list, tuple)):
            sample.append("(" + ", ".join(str(value) for value in point) + ")")
    backups = [str(name) for name in (history.get("backup_files") or [])]
    tail = ""
    if sample:
        tail += tr("; earliest recorded point(s): {p0}", p0=", ".join(sample[:4]))
    if backups:
        tail += tr("; backups still present: {p0}", p0=", ".join(backups[:4]))
    if points > 0:
        return tr(
            "source sampling bad point(s): {p0} point(s) were removed from the raw ser + nuslist "
            "in an earlier run and are not counted again in this step{p1}",
            p0=points,
            p1=tail,
        )
    return tr(
        "source sampling bad point(s): the raw ser + nuslist were cleaned in an earlier run; "
        "this step adds nothing new (details in qc_audit.jsonl){p0}",
        p0=tail,
    )


def _unique_lines(items: list[str]) -> list[str]:
    """Remove exact duplicate lines while preserving the order of first occurrence.

    The correction list combines several sources: source-data anomalies, archived
    ``sweep_width`` and ``carrier`` values, the ``fid_com_corrections`` sidecar, and
    archived ``mode_symbol`` values. The same value or note can arrive through two
    paths: ``patch_fid_com`` warnings enter ``corrections`` through the sidecar, while
    CAR/MODE text is also archived separately by ``carrier_audit`` or ``mode_audit``.
    The reader skips only ``xSW/ySW/zSW/xCAR/yCAR/zCAR/out:`` prefixes, so other CAR
    notes, ``yMODE``, and ``xN`` entries could otherwise appear twice.

    Only byte-for-byte identical lines are removed; content is not rewritten and no
    fuzzy matching is used. Distinct lines remain distinct, and numbering stays
    continuous for cross-reference.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        text = str(item)
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _diagnostics_payload(work: Path) -> dict[str, Any] | None:
    """Raw mapping of ``diagnostics.json`` (missing / unreadable / not a dict -> None)."""
    record = work / DIAGNOSTICS_FILENAME
    if not record.is_file():
        return None
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _result_from_payload(data: dict[str, Any]) -> DirectDiagnosticsResult:
    """Record mapping -> result object (missing fields degrade to "not detected / not handled")."""
    return DirectDiagnosticsResult(
        reports=[str(item) for item in (data.get("reports") or [])],
        metrics=dict(data.get("metrics") or {}),
        apply_poly_time=bool(data.get("apply_poly_time")),
        repaired_badpoints=_as_int(data.get("repaired_badpoints")),
        backup_dir=str(data.get("backup_dir") or ""),
        clean=bool(data.get("clean")),
        ran=bool(data.get("ran", True)),
        auto_handled=_as_int(data.get("auto_handled")),
        notes=[str(item) for item in (data.get("notes") or [])],
    )


def read_diagnostics_record(work_dir: Path | str) -> DirectDiagnosticsResult | None:
    """Read-only access to ``process/diagnostics.json`` (no fid signature check, no re-run,
    no repair).

    The GUI's Generate-FID step report uses it to show exactly what that step **already
    reported**; the fid-signature check that decides whether a record can be reused
    belongs to the processing path (see :func:`load_or_run_direct_diagnostics`) and is
    not done here. Returns None when the record is missing or unreadable.

    Parameters
    ----------
    work_dir : Path | str
        Processing working directory (``process/``).

    Returns
    -------
    DirectDiagnosticsResult | None
        The stored record; None when there is none (the caller then says "not executed
        in this step").

    Raises
    ------
    - Never raises.

    Side effects
    ------------
    Read-only.

    Examples
    --------
        record = read_diagnostics_record(process_dir)
    """
    payload = _diagnostics_payload(Path(work_dir))
    return _result_from_payload(payload) if payload is not None else None


def _read_cached_diagnostics(work: Path, experiment: Experiment) -> DirectDiagnosticsResult | None:
    """Read ``diagnostics.json`` back; None when it is missing, unreadable, or its fid
    signature does not match the current products.
    """
    data = _diagnostics_payload(work)
    if data is None:
        return None
    current = _fid_signature(collect_fid_paths(work, experiment))
    if not current or data.get("fid_signature") != current:
        return None
    return _result_from_payload(data)


def load_or_run_direct_diagnostics(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    progress: Callable[[str], None] | None = None,
) -> DirectDiagnosticsResult:
    """Read-back first: reuse the Generate-FID diagnostic record, else run it.

    2026-09-23 (user request): the diagnosis really runs exactly once, at the end of the
    Generate-FID step, and its conclusion is written to ``process/diagnostics.json``
    (including the fid signature); the Generate-Spectrum step reads that record back and
    reuses it, and neither re-runs the diagnosis nor announces it at the start of the
    step. Only when the record is missing (old working directory / manual route) or its
    signature does not match the current converted product does it fall back to a real
    run, so the behaviour matches the old version.

    Parameters
    ----------
    work_dir : Path | str
        Processing working directory (``process/``).
    experiment : Experiment
        Dataset description (dataset_id / segments / sampling mode are used to locate the
        fid).
    progress : Callable[[str], None] | None
        Optional progress callback; **only used when a real run happens**.

    Returns
    -------
    DirectDiagnosticsResult
        The same structure as :func:`run_direct_diagnostics` (the read-back values when
        the record is reused).

    Raises
    ------
    - Never raises: a failed diagnosis is handled by the caller (this function only
      reads, writes and falls back).

    Side effects
    ------------
    No disk write on reuse; on a fallback run identical to
    :func:`run_direct_diagnostics` (may repair the fid and write ``qc_audit.jsonl`` and
    ``diagnostics.json``).

    Examples
    --------
        result = load_or_run_direct_diagnostics(work, experiment)
        poly_time = result.apply_poly_time  # whether the final run needs POLY -time
    """
    work = Path(work_dir)
    cached = _read_cached_diagnostics(work, experiment)
    if cached is not None:
        return cached
    if progress is not None:
        progress(tr("Data quality diagnosis (direct dimension FID memory scan)"))
    result = run_direct_diagnostics(work, experiment)
    if progress is not None and result.reports:
        progress(tr("== data quality diagnosis =="))
        for index, report in enumerate(result.reports, 1):
            progress(f"{index}. {report}")
    return result


def drift_linewidths(drift_pts: float, fwhm_pts: float) -> float:
    """Return direct-dimension frequency drift in linewidths: shift points / FWHM points.

    The ratio (not the product) keeps a 12-point shift over a 10-point FWHM at 1.2
    linewidths. The denominator is floored at ``1e-9`` when FWHM cannot be measured.
    """
    return float(drift_pts) / max(float(fwhm_pts), 1e-9)


def format_drift_report_line(drift_pts: float, fwhm_pts: float, *, segmented: bool = False) -> str:
    """Render a direct-dimension field-drift screening line by apparent shift in linewidths.

    The report distinguishes four ranges and retains both the shift in points and its
    linewidth ratio:

    - ``< 0.5`` linewidths: normal sampling variation; no action needed.
    - ``0.5–3``: acceptable but worth monitoring; do not recommend reacquisition.
    - ``3–10``: processing cannot fully remove the drift; check temperature/lock and
      consider reacquisition.
    - ``>= 10``: the spectra barely overlap; recommend reacquisition.
    """
    mult = drift_linewidths(drift_pts, fwhm_pts)
    if mult < DRIFT_LW_NEGLIGIBLE:
        return tr(
            "Field-drift screening: the apparent direct-dimension peak-position change between "
            "the early and late non-empty traces is about "
            "{p0:.1f} points ({p1:.1f} linewidths); at this magnitude it is a normal sampling "
            "fluctuation and needs no action",
            p0=drift_pts,
            p1=mult,
        )
    if mult < DRIFT_LW_LARGE:
        line = tr(
            "Field-drift screening: the apparent direct-dimension peak-position change between "
            "the early and late non-empty traces is about "
            "{p0:.1f} points ({p1:.1f} linewidths), acceptable but should be monitored (no "
            "re-acquisition required)",
            p0=drift_pts,
            p1=mult,
        )
        if segmented:
            line += tr("; inter-part field-drift correction may be attempted for segmented data")
        return line
    if mult < DRIFT_LW_SEVERE:
        return tr(
            "Field-drift screening: the apparent direct-dimension peak-position change between "
            "the early and late non-empty traces is "
            "about {p0:.1f} points ({p1:.1f} linewidths), processing cannot remove it "
            "completely if it is true field drift; check temperature control / lock and the raw "
            "data before considering re-acquisition",
            p0=drift_pts,
            p1=mult,
        )
    return tr(
        "Field-drift screening: the apparent direct-dimension peak-position change between the "
        "early and late non-empty traces is about "
        "{p0:.1f} points ({p1:.1f} linewidths), far beyond the linewidth; the two periods barely "
        "overlap. Confirm temperature control / lock and the raw data; if this is true field "
        "drift, "
        "re-acquire the spectrum",
        p0=drift_pts,
        p1=mult,
    )


def _normalized_diagnostic_reports(diagnostics: Any, *, segmented: bool = False) -> list[str]:
    """FID-layer report lines: old records with the wrong "120.0 linewidths" value are
    recomputed from the metrics.

    2026-09-24 real-bug fix: the linewidth multiple is drift points / FWHM (points) but
    the old code multiplied them (real data d_019: 12 points / 10 points = 1.2
    linewidths -> the report said "120.0 linewidths"). Lines already written to
    ``diagnostics.json`` are re-rendered in place from the recorded metrics: the number
    is corrected while the wording and the conclusion stay the same, so no re-run is
    needed.
    """
    metrics = getattr(diagnostics, "metrics", None) or {}
    drift = _as_float((metrics or {}).get("drift_pts"), 0.0)
    fwhm = _as_float((metrics or {}).get("fwhm_pts"), 0.0)
    lines: list[str] = []
    for report in getattr(diagnostics, "reports", []) or []:
        item = str(report)
        if fwhm > 0 and ("个线宽" in item or "linewidths" in item.lower()):  # i18n: keep
            item = format_drift_report_line(drift, fwhm, segmented=segmented)
        lines.append(item)
    return lines


def _part_number_from_text(text: str) -> int | None:
    """Extract the part number from a per-part report line (``part 2`` etc.); None if absent."""
    import re

    # Matches the part-number wording already rendered in records (Chinese/English),
    # not UI text
    for pattern in (r"第\s*(\d+)\s*段", r"part\s+(\d+)"):  # i18n: keep
        found = re.search(pattern, text)
        if found:
            try:
                return int(found.group(1))
            except ValueError:
                return None
    return None


def _measurement_note_needed(round_record: dict[str, Any], lines: list[str]) -> bool:
    """Does this record need the note "no measurable drift != different data sources"?"""
    if round_record.get("trusted_parts") == []:
        return True
    for text in lines:
        # Matches already-rendered lines (Chinese or English), not UI text
        if (  # i18n: keep
            "量不出可信漂移" in text
            or "too low to measure a drift" in text
            or "no drift could be measured" in text
            or "not stable enough" in text
            or "not reproducible" in text
            or "too few comparable trace" in text
        ):
            return True
        # the three new phrasings of the same rule (all on the marked line below,
        # which keeps the bare-Chinese guard quiet)
        if any(token in text for token in ("测不出", "不可复现", "做不了稳定性")):  # i18n: keep
            return True
    return False


def _normalized_skip_lines(round_record: dict[str, Any]) -> list[str]:
    """Per-part skip reasons: the old "the two parts are not the same experiment" wording
    is replaced by the new criterion.

    2026-09-24 (user 2026-09-24: "the two parts look like different experiments, why is
    that still shown"): low-SNR data from the same experiment also has no comparable
    common signal (real data d_018: per-trace SNR 2-3, and the cross-part correlation
    median at the same plane index equals the within-part baseline), while the old
    wording read like a factual statement about the **data source** and contradicted the
    segment-consistency conclusion in the same report (different NS, everything else
    identical = the same experiment). Only the wording changes here: the numbers still
    come from the recorded measurements and the conclusion is still "no correction
    applied", with one note about the criterion appended.
    """
    from workflow.field_drift import is_identity_claim, no_measurement_note

    quality = list(round_record.get("quality") or [])
    # 2026-09-24: ``row_mad_hz`` (per-trace median absolute deviation) was replaced by
    # ``uncertainty_hz`` (the spread of random half-split resampling)
    row_mad = list(round_record.get("uncertainty_hz") or round_record.get("row_mad_hz") or [])
    point_hz = _as_float(round_record.get("point_hz"), 0.0)
    lines: list[str] = []
    for item in round_record.get("skipped") or []:
        text = str(item)
        if not is_identity_claim(text):
            lines.append(text)
            continue
        part = _part_number_from_text(text)
        if part is None:
            lines.append(
                tr(
                    "a part's per-trace signal-to-noise is too low to measure a drift; no drift "
                    "could "
                    "be measured, so no correction was applied to that part",
                )
            )
            continue
        index = part - 1
        ratio = quality[index] if 0 <= index < len(quality) else None
        mad = row_mad[index] if 0 <= index < len(row_mad) else None
        if ratio is None or mad is None:
            lines.append(
                tr(
                    "part {p0}: the per-trace signal-to-noise is too low to measure a drift; "
                    "no drift "
                    "could be measured, so no correction was applied",
                    p0=part,
                )
            )
            continue
        lines.append(
            tr(
                "part {p0}: the per-trace signal-to-noise is too low to measure a drift "
                "(correlation "
                "peak only {p1:.1f}x the background, per-trace estimates disagree by {p2:.1f} Hz, "
                "{p3:.2f} FFT point(s)); no drift could be measured, so no correction was applied",
                p0=part,
                p1=_as_float(ratio, 0.0),
                p2=_as_float(mad, 0.0),
                p3=(_as_float(mad, 0.0) / point_hz) if point_hz else 0.0,
            )
        )
    if _measurement_note_needed(round_record, lines):
        lines.append(no_measurement_note())
    return lines


def _as_float(value: Any, default: float) -> float:
    """Lenient float conversion (a recorded field may be None / a string / missing)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _drift_report_lines(record: dict[str, Any] | None) -> tuple[list[str], bool, bool]:
    """Field-drift record -> (report lines, whether a shift was really applied, whether it
    was checked and needs no correction).

    2026-09-24 (user: "if the linewidth criterion is more reasonable, change it"): the
    criterion basis is the record's ``criterion_basis`` (``linewidth`` = max(1.5 Hz,
    0.2x linewidth), ``points`` = the fallback when no linewidth can be measured) and the
    report states the basis that **actually took effect** (naming the wrong one makes the
    same report contradict itself). The late 2026-09-23 revision (triggered by d_018)
    fixed all the "unreasonable report" places at once:

    - when the check never ran (``checked=False``: direct-dimension SW/OBS unknown / a
      part without a converted product / ``segment_shift_hz`` given manually) it is
      stated as-is with its reason and no check mark is given (the old code returned no
      line at all, which read as "no correction needed");
    - parts measured below one FFT point (``below_resolution_parts``) say "cannot be
      resolved" -- neither treated as a numerical drift nor written as "within the
      criterion" (the old implementation called 4.0 Hz "within criterion");
    - segment consistency (different TD/SW/O1 = merging refused; different NS = a warning
      but **not** a weighting problem) and the merge statement ("N parts merged, X of them
      uncorrected") are both listed in the report;
    - when no part yields an offset, the per-part skip reasons are still listed as before.
    """
    if not isinstance(record, dict) or not record:
        return [], False, False
    notes: list[str] = []
    consistency = record.get("segment_consistency")
    if isinstance(consistency, dict):
        from workflow.field_drift import consistency_report_lines

        notes += consistency_report_lines(consistency)
    if not record.get("checked"):
        reason = str(record.get("reason") or "")
        applied_hz = record.get("applied_hz") or {}
        if reason == "manual-segment-shift" and applied_hz:
            parts_text = ", ".join(
                tr("part {p0}: {p1} Hz", p0=key, p1=f"{float(value):+.4f}")
                for key, value in sorted(
                    applied_hz.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0
                )
            )
            note = tr(
                "inter-part field drift: corrected manually - {p0} (part 1 is the reference, "
                "not shifted); each part's fid.com was rewritten (PS -rs) and re-converted",
                p0=parts_text,
            )
            return [note, *notes], True, False
        note = {
            "direct-axis-unknown": tr(
                "inter-part field drift: not checked (the direct-dimension SW/OBS is unknown)",
            ),
            "missing-part-fid": tr(
                "inter-part field drift: not checked (a part has no converted fid)",
            ),
            "manual-segment-shift": tr(
                "inter-part field drift: you set per-part offsets but none of them could be "
                "applied (no part reported a rewrite); check the conversion log for the reason - "
                "the parts are as they were acquired"
            ),
            "manual-only": tr(
                "inter-part field drift: not corrected (the automatic check was removed; set the "
                'per-part offsets with the "Inter-part field drift" button in the Generate FID '
                "step if you need one)",
            ),
        }.get(
            reason,
            tr("inter-part field drift: the check did not complete; no shift was applied"),
        )
        return [note, *notes], False, False
    rounds = [item for item in (record.get("rounds") or []) if isinstance(item, dict)]
    first = rounds[0] if rounds else {}
    offsets = list(first.get("offsets_hz") or [])
    ppms = list(first.get("offsets_ppm") or [])
    reference = _as_int(record.get("reference")) or 1
    criterion = _as_float(first.get("criterion_hz"), _as_float(record.get("hz_min"), 1.5))
    point_hz = _as_float(first.get("point_hz"), 0.0)
    # 2026-09-24: the criterion became max(1.5 Hz, 0.2x linewidth), so the report must
    # state the basis that **actually took effect**
    linewidth_hz = _as_float(first.get("linewidth_hz"), 0.0)
    basis = str(first.get("criterion_basis") or "")
    corrected: list[int] = []
    for part in record.get("corrected_parts") or []:
        try:
            corrected.append(int(part))
        except (TypeError, ValueError):
            continue
    below: list[int] = []
    for part in first.get("below_resolution_parts") or []:
        try:
            below.append(int(part))
        except (TypeError, ValueError):
            continue
    skipped = _normalized_skip_lines(first)
    merge = record.get("merge") if isinstance(record.get("merge"), dict) else {}
    # 2026-09-24 review B5: an inconsistent rollback makes the backend refuse the merge
    # (record["blocked"]), so the report must not say "merged"
    blocked = bool(record.get("blocked")) or bool(merge.get("blocked"))
    merged_parts = _as_int(merge.get("parts")) or _as_int(record.get("parts")) or 0
    uncorrected = [
        int(part) for part in (merge.get("uncorrected_parts") or []) if str(part).isdigit()
    ]
    merge_notes: list[str] = []
    if blocked:
        merge_notes.append(
            tr(
                "inter-part field drift: the per-part corrections could not be rolled back "
                "consistently, so merging was refused - these parts were NOT merged and are not "
                "on one frequency axis",
            )
        )
    elif merged_parts:
        if uncorrected:
            merge_notes.append(
                tr(
                    "inter-part field drift: {p0} part(s) were merged, of which {p1} were not "
                    "corrected ({p2}) - the parts that exceeded the criterion were all merged "
                    "without a frequency shift so the merged data stays consistent",
                    p0=merged_parts,
                    p1=len(uncorrected),
                    p2=", ".join(str(part) for part in uncorrected),
                )
            )
        else:
            merge_notes.append(
                tr(
                    "inter-part field drift: {p0} part(s) were merged, all of them on the same "
                    "(corrected or untouched) frequency axis",
                    p0=merged_parts,
                )
            )
    below_lines: list[str] = []
    if below:
        shown: list[str] = []
        for part in below:
            index = part - 1
            hz = offsets[index] if 0 <= index < len(offsets) else None
            if hz is None:
                shown.append(str(part))
                continue
            if point_hz:
                shown.append(
                    f"{part}: {float(hz):+.2f} Hz ({abs(float(hz)) / point_hz:.2f} point(s))"
                )
            else:
                shown.append(f"{part}: {float(hz):+.2f} Hz")
        below_lines.append(
            tr(
                "inter-part field drift: part(s) {p0} measured offsets below one FFT point "
                "({p1:.1f} Hz/point, criterion {p2:.2f} Hz) - the per-trace signal-to-noise "
                "cannot resolve a shift this small, so no correction was applied to them",
                p0="; ".join(shown),
                p1=point_hz,
                p2=criterion,
            )
        )
    if corrected:
        parts: list[str] = []
        detail: list[str] = []
        for part in corrected:
            parts.append(str(part))
            index = part - 1
            hz = offsets[index] if 0 <= index < len(offsets) else None
            ppm = ppms[index] if 0 <= index < len(ppms) else None
            if hz is None:
                continue
            if ppm is None:
                detail.append(f"{float(hz):+.2f} Hz")
            else:
                detail.append(f"{float(hz):+.2f} Hz ({float(ppm):+.4f} ppm)")
        trusted_parts = rounds[-1].get("trusted_parts") if rounds else None
        residual = 0.0
        for index, value in enumerate((rounds[-1].get("offsets_hz") if rounds else []) or []):
            if value is None:
                continue
            if isinstance(trusted_parts, list) and (index + 1) not in trusted_parts:
                continue
            residual = max(residual, abs(_as_float(value, 0.0)))
        if blocked:
            line = tr(
                "inter-part field drift: part(s) {p0} vs part {p1} exceeded the {p2:.2f} Hz "
                "criterion (offset {p3}), but the all-or-nothing rollback failed afterwards, so "
                "merging was refused; the converted parts are NOT on one frequency axis",
                p0=", ".join(parts),
                p1=reference,
                p2=criterion,
                p3=", ".join(detail),
            )
        elif linewidth_hz and basis != "points":
            line = tr(
                "inter-part field drift: part(s) {p0} vs part {p1} exceeded the {p2:.2f} Hz "
                "criterion ({p3:.2f} linewidth(s) at {p4:.1f} Hz line width; offset {p5}); each "
                "part's fid.com was rewritten (PS -rs) and re-converted (largest residual "
                "{p6:.2f} Hz)",
                p0=", ".join(parts),
                p1=reference,
                p2=criterion,
                p3=criterion / linewidth_hz,
                p4=linewidth_hz,
                p5=", ".join(detail),
                p6=residual,
            )
        elif point_hz:
            line = tr(
                "inter-part field drift: part(s) {p0} vs part {p1} exceeded the {p2:.2f} Hz "
                "criterion ({p3:.2f} FFT point(s) at {p4:.1f} Hz/point; offset {p5}); each part's "
                "fid.com was rewritten (PS -rs) and re-converted (largest residual {p6:.2f} Hz)",
                p0=", ".join(parts),
                p1=reference,
                p2=criterion,
                p3=criterion / point_hz,
                p4=point_hz,
                p5=", ".join(detail),
                p6=residual,
            )
        else:
            # Old records (criterion still in Hz only) keep their wording; no point count invented
            line = tr(
                "inter-part field drift: part(s) {p0} vs part {p1} exceeded the {p2:g} Hz "
                "criterion (offset {p3}); each part's fid.com was rewritten (PS -rs) and "
                "re-converted (largest residual {p4:.2f} Hz)",
                p0=", ".join(parts),
                p1=reference,
                p2=criterion,
                p3=", ".join(detail),
                p4=residual,
            )
        return [line, *merge_notes, *below_lines, *notes, *skipped], True, False
    if below:
        return [*below_lines, *merge_notes, *notes, *skipped], False, False
    trusted = first.get("trusted_parts")
    if isinstance(trusted, list):
        measured = []
        for part in trusted:
            try:
                index = int(part) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(offsets) and offsets[index] is not None:
                measured.append(abs(_as_float(offsets[index], 0.0)))
    else:
        measured = [abs(_as_float(value, 0.0)) for value in offsets if value is not None]
    # Parts that passed the "measurable + reproducible" gate and **measured over the
    # criterion** (part numbers count from 1).
    # 2026-09-24 re-check: if such a part was not corrected (the fid.com rewrite failed /
    # was rolled back), the report **must not** say "largest offset X Hz within the
    # criterion" and must not give a check mark -- the merged data still carries them.
    assessed_over = [
        int(part)
        for part in (trusted if isinstance(trusted, list) else [])
        if 0 <= int(part) - 1 < len(offsets)
        and offsets[int(part) - 1] is not None
        and abs(_as_float(offsets[int(part) - 1], 0.0)) > criterion
    ]
    if not measured:
        if isinstance(trusted, list):
            # The criterion gate rejected every part (not the same experiment / the estimate
            # failed): say "no reliable measurement" rather than the blanket "no part could
            # be measured" (real data d_018: all four parts produced numbers, none of them
            # trustworthy)
            line = tr(
                "inter-part field drift: no part gave a reliable measurement against part {p0} "
                "(see the per-part reasons below); no frequency shift was applied",
                p0=reference,
            )
        else:
            line = tr(
                "inter-part field drift: no part could be measured against part {p0}; no shift "
                "was applied",
                p0=reference,
            )
        return [line, *merge_notes, *notes, *skipped], False, False
    if assessed_over:
        over_detail = ", ".join(
            f"{float(offsets[part - 1]):+.2f} Hz"
            for part in assessed_over
            if 0 <= part - 1 < len(offsets) and offsets[part - 1] is not None
        )
        return (
            [
                tr(
                    "inter-part field drift: part(s) {p0} measured offsets over the {p1:.2f} "
                    "Hz "
                    "criterion ({p2}) but the correction did not happen (the fid.com rewrite "
                    "failed "
                    "or was rolled back); the merged data still carries those offsets",
                    p0=", ".join(str(part) for part in assessed_over),
                    p1=criterion,
                    p2=over_detail or "-",
                ),
                *merge_notes,
                *notes,
                *skipped,
            ],
            False,
            False,
        )
    if linewidth_hz and basis != "points":
        within = tr(
            "inter-part field drift: largest offset {p0:.2f} Hz vs part {p1} is within the "
            "{p2:.2f} Hz criterion ({p3:.2f} linewidth(s) at {p4:.1f} Hz line width); no shift "
            "applied",
            p0=max(measured),
            p1=reference,
            p2=criterion,
            p3=criterion / linewidth_hz,
            p4=linewidth_hz,
        )
    elif point_hz:
        within = tr(
            "inter-part field drift: largest offset {p0:.2f} Hz vs part {p1} is within the "
            "{p2:.2f} Hz criterion ({p3:.2f} FFT point(s) at {p4:.1f} Hz/point); no shift "
            "applied",
            p0=max(measured),
            p1=reference,
            p2=criterion,
            p3=criterion / point_hz,
            p4=point_hz,
        )
    else:
        within = tr(
            "inter-part field drift: largest offset {p0:.2f} Hz vs part {p1} is within the "
            "{p2:g} Hz criterion; no shift applied",
            p0=max(measured),
            p1=reference,
            p2=criterion,
        )
    return [within, *merge_notes, *notes, *skipped], False, True


def format_fid_step_report(
    *,
    diagnostics: DirectDiagnosticsResult | None = None,
    source_bad_points: list[tuple[int, ...]] | None = None,
    source_removed: bool = False,
    source_history: dict[str, Any] | None = None,
    field_drift: dict[str, Any] | None = None,
    sweep_width: list[dict[str, Any]] | None = None,
    carrier: dict[str, Any] | None = None,
    corrections: list[str] | None = None,
    mode_symbol: dict[str, Any] | None = None,
    parts: int | None = None,
) -> list[str]:
    """Generate-FID step quality report lines (detection + what was done).

    2026-09-23 (user request): the diagnosis conclusion moved to the very end of the
    Generate-FID step and every item states "what was detected / how it was handled";
    this round adds three things: (1) **source bad points cleaned in an earlier run are
    reported too** (``source_history`` -- otherwise a re-run only says "no new bad point
    in this step", which reads as if the data never had any); (2) a field-drift check
    that "did not run / could not measure anything" is no longer written as "within the
    criterion"; (3) the audit summary is no longer counted in "N issues detected".
    Structure:

    - ``== data quality report (FID generation) ==``
    - conversion and merging: source sampling bad points (from this step / cleaned
      earlier) and inter-part field drift;
    - FID inspection: the post-conversion direct-dimension memory scan (N issues
      detected + M auto-processed + notes).

    Parameters
    ----------
    diagnostics : DirectDiagnosticsResult | None
        FID-layer scan result; None means this step did not run it (stated explicitly in
        the report).
    source_bad_points : list[tuple[int, ...]] | None
        Coordinates of the sampling bad points removed from the source in this step
        (empty/None = nothing new in this step).
    source_removed : bool
        True when they really were removed from the raw ser/nuslist; with points present
        and False the wording says "zeroed in the generated fid instead".
    source_history : dict[str, Any] | None
        The result of :func:`read_source_cleanup_history` -- source bad points that were
        cleaned in earlier runs.
    field_drift : dict[str, Any] | None
        The content of ``process/field_drift.json`` (multi-part data only).
    sweep_width : list[dict[str, Any]] | None
        The spectral-width basis record from ``process/*.fid.conversion.json``
        (2026-09-24). Non-empty when the width was re-decided (``SW_h`` contradicting
        ``SW(ppm) x SFO1`` -> the ppm basis) or when ``SW_h`` was missing; it changes the
        ``-xSW/-ySW/-zSW`` of the conversion script, so it counts as a "correction"
        rather than as "nothing to correct".
    carrier : dict[str, Any] | None
        The carrier (CAR) basis record from ``process/*.fid.conversion.json``
        (2026-09-24): ``bruker -AUTO`` keeps the "water peak (with TE) + gamma ratio"
        basis for CAR by default, and the backend falls back to the acqus ``O1/BF1`` when
        it does not apply. ``fix_lines`` is listed in the "conversion" corrections (the
        value really changed / the key was missing) and ``summary`` explains the current
        basis in one note line.
    corrections : list[str] | None
        The fid.com parameter corrections from ``process/*.fid.conversion.json``
        (2026-09-24): which parameters were really changed during conversion (such as
        ``yMODE``/``xLAB``). They used to be printed line by line in the log only and now
        go into the report's correction list as well (sweep width and carrier have their
        own records, which the backend skips when writing the sidecar).
    mode_symbol : dict[str, Any] | None
        The "mode/symbol" record from ``process/*.fid.conversion.json`` (2026-09-24
        second revision): dimensions that cannot be decided (``F1EA`` / an unlisted
        sequence / a missing pulse program / a family conflict) get no ``FT -neg``, and
        the same reminder is listed in the report here (the same text as the conversion
        log and the import warning).
    parts : int | None
        How many parts this data has. With ``< 2`` (a single dataset, no merging needed)
        "merging" and "inter-part field drift" are no longer mentioned -- user
        2026-09-24: "such a report looks strange when no merging is needed". None = it
        cannot be decided, so the multi-part wording is used (as before).

    Returns
    -------
    list[str]
        Report lines (without a timestamp prefix; the caller writes them to the log /
        report channel).

    Raises
    ------
    - Never raises: missing fields degrade to "not detected / not checked" wording.

    Side effects
    ------------
    Read-only (no disk writes, no fid changes).

    Examples
    --------
        logs += format_fid_step_report(
            diagnostics=result,
            source_history=read_source_cleanup_history(work, [raw]),
            field_drift=read_field_drift_record(work),
            parts=len(experiment.segments) if experiment.segments else 1,
        )
    """
    lines: list[str] = [tr("== data quality report (FID generation) ==")]
    fixes: list[str] = []
    points = list(source_bad_points or [])
    if points:
        shown = ", ".join("(" + ", ".join(str(v) for v in point) + ")" for point in points)
        if source_removed:
            fixes.append(
                tr(
                    "source sampling bad point(s) {p0}: removed from the raw ser + nuslist "
                    "(originals backed up as .bak)",
                    p0=shown,
                )
            )
        else:
            fixes.append(
                tr(
                    "source sampling bad point(s) {p0}: cannot be removed at the source "
                    "(the raw ser layout is unusable); the generated fid was zeroed instead",
                    p0=shown,
                )
            )
    # 2026-09-23: no bad point removed in this step != the data never had one --
    # report the historical trace as it is
    history_line = "" if points else _source_history_line(source_history)
    # 2026-09-24: when the spectral-width basis (SW_h vs SW(ppm) x SFO1) is re-decided
    # during conversion it is listed in the "conversion" corrections together with the
    # source bad points / field drift -- it really changes -ySW etc. in fid.com, so it is
    # not "nothing to correct".
    for item in sweep_width or []:
        note = str(item.get("note") or "")
        if note:
            fixes.append(note)
    #
    #
    carrier = carrier or {}
    carrier_summary = str(carrier.get("summary") or "")
    carrier_notes = [str(item) for item in (carrier.get("notes") or []) if str(item)]
    correction_notes: list[str] = []
    for item in corrections or []:
        text = str(item)
        if not text:
            continue
        if is_advisory_line(text):
            correction_notes.append(text)
        else:
            fixes.append(text)
    mode_notes = [str(item) for item in ((mode_symbol or {}).get("lines") or []) if str(item)]
    multi_part = parts is None or int(parts) >= 2
    if multi_part:
        drift_lines, drift_corrected, drift_all_clear = _drift_report_lines(field_drift)
    else:
        drift_lines, drift_corrected, drift_all_clear = [], False, False
    if drift_corrected:
        fixes += drift_lines
        drift_notes: list[str] = []
    else:
        drift_notes = list(drift_lines)
    lines.append(tr("◆ conversion and merging"))
    for index, item in enumerate(_unique_lines(fixes), 1):
        lines.append(f"   {index}. {item}")
    section_clean = not fixes and not history_line
    if not multi_part:
        pass
    elif field_drift is None:
        drift_notes = [
            tr(
                "inter-part field drift: this step has no check record (no field_drift.json), so "
                "whether the parts share one frequency axis is unknown",
            ),
            *drift_notes,
        ]
        section_clean = False
    if multi_part and field_drift is not None and not drift_corrected and not drift_all_clear:
        # the field-drift check did not run / no part could be measured: no check mark
        section_clean = False
    if section_clean:
        lines.append(
            "   "
            + (
                tr("✓ source sampling points and inter-part field drift: nothing needed correcting")
                if multi_part
                else tr("✓ source sampling points: nothing needed correcting")
            )
        )
    if history_line:
        lines.append(f"   · {history_line}")
    for item in _unique_lines(
        [
            *([carrier_summary] if carrier_summary else []),
            *carrier_notes,
            *mode_notes,
            *correction_notes,
        ]
    ):
        lines.append(f"   · {item}")
    for item in _unique_lines(drift_notes):
        lines.append(f"   · {item}")
    lines.append(tr("◆ FID inspection (direct-dimension memory scan after conversion)"))
    diagnostic_reports: list[str] = []
    diagnostic_auto = 0
    diagnostic_ran = diagnostics is not None and bool(diagnostics.ran)
    if diagnostics is None or not diagnostics.ran:
        lines.append("   " + tr("⚠ FID inspection did not run in this step"))
        for note in list(getattr(diagnostics, "reports", []) or []):
            lines.append(f"      · {note}")
    elif diagnostics.clean:
        lines.append(
            "   "
            + tr(
                "✓ no DC offset, spike bad point, abnormal first point, broadband peak or "
                "drift detected"
            )
        )
    else:
        diagnostic_reports = _normalized_diagnostic_reports(
            diagnostics, segmented=parts is not None and parts >= 2
        )
        diagnostic_auto = int(diagnostics.auto_handled) or (
            int(bool(diagnostics.apply_poly_time)) + int(diagnostics.repaired_badpoints > 0)
        )
        diagnostic_auto = min(diagnostic_auto, len(diagnostic_reports))
        if diagnostic_auto:
            suffix = tr("(automatically processed: {p0})", p0=diagnostic_auto)
        else:
            suffix = tr("(not processed automatically)")
        lines.append(
            tr(
                " ⚠ {p0} issue(s) detected {p1}",
                p0=len(diagnostic_reports),
                p1=suffix,
            )
        )
        for index, report in enumerate(diagnostic_reports, 1):
            lines.append(f"      {index}. {report}")
        for note in list(diagnostics.notes):
            lines.append(f"      · {note}")
    #
    #
    pending_count = max(0, len(diagnostic_reports) - diagnostic_auto)
    if not diagnostic_ran:
        pending_count += 1
    if multi_part and (field_drift is None or not (drift_corrected or drift_all_clear)):
        pending_count += 1
    fix_count = len(_unique_lines(fixes))
    if not diagnostic_ran:
        fid_headline = tr("FID inspection did not run")
    elif diagnostic_reports:
        fid_headline = tr(
            "FID inspection found {p0} issue(s), {p1} handled automatically",
            p0=len(diagnostic_reports),
            p1=diagnostic_auto,
        )
    else:
        fid_headline = tr("FID inspection found no issues")
    review_headline = (
        tr("{p0} item(s) still need review", p0=pending_count)
        if pending_count
        else tr("no action needed")
    )
    lines.insert(
        1,
        "   "
        + tr(
            "summary: {p0}; {p1}; conversion settings changed in {p2} place(s)",
            p0=fid_headline,
            p1=review_headline,
            p2=fix_count,
        ),
    )
    return lines


def read_sweep_width_audit(work_dir: Path | str) -> list[dict[str, Any]]:
    """Read the spectral-width basis record from the conversion record (``sweep_width``
    in ``*.fid.conversion.json``).

    2026-09-24: when acqus ``SW_h`` contradicts ``SW(ppm) x SFO1`` the sweep width is
    taken from the ppm basis (see ``core.data.bruker_reader.resolve_sweep_width``) and
    the adopted/original values plus the source are written into the conversion record;
    reading it back here makes the Generate-FID step report say the same thing as the
    log.
    """
    work = Path(work_dir)
    try:
        for path in sorted(work.glob("*.fid.conversion.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items = payload.get("sweep_width") if isinstance(payload, dict) else None
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    except OSError:
        return []
    return []


def read_carrier_audit(work_dir: Path | str) -> dict[str, Any]:
    """Read the carrier (CAR) basis record from the conversion record (``carrier`` in
    ``*.fid.conversion.json``).

    2026-09-24 (software design fixed by the user): ``bruker_workflow.carrier_audit``
    writes per-dimension ``acquisition_center`` (acqus ``O1/BF1``, the adopted value) /
    ``configured_target`` (the script's current value) / ``delta_ppm`` / ``status``
    (``REFERENCE_*``) into the record and provides ``summary`` / ``notes`` / ``blocks``
    for the report. Reading it back here makes the Generate-FID step report, the log and
    the conversion record carry the same text.
    """
    work = Path(work_dir)
    try:
        for path in sorted(work.glob("*.fid.conversion.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            carrier = payload.get("carrier") if isinstance(payload, dict) else None
            if isinstance(carrier, dict) and carrier.get("dims"):
                carrier = dict(carrier)
                carrier["fix_lines"] = []
                return carrier
    except OSError:
        return {}
    return {}


def is_advisory_line(line: str) -> bool:
    """Return whether a line is an advisory rather than a script correction.

    ``patch_fid_com`` warnings mix actual changes (for example, ``ySW: fid.com=… →
    acqus=… (corrected)``, ``xLAB: …``, or ``out: …``) with notices. CAR notices
    describe the water-peak plus gyromagnetic-ratio convention, the CAR reference, or
    a mismatch between CAR and spectrum center; CAR has not been written back since
    2026-09-28. ``-neg`` notices report whether the mode is present; sign adjustment
    is controlled by processing-time FT flags, not by conversion-time script edits.

    These notices can enter the numbered correction list through
    ``fid_com_corrections.json`` and also appear in ``carrier_notes`` or ``mode_notes``.
    Classifying them as notes prevents duplicate messages and keeps the numbered list
    limited to actual script changes.

    Generated lines have stable wording but no common machine-readable marker.
    ``(corrected)`` is definitive and is checked first; otherwise CAR, ``-neg``, or
    explicit unchanged/no-geometry-check wording identifies a notice. This includes
    NUS axes intentionally kept as-is without geometry checks, cases where sign
    conversion is deferred to FT flags, and canonical ``-N`` modes retained as
    written. Separating unchanged notices from corrections makes actual changes such
    as ``xLAB`` or ``sampleCount`` easier to find.
    """
    text = str(line or "").strip()
    if not text:
        return False
    if "已修正" in text or "(corrected)" in text:  # i18n: keep
        return False
    if is_neg_notice(text):
        return True
    if "CAR" in text or "口径" in text:  # i18n: keep
        return True
    return any(
        marker in text
        for marker in (
            "kept as-is",
            "kept as written",
            "not at conversion",
            "no geometry check",
            "保留原值",  # i18n: keep
            "未做几何核对",  # i18n: keep
            "转换期不做",  # i18n: keep
        )
    )


def read_mode_symbol_audit(work_dir: Path | str) -> dict[str, Any]:
    """Read the "mode/symbol" record from the conversion record (``mode_symbol`` in
    ``*.fid.conversion.json``).

    2026-09-24 second revision: dimensions that cannot be decided (``F1EA`` / an
    unlisted sequence / a missing pulse program / a family conflict) get no ``FT -neg``,
    and the same reminder is given in the log, the report and the import warning. Its
    single source is ``core.experiment.pulse_pathways.review_line``; the backend writes
    ``lines`` + ``dims`` into the conversion provenance, and reading it back here makes
    the Generate-FID step report and the log carry the same text.
    """
    work = Path(work_dir)
    try:
        for path in sorted(work.glob("*.fid.conversion.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            symbol = payload.get("mode_symbol") if isinstance(payload, dict) else None
            if isinstance(symbol, dict) and (symbol.get("lines") or symbol.get("dims")):
                return symbol
    except OSError:
        return {}
    return {}


def read_fid_com_corrections(work_dir: Path | str) -> list[str]:
    """Read the fid.com parameter correction list from the conversion record
    (``*.fid.conversion.json``, 2026-09-24).

    2026-09-24 (user): "the log report of the Generate-FID step should be followed up in
    the GUI report" (``fid_com_corrections``): the "parameter corrections" printed line by
    line during conversion now go into the report too. Sweep width and carrier have their
    own records (``sweep_width`` / ``carrier``) and the backend already skips them when
    writing the sidecar, to avoid duplication.
    """
    work = Path(work_dir)
    try:
        for path in sorted(work.glob("*.fid.conversion.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            lines = payload.get("fid_com_corrections")
            if isinstance(lines, list) and lines:
                return [str(item) for item in lines if str(item)]
    except OSError:
        return []
    return []


def detect_part_count(work_dir: Path | str, raw_dirs: list[Path | str] | None = None) -> int | None:
    """How many parts this data has (for the report wording): only multi-part data
    mentions "merging" and "inter-part field drift".

    The criteria are ordered by confidence: (1) ``process/seg_002`` (a per-segment
    working directory that only multi-part conversion creates) or
    ``process/field_drift.json`` (written only for >= 2 parts) -> multi-part; (2) only
    ``seg_001`` -> a single part; (3) fall back to the raw directory: the number of
    datasets under the project's ``raw/segments/``; (4) None when it cannot be decided
    (the report then uses the multi-part wording, as before).
    """
    from workflow.field_drift import FIELD_DRIFT_FILENAME

    work = Path(work_dir)
    try:
        if (work / "seg_002").is_dir() or (work / FIELD_DRIFT_FILENAME).is_file():
            return 2
        if (work / "seg_001").is_dir():
            return 1
    except OSError:
        return None
    dirs = [Path(p) for p in (raw_dirs or [])]
    if len(dirs) >= 2:
        return len(dirs)
    if len(dirs) == 1:
        segments = dirs[0] / "segments"
        if segments.is_dir():
            try:
                count = sum(1 for child in segments.iterdir() if (child / "acqus").is_file())
            except OSError:
                return None
            if count:
                return count
        return 1
    return None


def fid_step_quality_report(
    work_dir: Path | str,
    raw_dirs: list[Path | str] | None = None,
    *,
    parts: int | None = None,
) -> list[str]:
    """Data quality report of the Generate-FID step (reads records only; it does not
    re-run the diagnosis or touch the fid).

    It shares the same format as :func:`format_fid_step_report`, so the step log and the
    Generate-FID step report in the GUI's intermediate panel show the same text (user
    request 2026-09-23: what the log reports should reach the report). Its content = the
    historical/current source bad-point cleanup + the inter-part field-drift conclusion
    (multi-part only) + the FID-layer inspection; when ``parts`` is omitted it is decided
    by :func:`detect_part_count`, and a single dataset does not mention "merging /
    inter-part field drift".

    Parameters
    ----------
    work_dir : Path | str
        Processing working directory (``process/``): ``diagnostics.json``,
        ``qc_audit.jsonl`` and ``field_drift.json`` all live here.
    raw_dirs : list[Path | str] | None
        The project's raw directory (or its per-part directories), used for the .bak
        fallback and to decide how many parts this data has.
    parts : int | None
        An explicit part count (decided automatically by default).

    Returns
    -------
    list[str]
        Report lines; a missing record still says "the FID-layer inspection was not
        executed in this step" instead of pretending "no problem".

    Raises
    ------
    - Never raises: a failed disk read counts as "no record".

    Side effects
    ------------
    Read-only.

    Examples
    --------
        lines = fid_step_quality_report(manager.data_dir(exp, data, "process"), [raw])
    """
    # local import: workflow.field_drift imports this module's _read_fid_raw back
    # (cycle avoidance)
    from workflow.field_drift import read_field_drift_record

    work = Path(work_dir)
    return format_fid_step_report(
        diagnostics=read_diagnostics_record(work),
        source_history=read_source_cleanup_history(work, raw_dirs),
        field_drift=read_field_drift_record(work),
        sweep_width=read_sweep_width_audit(work),
        carrier=read_carrier_audit(work),
        corrections=read_fid_com_corrections(work),
        mode_symbol=read_mode_symbol_audit(work),
        # a single dataset has no "merging" step: the part count is decided
        # automatically by default (user 2026-09-24)
        parts=parts if parts is not None else detect_part_count(work, raw_dirs),
    )


__all__ = [
    "DirectDiagnosticsResult",
    "collect_fid_paths",
    "detect_part_count",
    "fid_step_quality_report",
    "format_fid_step_report",
    "load_or_run_direct_diagnostics",
    "read_carrier_audit",
    "read_fid_com_corrections",
    "read_mode_symbol_audit",
    "read_sweep_width_audit",
    "read_diagnostics_record",
    "read_source_cleanup_history",
    "run_direct_diagnostics",
    "run_fid_diagnostics_paths",
]
