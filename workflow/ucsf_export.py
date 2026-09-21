"""Final spectrum -> Sparky UCSF conversion (NMRPipe pipe2ucsf,0.2.162-patch15). The spectrum
generation step will produce ``spectra/<data_id>.ucsf`` after the final spectrum is returned,
which can be opened directly by tools such as Sparky/POKY. The conversion is executed in the
Linux (csh) environment using NMRPipe's own ``pipe2ucsf``; failure will only downgrade (log
prompt) and does not affect the main spectrum generation process."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ui_support.i18n import tr


def export_ucsf(
    source: Path | str,
    target: Path | str,
    *,
    run: Callable[..., Any] | None = None,
) -> tuple[str | None, str]:
    """Use pipe2ucsf to convert NMRPipe spectrum (ft2/ft3) to Sparky UCSF file. run can be injected
    (for testing); the default is to use the backend csh runtime (``source ~/.cshrc`` after
    execution, VM environment has installed pipe2ucsf). Return (ucsf path or None, message), no
    exception will be thrown if the conversion fails."""
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        return None, tr("The source spectrum does not exist, skip UCSF conversion: {p0}", p0=source)
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        return None, tr("Unable to clean old UCSF files, skipping conversion: {p0}", p0=exc)
    if run is None:
        from backend.runtime import CshRuntime

        run = CshRuntime().run
    try:
        result = run(
            ["pipe2ucsf", str(source), str(target)],
            cwd=str(source.parent),
            timeout=300,
        )
    # Tool missing/Execution exceptions are not blocked spectrum generation.
    except Exception as exc:  # noqa: BLE001 -
        return None, tr("pipe2ucsf execution failed, skipping UCSF conversion: {p0}", p0=exc)
    if (
        getattr(result, "returncode", 1) != 0
        or not target.is_file()
        or target.stat().st_size == 0
    ):
        tail = (
            str(getattr(result, "stderr", "") or getattr(result, "stdout", ""))
            .strip()
            .splitlines()
        )
        detail = tail[-1] if tail else f"rc={getattr(result, 'returncode', '?')}"
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None, tr("pipe2ucsf conversion failed ({p0}), skip UCSF conversion", p0=detail)
    return str(target), tr("UCSF has generated: {p0}", p0=target)


__all__ = ["export_ucsf"]
