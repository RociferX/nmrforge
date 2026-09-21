"""Peak-position spread and delta-delta summary - a **test/detection helper** (2026-09-13).

Scope, as settled by the user on 2026-09-13 (kept for testing purposes):

- `run_parameter_study` / `run_sweep` / the CLI / `records/` **never call this module**,
  and no sigma/delta-delta file is produced: the software processes and records, nothing more;
- it exists for **tests and review**: regression tests use it to check that parameters really took
effect (all-zero sigma means a silently ignored parameter - a defect seen on real data before),
  to compare the peak positions of the two localisation methods, or as a reference implementation;
- significance statements in a paper remain the downstream code's own job.

---

Summarises where the same peaks land across parameter sets into a positional uncertainty.

Metric definitions (usable directly in a methods section):

- sigma_n: the standard deviation of that peak position on nucleus n (sample sd, ddof=1);
- range_n = max - min for that nucleus; it depends on the combination count, read it with sigma;
- **delta-delta lower bound (the main metric)** ``delta_std = sqrt(sum_n (w_n * sigma_n)^2)``,
  with default weights 1H = 1 and 15N = ``csp_n_weight``
  (0.2, the usual 1/5 weighting),
  other nuclei weigh 1. It assumes independent per-nucleus errors (**analytic combination**);
- ``delta_max``: the worst per-combination value under the same formula (empirical worst case);
- the dataset-level summary gives the median/p90/max of ``delta_std``: that number is the
  "processing uncertainty a CSP difference must exceed to be taken seriously".

Note: this quantifies the **positional uncertainty introduced by processing parameters** only.
Sampling noise, overlap and mis-assignment are excluded; it is a lower bound, not a budget.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nmrforge_api.peaks import PeakMeasurement
from ui_support.i18n import tr

DEFAULT_CSP_N_WEIGHT = 0.2
DEFAULT_NUCLEI: tuple[str, ...] = ("1H", "15N")

DEFINITION = (
    tr(
        "sigma is the peak-position standard deviation across parameter combinations; delta_std = "
        "sqrt(sum_n (w_n*sigma_n)^2), w(1H)=1, w(15N)=csp_n_weight, others 1; delta_max is the "
        "largest such value relative to the mean "
        "position.",
    )
)


@dataclass
class PeakUncertainty:
    """Positional uncertainty summary for one peak."""

    peak_id: int
    assignment: str = ""
    n_runs: int = 0
    missing_runs: int = 0
    mean: dict[str, float] = field(default_factory=dict)
    sigma: dict[str, float] = field(default_factory=dict)
    value_range: dict[str, float] = field(default_factory=dict)
    delta_std: float = 0.0
    delta_max: float = 0.0
    worst_run: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_id": int(self.peak_id),
            "assignment": self.assignment,
            "n_runs": int(self.n_runs),
            "missing_runs": int(self.missing_runs),
            "mean": {k: float(v) for k, v in self.mean.items()},
            "sigma": {k: float(v) for k, v in self.sigma.items()},
            "range": {k: float(v) for k, v in self.value_range.items()},
            "delta_std_ppm": float(self.delta_std),
            "delta_max_ppm": float(self.delta_max),
            "worst_run": self.worst_run,
        }


def _weight(nucleus: str, csp_n_weight: float) -> float:
    if nucleus == "15N":
        return float(csp_n_weight)
    return 1.0


def _normalize(
    runs: Mapping[str, Sequence[PeakMeasurement]] | Iterable[Any],
) -> dict[str, list[PeakMeasurement]]:
    if isinstance(runs, Mapping):
        return {str(k): list(v) for k, v in runs.items()}
    out: dict[str, list[PeakMeasurement]] = {}
    for item in runs:
        run_id = str(getattr(item, "run_id", "") or "")
        if not run_id:
            continue
        out[run_id] = list(getattr(item, "measurements", []) or [])
    return out


def position_uncertainty(
    runs: Mapping[str, Sequence[PeakMeasurement]] | Iterable[Any],
    *,
    csp_n_weight: float = DEFAULT_CSP_N_WEIGHT,
    nuclei: Sequence[str] | None = None,
) -> list[PeakUncertainty]:
    """Per-peak uncertainty across combinations (only ``status=success`` combinations)."""
    by_run = _normalize(runs)
    wanted = tuple(nuclei) if nuclei else DEFAULT_NUCLEI
    per_peak: dict[int, dict[str, Any]] = {}
    for run_id, measurements in by_run.items():
        for measurement in measurements:
            entry = per_peak.setdefault(
                measurement.peak_id,
                {
                    "assignment": measurement.assignment,
                    "values": {},
                    "missing": 0,
                    "runs": 0,
                },
            )
            entry["runs"] += 1
            if not measurement.found:
                entry["missing"] += 1
                continue
            for nucleus in wanted:
                if nucleus in measurement.positions:
                    entry["values"].setdefault(nucleus, {})[run_id] = float(
                        measurement.positions[nucleus]
                    )

    results: list[PeakUncertainty] = []
    for peak_id in sorted(per_peak):
        entry = per_peak[peak_id]
        # only combinations where every measured nucleus was found count,
# so half-data cannot skew sigma
        key_sets = [set(entry["values"].get(n, {}).keys()) for n in wanted]
        common = set.intersection(*key_sets) if key_sets and all(key_sets) else set()
        item = PeakUncertainty(
            peak_id=peak_id,
            assignment=str(entry["assignment"] or ""),
            n_runs=len(common),
            missing_runs=entry["runs"] - len(common),
        )
        if not common:
            results.append(item)
            continue
        ordered = sorted(common)
        for nucleus in wanted:
            values = [entry["values"][nucleus][run_id] for run_id in ordered]
            item.mean[nucleus] = statistics.fmean(values)
            item.sigma[nucleus] = (
                statistics.stdev(values) if len(values) > 1 else 0.0
            )
            item.value_range[nucleus] = max(values) - min(values)
        item.delta_std = math.sqrt(
            sum(
                (_weight(n, csp_n_weight) * item.sigma.get(n, 0.0)) ** 2
                for n in wanted
            )
        )
        worst_run = ""
        worst_delta = 0.0
        for run_id in ordered:
            delta = math.sqrt(
                sum(
                    (
                        _weight(n, csp_n_weight)
                        * (
                            entry["values"][n][run_id]
                            - item.mean[n]
                        )
                    )
                    ** 2
                    for n in wanted
                )
            )
            if delta > worst_delta:
                worst_delta = delta
                worst_run = run_id
        item.delta_max = worst_delta
        item.worst_run = worst_run
        results.append(item)
    return results


def uncertainty_summary(
    uncertainties: Sequence[PeakUncertainty],
    *,
    csp_n_weight: float = DEFAULT_CSP_N_WEIGHT,
    n_runs: int = 0,
) -> dict[str, Any]:
    """Dataset-level summary: the uncertainty distribution -> the CSP lower bound."""
    usable = [u for u in uncertainties if u.n_runs > 0]
    delta = sorted(u.delta_std for u in usable)
    summary: dict[str, Any] = {
        "csp_n_weight": float(csp_n_weight),
        "n_runs": int(n_runs or (usable[0].n_runs if usable else 0)),
        "n_peaks": len(usable),
        "n_peaks_unmeasured": len(uncertainties) - len(usable),
        "definition": DEFINITION,
    }
    if delta:
        summary["delta_std_ppm"] = {
            "min": delta[0],
            "median": _percentile(delta, 50.0),
            "p90": _percentile(delta, 90.0),
            "max": delta[-1],
        }
    else:
        summary["delta_std_ppm"] = {}
    sigma: dict[str, dict[str, float]] = {}
    for nucleus in sorted({n for u in usable for n in u.sigma}):
        values = sorted(u.sigma.get(nucleus, 0.0) for u in usable)
        sigma[nucleus] = {
            "median": _percentile(values, 50.0),
            "p90": _percentile(values, 90.0),
            "max": values[-1] if values else 0.0,
        }
    summary["sigma_ppm"] = sigma
    return summary


def _percentile(sorted_values: Sequence[float], percent: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * (float(percent) / 100.0)
    lower = int(math.floor(rank))
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return float(
        sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight
    )


__all__ = [
    "DEFAULT_CSP_N_WEIGHT",
    "DEFAULT_NUCLEI",
    "DEFINITION",
    "PeakUncertainty",
    "position_uncertainty",
    "uncertainty_summary",
]
