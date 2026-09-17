"""Phase 22:统一 logging 配置、每 run ``run.log``、日志脱敏与「GUI 无裸 print」守卫。"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from core.logging_setup import (
    LEVEL_ENV,
    LOGGER_NAME,
    append_run_log_line,
    attach_run_log,
    configure_logging,
    detach_run_log,
    resolve_level,
    sanitize_path,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_logger():
    """测试自己装的 handler/级别不要泄漏给别的测试。"""
    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    yield
    for handler in list(logger.handlers):
        if handler not in before:
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(level)
    logger.propagate = propagate


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("debug", logging.DEBUG),
        ("INFO", logging.INFO),
        ("warning", logging.WARNING),
        ("error", logging.ERROR),
        (logging.INFO, logging.INFO),
    ],
)
def test_resolve_level_accepts_names_and_numbers(given, expected) -> None:
    assert resolve_level(given) == expected


def test_resolve_level_falls_back_to_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_level("nonsense") == logging.WARNING
    monkeypatch.setenv(LEVEL_ENV, "debug")
    assert resolve_level() == logging.DEBUG
    monkeypatch.setenv(LEVEL_ENV, "not-a-level")
    assert resolve_level() == logging.WARNING


def test_configure_logging_is_idempotent_and_writes_to_the_stream() -> None:
    """重复调用(入口 + 测试)不叠加 handler,否则同一条日志会重复输出。"""
    stream = io.StringIO()
    logger = configure_logging(level="INFO", stream=stream)
    count = len(logger.handlers)
    configure_logging(level="DEBUG", stream=stream)
    assert len(logging.getLogger(LOGGER_NAME).handlers) == count
    logging.getLogger("nmrforge.demo").info("你好")
    assert "你好" in stream.getvalue()


def test_configure_logging_respects_the_level() -> None:
    stream = io.StringIO()
    configure_logging(level="ERROR", stream=stream)
    logging.getLogger("nmrforge.demo").warning("warning 不该出现")
    logging.getLogger("nmrforge.demo").error("error 应该出现")
    out = stream.getvalue()
    assert "error 应该出现" in out
    assert "warning 不该出现" not in out


def test_attach_run_log_defers_file_creation_until_a_record(tmp_path: Path) -> None:
    """没有日志记录就不留空 run.log;有记录时消息与级别都落盘。"""
    handler = attach_run_log(tmp_path)
    try:
        assert not (tmp_path / "run.log").exists()
        logging.getLogger("nmrforge.demo").error("失败了")
        for item in logging.getLogger(LOGGER_NAME).handlers:
            item.flush()
        text = (tmp_path / "run.log").read_text(encoding="utf-8")
        assert "失败了" in text and "ERROR" in text
    finally:
        detach_run_log(handler)
    # detach 可重复调用且关闭文件句柄
    detach_run_log(handler)


def test_append_run_log_line_writes_and_sanitizes(tmp_path: Path) -> None:
    """头/尾行直接写文件:不受日志级别影响,且 home 绝对路径被脱敏。"""
    target = tmp_path / "run.log"
    append_run_log_line(target, f"读到 {Path.home() / 'data' / 'x.fid'}")
    append_run_log_line(target, "第二行")
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("20") and "INFO nmrforge.run:" in lines[0]
    assert str(Path.home()) not in lines[0], "绝对路径必须脱敏成 ~"
    assert "~" in lines[0] and "第二行" in lines[1]


def test_sanitize_path_folds_the_home_directory() -> None:
    folded = sanitize_path(Path.home() / "work" / "run.log")
    assert folded.startswith("~")
    assert str(Path.home()) not in folded, "已折叠的路径不应再含完整 home 前缀"
    assert sanitize_path("没有任何路径") == "没有任何路径"


def test_gui_and_viewer_have_no_bare_print() -> None:
    """Phase 22:界面层不得用裸 ``print()``(日志走 logging,控制台输出只在入口/CLI)。"""
    offenders: list[str] = []
    for base in ("gui", "viewer"):
        for path in sorted((ROOT / base).glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("print(") or stripped.startswith("print ("):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, "GUI/viewer 不得使用裸 print: " + ", ".join(offenders)
