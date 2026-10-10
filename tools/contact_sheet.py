#!/usr/bin/env python3
"""Contact sheet: original-colour crops, printed labels, grey text indexes.

Columns run right to left. A column listed as low-confidence gets a red
border. repeatMark cells are grey; noCard cells are blue, with a short reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boxannotator import (  # noqa: E402
    add_common_arguments,
    discover_plates,
    find_cjk_font,
    load_boxes,
    load_settings,
)

REASON_LABEL = {"repair": "补纸", "blank": "空白", "damaged": "残损"}


def group_columns(boxes: list[dict]) -> list[tuple[int, list[dict]]]:
    """Group by ``col`` when every box has one. Otherwise cluster on x, right to left."""
    if boxes and all(isinstance(box.get("col"), int) and not isinstance(box.get("col"), bool) for box in boxes):
        grouped: dict[int, list[dict]] = {}
        for box in boxes:
            grouped.setdefault(int(box["col"]), []).append(box)
        columns = []
        for col in sorted(grouped):
            columns.append((col, sorted(grouped[col], key=lambda box: (box.get("ti", 10**9), box["y"]))))
        return columns
    if not boxes:
        return []
    widths = sorted(max(1.0, float(box["w"])) for box in boxes)
    median_w = widths[len(widths) // 2]
    threshold = 0.55 * median_w
    ordered = sorted(boxes, key=lambda box: -(box["x"] + box["w"] / 2))
    clusters: list[dict] = []
    for box in ordered:
        center = box["x"] + box["w"] / 2
        placed = False
        for cluster in clusters:
            if abs(center - cluster["cx"]) < threshold:
                cluster["boxes"].append(box)
                count = len(cluster["boxes"])
                cluster["cx"] = (cluster["cx"] * (count - 1) + center) / count
                placed = True
                break
        if not placed:
            clusters.append({"cx": center, "boxes": [box]})
    clusters.sort(key=lambda cluster: -cluster["cx"])
    columns = []
    for index, cluster in enumerate(clusters, start=1):
        columns.append((index, sorted(cluster["boxes"], key=lambda box: box["y"])))
    return columns


def load_low_cols(path: Path | None, listed: str | None) -> set[int]:
    cols: set[int] = set()
    if listed:
        for part in listed.split(","):
            part = part.strip()
            if part:
                cols.add(int(part))
    if path is None or not path.is_file():
        return cols
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        cols.update(int(item) for item in data)
        return cols
    if isinstance(data, dict):
        raw = data.get("columns", data)
        if isinstance(raw, list):
            cols.update(int(item) for item in raw)
        elif isinstance(raw, dict):
            for key in raw:
                if key in {"columns", "notes"}:
                    continue
                try:
                    cols.add(int(key))
                except (TypeError, ValueError):
                    continue
    return cols


def render_plate(image, boxes: list[dict], low_cols: set[int], font_path: str, title: str):
    from PIL import Image, ImageDraw, ImageFont

    columns = group_columns(boxes)
    if not columns:
        sheet = Image.new("RGB", (640, 160), "white")
        ImageDraw.Draw(sheet).text((16, 16), title + "  （无框）", fill="black")
        return sheet
    cell_w, cell_h = 120, 110
    label_w = 72
    slot_w = cell_w + label_w + 16
    header = 64
    longest = max(len(items) for _, items in columns)
    width = slot_w * len(columns) + 24
    height = header + 24 + longest * (cell_h + 8) + 16
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font_label = ImageFont.truetype(font_path, 36)
    font_small = ImageFont.truetype(font_path, 16)
    font_title = ImageFont.truetype(font_path, 22)
    draw.text((12, 10), title, fill="black", font=font_title)
    for index, (col, items) in enumerate(columns):
        x0 = width - 12 - (index + 1) * slot_w
        bad = col in low_cols
        draw.text((x0 + 4, header - 28), f"列 {col}", fill=(200, 0, 0) if bad else (0, 0, 160), font=font_small)
        y = header
        column_bottom = y
        for box in items:
            left = max(0, int(round(box["x"])))
            top = max(0, int(round(box["y"])))
            right = min(image.width, int(round(box["x"] + box["w"])))
            bottom = min(image.height, int(round(box["y"] + box["h"])))
            if right > left and bottom > top:
                crop = image.crop((left, top, right, bottom))
                scale = min(cell_w / crop.width, cell_h / crop.height)
                crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
                sheet.paste(crop, (x0 + (cell_w - crop.width) // 2, y + (cell_h - crop.height) // 2))
            draw.rectangle((x0, y, x0 + cell_w, y + cell_h), outline=(190, 190, 190))
            char = box.get("char") or ""
            repeat = box.get("repeatMark") is True
            no_card = box.get("noCard") is True
            draw.text((x0 + cell_w + 6, y + 6), char, fill=(150, 150, 150) if repeat else (0, 0, 0), font=font_label)
            if repeat:
                draw.rectangle((x0, y, x0 + cell_w, y + cell_h), outline=(140, 140, 140), width=3)
                draw.text((x0 + cell_w + 6, y + 48), "重文", fill=(120, 120, 120), font=font_small)
            if no_card:
                draw.rectangle((x0, y, x0 + cell_w, y + cell_h), outline=(0, 140, 210), width=4)
                draw.line((x0, y, x0 + cell_w, y + cell_h), fill=(0, 140, 210), width=2)
                reason = REASON_LABEL.get(box.get("noCardReason"), "不制卡")
                draw.text((x0 + cell_w + 6, y + 70), reason, fill=(0, 110, 180), font=font_small)
            index_text = str(box["ti"]) if isinstance(box.get("ti"), int) else f"#{box.get('id', '')}"
            draw.text((x0 + cell_w + 6, y + cell_h - 22), index_text, fill=(110, 110, 110), font=font_small)
            y += cell_h + 8
            column_bottom = y
        if bad:
            draw.rectangle((x0 - 6, header - 34, x0 + slot_w - 10, column_bottom), outline=(220, 0, 0), width=5)
    return sheet


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render per-plate contact sheets from boxes.json.")
    add_common_arguments(parser, crop=False)
    parser.add_argument("--out", default="sheets", help="directory for sheet PNGs")
    parser.add_argument("--font", dest="cjk_font", help="CJK font file")
    parser.add_argument("--low-cols", help="comma-separated low-confidence column ids, drawn with a red border")
    parser.add_argument("--lowconf", help="JSON list or object of low-confidence column ids")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from PIL import Image

    args = parse_args(argv)
    settings = load_settings(args)
    plates = dict(discover_plates(settings.plates_dir, settings.plate_glob))
    if not plates:
        print(f"no plates in {settings.plates_dir}", file=sys.stderr)
        return 1
    boxes_path = settings.boxes_path
    if not boxes_path.is_file():
        example = boxes_path.with_name("boxes.example.json")
        if example.is_file():
            boxes_path = example
        else:
            print(f"boxes file not found: {settings.boxes_path}", file=sys.stderr)
            return 1
    document = load_boxes(boxes_path, list(plates), settings.source)
    low_path = Path(args.lowconf).expanduser() if args.lowconf else None
    if low_path is not None and not low_path.is_absolute():
        low_path = (Path.cwd() / low_path).resolve()
    low_cols = load_low_cols(low_path, args.low_cols)
    font_path = find_cjk_font(settings.cjk_font)
    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for plate_id, image_path in plates.items():
        items = document["plates"].get(plate_id) or []
        if not items:
            continue
        with Image.open(image_path) as image:
            image.load()
            title = f"{plate_id}  {len(items)} 框   红框 = 低置信列；灰字 = 释文序号；蓝框 = 不制卡"
            sheet = render_plate(image, items, low_cols, font_path, title)
        dest = out_dir / f"{plate_id}.png"
        sheet.save(dest, format="PNG")
        print(f"wrote {dest}")
        written += 1
    if not written:
        print("no boxes to draw")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
