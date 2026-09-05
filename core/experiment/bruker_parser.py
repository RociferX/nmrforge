"""Bruker 参数解析（acqus / acqu2s / acqu3s）。

要求：
- 支持跨行数组、引号/尖括号剥离（TopSpin 4 间接维参数在 acqu2s/acqu3s）；
- 支持别名与缺失参数，不因某个参数缺失而崩溃；
- 只负责「读出原始参数」，语义判断交给上层。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_PARAM_PREFIX = "##$"


def _to_scalar(token: str) -> Any:
    """把单个 token 转成 int/float，失败则保留字符串。"""
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
    """解析单个参数文件，返回键值字典。

    规则：
    - 只收集 `##$KEY= value` 参数；
    - 值支持跨行续接（直到下一个 `##$`/`##` 行或空行）；
    - 剥离引号与尖括号；单 token 转标量，多 token 转 list。
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
    """解析整个数据集的参数（acqus + acqu2s + acqu3s，分维存放）。"""
    result: dict[str, Any] = {}
    order: list[str] = []
    for name in ("acqus", "acqu2s", "acqu3s"):
        path = dataset_dir / name
        if path.exists():
            order.append(name)
            result[name] = parse_param_file(path)
    # 0.2.199-补29gk:acqu 是直接维权威采集参数(acqus 有时把 TD 写成 0,
    # 如数据 acqus TD=0 但 acqu TD 正常,导致补丁后 fid.com xN=0 转换卡死);
    # 这里额外读入 acqu 作为直接维 TD 的回退源。
    acq_path = dataset_dir / "acqu"
    if acq_path.exists():
        result["acqu"] = parse_param_file(acq_path)
    result["order"] = order
    return result
