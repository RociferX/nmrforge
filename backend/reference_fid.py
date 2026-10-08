"""Read-only validation of the converted input frozen by an API reference."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from backend.conversion_provenance import read_conversion_provenance
from core.data.internal_data_model import Experiment, SamplingMode
from core.data.nus_reader import read_nuslist, schedule_grid_shape
from core.data.raw_fingerprint import file_fingerprint, raw_dir_fingerprint
from ui_support.i18n import tr


def reference_fid_error(reason: str) -> ValueError:
    """One actionable error for reference input failures, including old references."""
    return ValueError(tr(
        "Reference FID is unavailable or no longer matches the reference ({p0}). "
        "Rebuild this condition's reference with force=True (CLI: reference --force) "
        "before running combinations; automatic reconversion is disabled.", p0=reason,
    ))


def freeze_fid_input(work: Path, dataset_id: str, provenance: dict[str, Any]) -> None:
    """Attach fast file fingerprints to the reference's existing conversion evidence."""
    record_name = f"{dataset_id}.fid.conversion.json"
    record = next((item.get("record") for item in provenance.get("sidecars", [])
                   if item.get("path") == record_name), None)
    if not isinstance(record, dict):
        return
    products = record.get("fid_products") or {}
    provenance["reference_fid"] = {
        "products": {
            name: file_fingerprint(work / name)
            if (work / name).resolve().is_relative_to(work.resolve()) else None
            for name in products
        },
        "nuslist": file_fingerprint(work / "nuslist"),
    }


def validate_fid_input(
    work: Path, experiment: Experiment, context: dict[str, Any],
    params: dict[str, Any], products: dict[str, int], in_file: str | None,
) -> int:
    """Validate only; never repair, convert, clean, expand or rewrite any input."""
    def fail(reason: str) -> None:
        raise reference_fid_error(reason)

    if not in_file:
        fail("missing converted FID")
    if ("segment_shift_hz" in params and (params.get("segment_shift_hz") or [])
            != (context.get("segment_shift_hz") or [])):
        fail("segment shifts require a new conversion")
    provenance = context.get("provenance") or {}
    record_name = f"{experiment.dataset_id}.fid.conversion.json"
    frozen = next((item.get("record") for item in provenance.get("sidecars", [])
                   if item.get("path") == record_name), None)
    if not isinstance(frozen, dict):
        fail("missing frozen conversion evidence")
    try:
        current = json.loads((work / record_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fail("missing or unreadable conversion record")
    if current != frozen:
        fail("conversion evidence changed")
    if read_conversion_provenance(work).get("sidecars") != provenance.get("sidecars"):
        fail("conversion sidecars changed")
    expected = frozen.get("fid_products")
    if not isinstance(expected, dict) or not expected or products != expected:
        fail("missing, additional or resized FID files")
    if any(not isinstance(size, int) or isinstance(size, bool) or size <= 0
           for size in expected.values()):
        fail("empty or invalid FID files")
    fingerprints = (provenance.get("reference_fid") or {}).get("products") or {}
    if set(fingerprints) != set(expected):
        fail("missing frozen FID fingerprints")
    for name in expected:
        path = (work / name).resolve()
        if not path.is_relative_to(work.resolve()):
            fail("invalid FID product path")
        if not fingerprints[name] or file_fingerprint(path) != fingerprints[name]:
            fail("FID content or file identity changed")
    sources = [Path(path) for path in experiment.segments] or [Path(experiment.source_path)]
    if any(not (path / "acqus").is_file() for path in sources):
        fail("missing original acquisition parameters")
    if (not frozen.get("raw_fingerprint")
            or raw_dir_fingerprint(sources) != frozen["raw_fingerprint"]):
        fail("original input changed")
    widths = {entry.get("axis"): entry.get("sw_hz_used")
              for entry in frozen.get("sweep_width", [])}
    for dim in experiment.dimensions:
        try:
            unchanged = math.isclose(float(widths[dim.logical_axis]), float(dim.sw),
                                     rel_tol=1e-7, abs_tol=1e-6)
        except (KeyError, TypeError, ValueError):
            unchanged = False
        if not unchanged:
            fail("conversion sweep width is different or unverified")
    if experiment.segments and "field_drift" not in frozen:
        fail("missing inter-segment field-drift evidence")
    if experiment.sampling.mode is not SamplingMode.NUS:
        return 0
    schedule = work / "nuslist"
    schedule_fp = (provenance.get("reference_fid") or {}).get("nuslist")
    if not schedule_fp or file_fingerprint(schedule) != schedule_fp:
        fail("missing or changed converted sampling schedule")
    try:
        points = [tuple(point) for point in read_nuslist(schedule)]
        shape = schedule_grid_shape(experiment)
        valid = bool(points) and bool(shape) and len(set(points)) == len(points) and all(
            len(point) == len(shape) and all(0 <= value < bound
                                           for value, bound in zip(point, shape, strict=True))
            for point in points
        )
    except (OSError, ValueError, TypeError):
        valid = False
    if not valid:
        fail("invalid converted sampling schedule")
    return len(points)
