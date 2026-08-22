"""终谱 → Sparky UCSF 转换(NMRPipe pipe2ucsf,0.2.162-补15)。

生成谱图步骤在终谱归位后顺带产出 ``spectra/<data_id>.ucsf``,供
Sparky/POKY 等工具直接打开。转换在 Linux(csh)环境用 NMRPipe 自带
``pipe2ucsf`` 执行;失败只降级(日志提示),不影响主谱图生成流程。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def export_ucsf(
    source: Path | str,
    target: Path | str,
    *,
    run: Callable[..., Any] | None = None,
) -> tuple[str | None, str]:
    """用 pipe2ucsf 把 NMRPipe 谱(ft2/ft3)转为 Sparky UCSF 文件。

    run 可注入(测试用);缺省用后端 csh 运行时(``source ~/.cshrc`` 后
    执行,VM 环境已安装 pipe2ucsf)。返回 (ucsf 路径或 None, 消息),
    转换失败不抛异常。
    """
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        return None, f"源谱不存在,跳过 UCSF 转换: {source}"
    if run is None:
        from backend.runtime import CshRuntime

        run = CshRuntime().run
    try:
        result = run(
            ["pipe2ucsf", str(source), str(target)],
            cwd=str(source.parent),
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001 - 工具缺失/执行异常不阻断谱图生成
        return None, f"pipe2ucsf 执行失败,跳过 UCSF 转换: {exc}"
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
        return None, f"pipe2ucsf 转换失败({detail}),跳过 UCSF 转换"
    return str(target), f"UCSF 已生成: {target}"


__all__ = ["export_ucsf"]
