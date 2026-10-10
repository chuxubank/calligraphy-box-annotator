#!/usr/bin/env python3
"""Apply one hand-written column spec to boxes.json, after writing a backup.

This is the generalised form of a spec-driven fix step. Specs are layered by
re-running them in order; each run copies the current file to
``boxes.json.pre_<label>`` before replacing the columns it names.

A column entry replaces that column's boxes. Other columns are left alone.
``spans`` are ``[y0, y1]`` or ``[y0, y1, x, w]`` in full-image pixels, top to
bottom. ``t0`` is the transcription index of the first glyph. ``repeat`` lists
transcription indexes to flag as repeatMark. ``noCard`` maps a transcription
index to ``repair``, ``blank``, or ``damaged``.

Columns marked ``approved`` in the review sidecar, or that contain a box with
``approved: true``, are refused. The file is left unchanged.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boxannotator import (  # noqa: E402
    NO_CARD_REASONS,
    add_common_arguments,
    load_boxes,
    load_settings,
    load_text_chars,
    save_boxes,
)


def _spec_plates(spec: dict) -> dict:
    plates = spec.get("plates")
    if isinstance(plates, dict):
        return plates
    return spec


def apply_spec(document: dict, spec: dict, chars: list[str] | None = None) -> dict:
    data = copy.deepcopy(document)
    plates = data.setdefault("plates", {})
    if not isinstance(plates, dict):
        raise ValueError("boxes plates must be an object")
    max_id = 0
    for items in plates.values():
        if not isinstance(items, list):
            continue
        for box in items:
            if isinstance(box, dict) and isinstance(box.get("id"), int):
                max_id = max(max_id, box["id"])
    for plate_id, columns in _spec_plates(spec).items():
        if not isinstance(columns, dict):
            continue
        current = list(plates.get(plate_id) or [])
        for col_key, entry in columns.items():
            if not isinstance(entry, dict) or "spans" not in entry:
                continue
            col = int(col_key)
            spans = entry["spans"]
            t0 = int(entry.get("t0", 0))
            default_x = entry.get("x")
            default_w = entry.get("w")
            if default_x is None or default_w is None:
                same = [box for box in current if box.get("col") == col]
                if same:
                    default_x = sorted(box["x"] for box in same)[len(same) // 2]
                    default_w = sorted(box["w"] for box in same)[len(same) // 2]
                else:
                    default_x = 0
                    default_w = 10
            repeat = {int(value) for value in entry.get("repeat") or []}
            no_card = {int(key): value for key, value in (entry.get("noCard") or {}).items()}
            current = [box for box in current if box.get("col") != col]
            for offset, span in enumerate(spans):
                if len(span) not in (2, 4):
                    raise ValueError(f"{plate_id} column {col} span {offset} must have 2 or 4 numbers")
                y0, y1 = float(span[0]), float(span[1])
                if len(span) == 4:
                    x, w = float(span[2]), float(span[3])
                else:
                    x, w = float(default_x), float(default_w)
                ti = t0 + offset
                max_id += 1
                box = {
                    "id": max_id,
                    "x": int(round(x)),
                    "y": int(round(y0)),
                    "w": int(round(w)),
                    "h": int(round(y1 - y0)),
                    "col": col,
                    "ti": ti,
                }
                if chars is not None and 0 <= ti < len(chars):
                    box["char"] = chars[ti]
                if ti in repeat:
                    box["repeatMark"] = True
                reason = no_card.get(ti)
                if reason in NO_CARD_REASONS:
                    box["noCard"] = True
                    box["noCardReason"] = reason
                current.append(box)
        current.sort(key=lambda box: (box.get("ti", 10**9), box.get("y", 0), box.get("id", 0)))
        plates[plate_id] = current
    return data


class ApplyBlocked(Exception):
    """Spec names a column a person has already approved."""

    def __init__(self, columns: list[dict]) -> None:
        self.columns = columns
        super().__init__("refusing to replace user-approved columns")


def spec_targets(spec: dict) -> list[tuple[str, int]]:
    """Columns a spec would replace: ``(plate id, col)``."""
    if not isinstance(spec, dict):
        return []
    plates = spec.get("plates") if isinstance(spec.get("plates"), dict) else spec
    if not isinstance(plates, dict):
        return []
    targets: list[tuple[str, int]] = []
    for plate_id, columns in plates.items():
        if not isinstance(columns, dict):
            continue
        for col_key, entry in columns.items():
            if not isinstance(entry, dict) or "spans" not in entry:
                continue
            if isinstance(col_key, bool):
                continue
            try:
                col = int(col_key)
            except (TypeError, ValueError):
                continue
            targets.append((str(plate_id), col))
    return targets


def execute_apply(
    boxes_path: Path,
    document: dict,
    spec: dict,
    chars: list[str] | None,
    *,
    label: str,
    dry_run: bool,
    review_state: dict | None = None,
) -> dict:
    """Apply ``spec`` unless it names an approved column.

    Does not write when ``dry_run`` is set. Raises ``ApplyBlocked`` before any
    backup or write. Raises ``ValueError`` for a malformed span.
    """
    from cba.review_state import targeted_protected

    blocked = targeted_protected(document, spec, review_state)
    if blocked:
        raise ApplyBlocked(blocked)
    updated = apply_spec(document, spec, chars)
    backup: Path | None = None
    if not dry_run:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label) or "spec"
        backup = boxes_path.with_name(boxes_path.name + ".pre_" + safe)
        backup.write_bytes(boxes_path.read_bytes())
        save_boxes(boxes_path, updated)
    applied = []
    plates = updated.get("plates") if isinstance(updated.get("plates"), dict) else {}
    for plate_id, col in spec_targets(spec):
        boxes = plates.get(plate_id) or []
        count = sum(1 for box in boxes if isinstance(box, dict) and box.get("col") == col)
        applied.append({"plate": plate_id, "col": col, "boxes": count})
    return {"document": updated, "backup": backup, "applied": applied, "dryRun": dry_run}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a column spec JSON onto boxes.json.")
    add_common_arguments(parser, crop=False)
    parser.add_argument("--spec", required=True, help="spec JSON path")
    parser.add_argument("--label", default="spec", help="backup suffix: boxes.json.pre_<label>")
    parser.add_argument("--dry-run", action="store_true", help="print the result, do not write")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(args)
    if not settings.boxes_path.is_file():
        print(f"boxes file not found: {settings.boxes_path}", file=sys.stderr)
        return 1
    spec_path = Path(args.spec).expanduser()
    if not spec_path.is_absolute():
        spec_path = (Path.cwd() / spec_path).resolve()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid spec: {exc}", file=sys.stderr)
        return 1
    from boxannotator import discover_plates
    from cba.review_state import ReviewError, load_review, review_sidecar

    plate_ids = [plate_id for plate_id, _ in discover_plates(settings.plates_dir, settings.plate_glob)]
    document = load_boxes(settings.boxes_path, plate_ids, settings.source)
    try:
        review = load_review(review_sidecar(settings.boxes_path))
    except ReviewError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        result = execute_apply(
            settings.boxes_path,
            document,
            spec,
            load_text_chars(settings.text_path),
            label=args.label,
            dry_run=args.dry_run,
            review_state=review,
        )
    except ApplyBlocked as exc:
        names = ", ".join(f"{item['plate']} col {item['col']}" for item in exc.columns)
        print(f"refusing to replace user-approved columns: {names}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.dry_run:
        json.dump(result["document"], sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    print(f"backup {result['backup']}")
    print(f"wrote {settings.boxes_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
