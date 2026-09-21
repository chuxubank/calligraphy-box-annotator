#!/usr/bin/env python3
"""Write tiny synthetic demo plates (no scanned calligraphy).

Each plate is a paper-colored PNG with a few large CJK characters laid out in
traditional columns: the first column is on the right, and characters run
top-to-bottom within a column.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "plates"

# (filename stem, right-to-left columns of characters top-to-bottom, corner label)
PLATES = (
    ("plate-01", (("甲", "乙", "丙", "丁"), ("戊", "己", "庚", "辛")), "01"),
    ("plate-02", (("子", "丑", "寅", "卯"), ("辰", "巳", "午", "未")), "02"),
)

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


def find_font() -> str:
    import os

    override = os.environ.get("CBA_DEMO_FONT")
    candidates = ([override] if override else []) + list(FONT_CANDIDATES)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    print("No CJK font found. Install WenQuanYi, Noto Sans CJK, or set CBA_DEMO_FONT.", file=sys.stderr)
    raise SystemExit(1)


def draw_plate(path: Path, columns: tuple[tuple[str, ...], ...], label: str, font_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 560, 760
    image = Image.new("RGB", (width, height), "#f4efe2")
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, width - 17, height - 17), outline="#b7a98a", width=3)
    draw.rectangle((26, 26, width - 27, height - 27), outline="#d9ccb2", width=1)

    mark = ImageFont.load_default()
    draw.text((36, 34), f"SYNTHETIC DEMO {label}", fill="#a89880", font=mark)

    font = ImageFont.truetype(font_path, 108)
    left, top, right, bottom = 48, 78, width - 48, height - 48
    col_w = (right - left) / len(columns)
    row_count = max(len(column) for column in columns)
    row_h = (bottom - top) / row_count
    for col_index, column in enumerate(columns):
        center_x = right - (col_index + 0.5) * col_w
        for row_index, char in enumerate(column):
            center_y = top + (row_index + 0.5) * row_h
            draw.text((center_x, center_y), char, fill="#1c1c1c", font=font, anchor="mm")
    image.save(path, format="PNG")


def main() -> None:
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Pillow is required. Install it with: python -m pip install -r requirements.txt", file=sys.stderr)
        raise SystemExit(1)
    font_path = find_font()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stem, columns, label in PLATES:
        dest = OUT_DIR / f"{stem}.png"
        draw_plate(dest, columns, label, font_path)
        print(f"wrote {dest.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    main()
