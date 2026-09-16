"""Phase 21:用户可见错误信息的翻译,以及「不把裸类型名交给用户」守卫。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.user_errors import describe_exception, user_error_text

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (FileNotFoundError(2, "no such file", "ref.json"), "找不到文件或目录"),
        (NotADirectoryError(20, "not a dir", "x.txt"), "路径不是目录"),
        (IsADirectoryError(21, "is a dir", "x"), "路径是目录"),
        (PermissionError(13, "denied", "x"), "没有权限访问"),
        (KeyError("peak_id"), "缺少必需字段"),
        (ValueError("bad axis spec"), "输入内容不合法"),
        (OSError("disk gone"), "文件/系统操作失败"),
    ],
)
def test_describe_exception_maps_user_fixable_failures(
    exc: BaseException, expected: str
) -> None:
    """用户能自己修的几类失败 → 一句可照做的中文提示。"""
    message = describe_exception(exc)
    assert expected in message
    assert message.strip()


@pytest.mark.parametrize(
    "exc",
    [
        IndexError("index out of range"),
        TypeError("unsupported operand"),
        AttributeError("'NoneType' object has no attribute 'x'"),
    ],
)
def test_structure_failures_explain_without_swallowing(exc: BaseException) -> None:
    """结构不符:带一句解释,同时保留类型名(不吞信息、也不只给类型名)。"""
    message = describe_exception(exc)
    assert message.startswith("输入数据与预期结构不符")
    assert type(exc).__name__ in message


def test_unknown_exception_keeps_type_and_text() -> None:
    class Custom(RuntimeError):
        pass

    assert describe_exception(Custom("boom")) == "Custom: boom"


def test_user_error_text_is_the_same_translator() -> None:
    exc = KeyError("x")
    assert user_error_text(exc) == describe_exception(exc)


def test_gui_never_shows_a_bare_exception_type_name() -> None:
    """守卫:gui/ 与 viewer/ 不得把「类型名: 消息」直接交给用户(Phase 21)。

    类型名 + 原始文本仍会出现在日志/调试通道里;这条只约束**用户可见出口**。
    """
    offenders: list[str] = []
    for base in ("gui", "viewer"):
        for path in sorted((ROOT / base).glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "type(exc).__name__" in line or "type(err).__name__" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, "用户可见错误信息不得只给类型名: " + ", ".join(offenders)
