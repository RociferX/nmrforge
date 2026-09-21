"""Direct-dimension range (ppm): parsing, validation and recording.

Requested by the user on 2026-09-14: "can the API take a direct-dimension range?"

Semantics (aligned with NMRPipe `EXT -x1/-xn` and `processing.ext_lo/ext_hi`):

- ``ext_lo`` is the **high end** of the direct dimension (larger ppm, EXT -x1);
- ``ext_hi`` is the **low end** (smaller ppm, EXT -xn).

Accepted forms:

- ``direct_range=(10.5, 6.5)`` (high, low; same order as ext_lo/ext_hi);
- ``direct_range=(6.5, 10.5)`` (low, high) is swapped back and ``swapped`` is set;
- ``direct_range={"lo": 10.5, "hi": 6.5}`` / ``{"ext_lo": …, "ext_hi": …}``;
- explicit ``ext_lo=`` / ``ext_hi=`` override the corresponding item;
- ``params={"ext_lo": ..., "ext_hi": ...}`` still works and is recorded the same way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nmrforge_api.errors import SweepError
from ui_support.i18n import tr

_EPS = 1e-9


def _as_float(value: Any, label: str) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SweepError(tr(
            "direct-dimension range {p0} is not a number: "
            "{p1!r}",
            p0=label,
            p1=value,
        )) from exc
    if not (-1e6 < number < 1e6):
        raise SweepError(
            tr(
            "direct-dimension range {p0} is outside a sensible ppm range: "
            "{p1!r}",
            p0=label,
            p1=value,
        )
        )
    return number


@dataclass(frozen=True)
class DirectRange:
    """Direct-dimension range (ppm): ``lo`` = ext_lo (high), ``hi`` = ext_hi (low).

    ``source`` records where it came from (P1-4): ``explicit`` when a ``direct_range=`` /
    ``ext_lo=`` / ``ext_hi=`` argument was given, ``params`` when it came only from the
    legacy ``params={'ext_lo': ...}`` spelling. With none of them,
    :func:`direct_range_record` records ``default`` (the backend/config default) plus a
    warning.
    """

    lo: float
    hi: float
    requested: tuple[float, float] = ()
    swapped: bool = False
    source: str = "explicit"

    def params(self) -> dict[str, str]:
        """Backend parameter keys (``ext_lo``/``ext_hi``, strings, same shape as GUI/config)."""
        return {"ext_lo": f"{self.lo:g}", "ext_hi": f"{self.hi:g}"}

    def matches_params(self, params: Mapping[str, Any] | None) -> bool:
        """Whether this matches the ext_lo/ext_hi of some resolved parameter set."""
        if not params:
            return False
        raw_lo = params.get("ext_lo")
        raw_hi = params.get("ext_hi")
        if raw_lo in (None, "") or raw_hi in (None, ""):
            return False
        try:
            current_lo = float(str(raw_lo))
            current_hi = float(str(raw_hi))
        except (TypeError, ValueError):
            return False
        return abs(current_lo - self.lo) < 1e-6 and abs(current_hi - self.hi) < 1e-6

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ext_lo": self.lo,
            "ext_hi": self.hi,
            "unit": "ppm",
            "source": str(self.source),
        }
        if self.requested:
            payload["requested"] = [float(self.requested[0]), float(self.requested[1])]
        if self.swapped:
            payload["swapped_to_nmrpipe_order"] = True
        return payload


def parse_direct_range(
    value: Any = None,
    *,
    ext_lo: Any = None,
    ext_hi: Any = None,
    params: Mapping[str, Any] | None = None,
) -> DirectRange | None:
    """Parse a direct-dimension range; ``None`` when nothing was supplied.

    Precedence: ``params`` (compatibility) < ``direct_range`` < explicit
    ``ext_lo``/``ext_hi``.

    Parameters
    ----------
    value : Any, optional
        ``(high, low)`` tuple/list, ``{"high": ..., "low": ...}`` dict, or None.
    ext_lo, ext_hi : Any, optional
        Explicit bounds; these win over ``value``.
    params : Mapping[str, Any], optional
        Compatibility: read ``params["ext_lo"]``/``["ext_hi"]``; lowest precedence.

    Returns
    -------
    DirectRange | None
        The normalised range (``high``/``low`` as ppm strings), or None if no source supplied one.

    Raises
    ------
    SweepError
        Bounds in an unresolvable order, a non-numeric value, or high == low.

    Side effects
    ------------
    Pure parsing, no side effects.

    Examples
    --------
        parse_direct_range((10.5, 6.5))
        parse_direct_range({"high": 10.5, "low": 6.5})
    """
    raw_lo = params.get("ext_lo") if params else None
    raw_hi = params.get("ext_hi") if params else None
    if value is not None:
        if isinstance(value, Mapping):
            if "ext_lo" in value or "lo" in value:
                raw_lo = value.get("ext_lo", value.get("lo"))
            if "ext_hi" in value or "hi" in value:
                raw_hi = value.get("ext_hi", value.get("hi"))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if len(value) != 2:
                raise SweepError(
                    tr("direct_range needs two ppm values (high, low); got {p0!r}", p0=value)
                )
            raw_lo, raw_hi = value
        else:
            raise SweepError(
                tr("direct_range is malformed: use (high_ppm, low_ppm) or {'lo': …, 'hi': …}")
            )
    if ext_lo is not None:
        raw_lo = ext_lo
    if ext_hi is not None:
        raw_hi = ext_hi
    if raw_lo is None and raw_hi is None:
        return None
    if raw_lo is None or raw_hi is None:
        raise SweepError(
            tr(
                "give the whole range: direct_range=(high_ppm, low_ppm), or both ext_lo= and "
                "ext_hi=",
            )
        )
    first = _as_float(raw_lo, "ext_lo")
    second = _as_float(raw_hi, "ext_hi")
    if abs(first - second) < _EPS:
        raise SweepError(tr("direct-dimension range ends are identical ({p0:g} ppm)", p0=first))
    swapped = first < second
    lo, hi = (second, first) if swapped else (first, second)
    # P1-4: record the origin - an explicit entry point vs the legacy params spelling
    source = (
        "explicit"
        if (value is not None or ext_lo is not None or ext_hi is not None)
        else "params"
    )
    return DirectRange(
        lo=lo, hi=hi, requested=(first, second), swapped=swapped, source=source
    )


def resolved_ext_range(
    effective: Mapping[str, Any] | None,
) -> tuple[float, float] | None:
    """The direct-dimension range in the effective params (``ext_lo/ext_hi``, else
    ``final_ext_*``)."""
    if not effective:
        return None
    for lo_key, hi_key in (("ext_lo", "ext_hi"), ("final_ext_lo", "final_ext_hi")):
        lo = effective.get(lo_key)
        hi = effective.get(hi_key)
        if lo in (None, "") or hi in (None, ""):
            continue
        try:
            return (float(str(lo)), float(str(hi)))
        except (TypeError, ValueError):
            continue
    return None


def direct_range_record(
    direct: DirectRange | None, effective: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The reference-layer record of the direct-dimension range (P1-4).

    ``source`` is ``explicit``, ``params`` or ``default``. ``default`` means the caller
    gave no range at all, so the backend/config default applies; the record then carries
    a ``warning`` plus whatever range actually took effect (``ext_lo/ext_hi`` or
    ``final_ext_*`` from ``effective``), so a third party never has to guess whether
    "10.5/6.5" was written explicitly or simply omitted.
    """
    if direct is not None:
        return direct.to_dict()
    payload: dict[str, Any] = {
        "ext_lo": None,
        "ext_hi": None,
        "unit": "ppm",
        "source": "default",
    }
    resolved = resolved_ext_range(effective)
    if resolved is not None:
        payload["ext_lo"], payload["ext_hi"] = resolved
    payload["warning"] = (
        tr(
            "the reference mode was not given an explicit direct-dimension range (direct_range= / "
            "ext_lo= / ext_hi=): the backend/config default is in use, and the frozen record "
            "cannot tell an explicitly written default from an omission - write the default out "
            "explicitly if that is what you "
            "want",
        )
    )
    return payload


def direct_matches_ext(
    direct: DirectRange, ext: tuple[float, float] | None
) -> bool:
    """Whether ``direct`` equals ``(ext_lo, ext_hi)`` (1e-6 tolerance; None -> False)."""
    if ext is None:
        return False
    return abs(ext[0] - direct.lo) < 1e-6 and abs(ext[1] - direct.hi) < 1e-6



__all__ = [
    "DirectRange",
    "direct_matches_ext",
    "direct_range_record",
    "parse_direct_range",
    "resolved_ext_range",
]
