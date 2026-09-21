"""SMILE parameter optimisation (optional, user selects the optimisation option afterwards; does
not enter the automatic processing process). User plan (2026-09-10/11): ** Use the final run
script as the template and only replace SMILE parameter ** -- 1) Direct dimension processing is
run once (3D takes slice files; 2D single file whole script is run group by group); 2) Each
group of parameters is run once with "SMILE + indirect dimension" to get the candidate spectrum;
3) Immediately evaluate (peak + mass score + SMILE fitting residual + consistency residual) and
**delete** candidate spectrum; 4) Compare parameter combinations by sorting caliber -> parameter
combination sorting table (CSV/JSON) + top three scripts; the script used in "Rerun by Rank1" is
always **full sampling** script. Operation mode and sorting caliber (revision 23): Net true peak
priority -> full sampling reconstruction (number of peaks = final spectrum) caliber);
consistency priority -> set aside (train) reconstruction (save points do not participate in
reconstruction, residuals are used as the basis); each candidate is only run once."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment
from core.project.manager import atomic_write_text
from core.qc import peak_detection, spectrum_quality
from ui_support.i18n import tr


@dataclass
class SmileParameterResult:
    """A parameter group (SMILE parameter combination)stability/Score and retain peaks across
    combinations."""

    params: dict[str, Any]
    repeats: int = 1
    n_combos: int = 0
    rank: int = 0
    true_peak_count: int = 0
    decision: str = ""
    overall: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    spectrum_path: str = ""
    stable_peaks: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Optimisation degree 2x2..5x5: uniformly sample from the 5th level default value
# (0.2.199-patch29hz-fix 4).
_NSIGMA_FULL: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 7.0)
_THRESH_FULL: tuple[float, ...] = (0.90, 0.93, 0.95, 0.97, 0.99)
SMILE_GRID_MIN, SMILE_GRID_MAX = 2, 5
SMILE_GRID_DEFAULT = 4  # 0.2.199-Patch29hz-fix 5(user): default 4x4=16 group.
# 0.2.199-patch29hz - Modify 10 (user): Leave sampling point residuals by default, leaving 1 for
# every 4 sampling points (25%), which is used as the sorting basis for "judging true spurious peaks
# when there is no full sampling reference". 0.2.199-patch29hz - Modify 23 (user): Select the
# operation mode according to the sorting caliber (once per candidate) -- Net true peak priority:
# full sampling reconstruction, Peak count/The quality score is final spectrum caliber (not
# reserved); Consistency first: Leave for reconstruction, set aside points not to participate in
# reconstruction, and use residuals as the basis for sorting.
SMILE_HOLDOUT_RATIO = 0.25
# 0.2.199-patch29hz-Xiu 16 (user): The purpose of this step is to "try to reconstruct as many true
# peaks as possible", so the candidate evaluation uses an independent low threshold (3σ, the default
# profile of the peak detection algorithm), which has nothing to do with the threshold of the "peak
# selection" step (default 35σ, which is used for the peak table) -- a threshold that is too high
# will miss weak true peaks together, and the ranking will not reflect the quality of
# reconstruction. Spurious peaks are suppressed by the sorting caliber itself: only in individual
# parameters The peaks that appear in the combination will be counted as "spurious peaks" and points
# will be deducted.
SMILE_SCAN_SIGMA = 3.0


def smile_scan_sign_mode(experiment: Any) -> str:
    """SMILE The peak symbol mode for candidate evaluation -- has the same origin as the peak
    selection step (presets peak_sign). For mixed experiments (HNCACB/ CBCANCO and other
    positive and negative coexistences) use both, uniform/unknown use dominant (only the peaks
    with the majority of symbols are retained, and the positive and negative are not concerned)
    to avoid missing the reversed-phase true peaks as noise."""
    from core.experiments.registry import get as get_template

    etype = getattr(experiment, "experiment_type", None)
    name = str(getattr(etype, "name", "") or "")
    template = get_template(name) if name else None
    peak_sign = str(getattr(template, "peak_sign", "uniform") or "uniform")
    return "both" if peak_sign == "mixed" else "dominant"


def smile_scan_edge_margin(
    experiment: Any | None = None,
    n_points: int | None = None,
    *,
    axis_ppm: Any | None = None,
    nucleus: str = "",
    obs_mhz: float = 0.0,
) -> int:
    """Candidate evaluation excludes upper and lower edge axis peaks -> points (**same conversion
    caliber as peak selection**). 2026-09-13 (user plan A): The margin is the physical width
    (default 3 ``axis_ppm``/``nucleus``/``obs_mhz``; This correctly overrides EXT cropping, zero
    filling and axis rearrangement. Use ``experiment`` + ``n_points`` only if there is no actual
    spectral axis; if it fails then it falls back to the old point constant."""
    from workflow.pick_peaks import PICK_EDGE_MARGIN

    try:
        from backend.config import load_processing_defaults
        from core.peaks import axis_units

        try:
            linewidths = load_processing_defaults().get("linewidth_hz") or {}
        # The configuration is not readable and uses the built-in default.
        except Exception:  # noqa: BLE001 -
            linewidths = {}
        actual_axis = np.asarray(axis_ppm, dtype=float) if axis_ppm is not None else None
        if actual_axis is not None and actual_axis.size >= 2:
            width_ppm = axis_units.edge_margin_ppm(
                nucleus,
                float(obs_mhz or 0.0),
                linewidth_hz_by_nucleus=linewidths,
            )
            points = axis_units.points_for_ppm(actual_axis, width_ppm)
            if points > 0:
                return int(points)
        if experiment is None or not n_points or int(n_points) < 2:
            return int(PICK_EDGE_MARGIN)
        estimated_nucleus = ""
        estimated_obs = 0.0
        for dim in getattr(experiment, "dimensions", None) or []:
            if str(getattr(dim, "logical_axis", "")) == "F1":
                estimated_nucleus = str(getattr(dim, "nucleus", "") or "")
                estimated_obs = float(getattr(dim, "sf", 0.0) or 0.0)
                break
        sw_hz = 0.0
        for dim in getattr(experiment, "dimensions", None) or []:
            if str(getattr(dim, "logical_axis", "")) == "F1":
                sw_hz = float(getattr(dim, "sw", 0.0) or 0.0)
                break
        if estimated_obs <= 0 or sw_hz <= 0:
            return int(PICK_EDGE_MARGIN)
        estimated_axis = sw_hz / (int(n_points) * estimated_obs) * (
            int(n_points) / 2 - np.arange(int(n_points))
        )
        width_ppm = axis_units.edge_margin_ppm(
            estimated_nucleus,
            estimated_obs,
            linewidth_hz_by_nucleus=linewidths,
        )
        points = axis_units.points_for_ppm(estimated_axis, width_ppm)
        return int(points) if points > 0 else int(PICK_EDGE_MARGIN)
    except Exception:  # noqa: BLE001 - Fallback point constant for conversion failure.
        return int(PICK_EDGE_MARGIN)


def evaluate_candidate_peaks(
    arr: Any, *, sign_mode: str = "dominant", edge_margin: int | None = None
) -> list[Any]:
    """Candidate spectrum peak detection: low threshold (SMILE_SCAN_SIGMA) + homologous symbol mode
    + exclusion of axial peaks. user 2026-09-11: "SMILE This step is to reconstruct as many true
    peaks as possible" -- so a low threshold (3σ) is deliberately used here to try not to leak
    true peaks. The difference between candidates is reflected by "stable peaks − spurious
    peaks"."""
    params = peak_detection.PeakDetectionParams(
        sigma_multiplier=SMILE_SCAN_SIGMA,
        min_snr=SMILE_SCAN_SIGMA,
        sign_mode=sign_mode,
        edge_margin=(
            int(edge_margin)
            if edge_margin is not None
            else smile_scan_edge_margin()
        ),
    )
    return peak_detection.detect(np.asarray(arr), params)


def _subsample(values: tuple[float, ...], count: int) -> tuple[float, ...]:
    """Take count evenly from values (including the first and last)."""
    if count <= 1:
        return (values[len(values) // 2],)
    if count >= len(values):
        return tuple(values)
    picked = [round(i * (len(values) - 1) / (count - 1)) for i in range(count)]
    return tuple(values[i] for i in dict.fromkeys(picked))


def smile_grid(size: int = SMILE_GRID_DEFAULT) -> list[dict[str, Any]]:
    """Generate n x n grids according to the degree of optimisation (2x2..5x5, default 4x4=16
    groups). 2x2 is the fastest (4 groups), 5x5 is the finest (25 groups); the time consumption
    is roughly proportional to the number of groups."""
    count = max(SMILE_GRID_MIN, min(SMILE_GRID_MAX, int(size or SMILE_GRID_DEFAULT)))
    return default_smile_grid(
        _subsample(_NSIGMA_FULL, count), _subsample(_THRESH_FULL, count)
    )


def estimate_scan_seconds(
    experiment: Experiment, n_combos: int
) -> tuple[float, float]:
    """Roughly estimate each group based on data size/Total time spent(seconds); The first group is
    covered by measured after running. Empirical model (2026-09-10, sampleC 3D NUS 250
    points/direct dimension TD 2048 measured 86s/Group): each group ≈ 85s x (sampling point
    number/250) x (direct dimension TD/2048); 2D then x 0.3."""
    sampling = getattr(experiment, "sampling", None)
    nus_points = len(getattr(sampling, "nus_list", None) or []) or 250
    direct_td = 2048.0
    for dim in getattr(experiment, "dimensions", None) or []:
        role = str(getattr(getattr(dim, "role", None), "name", ""))
        if role.startswith("DIRECT"):
            try:
                direct_td = float(getattr(dim, "td", 0) or 0) or direct_td
            except (TypeError, ValueError):
                pass
    per_group = 85.0 * (float(nus_points) / 250.0) * (direct_td / 2048.0)
    if int(getattr(experiment, "ndim", 2) or 2) <= 2:
        per_group *= 0.3
    per_group = max(5.0, per_group)
    return per_group, per_group * max(1, int(n_combos))

def default_smile_grid(
    nsigma_values: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 7.0),
    thresh_values: tuple[float, ...] = (0.90, 0.93, 0.95, 0.97, 0.99),
) -> list[dict[str, Any]]:
    """Default parameter grid: nSigma × thresh (since 0.2.162 only the SMILE
    parameters are optimised; the 0.2.162 patch densified it to 5×5=25 combinations for
    finer tuning)."""
    return [
        {"nsigma": nsigma, "thresh": thresh}
        for nsigma in nsigma_values
        for thresh in thresh_values
    ]


def _snap_key(position: tuple[float, ...], tol_pts: float) -> tuple[float, ...]:
    """Peak position snapshot key: round according to the tolerance grid, Within the donor
    group/Match across combinations. 0.2.199-patch29eo: first normalise to integer pixels --
    sub-pixel correction causes the position to jitter within +/-0.5px. If not rounded first,
    the jitter will cause the peak to flip when it is exactly at the tol grid boundary (such as
    10pt/4pt bucket), and cross-combination matching will break (stable peaks will be misjudged
    as spurious peaks)."""
    ipos = tuple(round(float(v)) for v in position)
    if tol_pts > 0:
        return tuple(round(float(v) / tol_pts) * tol_pts for v in ipos)
    return ipos


def scan_smile_parameters(
    experiment: Experiment,
    backend: Any,
    base_params: dict[str, Any] | None = None,
    *,
    scan_dir: Path | str,
    grid: list[dict[str, Any]] | None = None,
    grid_size: int = SMILE_GRID_DEFAULT,
    rank_mode: str = "true_peaks",
    cross_min: int = 2,
    peak_tol_pts: float = 4.0,
    keep_top: int = 3,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """SMILE parameter scan (0.2.199-patch29hz-repair 3, user plan). Use the final script as the
    template **replace only SMILE parameter **: ① run the direct dimension once to get the slice
    file; ② run "SMILE + indirect dimension" once for each set of parameters to get the final
    spectrum; ③ get the index immediately; ④ delete the spectrum (the candidate spectrum only
    exists temporarily in scan_dir, usually located in the memory disk). Indicators (no need to
    keep spectra): number of detected peaks, number of stable peaks across parameter
    combinations (>= cross_min group appears), average stable peak S/N, spectrum comprehensive
    quality score; sorted by (number of stable peaks, average S/N, quality score). Return
    {success, message, logs, rows (by ranking), scripts({ranking: script text}), scan_dir,
    n_combos}."""
    combos = list(grid) if grid is not None else smile_grid(grid_size)
    base = dict(base_params or {})
    sign_mode = smile_scan_sign_mode(experiment)
    # The margin is converted according to **actual candidate spectrum points** (the physical width
    # remains unchanged); each actual value is collected for summary log/recording use
    # (smile_scan_edge_margin falls back to the point constant when the context cannot be obtained).
    edge_margin_seen: list[int] = []
    mode = str(rank_mode or "true_peaks").lower()
    # Repair 23 (user): Select the running mode according to the required sorting method (only run
    # once per candidate) -- Net true peak priority -> full sampling run (Number of peaks/Quality
    # points final spectrum caliber, no set aside); consistency priority -> set aside run (set aside
    # points will not participate in reconstruction, residuals are used as the basis for sorting).
    holdout_ratio = 0.0
    if mode == "consistency":
        holdout_ratio = float(
            base.get("holdout_ratio", SMILE_HOLDOUT_RATIO) or 0.0
        )
    est_group, est_total = estimate_scan_seconds(experiment, len(combos))
    started = time.time()
    _first_done: list[float] = []

    def _progress(index: int, total: int, message: str) -> None:
        if progress is None:
            return
        if index == 0:
            progress(
                index,
                total,
                tr(
                    "{p0} | Estimate from the data size: approx. {p1:.0f}s per set, {p2:.1f} "
                    "minutes in total (updated after the first set "
                    "completes)",
                    p0=message,
                    p1=est_group,
                    p2=est_total / 60.0,
                ),
            )
            return
        if index >= 2 and not _first_done:
            measured = time.time() - started
            _first_done.append(measured)
            remain = measured * max(0, total - index + 1)
            progress(
                index,
                total,
                tr(
                    "{p0} | Measured approx. {p1:.0f}s per set, {p2:.1f} minutes "
                    "remaining",
                    p0=message,
                    p1=measured,
                    p2=remain / 60.0,
                ),
            )
            return
        progress(index, total, message)

    def _evaluate(path: str) -> dict[str, Any]:
        """Candidate spectrum evaluation: peak + quality score (the spectrum is still there at this
        moment, and will be deleted after the evaluation)."""
        from workflow.pick_peaks import read_spectrum_axes

        spectrum = read_spectrum_axes(path)
        arr = spectrum.data
        # Low threshold + homologous sign mode + exclude axial peaks (0.2.199-patch29hz-modify 16);
        # does not follow the threshold of the "peak selection" step (default 35σ) -- that step is
        # to generate a peak table, not to evaluate candidates.
        edge_margin_points = smile_scan_edge_margin(
            experiment,
            int(arr.shape[0]),
            axis_ppm=spectrum.ppm[0],
            nucleus=(spectrum.nuclei[0] if spectrum.nuclei else ""),
            obs_mhz=(spectrum.obs[0] if spectrum.obs else 0.0),
        )
        edge_margin_seen.append(int(edge_margin_points))
        peaks = evaluate_candidate_peaks(
            arr, sign_mode=sign_mode, edge_margin=edge_margin_points
        )
        quality = spectrum_quality.evaluate(arr, sign_mode=sign_mode)
        # 0.2.199-patch29hz-Repair 4: The overall score is on QualityResult.score.overall,
        # QualityResult itself has no overall (the previous value was always 0).
        _qscore = getattr(quality, "score", None)
        return {
            "peak_count": len(peaks),
            "quality": float(getattr(_qscore, "overall", 0.0) or 0.0),
            "peaks": [
                {
                    "position": [float(v) for v in peak.position],
                    "height": float(peak.height),
                    "snr": float(peak.snr),
                }
                for peak in peaks
            ],
        }

    scan = backend.smile_scan(
        experiment,
        base,
        combos,
        work_dir=scan_dir,
        evaluate=_evaluate,
        progress=_progress,
        holdout_ratio=holdout_ratio,
    )
    if not scan.get("success"):
        raise RuntimeError(str(scan.get("message", tr("SMILE Scan failed"))))
    # Must be generated after calling _evaluate ** on backend.smile_scan, otherwise the list will
    # always be empty, and log will falsely report the actual evaluation as "not evaluated".
    _criteria_log = (
        tr(
            "Candidate evaluation criteria: threshold {p0:g}σ (independent of the peak-picking "
            "step), sign mode {p1}, exclude axial peaks ",
            p0=SMILE_SCAN_SIGMA,
            p1=sign_mode,
        )
        + (
            tr(
                "{p0}–{p1} points(converted on the real axis of the candidate spectrum; the "
                "physical width is the "
                "same)",
                p0=min(edge_margin_seen),
                p1=max(edge_margin_seen),
            )
            if edge_margin_seen
            else tr("(The backend did not return an evaluable candidate spectrum)")
        )
    )
    scan["logs"] = [_criteria_log] + list(scan.get("logs") or [])
    candidates = list(scan.get("candidates") or [])
    n_combos = len(candidates) or 1
    effective_cross = cross_min if n_combos >= cross_min else 1
    key_to_combos: dict[tuple[float, ...], set[int]] = defaultdict(set)
    keys_by_index: dict[int, set[tuple[float, ...]]] = {}
    for entry in candidates:
        metrics = dict(entry.get("metrics") or {})
        seen: set[tuple[float, ...]] = set()
        for peak in metrics.get("peaks") or []:
            key = _snap_key(tuple(peak["position"]), peak_tol_pts)
            seen.add(key)
            key_to_combos[key].add(int(entry["index"]))
        keys_by_index[int(entry["index"])] = seen
    rows: list[dict[str, Any]] = []
    for entry in candidates:
        metrics = dict(entry.get("metrics") or {})
        params = dict(entry.get("params") or {})
        stable = 0
        suspect = 0
        snr_values: list[float] = []
        for peak in metrics.get("peaks") or []:
            key = _snap_key(tuple(peak["position"]), peak_tol_pts)
            if len(key_to_combos.get(key, set())) < effective_cross:
                # Peaks that only appear in certain parameter combinations: spurious peak.
                suspect += 1
                continue
            stable += 1
            try:
                snr_values.append(float(peak.get("snr", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        mean_snr = float(np.mean(snr_values)) if snr_values else 0.0
        quality = float(metrics.get("quality", 0.0) or 0.0)
        rows.append(
            {
                "index": int(entry["index"]),
                "nsigma": float(params.get("nsigma", 0.0) or 0.0),
                "thresh": float(params.get("thresh", 0.0) or 0.0),
                "peak_count": int(metrics.get("peak_count", 0) or 0),
                "stable_count": int(stable),
                "suspect_count": int(suspect),
                "net_peaks": int(stable - suspect),
                "mean_snr": round(mean_snr, 3),
                "quality": round(quality, 2),
                "holdout_rmse": float(metrics.get("holdout_rmse", 0.0) or 0.0),
                "holdout_corr": float(metrics.get("holdout_corr", 0.0) or 0.0),
                "smile_rms_ratio": float(
                    metrics.get("smile_rms_ratio", 0.0) or 0.0
                ),
                "composite": round(stable - suspect + 0.01 * mean_snr + 0.01 * quality, 3),
                "ok": bool(entry.get("ok")),
                "error": str(metrics.get("error", "") or ""),
                "_script": str(entry.get("script", "")),
            }
        )
    # User goal (2026-09-10): as few spurious peaks as possible + as many true peaks as possible ->
    # First press "net true peak" (stable peak − suspected spurious peak), then press the number of
    # stable peaks, average S/N, quality score 0.2.199-patch29hz - repair 7 (user): two sorting
    # calibers -- "true_peaks" (default): net true peak (stable peak − suspected spurious peak)
    # takes priority, restabilize peak/average S/N/residual; "consistency": Leave the residuals
    # first (more accurate predictions for sampling points not involved in reconstruction), then fit
    # the residuals, net true peaks, stable peaks, average S/N, and quality score.
    if mode == "consistency":
        rows.sort(
            key=lambda r: (
                -float(r.get("holdout_rmse", 0.0) or 0.0),
                -float(r.get("smile_rms_ratio", 0.0) or 0.0),
                r["net_peaks"],
                r["stable_count"],
                r["mean_snr"],
                r["quality"],
            ),
            reverse=True,
        )
    else:
        rows.sort(
            key=lambda r: (
                r["net_peaks"],
                r["stable_count"],
                r["mean_snr"],
                -float(r.get("holdout_rmse", 0.0) or 0.0),
                -float(r.get("smile_rms_ratio", 0.0) or 0.0),
                r["quality"],
            ),
            reverse=True,
        )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    scripts = {
        int(row["rank"]): str(row.get("_script", ""))
        for row in rows[: max(1, int(keep_top))]
    }
    for row in rows:
        row.pop("_script", None)
    return {
        "success": True,
        "message": str(scan.get("message", "")),
        "logs": list(scan.get("logs") or []),
        "rows": rows,
        "scripts": scripts,
        "scan_dir": str(scan.get("scan_dir", scan_dir)),
        "n_combos": n_combos,
        "rank_mode": mode,
    }


def write_smile_scan_output(
    manager: Any,
    exp_id: str,
    data_id: str,
    rows: list[dict[str, Any]],
    scripts: dict[int, str],
) -> dict[str, str]:
    """Write "parameter combination sorting list" (CSV+JSON) and the top three scripts
    (0.2.199-patch29hz-repair 3). The sorting list falls into `<data>/smile_optimized/`; the top
    three scripts fall into `<data>/process/ <data_id>_nus_rankN.com` (same place as the final
    script and can be run directly). Return {csv, json, rank1, rank2, rank3} (missing items do
    not appear)."""
    import csv
    import json

    out_dir = manager.data_dir(exp_id, data_id, "smile_optimized")
    out_dir.mkdir(parents=True, exist_ok=True)
    proc_dir = manager.data_dir(exp_id, data_id, "process")
    proc_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{exp_id}-{data_id}_smile_ranking.csv"
    json_path = out_dir / f"{exp_id}-{data_id}_smile_ranking.json"
    fields = [
        "rank", "index", "nsigma", "thresh", "net_peaks", "stable_count",
        "suspect_count", "peak_count",
        "mean_snr", "quality", "smile_rms_ratio", "holdout_rmse", "holdout_corr",
        "composite", "ok", "error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    atomic_write_text(
        json_path,
        json.dumps({"rows": rows, "count": len(rows)}, ensure_ascii=False, indent=2)
        + "\n",
    )
    paths: dict[str, str] = {
        "csv": str(csv_path),
        "json": str(json_path),
    }
    for rank, script in sorted(scripts.items()):
        if not script or rank < 1 or rank > 3:
            continue
        target = proc_dir / f"{data_id}_nus_rank{rank}.com"
        target.write_text(script, encoding="utf-8", newline="\n")
        paths[f"rank{rank}"] = str(target)
    return paths

def format_results(results: list[SmileParameterResult]) -> str:
    """Render the candidate list as an aligned parameter combination + stability score table."""
    header = "{:>6} {:>6} {:>8} {:>7} {:>9} {:>6} {:>6} {:>6} {:>6}".format(
        "nSigma", "thresh", "decision", "overall", "stability", "cross", "snr",
        "peaks", "artifact"
    )
    lines = [header, "-" * len(header)]
    for result in results:
        comp = result.components
        lines.append(
            "{:>6} {:>6} {:>8} {:>7.1f} {:>9.2f} {:>6.2f} {:>6.1f} {:>6} {:>6.1f}".format(
                result.params.get("nsigma", 0),
                result.params.get("thresh", 0),
                result.decision,
                result.overall,
                comp.get("stability", 0),
                comp.get("cross_support", 0),
                comp.get("snr", 0),
                comp.get("peak_count", 0),
                comp.get("artifact", 0),
            )
        )
    return "\n".join(lines)


def save_report(results: list[SmileParameterResult], path: Path | str) -> Path:
    """Save parameter group + score report (JSON)."""
    out = Path(path)
    atomic_write_text(
        out,
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2)
        + "\n",
    )
    return out
