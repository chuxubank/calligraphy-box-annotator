#!/usr/bin/env python3
"""Crop one PNG per character box from plate images and boxes.json.

Output names are ``{plate}_{id:04d}_{char}.png`` when the box has a single
CJK label, otherwise ``{plate}_{id:04d}.png``. Coordinates are full-image
pixels, the same space the annotator saves.
"""

from __future__ import annotations

import argparse
import sys

from boxannotator import add_common_arguments, crop_plates, load_settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crop glyph PNGs from boxes.json and plate images.")
    add_common_arguments(parser, crop=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    settings = load_settings(parse_args(argv))
    if not settings.boxes_path.is_file():
        print(f"boxes file not found: {settings.boxes_path}", file=sys.stderr)
        print("Draw boxes in the web UI and save, or pass --boxes.", file=sys.stderr)
        return 1
    if not settings.plates_dir.is_dir():
        print(f"plates directory not found: {settings.plates_dir}", file=sys.stderr)
        return 1
    report = crop_plates(settings)
    for plate_id in report.missing_plates:
        print(f"warning: no image for plate {plate_id}", file=sys.stderr)
    print(f"wrote {len(report.written)} glyph(s) to {settings.crop_out}")
    if report.skipped:
        print(f"skipped {report.skipped} box(es)")
    if not report.written:
        print("no boxes to crop; draw and save boxes in the web UI first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
