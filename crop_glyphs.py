#!/usr/bin/env python3
"""Crop one PNG per character box from plate images and boxes.json.

Output names are ``{plate}_{id:04d}_{char}.png`` when the box has a single
CJK label, otherwise ``{plate}_{id:04d}.png``. A repeatMark box adds
``_repeat`` before the extension. noCard boxes are skipped unless
``--include-nocard`` is set. Coordinates are full-image pixels, the same
space the annotator saves.
"""

from __future__ import annotations

import argparse
import sys

from boxannotator import add_common_arguments, crop_plates, load_settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crop glyph PNGs from boxes.json and plate images.")
    add_common_arguments(parser, crop=True)
    parser.add_argument(
        "--include-nocard",
        action="store_true",
        help="also crop boxes flagged noCard (repair, blank, or damaged)",
    )
    parser.add_argument(
        "--skip-repeat",
        action="store_true",
        help="skip repeatMark boxes instead of writing them with a _repeat filename",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args)
    if not settings.boxes_path.is_file():
        print(f"boxes file not found: {settings.boxes_path}", file=sys.stderr)
        print("Draw boxes in the web UI and save, or pass --boxes.", file=sys.stderr)
        return 1
    if not settings.plates_dir.is_dir():
        print(f"plates directory not found: {settings.plates_dir}", file=sys.stderr)
        return 1
    report = crop_plates(
        settings,
        include_nocard=args.include_nocard,
        skip_repeat=args.skip_repeat,
    )
    for plate_id in report.missing_plates:
        print(f"warning: no image for plate {plate_id}", file=sys.stderr)
    print(f"wrote {len(report.written)} glyph(s) to {settings.crop_out}")
    if report.skipped_nocard:
        print(f"skipped {report.skipped_nocard} noCard box(es)")
    if report.skipped_repeat:
        print(f"skipped {report.skipped_repeat} repeatMark box(es)")
    if report.skipped:
        print(f"skipped {report.skipped} box(es)")
    if not report.written:
        print("no boxes to crop; draw and save boxes in the web UI first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
