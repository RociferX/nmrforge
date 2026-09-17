"""统一 logging 配置(Phase 22)。

约定(见 ``docs/development.md``「日志(Phase 22)」):

- 库代码只做 ``logging.getLogger("nmrforge.<module>")``,不在 import 期配置 handler;
- 入口负责配置:GUI 入口 ``main.py`` 与命令行 ``python -m nmrforge_api`` 都调用
  :func:`configure_logging`;
- 级别由 ``NMRFORGE_LOG_LEVEL``(DEBUG/INFO/WARNING/ERROR,默认 WARNING)或显式参数给出;
- 日志文本里的绝对路径用 :func:`sanitize_path` 把 home 折成 ``~``(脱敏);
- 单次运行的日志文件用 :func:`attach_run_log` 挂上 ``<run_dir>/run.log``(延迟创建:没有
  记录就不留空文件),结束时 :func:`detach_run_log` 收回。

本模块 Qt-free、无第三方依赖,属于 ``core/`` 叶子模块。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import IO, Any

__all__ = [
    "DEFAULT_LEVEL",
    "LEVEL_ENV",
    "LOG_FORMAT",
    "LOGGER_NAME",
    "append_run_log_line",
    "attach_run_log",
    "configure_logging",
    "detach_run_log",
    "resolve_level",
    "sanitize_path",
]

#: 全局 logger 名(库代码统一挂在这一层下面)
LOGGER_NAME = "nmrforge"
#: 级别环境变量(DEBUG/INFO/WARNING/ERROR;非法值回退 WARNING)
LEVEL_ENV = "NMRFORGE_LOG_LEVEL"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DEFAULT_LEVEL = "WARNING"
#: 标记入口装的 console handler,避免重复调用时叠加
_OWN_HANDLER_FLAG = "_nmrforge_handler"
#: 单独标记逐 run 的 FileHandler(不能与 console handler 混为一谈,
#: 否则有 run 在跑时 configure_logging 会以为「已经装过了」而不再装 console handler)
_RUN_HANDLER_FLAG = "_nmrforge_run_handler"


def resolve_level(level: str | int | None = None) -> int:
    """把 ``--level``/环境变量/数字解析成 logging 级别(非法/未知回退 WARNING)。"""
    if isinstance(level, int):
        return int(level)
    name = str(level or os.environ.get(LEVEL_ENV, DEFAULT_LEVEL)).strip().upper()
    value: Any = logging.getLevelName(name)
    return int(value) if isinstance(value, int) else logging.WARNING


def configure_logging(
    level: str | int | None = None,
    *,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """配置 ``nmrforge`` logger(幂等:重复调用只更新级别,不叠加 console handler)。

    日志默认写 stderr——CLI 的 stdout 是给机器读的 JSON,不能被日志污染。
    显式给 ``stream`` 时,复用/改向 console handler 到该流(测试与嵌入调用需要)。
    逐 run 的 FileHandler 由 :func:`attach_run_log` 管理,不参与这里的判定。
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolve_level(level))
    logger.propagate = False
    console = next(
        (h for h in logger.handlers if getattr(h, _OWN_HANDLER_FLAG, False)), None
    )
    if console is None:
        console = logging.StreamHandler(stream if stream is not None else sys.stderr)
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        setattr(console, _OWN_HANDLER_FLAG, True)
        logger.addHandler(console)
    elif stream is not None and isinstance(console, logging.StreamHandler):
        console.setStream(stream)
    return logger


def attach_run_log(
    directory: Path | str, *, logger: logging.Logger | None = None
) -> logging.Handler:
    """给单次运行挂 ``<directory>/run.log``(目录不存在则创建),返回 handler。"""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    # delay=True:只有真的写出日志记录时才建 run.log,不给成功的 run 留空文件
    handler = logging.FileHandler(target_dir / "run.log", encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _RUN_HANDLER_FLAG, True)
    (logger or logging.getLogger(LOGGER_NAME)).addHandler(handler)
    return handler


def append_run_log_line(path: Path | str, message: str) -> None:
    """直接往 run.log 追加一行(绕开 logger 级别)。

    「每次 run 都要有 run.log」不能依赖全局级别:默认 WARNING 下 INFO 记录会被丢掉。
    因此 run 的头/尾两行直接写文件(格式与 :data:`LOG_FORMAT` 一致),消息里的 home
    绝对路径按 :func:`sanitize_path` 脱敏。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    line = f"{stamp} INFO nmrforge.run: {sanitize_path(message)}\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)


def detach_run_log(handler: logging.Handler | None) -> None:
    """收回 :func:`attach_run_log` 的 handler(可重复调用;始终关闭文件)。"""
    if handler is None:
        return
    for logger in (logging.getLogger(LOGGER_NAME), logging.getLogger()):
        if handler in logger.handlers:
            logger.removeHandler(handler)
    try:
        handler.close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
        pass


def sanitize_path(value: Any) -> str:
    """日志文本脱敏:把用户 home 折成 ``~``(Phase 22「敏感绝对路径」要求)。"""
    text = str(value)
    try:
        home = str(Path.home())
    except Exception:  # noqa: BLE001 - 取不到 home 就原样返回
        return text
    if home and home in text:
        text = text.replace(home, "~")
    return text
