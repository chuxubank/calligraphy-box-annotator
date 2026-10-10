#!/usr/bin/env python3
"""Cut each ink column into exactly N boxes, then centre the column.

Box-by-box centring pulls a small glyph toward a neighbour, so the column is
shifted onto its ink first (at most ±20% of the median width, up to three
passes). Each box is then nudged by at most ±15% of its own width.

Columns are numbered in reading order: column 1 is the rightmost.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boxannotator import add_common_arguments, load_settings  # noqa: E402


def _ink_mask(image, threshold: int = 80) -> tuple[bytearray, int, int]:
    gray = image.convert("L")
    width, height = gray.size
    raw = gray.tobytes()
    ink = bytearray(1 if value < threshold else 0 for value in raw)
    return ink, width, height


def _smooth(values: list[float], radius: int) -> list[float]:
    if radius <= 0:
        return list(values)
    out: list[float] = []
    last = len(values)
    for index in range(last):
        start = max(0, index - radius)
        stop = min(last, index + radius + 1)
        out.append(sum(values[start:stop]) / (stop - start))
    return out


def _projection_x(ink: bytearray, width: int, height: int, y0: int, y1: int) -> list[int]:
    totals = [0] * width
    for y in range(y0, y1):
        row = y * width
        for x in range(width):
            totals[x] += ink[row + x]
    return totals


def _projection_y(ink: bytearray, width: int, x0: int, x1: int, y0: int, y1: int) -> list[int]:
    totals = [0] * (y1 - y0)
    for y in range(y0, y1):
        row = y * width
        totals[y - y0] = sum(ink[row + x] for x in range(x0, x1))
    return totals


def _bands(profile: list[float], min_width: int, frac: float) -> list[tuple[int, int]]:
    peak = max(profile) if profile else 0
    if peak <= 0:
        return []
    threshold = peak * frac
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(profile):
        if value >= threshold:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= min_width:
                bands.append((start, index))
            start = None
    if start is not None and len(profile) - start >= min_width:
        bands.append((start, len(profile)))
    return bands


def _merge_to(runs: list[tuple[int, int]], count: int) -> list[tuple[int, int]]:
    runs = list(runs)
    while len(runs) > count and len(runs) > 1:
        gap_at = min(range(len(runs) - 1), key=lambda i: runs[i + 1][0] - runs[i][1])
        runs[gap_at : gap_at + 2] = [(runs[gap_at][0], runs[gap_at + 1][1])]
    return runs


def _split_to(runs: list[tuple[int, int]], profile: list[float], count: int) -> list[tuple[int, int]]:
    runs = list(runs)
    guard = 0
    while len(runs) < count and guard < count + 4:
        guard += 1
        choice: tuple[int, int, int] | None = None
        for index, (y0, y1) in enumerate(runs):
            span = y1 - y0
            if span < 16:
                continue
            left = y0 + max(4, span // 5)
            right = y1 - max(4, span // 5)
            if right <= left:
                continue
            cut = min(range(left, right), key=lambda pos: profile[pos])
            if choice is None or span > choice[0]:
                choice = (span, index, cut)
        if choice is None:
            break
        _, index, cut = choice
        y0, y1 = runs[index]
        if cut <= y0 or cut >= y1:
            break
        runs[index : index + 1] = [(y0, cut), (cut, y1)]
    return runs


def _fit_runs(profile: list[float], count: int) -> list[tuple[int, int]]:
    runs = _bands(profile, min_width=8, frac=0.12)
    if not runs:
        return []
    if len(runs) > count:
        runs = _merge_to(runs, count)
    elif len(runs) < count:
        runs = _split_to(runs, profile, count)
    return runs


def _shift_column(boxes: list[dict], ink: bytearray, width: int, max_frac: float = 0.20) -> None:
    if not boxes:
        return
    for _ in range(3):
        x0 = max(0, min(int(box["x"]) for box in boxes))
        x1 = min(width, max(int(box["x"] + box["w"]) for box in boxes))
        y0 = max(0, min(int(box["y"]) for box in boxes))
        y1 = max(int(box["y"] + box["h"]) for box in boxes)
        mass = 0
        moment = 0.0
        for y in range(y0, y1):
            row = y * width
            for x in range(x0, x1):
                if ink[row + x]:
                    mass += 1
                    moment += x + 0.5
        if mass < 10 or x1 <= x0:
            return
        delta = moment / mass - (x0 + x1) / 2
        median_w = sorted(box["w"] for box in boxes)[len(boxes) // 2]
        limit = max_frac * median_w
        delta = max(-limit, min(limit, delta))
        if abs(delta) < 0.5:
            return
        for box in boxes:
            box["x"] = int(round(box["x"] + delta))


def _nudge_box(box: dict, ink: bytearray, width: int, height: int, frac: float = 0.15) -> None:
    x0 = max(0, min(width, int(box["x"])))
    x1 = max(0, min(width, int(box["x"] + box["w"])))
    y0 = max(0, min(height, int(box["y"])))
    y1 = max(0, min(height, int(box["y"] + box["h"])))
    mass = 0
    moment = 0.0
    for y in range(y0, y1):
        row = y * width
        for x in range(x0, x1):
            if ink[row + x]:
                mass += 1
                moment += x + 0.5
    if mass < 5 or box["w"] <= 0:
        return
    delta = moment / mass - (box["x"] + box["w"] / 2)
    limit = frac * box["w"]
    delta = max(-limit, min(limit, delta))
    box["x"] = int(round(box["x"] + delta))


def cut_image(image, per_column: int, margin: int = 24) -> list[dict]:
    """Return boxes for every detected ink column, rightmost column first.

    ``per_column`` is the exact box count inside each column. ``col`` is 1 for
    the rightmost column.
    """
    if per_column < 1:
        raise ValueError("per_column must be at least 1")
    ink, width, height = _ink_mask(image)
    y0 = min(margin, height // 10)
    y1 = max(y0 + 1, height - margin)
    profile = _smooth([float(value) for value in _projection_x(ink, width, height, y0, y1)], 4)
    columns = _bands(profile, min_width=20, frac=0.18)
    boxes: list[dict] = []
    # Reading order is right to left.
    for col_index, (x_start, x_stop) in enumerate(reversed(columns), start=1):
        y_profile_raw = _projection_y(ink, width, x_start, x_stop, 0, height)
        y_profile = _smooth([float(value) for value in y_profile_raw], 3)
        runs = _fit_runs(y_profile, per_column)
        column_boxes: list[dict] = []
        for run_y0, run_y1 in runs:
            if run_y1 - run_y0 < 4:
                continue
            column_boxes.append(
                {
                    "col": col_index,
                    "x": int(x_start),
                    "y": int(run_y0),
                    "w": int(x_stop - x_start),
                    "h": int(run_y1 - run_y0),
                }
            )
        _shift_column(column_boxes, ink, width)
        for box in column_boxes:
            _nudge_box(box, ink, width, height)
            box["x"] = max(0, min(width - 1, int(box["x"])))
            box["w"] = max(1, min(width - box["x"], int(box["w"])))
        boxes.extend(column_boxes)
    return boxes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cut plate columns into N boxes by ink gaps.")
    add_common_arguments(parser, crop=False)
    parser.add_argument("--per-column", type=int, default=4, help="exact box count inside each column")
    parser.add_argument("--plate", help="plate id to cut (default: the first plate)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from PIL import Image

    args = parse_args(argv)
    settings = load_settings(args)
    from boxannotator import discover_plates

    plates = discover_plates(settings.plates_dir, settings.plate_glob)
    if not plates:
        print(f"no plates in {settings.plates_dir}", file=sys.stderr)
        return 1
    plate_id, path = plates[0]
    if args.plate:
        match = [item for item in plates if item[0] == args.plate]
        if not match:
            print(f"unknown plate {args.plate}", file=sys.stderr)
            return 1
        plate_id, path = match[0]
    with Image.open(path) as image:
        boxes = cut_image(image, args.per_column)
    counts: dict[int, int] = {}
    for box in boxes:
        counts[box["col"]] = counts.get(box["col"], 0) + 1
    print(f"{plate_id}: {len(boxes)} boxes in {len(counts)} column(s) {counts}")
    for box in boxes:
        print(f"  col {box['col']} x={box['x']} y={box['y']} w={box['w']} h={box['h']}")
    return 0 if boxes else 1


if __name__ == "__main__":
    raise SystemExit(main())
