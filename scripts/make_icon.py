"""生成 NMRForge 应用图标（256x256 PNG，与 packaging/linux/icons/nmrforge.svg 同设计）。

用法：python scripts/make_icon.py
输出：packaging/linux/icons/nmrforge.png（供 AppImage desktop 图标使用）
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
SS = 4  # 超采样倍数，用于抗锯齿
POINTS = [
    (36, 196),
    (76, 196),
    (92, 120),
    (108, 178),
    (124, 62),
    (140, 168),
    (156, 104),
    (172, 186),
    (220, 196),
]


def _scale(point: tuple[int, int]) -> tuple[int, int]:
    return (point[0] * SS, point[1] * SS)


def main() -> None:
    img = Image.new("RGB", (SIZE * SS, SIZE * SS), "#0f172a")
    draw = ImageDraw.Draw(img)

    # 圆角背景（深蓝渐变用上下两色近似）
    draw.rounded_rectangle(
        (8 * SS, 8 * SS, 248 * SS, 248 * SS),
        radius=52 * SS,
        fill="#1e3a8a",
    )

    # 基线
    draw.line(
        [_scale((36, 196)), _scale((220, 196))],
        fill="#94a3b8",
        width=6 * SS,
    )

    # 谱峰折线
    draw.line(
        [_scale(p) for p in POINTS],
        fill="#e2e8f0",
        width=10 * SS,
        joint="curve",
    )

    out = (
        Path(__file__).resolve().parent.parent
        / "packaging"
        / "linux"
        / "icons"
        / "nmrforge.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.resize((SIZE, SIZE), Image.LANCZOS).save(out)
    print(f"written {out}")


if __name__ == "__main__":
    main()
