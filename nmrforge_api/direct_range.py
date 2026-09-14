"""直接维范围(ppm)输入:解析 / 校验 / 落档。

用户 2026-09-14:「api 可以指定直接维范围吗,没有就加入」。

语义(与 NMRPipe `EXT -x1/-xn` 及 config 的 `processing.ext_lo/ext_hi` 对齐):

- ``ext_lo`` = 直接维**高端**(较大 ppm,EXT -x1);
- ``ext_hi`` = 直接维**低端**(较小 ppm,EXT -xn)。

入口写法:

- ``direct_range=(10.5, 6.5)``(high, low;与 ext_lo/ext_hi 同序);
- ``direct_range=(6.5, 10.5)``(low, high)→ 自动换回规范序并在 ``swapped`` 记真;
- ``direct_range={"lo": 10.5, "hi": 6.5}`` / ``{"ext_lo": …, "ext_hi": …}``;
- 显式 ``ext_lo=`` / ``ext_hi=``(覆盖 ``direct_range`` 对应项);
- 也可以继续写在 ``params={"ext_lo": …, "ext_hi": …}``(会被解析并统一留档)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nmrforge_api.errors import SweepError

_EPS = 1e-9


def _as_float(value: Any, label: str) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SweepError(f"直接维范围 {label} 不是数值: {value!r}") from exc
    if not (-1e6 < number < 1e6):
        raise SweepError(f"直接维范围 {label} 超出合理区间(ppm): {value!r}")
    return number


@dataclass(frozen=True)
class DirectRange:
    """直接维范围(ppm):``lo`` = ext_lo(高端),``hi`` = ext_hi(低端)。"""

    lo: float
    hi: float
    requested: tuple[float, float] = ()
    swapped: bool = False

    def params(self) -> dict[str, str]:
        """→ 后端参数键(``ext_lo``/``ext_hi``,字符串,与 GUI/config 同形)。"""
        return {"ext_lo": f"{self.lo:g}", "ext_hi": f"{self.hi:g}"}

    def matches_params(self, params: Mapping[str, Any] | None) -> bool:
        """与某份有效参数里的 ext_lo/ext_hi 是否一致(缺任一 → False)。"""
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
    """解析直接维范围;没给任何输入返回 ``None``,非法抛 :class:`SweepError`。

    输入优先级为 ``params``(兼容) < ``direct_range`` < 显式
    ``ext_lo``/``ext_hi``。
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
                    f"direct_range 需要两个 ppm 值(high, low),收到 {value!r}"
                )
            raw_lo, raw_hi = value
        else:
            raise SweepError(
                "direct_range 写法不对:用 (high_ppm, low_ppm) 或 "
                "{'lo': …, 'hi': …}"
            )
    if ext_lo is not None:
        raw_lo = ext_lo
    if ext_hi is not None:
        raw_hi = ext_hi
    if raw_lo is None and raw_hi is None:
        return None
    if raw_lo is None or raw_hi is None:
        raise SweepError(
            "直接维范围要给全:direct_range=(high_ppm, low_ppm) 或 ext_lo=/ext_hi= 同时给"
        )
    first = _as_float(raw_lo, "ext_lo")
    second = _as_float(raw_hi, "ext_hi")
    if abs(first - second) < _EPS:
        raise SweepError(f"直接维范围两端相同({first:g} ppm),不是有效范围")
    swapped = first < second
    lo, hi = (second, first) if swapped else (first, second)
    return DirectRange(lo=lo, hi=hi, requested=(first, second), swapped=swapped)


__all__ = ["DirectRange", "parse_direct_range"]
