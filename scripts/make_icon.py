"""Generate NMRForge application icon (256x256 PNG, same design as
packaging/linux/icons/nmrforge.svg). Usage: python scripts/make_icon.py Output:
packaging/linux/icons/nmrforge.png (for AppImage desktop icon use)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
SS = 4  # Oversampling multiple, used for anti-aliasing.
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

    # Rounded background (dark blue gradient is approximated by upper and lower colors).
    draw.rounded_rectangle(
        (8 * SS, 8 * SS, 248 * SS, 248 * SS),
        radius=52 * SS,
        fill="#1e3a8a",
    )

    # Baseline.
    draw.line(
        [_scale((36, 196)), _scale((220, 196))],
        fill="#94a3b8",
        width=6 * SS,
    )

    # Spectrum peak polyline.
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
