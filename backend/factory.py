"""后端工厂：按配置创建后端实例。"""

from __future__ import annotations

from typing import Any

from backend.base import ProcessingBackend
from backend.nmrpipe_backend import NMRPipeBackend

SUPPORTED_PROVIDERS: tuple[str, ...] = ("nmrpipe",)


def create_backend(config: dict[str, Any]) -> ProcessingBackend:
    """根据 config['backend']['provider'] 返回后端(默认 nmrpipe)。

    STUB-013(2026-09-12):未实现的 ``native`` 纯 Python 后端骨架已删除,
    可选 provider 只保留真正可用的项;未知 provider 在创建期即失败,
    不再允许运行到中途才抛 NotImplementedError。
    """
    backend_cfg = config.get("backend") or {}
    provider = (
        str(backend_cfg.get("provider", "nmrpipe") or "nmrpipe").strip().lower()
    )
    if provider == "nmrpipe":
        from backend.config import nmrpipe_path

        return NMRPipeBackend(nmrpipe_bin=nmrpipe_path(config))
    supported = "、".join(SUPPORTED_PROVIDERS)
    raise ValueError(f"未知后端 provider: {provider}(当前仅支持 {supported})")
