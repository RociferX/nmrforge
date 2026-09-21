"""Backend factory: create backend instances from configuration."""

from __future__ import annotations

from typing import Any

from backend.base import ProcessingBackend
from backend.nmrpipe_backend import NMRPipeBackend
from ui_support.i18n import tr

SUPPORTED_PROVIDERS: tuple[str, ...] = ("nmrpipe",)


def create_backend(config: dict[str, Any]) -> ProcessingBackend:
    """Return the backend named by config['backend']['provider'] (nmrpipe by
    default).

    STUB-013 (2026-09-12): the unimplemented ``native`` pure-Python backend
    skeleton has been removed and only genuinely usable providers are kept; an
    unknown provider fails at creation time rather than raising
    NotImplementedError halfway through a run.
    """
    backend_cfg = config.get("backend") or {}
    provider = (
        str(backend_cfg.get("provider", "nmrpipe") or "nmrpipe").strip().lower()
    )
    if provider == "nmrpipe":
        from backend.config import nmrpipe_path

        return NMRPipeBackend(nmrpipe_bin=nmrpipe_path(config))
    supported = ",".join(SUPPORTED_PROVIDERS)
    raise ValueError(
        tr(
        "Unknown backend provider: {p0} (currently only {p1} is "
        "supported)",
        p0=provider,
        p1=supported,
    )
    )
