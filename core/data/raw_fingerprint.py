"""Raw-data fingerprint (Qt-free **single source**, 2026-09-22).

The GUI step status (``gui/pipeline_state.py``) and the backend decision whether an
already converted fid may be reused (``backend/nmrpipe_backend.py``) share this
definition: which files count, how small files are hashed and how large ones are
summarised. Two copies of that rule drift apart (2026-09-22 external review: reused
products had no fingerprint check).

This module imports no Qt and depends on no project model, so core / backend / gui
can all use it.

Scope (2026-09-22 external review): **this is not content attestation**. Files up to 8 MiB are
hashed by content; larger ones (such as `ser`) fall back to a `size + mtime_ns` digest, which
detects that a file was rewritten or replaced but does not prove the input was not tampered
with. For content-level proof, hash once at import time and store that in the project record
instead of hashing again on every refresh (which would fight the speed-first tradeoff).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

__all__ = [
    "RAW_KEY_FILES",
    "SMALL_FILE_LIMIT",
    "file_fingerprint",
    "raw_dir_fingerprint",
]

#: Authoritative raw input files (the sha256:<name> set of an import WorkflowRun).
#: Intermediate products written or moved under raw/ (fid/, mask/, ft/, ...) are not
#: part of the input fingerprint.
RAW_KEY_FILES = ("acqus", "acqu2s", "acqu3s", "ser", "fid", "nuslist")

#: Files up to this size are hashed by content; larger ones use a size+mtime_ns digest.
SMALL_FILE_LIMIT = 8 * 1024 * 1024


def file_fingerprint(path: Path | str) -> str | None:
    """File fingerprint: <= 8MiB chunked SHA-256, larger files size+mtime_ns, else None."""
    target = Path(path)
    try:
        st = target.stat()
    except OSError:
        return None
    if st.st_size <= SMALL_FILE_LIMIT:
        digest = hashlib.sha256()
        try:
            with target.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            return None
        return digest.hexdigest()
    digest = hashlib.sha256()
    digest.update(f"stat:{st.st_size}:{st.st_mtime_ns}".encode())
    return digest.hexdigest()


def raw_dir_fingerprint(raw_dirs: Any) -> str:
    """Fingerprint of one or more raw directories: names plus the key-file fingerprints."""
    if isinstance(raw_dirs, (str, Path)):
        dirs = [Path(raw_dirs)]
    else:
        dirs = [Path(item) for item in (raw_dirs or [])]
    digest = hashlib.sha256()
    for raw in dirs:
        digest.update(f"|dir:{raw.name}".encode())
        for name in RAW_KEY_FILES:
            fp = file_fingerprint(raw / name)
            if fp is None:
                continue
            digest.update(f"|{name}:{fp}".encode())
    return digest.hexdigest()
