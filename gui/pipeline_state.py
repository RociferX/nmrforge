"""GUI Side Pipeline step fingerprint status (OUTDATED determination). Supplement fingerprint
verification for "file exists" status inference (GUI_ARCHITECTURE_VISION §14/§16/§27): record
the input/script fingerprint in the data directory after each step is successfully executed;
recalculate the current fingerprint when refreshing the status, and inconsistency is OUTDATED
(upstream rerun, external modification, script change). The status file is only GUI Used,
located at the data directory base <exp>/<data>/.pipeline_state.json (cleared with data
deletion, not part of the contract §9.2 product directory): { "version": 1, "steps": { "fid":
{"input_hash":..., "script_hash":..., "output":..., "updated_at":...},... } }."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from core.data.raw_fingerprint import RAW_KEY_FILES, file_fingerprint
from core.project.artifacts import find_primary_spectrum

# Step -> Possible workflow ref (used to check recent runs/determine failures). 0.2.199-patch29hz:
# Pipeline and project tree originally saved one copy each, unified here; Fix 24: Table body sinks
# core/project/run_refs.py (workflow layer also needs to be used, and gui cannot be relied on in
# reverse), here re-export the symbol of the same name.
from core.project.run_refs import (  # noqa: F401  (Continue to use this name externally).
    ALL_STEP_RUN_REFS,
    MANUAL_SPECTRUM_RUN_REFS,
    STEP_RUN_REFS,
)

STATE_VERSION = 1
STATE_FILENAME = ".pipeline_state.json"

# The file fingerprint comes from core (single source, 2026-09-22): byte-identical to
# the previous implementation, and shared with the backend reuse check.


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def pipeline_state_path(manager: Any, exp_id: str, data_id: str) -> Path:
    """Status file location:<data directory base>/.pipeline_state.json."""
    return manager.data_base(exp_id, data_id) / STATE_FILENAME


def load_pipeline_state(manager: Any, exp_id: str, data_id: str) -> dict:
    """Read fingerprint status (Missing/Return empty status if damaged, no exception is thrown)."""
    path = pipeline_state_path(manager, exp_id, data_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "steps": {}}
    if not isinstance(raw, dict):
        return {"version": STATE_VERSION, "steps": {}}
    steps = raw.get("steps")
    return {
        "version": STATE_VERSION,
        "steps": steps if isinstance(steps, dict) else {},
    }


def save_pipeline_state(manager: Any, exp_id: str, data_id: str, state: dict) -> None:
    """Atomic write fingerprint state."""
    path = pipeline_state_path(manager, exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _raw_dir(manager: Any, exp_id: str, data_id: str) -> Path | None:
    """Parse the original data directory (prioritize the raw copy in the project, fallback to
    source)."""
    try:
        entry = manager.data(exp_id, data_id)
    # Directory parsing failure will be handled as no original directory.
    except Exception:  # noqa: BLE001 -
        return None
    raw = Path(entry.raw_dir) if getattr(entry, "raw_dir", "") else Path(entry.source)
    if not raw.is_absolute():
        raw = manager.root / raw
    return raw if raw.is_dir() else None


# Raw data authoritative input file (consistent with sha256:<name> of import WorkflowRun.inputs);
# processing in raw/ write down/mobile intermediates (fid/, mask/, ft/, etc.) does not count the
# input fingerprint.
_RAW_KEY_FILES = RAW_KEY_FILES  # single source: core/data/raw_fingerprint.py (2026-09-22)


def raw_fingerprint(manager: Any, exp_id: str, data_id: str) -> str | None:
    """Raw data input fingerprint: metadata.json content + authoritative Bruker input file
    fingerprint. 0.2.89: Only count acqus/acqu2s/acqu3s/ser/fid/nuslist -- 3D NUS processing
    will generate hundreds of intermediate files such as fid/, mask/, ft/ under raw/ and move
    individual files. If the full directory Scanning will cause the import/FID step misjudges
    OUTDATED after "generating spectrum", and each time the data is selected, the full hash of
    the directory will result UI Stuttering (measured 1537 file ~1s/Second-rate). Small files
    still use content hashing (<= 8MiB) to avoid backend touch-assisted file mtime misjudgment."""
    digest = hashlib.sha256()
    meta = manager.data_metadata_path(exp_id, data_id)
    try:
        digest.update(b"metadata:")
        digest.update(meta.read_bytes())
    except OSError:
        pass
    raw = _raw_dir(manager, exp_id, data_id)
    if raw is None:
        return digest.hexdigest()
    for name in _RAW_KEY_FILES:
        fp = file_fingerprint(raw / name)
        if fp is None:
            continue
        digest.update(f"|{name}:{fp}".encode())
    return digest.hexdigest()


def _fid_file(manager: Any, exp_id: str, data_id: str) -> Path | None:
    try:
        entry = manager.data(exp_id, data_id)
    except Exception:  # noqa: BLE001
        return None
    candidate = getattr(entry, "fid_path", "") or ""
    if candidate:
        path = Path(candidate)
        if not path.is_absolute():
            path = manager.root / path
        # 0.2.108: The merge of segmented collection FID is process/merged/fid directory
        # (incremental test%03d.fid), file or directory are regarded as products.
        if path.is_file() or path.is_dir():
            return path
    proc = manager.data_dir(exp_id, data_id, "process")
    try:
        fids = sorted(proc.glob("*.fid"))
    except OSError:
        fids = []
    if fids:
        return fids[0]
    merged_fid = proc / "merged" / "fid"
    if merged_fid.is_dir():
        return merged_fid
    return None


def _spectrum_file(manager: Any, exp_id: str, data_id: str) -> Path | None:
    """Compatible with internal calls; main score search rules are uniformly provided by
    core.project."""
    return find_primary_spectrum(manager, exp_id, data_id)


def _peaks_file(manager: Any, exp_id: str, data_id: str) -> Path | None:
    """Peak table file:.list takes priority (peak table is list), CSV is the bottom line."""
    for suffix in (".list", ".csv"):
        path = manager.data_dir(exp_id, data_id, "peaks") / (
            f"{exp_id}-{data_id}{suffix}"
        )
        if path.is_file():
            return path
    return None


def input_fingerprint(
    manager: Any, exp_id: str, data_id: str, step_id: str
) -> str | None:
    """Enter your fingerprint in the current step (for status verification)."""
    if step_id in ("import", "fid"):
        return raw_fingerprint(manager, exp_id, data_id)
    if step_id == "spectrum":
        fid = _fid_file(manager, exp_id, data_id)
        return file_fingerprint(fid) if fid is not None else None
    if step_id in ("smile", "peaks"):
        spectrum = _spectrum_file(manager, exp_id, data_id)
        return file_fingerprint(spectrum) if spectrum is not None else None
    return None


def script_fingerprint(
    manager: Any, exp_id: str, data_id: str, step_id: str
) -> str | None:
    """Step script fingerprint (fid.com in process/, old data fallback raw/; spectrum script in
    process/)."""
    if step_id == "fid":
        fid_com = manager.data_dir(exp_id, data_id, "process") / "fid.com"
        if not fid_com.is_file():
            raw = _raw_dir(manager, exp_id, data_id)
            fid_com = raw / "fid.com" if raw is not None else None
        return (
            file_fingerprint(fid_com)
            if fid_com is not None and fid_com.is_file()
            else None
        )
    if step_id == "spectrum":
        proc = manager.data_dir(exp_id, data_id, "process")
        try:
            coms = sorted(
                p
                for p in proc.glob("*.com")
                if p.name != "fid.com"
                and re.search(r"_nus_rank\d+\.com$", p.name, re.IGNORECASE) is None
            )
        except OSError:
            coms = []
        if not coms:
            return None
        digest = hashlib.sha256()
        for path in coms:
            digest.update(path.name.encode())
            digest.update((file_fingerprint(path) or "").encode())
        return digest.hexdigest()
    return None


def record_step_success(
    manager: Any,
    exp_id: str,
    data_id: str,
    step_id: str,
    params: dict[str, Any] | None = None,
) -> dict:
    """Register the fingerprint of a successful step (input/script/parameter hash) and return the
    merged status."""
    state = load_pipeline_state(manager, exp_id, data_id)
    steps = state.setdefault("steps", {})
    output = ""
    if step_id == "fid":
        fid = _fid_file(manager, exp_id, data_id)
        output = str(fid) if fid is not None else ""
    elif step_id == "spectrum":
        spectrum = _spectrum_file(manager, exp_id, data_id)
        output = str(spectrum) if spectrum is not None else ""
    elif step_id == "peaks":
        peaks = _peaks_file(manager, exp_id, data_id)
        output = str(peaks) if peaks is not None else ""
    entry = {
        "input_hash": input_fingerprint(manager, exp_id, data_id, step_id),
        "script_hash": script_fingerprint(manager, exp_id, data_id, step_id),
        "output": output,
    }
    if params:
        entry["params_hash"] = _sha256_text(json.dumps(params, sort_keys=True))
    steps[step_id] = entry
    save_pipeline_state(manager, exp_id, data_id, state)
    return state


__all__ = [
    "STATE_FILENAME",
    "file_fingerprint",
    "input_fingerprint",
    "load_pipeline_state",
    "pipeline_state_path",
    "raw_fingerprint",
    "record_step_success",
    "save_pipeline_state",
    "script_fingerprint",
]

