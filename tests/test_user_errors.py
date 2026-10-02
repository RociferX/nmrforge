"""Phase 21: translation of user-visible error messages, plus the guard against handing a
bare type name to the user."""

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
def test_describe_exception_maps_user_fixable_failures(exc: BaseException, expected: str) -> None:
    """The failure classes a user can fix -> one actionable Chinese sentence."""
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
    """Structure mismatch: one sentence of explanation, with the type name kept (nothing
    swallowed, and never the type name alone).
    """
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
    """Guard: gui/ and viewer/ must not hand the user a bare "type name: message"
    (Phase 21).

    The type name plus the original text still appears in the log/debug channel; this
    only constrains the **user-visible exits**.
    """
    offenders: list[str] = []
    for base in ("gui", "viewer"):
        for path in sorted((ROOT / base).glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "type(exc).__name__" in line or "type(err).__name__" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, "用户可见错误信息不得只给类型名: " + ", ".join(offenders)
