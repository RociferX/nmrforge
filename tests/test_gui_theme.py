"""暗色主题可读性守卫(0.2.199-补29hz 审阅修复)。

背景:软件强制暗色主题(窗口 #1e1e1e),但历史上不少 QSS 仍写浅色主题的
深色文字(#2c3e50 ≈1.5:1、#333 ≈1.6:1、#444 ≈2.1:1、#555 ≈2.4:1、
#666 ≈2.9:1),实际等于看不见。本测试把「文字色必须可读」固化成规则,
防止回退写法再次混入;语义色集中在 gui/theme.py。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.theme import (  # noqa: E402
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WINDOW_BACKGROUND,
)

ROOT = Path(__file__).resolve().parent.parent

# 文字色(排除 background-color 等以 "color:" 结尾的其它属性)
COLOR_RE = re.compile(r"(?<!background-)color:\s*(#[0-9a-fA-F]{3,6})")

# 明确画在浅色底上的文字:白底运行详情框、白底内联命名编辑器、浅黄提示气泡。
ALLOWED_ON_LIGHT = {
    ("gui/pipeline_panel.py", "#222"),      # detail_frame 白底
    ("gui/project_tree.py", "#222"),        # 内联重命名编辑器白底
    ("gui/pipeline_panel.py", "#935116"),   # hint_bubble 浅黄底
}


def _channel(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    text = color.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    r, g, b = (int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _iter_text_colors():
    for pkg in ("gui", "viewer"):
        for path in sorted((ROOT / pkg).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel == "gui/theme.py":  # 主题定义本身(含 disabled 等豁免色)
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                for match in COLOR_RE.finditer(line):
                    yield rel, lineno, match.group(1).lower(), line.strip()


def test_theme_constants_are_readable() -> None:
    """语义色必须都在暗色窗口底色上达到 4.5:1。"""
    for color in (TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED):
        assert _contrast(color, WINDOW_BACKGROUND) >= 4.5, color
    assert _contrast(TEXT_MUTED, WINDOW_BACKGROUND) > _contrast(
        "#666666", WINDOW_BACKGROUND
    )


def test_no_dark_text_on_dark_theme() -> None:
    """gui/ 与 viewer/ 不得再出现暗色窗口上的低对比文字。"""
    offenders = []
    for rel, lineno, color, line in _iter_text_colors():
        if (rel, color) in ALLOWED_ON_LIGHT:
            continue
        ratio = _contrast(color, WINDOW_BACKGROUND)
        if ratio < 4.5:
            offenders.append(f"{rel}:{lineno} {color} ({ratio:.2f}:1) {line}")
    assert not offenders, "暗色主题下的低对比文字:\n" + "\n".join(offenders)


def test_retired_light_theme_colors_are_gone() -> None:
    """历史浅色主题的深色文字字面量不得回退。"""
    retired = {"#2c3e50", "#333333", "#444444", "#555555", "#666666"}
    found = [
        f"{rel}:{lineno} {color}"
        for rel, lineno, color, _line in _iter_text_colors()
        if color in retired
    ]
    assert not found, "回退的深色文字: " + ", ".join(found)


def test_tree_icon_uses_theme_color() -> None:
    """树图标颜色取自主题(原来写死 #2c3e50,在暗色树上不可见)。"""
    source = (ROOT / "gui" / "project_tree.py").read_text(encoding="utf-8")
    assert 'painter.setPen(QColor(TEXT_SECONDARY))' in source
    assert 'QColor("#2c3e50")' not in source
