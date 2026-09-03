"""VM 轻量验证:线程偏移配置(0.2.199-补24,不跑 SMILE)。"""

from __future__ import annotations

import sys

from backend.config import load_processing_defaults, resolve_nthread


def main() -> int:
    d = load_processing_defaults()
    print("thread_offset:", d["thread_offset"], "| auto nthread:", d["nthread"])
    cfg = {"smile": {"thread_offset": 6}}
    print("offset 6 -> nthread:", load_processing_defaults(cfg)["nthread"])
    print("resolve_nthread(0, cfg):", resolve_nthread(0, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
