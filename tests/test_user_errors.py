"""Phase 21: the user-facing error translation, and the "never show a bare type name" guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.user_errors import describe_exception, user_error_text

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (FileNotFoundError(2, "no such file", "ref.json"), "File or directory not found"),
        (NotADirectoryError(20, "not a dir", "x.txt"), "Path is not a directory"),
        (IsADirectoryError(21, "is a dir", "x"), "Path is a directory"),
        (PermissionError(13, "denied", "x"), "Permission denied"),
        (KeyError("peak_id"), "Missing required field"),
        (ValueError("bad axis spec"), "Invalid input"),
        (OSError("disk gone"), "File or system operation failed"),
    ],
)
def test_describe_exception_maps_user_fixable_failures(
    exc: BaseException, expected: str
) -> None:
    """Failures the user can fix themselves map to one actionable sentence."""
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
    """A structure mismatch gets an explanation *and* keeps the type name."""
    message = describe_exception(exc)
    assert message.startswith("Input does not match the expected structure")
    assert type(exc).__name__ in message


def test_unknown_exception_keeps_type_and_text() -> None:
    class Custom(RuntimeError):
        pass

    assert describe_exception(Custom("boom")) == "Custom: boom"


def test_user_error_text_is_the_same_translator() -> None:
    exc = KeyError("x")
    assert user_error_text(exc) == describe_exception(exc)


def test_gui_never_shows_a_bare_exception_type_name() -> None:
    """Guard: ``gui/`` and ``viewer/`` must not hand the user "type name: message" (Phase 21).

    The type name and the raw text still appear in logs and on the debug channel; this
    guard only constrains the **user-visible exits**.
    """
    offenders: list[str] = []
    for base in ("gui", "viewer"):
        for path in sorted((ROOT / base).glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "type(exc).__name__" in line or "type(err).__name__" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, "user-visible errors must not be a bare type name: " + ", ".join(
        offenders
    )
