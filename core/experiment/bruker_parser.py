"""Bruker parameter parsing (acqus / acqu2s / acqu3s).

Requirements:
- support multi-line arrays and the stripping of quotes/angle brackets (the TopSpin 4 indirect
  dimension parameters live in acqu2s/acqu3s);
- support aliases and missing parameters, and never crash because one parameter is absent;
- only "read the raw parameters"; the semantic interpretation belongs to the layer above.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_PARAM_PREFIX = "##$"


def _to_scalar(token: str) -> Any:
    """Convert a single token to int/float, keeping the string when that fails."""
    token = token.strip()
    if not token:
        return ""
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def parse_param_file(path: Path) -> dict[str, Any]:
    """Parse one parameter file and return a key/value dictionary.

    Rules:
    - only collect `##$KEY= value` parameters;
    - values may continue across lines (until the next `##$`/`##` line or a blank line);
    - quotes and angle brackets are stripped; one token becomes a scalar, several tokens a list.
    """
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    params: dict[str, Any] = {}
    current_key: str | None = None
    current_tokens: list[str] = []
    quoted = False

    def flush() -> None:
        nonlocal current_key, current_tokens, quoted
        if current_key is None:
            return
        if quoted:
            value: Any = " ".join(current_tokens).strip()
        elif len(current_tokens) == 1:
            value = _to_scalar(current_tokens[0])
        else:
            value = [_to_scalar(t) for t in current_tokens]
        params[current_key] = value
        current_key = None
        current_tokens = []
        quoted = False

    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith(_PARAM_PREFIX):
            flush()
            rest = stripped[len(_PARAM_PREFIX) :]
            key, _, raw_value = rest.partition("=")
            current_key = key.strip()
            raw_value = raw_value.strip()
            if raw_value.startswith(("\"", "'")):
                quoted = True
                raw_value = raw_value[1:]
                if raw_value.endswith(("\"", "'")):
                    raw_value = raw_value[:-1]
            raw_value = raw_value.strip("<>")
            current_tokens = raw_value.split()
        elif stripped.startswith("##") or stripped == "":
            flush()
        else:
            current_tokens.extend(stripped.split())
    flush()
    return params


def parse_dataset_params(dataset_dir: Path) -> dict[str, Any]:
    """Parse the parameters of a whole dataset (acqus + acqu2s + acqu3s, kept per dimension)."""
    result: dict[str, Any] = {}
    order: list[str] = []
    for name in ("acqus", "acqu2s", "acqu3s"):
        path = dataset_dir / name
        if path.exists():
            order.append(name)
            result[name] = parse_param_file(path)
    # 0.2.199-patch29gk: acqu holds the authoritative direct-dimension acquisition parameters
    # (acqus sometimes writes TD as 0, e.g. acqus TD=0 while acqu TD is fine, which used to make
    # the patched fid.com hang with xN=0); acqu is read in here as a fallback source of that TD.
    acq_path = dataset_dir / "acqu"
    if acq_path.exists():
        result["acqu"] = parse_param_file(acq_path)
    result["order"] = order
    return result
