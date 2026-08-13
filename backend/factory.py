"""后端工厂：按配置创建后端实例。"""

from __future__ import annotations

from typing import Any

from backend.base import ProcessingBackend
from backend.native_backend import NativeBackend
from backend.nmrpipe_backend import NMRPipeBackend


def create_backend(config: dict[str, Any]) -> ProcessingBackend:
    """根据 config['backend']['provider'] 返回后端（默认 nmrpipe）。"""
    backend_cfg = config.get("backend", {})
    provider = str(backend_cfg.get("provider", "nmrpipe"))
    if provider == "native":
        return NativeBackend()
    if provider == "nmrpipe":
        from backend.config import nmrpipe_path

        return NMRPipeBackend(nmrpipe_bin=nmrpipe_path(config))
    raise ValueError(f"未知后端 provider: {provider}")
