"""用户可见错误信息的统一翻译(Phase 21)。

规则(见 ``docs/development.md``「用户可见错误与调试信息」):

- 用户看到的消息必须能照着改:路径/权限/输入格式/缺少字段各有明确说法;
- **禁止**只把 ``KeyError`` / ``IndexError`` / ``TypeError`` / ``NoneType`` 之类的
  类型名或裸 traceback 丢给用户;
- 完整 traceback 走 debug 通道(CLI 的 ``--debug`` / ``NMRFORGE_DEBUG=1``,GUI 的日志)。

本模块是 Qt-free 的叶子模块:``gui/``、``viewer/``、``nmrforge_api/`` 只读引用。
"""

from __future__ import annotations

__all__ = ["describe_exception", "user_error_text"]


def describe_exception(exc: BaseException) -> str:
    """把常见异常翻译成用户能照着改的一句话。

    只映射**用户可修**的几类:路径/权限、输入内容不合法、缺少必需字段、结构与预期
    不符。未知异常保留类型名与原文——既不吞掉信息,也不编造原因。
    """
    if isinstance(exc, FileNotFoundError):
        return f"找不到文件或目录: {exc.filename or exc}"
    if isinstance(exc, NotADirectoryError):
        return f"路径不是目录: {exc.filename or exc}"
    if isinstance(exc, IsADirectoryError):
        return f"路径是目录,但这里需要文件: {exc.filename or exc}"
    if isinstance(exc, PermissionError):
        return f"没有权限访问: {exc.filename or exc}"
    if isinstance(exc, KeyError):
        return f"缺少必需字段 {exc}(输入文件或运行记录与当前版本不匹配?)"
    if isinstance(exc, (IndexError, TypeError, AttributeError)):
        return f"输入数据与预期结构不符({type(exc).__name__}): {exc}"
    if isinstance(exc, ValueError):
        return f"输入内容不合法: {exc}"
    if isinstance(exc, OSError):
        return f"文件/系统操作失败: {exc}"
    return f"{type(exc).__name__}: {exc}"


def user_error_text(exc: BaseException) -> str:
    """GUI 调用点用的语义别名(与 :func:`describe_exception` 同源,便于阅读)。"""
    return describe_exception(exc)
