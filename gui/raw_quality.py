"""Raw data quality check (automatically executed after import, GUI side read-only use core.data).
Check items: acqus parameter existence and dimension /nuclear/temperature, ser/fid time domain
file existence and size, small file Estimated first paragraph/tail signal noise. returns report
dict, no exception is thrown."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ui_support.i18n import tr


def _raw_dir(project, exp_id: str, data_id: str) -> Path | None:
    """Locate the original data directory (raw/ takes precedence, fallback to metadata source_path
    if not copied)."""
    try:
        raw = project.data_dir(exp_id, data_id, "raw")
        if raw.is_dir():
            return raw
    except Exception:  # noqa: BLE001 - Fallback when layout is unavailable.
        pass
    try:
        meta_path = project.data_metadata_path(exp_id, data_id)
        if meta_path is not None and meta_path.is_file():
            import json

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            source = meta.get("source_path") or ""
            if source and Path(source).is_dir():
                return Path(source)
    # If the metadata cannot be read, it will be treated as no original directory.
    except Exception:  # noqa: BLE001 -
        pass
    return None


def _param(acqus: Path, key: str) -> str:
    """Read a single ``##$KEY= value`` in the Bruker parameter file."""
    try:
        text = acqus.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return ""
    match = re.search(rf"##\${key}=([^\n]+)", text)
    return match.group(1).strip() if match else ""


def _te_to_kelvin(te: str) -> float | None:
    """Bruker TE -> Kelvin; automatically recognizes 0.1 K / K / °C, cannot be parsed and returns
    None. - TE The convention is 0.1 K (such as 2980 -> 298.0 K); - Some data are directly
    stored in K (such as 298.0) or °C (such as 25), and are judged according to the numerical
    range."""
    try:
        number = float(te.split()[0])
    except (TypeError, ValueError, IndexError):
        return None
    kelvin_tenths = number / 10.0
    if 240.0 <= kelvin_tenths <= 340.0:
        return kelvin_tenths
    if 240.0 <= number <= 340.0:
        return number
    if -40.0 <= number <= 100.0:
        return number + 273.15
    return None


def _estimate_snr(raw: Path) -> float | None:
    """Read the time-domain data with the Bruker reader and estimate the first peak value
    over the tail noise standard deviation."""
    try:
        from core.data.bruker_reader import read_data, read_dataset

        experiment = read_dataset(raw)
        data = read_data(experiment).matrix
        flat = np.abs(np.asarray(data).reshape(-1))
        if flat.size < 64:
            return None
        head = flat[: max(1, flat.size // 20)]
        tail = flat[-max(1, flat.size // 5) :]
        noise = float(np.std(tail))
        if noise <= 0:
            noise = float(np.mean(tail)) or 1e-12
        return float(np.max(head) / noise)
    except Exception:  # noqa: BLE001 - Skip signal-to-noise estimation when data cannot be read.
        return None


def check_raw_quality(project, exp_id: str, data_id: str) -> dict:
    """Check the original data quality and return ``{"ok", "issues", "info"}``."""
    info: dict[str, str] = {}
    issues: list[str] = []
    raw = _raw_dir(project, exp_id, data_id)
    if raw is None:
        return {"ok": False, "issues": [tr("Raw data directory (raw/) not found")], "info": {}}
    # 0.2.199: The segmented data collection raw/ is the container root directory (without
    # acqus/ser). The quality check is evaluated based on the first segment directory (the
    # parameters collected in each segment of the same experiment are consistent), and no missing
    # information is falsely reported acqus/ser.
    try:
        data_entry = project.data(exp_id, data_id)
        segments = list(getattr(data_entry, "segments", None) or [])
        if segments:
            seg0 = Path(segments[0])
            if not seg0.is_absolute():
                seg0 = Path(project.root) / seg0
            if seg0.is_dir():
                raw = seg0
                info[tr("segmented")] = tr("Evaluate by first paragraph ({p0})", p0=seg0.name)
    except Exception:  # noqa: BLE001 - Unable to read segments, evaluate as normal data.
        pass

    acqus = raw / "acqus"
    if not acqus.is_file():
        issues.append(tr("Missing acqus parameter file"))
    else:
        ndim = 1 + (raw / "acqu2s").is_file() + (raw / "acqu3s").is_file()
        info[tr("Dimensions")] = f"{ndim}D"
        def _nuc(file: Path, key: str) -> str:
            return _param(file, key) if file.is_file() else ""

        nuclei = [
            value
            for value in (
                _nuc(acqus, "NUC1"),
                _nuc(raw / "acqu2s", "NUC2") or _nuc(acqus, "NUC2"),
                _nuc(raw / "acqu3s", "NUC3") or _nuc(acqus, "NUC3"),
            )
            if value
        ]
        if nuclei:
            info[tr("nuclear")] = "-".join(nuclei)
        te = _param(acqus, "TE")
        if te:
            kelvin = _te_to_kelvin(te)
            if kelvin is not None:
                info[tr("temperature")] = f"{kelvin:.1f} K"
        td = _param(acqus, "TD")
        if td:
            info[tr("direct dimension TD")] = td.split()[0]

    data_file = next(
        (raw / name for name in ("ser", "fid") if (raw / name).is_file()),
        None,
    )
    if data_file is None:
        issues.append(tr("Missing time domain data file (ser/fid)"))
    else:
        size = data_file.stat().st_size
        info[tr("time domain file")] = tr("{p0} {p1} byte", p0=data_file.name, p1=size)
        if size < 1024:
            issues.append(
                tr(
                "The time domain data file is too small (<1KB), and the collection may "
                "fail",
            )
            )
        if size < 64 * 1024 * 1024:
            snr = _estimate_snr(raw)
            if snr is not None:
                info[tr("SNR (head/tail)")] = f"{snr:.1f}"
                if snr < 3.0:
                    issues.append(tr("Raw signal-to-noise ratio too low ({p0:.1f})", p0=snr))

    return {"ok": not issues, "issues": issues, "info": info}


def format_quality_report(report: dict) -> str:
    """Convert quality report dict into log/pop-up window readable text."""
    if not report:
        return tr("(not implemented)")
    lines: list[str] = []
    info = report.get("info") or {}
    if info:
        lines.append("  " + " | ".join(f"{key}={value}" for key, value in info.items()))
    issues = report.get("issues") or []
    if issues:
        lines.append(tr(" warn: ") + ";".join(issues))
    else:
        lines.append(tr(" pass"))
    return "\n".join(lines)


__all__ = ["check_raw_quality", "format_quality_report"]
