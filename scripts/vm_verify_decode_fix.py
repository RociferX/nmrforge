"""VM 验证:修复后 CshRuntime 对坏字节输出容错(不抛 UnicodeDecodeError)。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/home/<lab-user>/NMRForge")

from backend.runtime import CshRuntime  # noqa: E402


def main() -> int:
    work = Path("/tmp/verify_decode_fix")
    work.mkdir(exist_ok=True)
    script = work / "bad.com"
    # echo 参数里直接放原始坏字节(0xEF 0x40),不经 \x 转义
    script.write_bytes(
        b"#!/bin/csh\n"
        b"echo before\n"
        b"echo raw:" + b"\xef\x40" + b":end\n"
        b"echo after\n"
    )
    rt = CshRuntime()
    try:
        res = rt.run(["csh", "bad.com"], cwd=str(work), on_line=print)
        print("rc:", res.returncode)
        print("OK: no UnicodeDecodeError")
    except Exception as exc:  # noqa: BLE001
        print("RAISED:", type(exc).__name__, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
