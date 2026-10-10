"""Non-interactive CLI. Every subcommand accepts ``--json`` and never prompts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from boxannotator import (
    ROOT,
    add_common_arguments,
    discover_plates,
    empty_boxes,
    ensure_outside_package,
    load_settings,
    load_text_chars,
    normalize_boxes,
    resolve_data_dir,
    save_boxes,
)
from cba.columns import column_boxes, is_int, split_columns
from cba.review_state import (
    ReviewError,
    clear_review,
    column_entry,
    load_review,
    review_sidecar,
    save_review,
    set_reviewed,
    set_unresolved,
)
from cba.validate import validate_document

_SAFE = re.compile(r"[^\w.\-]+", re.UNICODE)


class CliError(Exception):
    def __init__(self, message: str, **extra: object) -> None:
        super().__init__(message)
        self.extra = extra


def display_path(path: Path) -> str:
    """Prefer a path relative to the working directory, then the repo root."""
    resolved = path.resolve()
    for base in (Path.cwd().resolve(), ROOT.resolve()):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


def emit(payload: dict, as_json: bool, text: str, *, ok: bool = True) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif ok:
        print(text)
    else:
        print(text, file=sys.stderr)
    return 0 if ok else 1


def fail(message: str, as_json: bool, **extra: object) -> int:
    if as_json:
        body: dict = {"ok": False, "error": message}
        body.update(extra)
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        print(message, file=sys.stderr)
    return 1


def _read_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise CliError(f"cannot read {display_path(path)}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"invalid JSON {display_path(path)}: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError(f"expected a JSON object: {display_path(path)}")
    return data


def _resolve_user_path(value: str, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path.resolve()


def _data_dir(args: argparse.Namespace) -> Path:
    return resolve_data_dir(args)


class Context:
    def __init__(self, args: argparse.Namespace, *, need_boxes: bool, need_review: bool) -> None:
        self.settings = load_settings(args)
        self.plates = discover_plates(self.settings.plates_dir, self.settings.plate_glob)
        self.plate_ids = [plate_id for plate_id, _ in self.plates]
        self.data_path, self.origin = _resolve_boxes(self.settings)
        self.review_path = review_sidecar(self.settings.boxes_path)
        if need_boxes and self.data_path is None:
            raise CliError(f"boxes file not found: {display_path(self.settings.boxes_path)}")
        if self.data_path is None:
            self.raw = None
            self.document = empty_boxes(self.plate_ids, self.settings.source)
        else:
            self.raw = _read_object(self.data_path)
            self.document = normalize_boxes(self.raw, self.plate_ids, self.settings.source)
        self.chars = load_text_chars(self.settings.text_path)
        if need_review:
            try:
                self.review = load_review(self.review_path)
            except ReviewError as exc:
                raise CliError(str(exc)) from exc
        else:
            self.review = {"version": 1, "columns": {}}


def _resolve_boxes(settings) -> tuple[Path | None, str]:
    if settings.boxes_path.is_file():
        return settings.boxes_path, "boxes"
    sibling = settings.boxes_path.with_name("boxes.example.json")
    if sibling.is_file():
        return sibling, "example"
    from boxannotator import demo_dir

    demo = demo_dir()
    if settings.plates_dir.resolve() == (demo / "plates").resolve():
        bundled = demo / "boxes.example.json"
        if bundled.is_file():
            return bundled, "example"
    return None, "missing"


def _safe_component(value: str) -> str:
    cleaned = _SAFE.sub("_", value).strip("._")
    return cleaned or "plate"


def _clean_note(text: str) -> str:
    note = " ".join(text.split())
    if len(note) > 500:
        note = note[:500]
    return note


def _column_record(plate: str, col: int, items: list[dict], review: dict) -> dict:
    entry = column_entry(review, plate, col)
    approved = entry["approved"] or any(box.get("approved") is True for box in items)
    tis = [box["ti"] for box in items if is_int(box.get("ti"))]
    chars = "".join(box["char"] if isinstance(box.get("char"), str) and box["char"] else "?" for box in items)
    return {
        "col": col,
        "boxes": len(items),
        "chars": chars,
        "tiMin": min(tis) if tis else None,
        "tiMax": max(tis) if tis else None,
        "reviewed": entry["reviewed"],
        "unresolved": entry["unresolved"],
        "approved": approved,
        "note": entry["note"],
        "ids": [box.get("id") for box in items],
        "repeat": sum(1 for box in items if box.get("repeatMark") is True),
        "noCard": sum(1 for box in items if box.get("noCard") is True),
    }


def build_status(document: dict, plate_ids: list[str], review: dict) -> dict:
    plates_out = []
    unreviewed = []
    unresolved = []
    approved = []
    raw_plates = document.get("plates") if isinstance(document.get("plates"), dict) else {}
    for plate_id in plate_ids:
        boxes = raw_plates.get(plate_id) or []
        if not isinstance(boxes, list):
            boxes = []
        columns = []
        for col, items in split_columns(boxes):
            record = _column_record(plate_id, col, items, review)
            columns.append(record)
            ref = {"plate": plate_id, "col": col}
            if record["unresolved"]:
                unresolved.append({**ref, "note": record["note"]})
            if record["approved"]:
                approved.append(ref)
            elif not record["reviewed"] and not record["unresolved"]:
                unreviewed.append(ref)
        plates_out.append({"id": plate_id, "boxes": len(boxes), "columns": columns})
    column_count = sum(len(plate["columns"]) for plate in plates_out)
    box_count = sum(plate["boxes"] for plate in plates_out)
    return {
        "plates": plates_out,
        "unreviewed": unreviewed,
        "unresolved": unresolved,
        "approved": approved,
        "counts": {
            "plates": len(plate_ids),
            "boxes": box_count,
            "columns": column_count,
            "unreviewed": len(unreviewed),
            "unresolved": len(unresolved),
            "approved": len(approved),
        },
    }


def _format_status(payload: dict) -> str:
    lines = [
        f"boxes: {payload['boxes']} ({payload['origin']})",
        f"review: {payload['review']}" + ("" if payload["reviewExists"] else " (missing)"),
        f"text: {payload['textLength']} chars",
        "",
    ]
    for plate in payload["plates"]:
        lines.append(f"{plate['id']}  boxes={plate['boxes']}  columns={len(plate['columns'])}")
        for col in plate["columns"]:
            marks = []
            if col["approved"]:
                marks.append("approved")
            if col["unresolved"]:
                marks.append("unresolved")
            elif col["reviewed"]:
                marks.append("reviewed")
            elif not col["approved"]:
                marks.append("unreviewed")
            ti = "-" if col["tiMin"] is None else f"{col['tiMin']}-{col['tiMax']}"
            note = f"  {col['note']}" if col["note"] else ""
            lines.append(
                f"  col {col['col']}  boxes={col['boxes']}  ti={ti}  {col['chars']}  {' '.join(marks)}{note}"
            )

    def names(rows: list[dict]) -> str:
        if not rows:
            return "(none)"
        parts = []
        for row in rows:
            label = f"{row['plate']} col {row['col']}"
            if row.get("note"):
                label += f" ({row['note']})"
            parts.append(label)
        return ", ".join(parts)

    lines.extend(["", "unreviewed: " + names(payload["unreviewed"]), "unresolved: " + names(payload["unresolved"])])
    return "\n".join(lines)


def _require_column(ctx: Context, plate: str, col: int) -> list[dict]:
    if col < 1:
        raise CliError("column must be >= 1")
    known = set(ctx.plate_ids)
    plates = ctx.document.get("plates") if isinstance(ctx.document.get("plates"), dict) else {}
    if plate not in known and plate not in plates:
        raise CliError(f"unknown plate {plate}")
    items = column_boxes(list(plates.get(plate) or []), col)
    if not items:
        raise CliError(f"unknown column {plate} col {col}")
    return items


def _slice(boxes: list[dict], chars: list[str]) -> tuple[int | None, int | None, str]:
    tis = [box["ti"] for box in boxes if is_int(box.get("ti"))]
    if not tis:
        return None, None, ""
    t0 = min(tis)
    t1 = max(tis) + 1
    if t0 < 0:
        return t0, t1, ""
    return t0, t1, "".join(chars[t0:t1])


def _flags(box: dict) -> str:
    parts = []
    if box.get("repeatMark") is True:
        parts.append("repeat")
    if box.get("noCard") is True:
        reason = box.get("noCardReason") if isinstance(box.get("noCardReason"), str) else ""
        parts.append(f"noCard:{reason}" if reason else "noCard")
    if box.get("approved") is True:
        parts.append("approved")
    return ",".join(parts) if parts else "-"


def _expected(chars: list[str], box: dict) -> str:
    if not is_int(box.get("ti")):
        return ""
    ti = box["ti"]
    if 0 <= ti < len(chars):
        return chars[ti]
    return ""


def _render_column(image, boxes: list[dict], margin: int):
    from PIL import ImageDraw, ImageFont

    xs0 = min(int(box["x"]) for box in boxes)
    ys0 = min(int(box["y"]) for box in boxes)
    xs1 = max(int(box["x"] + box["w"]) for box in boxes)
    ys1 = max(int(box["y"] + box["h"]) for box in boxes)
    left = max(0, xs0 - margin)
    top = max(0, ys0 - margin)
    right = min(image.width, xs1 + margin)
    bottom = min(image.height, ys1 + margin)
    if right <= left or bottom <= top:
        raise CliError("column crop is empty")
    crop = image.crop((left, top, right, bottom)).convert("RGB")
    draw = ImageDraw.Draw(crop)
    try:
        font = ImageFont.load_default(22)
    except TypeError:
        font = ImageFont.load_default()
    for number, box in enumerate(boxes, start=1):
        x0 = int(box["x"]) - left
        y0 = int(box["y"]) - top
        x1 = int(box["x"] + box["w"]) - left
        y1 = int(box["y"] + box["h"]) - top
        if box.get("noCard") is True:
            outline = (0, 110, 180)
        elif box.get("repeatMark") is True:
            outline = (120, 120, 120)
        else:
            outline = (160, 30, 30)
        draw.rectangle((x0, y0, x1, y1), outline=outline, width=3)
        label = str(number)
        bbox = draw.textbbox((0, 0), label, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        pad = 3
        draw.rectangle((x0, y0, x0 + width + pad * 2, y0 + height + pad * 2), fill=(180, 20, 20))
        draw.text((x0 + pad - bbox[0], y0 + pad - bbox[1]), label, fill=(255, 255, 255), font=font)
    return crop


def _check_spec(spec: object, known: set[str]) -> None:
    from boxannotator import NO_CARD_REASONS

    if not isinstance(spec, dict):
        raise CliError("spec must be a JSON object")
    plates = spec.get("plates")
    if not isinstance(plates, dict) or not plates:
        raise CliError("spec plates must be a non-empty object")
    named = 0
    for plate_id, columns in plates.items():
        if not isinstance(plate_id, str) or plate_id not in known:
            raise CliError(f"unknown plate {plate_id}")
        if not isinstance(columns, dict) or not columns:
            raise CliError(f"{plate_id} columns must be a non-empty object")
        for col_key, entry in columns.items():
            if isinstance(col_key, bool):
                raise CliError(f"invalid column key {col_key!r}")
            try:
                col = int(col_key)
            except (TypeError, ValueError) as exc:
                raise CliError(f"invalid column key {col_key!r}") from exc
            if col < 1:
                raise CliError(f"invalid column key {col_key!r}")
            if not isinstance(entry, dict):
                raise CliError(f"{plate_id} column {col} must be an object")
            if "t0" not in entry or isinstance(entry.get("t0"), bool) or not isinstance(entry.get("t0"), int):
                raise CliError(f"{plate_id} column {col} needs an integer t0")
            if entry["t0"] < 0:
                raise CliError(f"{plate_id} column {col} t0 must be >= 0")
            spans = entry.get("spans")
            if not isinstance(spans, list) or not spans:
                raise CliError(f"{plate_id} column {col} spans must be a non-empty list")
            for offset, span in enumerate(spans):
                if not isinstance(span, (list, tuple)) or len(span) not in (2, 4):
                    raise CliError(f"{plate_id} column {col} span {offset} must have 2 or 4 numbers")
            no_card = entry.get("noCard") or {}
            if not isinstance(no_card, dict):
                raise CliError(f"{plate_id} column {col} noCard must be an object")
            for reason in no_card.values():
                if reason not in NO_CARD_REASONS:
                    raise CliError(f"{plate_id} column {col} noCard reason must be repair, blank, or damaged")
            repeat = entry.get("repeat") or []
            if not isinstance(repeat, list):
                raise CliError(f"{plate_id} column {col} repeat must be a list")
            named += 1
    if named == 0:
        raise CliError("spec does not name any column")


def cmd_status(args: argparse.Namespace) -> int:
    ctx = Context(args, need_boxes=True, need_review=True)
    if not ctx.plate_ids:
        raise CliError(f"no plates in {display_path(ctx.settings.plates_dir)}")
    plate_ids = ctx.plate_ids
    if args.plate:
        if args.plate not in plate_ids:
            raise CliError(f"unknown plate {args.plate}")
        plate_ids = [args.plate]
    body = build_status(ctx.document, plate_ids, ctx.review)
    raw_count = 0
    raw_plates = ctx.raw.get("plates") if isinstance(ctx.raw, dict) else None
    if isinstance(raw_plates, dict):
        for plate_id in plate_ids:
            items = raw_plates.get(plate_id)
            if isinstance(items, list):
                raw_count += len(items)
    body["counts"]["droppedBoxes"] = max(0, raw_count - body["counts"]["boxes"])
    payload = {
        "ok": True,
        "boxes": display_path(ctx.data_path) if ctx.data_path is not None else "",
        "origin": ctx.origin,
        "review": display_path(ctx.review_path),
        "reviewExists": ctx.review_path.is_file(),
        "textLength": len(ctx.chars),
    }
    payload.update(body)
    return emit(payload, args.json, _format_status(payload))


def cmd_export_column(args: argparse.Namespace) -> int:
    from PIL import Image

    ctx = Context(args, need_boxes=True, need_review=True)
    if args.margin < 0:
        raise CliError("margin must be >= 0")
    boxes = _require_column(ctx, args.plate, args.col)
    image_path = dict(ctx.plates).get(args.plate)
    if image_path is None:
        raise CliError(f"no image for plate {args.plate}")
    out_dir = _resolve_user_path(args.out, _data_dir(args)) / _safe_component(args.plate)
    ensure_outside_package(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"col-{args.col:02d}"
    image_dest = out_dir / f"{stem}.png"
    text_dest = out_dir / f"{stem}.txt"
    try:
        with Image.open(image_path) as image:
            image.load()
            rendered = _render_column(image, boxes, args.margin)
    except OSError as exc:
        raise CliError(f"cannot read {display_path(image_path)}: {exc}") from exc
    rendered.save(image_dest, format="PNG")
    t0, t1, transcription = _slice(boxes, ctx.chars)
    box_chars = "".join(box["char"] if isinstance(box.get("char"), str) and box["char"] else "?" for box in boxes)
    records = []
    lines = [
        f"plate: {args.plate}",
        f"column: {args.col}",
        f"t0: {'' if t0 is None else t0}",
        f"t1: {'' if t1 is None else t1}",
        f"transcription: {transcription}",
        f"box_chars: {box_chars}",
        "rules: docs/CURSIVE_RULES.md",
        f"image: {image_dest.name}",
        "",
        "n\tid\tti\tchar\texpected\tx\ty\tw\th\tflags",
    ]
    for number, box in enumerate(boxes, start=1):
        expected = _expected(ctx.chars, box)
        record = {
            "n": number,
            "id": box.get("id"),
            "ti": box.get("ti") if is_int(box.get("ti")) else None,
            "char": box.get("char") if isinstance(box.get("char"), str) else "",
            "expected": expected,
            "x": box.get("x"),
            "y": box.get("y"),
            "w": box.get("w"),
            "h": box.get("h"),
            "flags": _flags(box),
        }
        records.append(record)
        ti_text = "" if record["ti"] is None else str(record["ti"])
        lines.append(
            "\t".join(
                [
                    str(number),
                    str(record["id"]),
                    ti_text,
                    record["char"],
                    expected,
                    str(record["x"]),
                    str(record["y"]),
                    str(record["w"]),
                    str(record["h"]),
                    record["flags"],
                ]
            )
        )
    text_dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {
        "ok": True,
        "plate": args.plate,
        "col": args.col,
        "image": display_path(image_dest),
        "text": display_path(text_dest),
        "t0": t0,
        "t1": t1,
        "transcription": transcription,
        "boxChars": box_chars,
        "boxes": records,
    }
    summary = (
        f"wrote {payload['image']}\n"
        f"wrote {payload['text']}\n"
        f"transcription: {transcription}"
    )
    return emit(payload, args.json, summary)


def cmd_apply_spec(args: argparse.Namespace) -> int:
    from tools.apply_column_spec import ApplyBlocked, execute_apply

    ctx = Context(args, need_boxes=True, need_review=True)
    spec_path = _resolve_user_path(args.spec, _data_dir(args))
    if not spec_path.is_file():
        raise CliError(f"spec not found: {display_path(spec_path)}")
    spec = _read_object(spec_path)
    _check_spec(spec, set(ctx.plate_ids))
    from cba.review_state import targeted_protected

    blocked = targeted_protected(ctx.document, spec, ctx.review)
    if blocked:
        names = ", ".join(f"{item['plate']} col {item['col']}" for item in blocked)
        return fail(
            f"refusing to replace user-approved columns: {names}",
            args.json,
            code="approved",
            columns=blocked,
        )
    seeded = False
    boxes_path = ctx.settings.boxes_path
    if not args.dry_run and not boxes_path.is_file():
        if ctx.origin != "example":
            raise CliError(f"boxes file not found: {display_path(boxes_path)}")
        save_boxes(boxes_path, ctx.document)
        seeded = True
    try:
        result = execute_apply(
            boxes_path,
            ctx.document,
            spec,
            ctx.chars,
            label=args.label,
            dry_run=args.dry_run,
            review_state=ctx.review,
        )
    except ApplyBlocked as exc:
        return fail(
            "refusing to replace user-approved columns",
            args.json,
            code="approved",
            columns=exc.columns,
        )
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    backup = result["backup"]
    payload = {
        "ok": True,
        "dryRun": bool(args.dry_run),
        "seeded": seeded,
        "boxes": display_path(boxes_path),
        "backup": display_path(backup) if backup is not None else None,
        "applied": result["applied"],
    }
    if args.dry_run:
        text = "dry-run; boxes.json not written\n" + json.dumps(result["applied"], ensure_ascii=False)
    else:
        text = f"backup {payload['backup']}\nwrote {payload['boxes']}"
    return emit(payload, args.json, text)


def cmd_contact_sheet(args: argparse.Namespace) -> int:
    from PIL import Image

    from boxannotator import find_cjk_font
    from tools.contact_sheet import load_low_cols, render_plate

    ctx = Context(args, need_boxes=True, need_review=False)
    if not ctx.plate_ids:
        raise CliError(f"no plates in {display_path(ctx.settings.plates_dir)}")
    low_path = None
    if args.lowconf:
        low_path = _resolve_user_path(args.lowconf, _data_dir(args))
        if not low_path.is_file():
            raise CliError(f"lowconf file not found: {display_path(low_path)}")
    low_cols = load_low_cols(low_path, args.low_cols)
    font_path = find_cjk_font(ctx.settings.cjk_font)
    out_dir = _resolve_user_path(args.out, _data_dir(args))
    ensure_outside_package(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for plate_id, image_path in ctx.plates:
        items = ctx.document["plates"].get(plate_id) or []
        if not items:
            continue
        try:
            with Image.open(image_path) as image:
                image.load()
                title = f"{plate_id}  {len(items)} 框   红框 = 低置信列；灰字 = 释文序号；蓝框 = 不制卡"
                sheet = render_plate(image, items, low_cols, font_path, title)
        except OSError as exc:
            raise CliError(f"cannot read {display_path(image_path)}: {exc}") from exc
        dest = out_dir / f"{plate_id}.png"
        sheet.save(dest, format="PNG")
        written.append(dest)
    if not written:
        return fail("no boxes to draw", args.json)
    payload = {"ok": True, "out": display_path(out_dir), "sheets": [display_path(path) for path in written]}
    text = "\n".join(f"wrote {display_path(path)}" for path in written)
    return emit(payload, args.json, text)


def cmd_validate(args: argparse.Namespace) -> int:
    ctx = Context(args, need_boxes=True, need_review=False)
    issues = validate_document(ctx.raw, ctx.chars)
    by_code: dict[str, int] = {}
    for issue in issues:
        by_code[issue["code"]] = by_code.get(issue["code"], 0) + 1
    box_count = 0
    plates = ctx.raw.get("plates") if isinstance(ctx.raw, dict) else None
    if isinstance(plates, dict):
        for items in plates.values():
            if isinstance(items, list):
                box_count += len(items)
    payload = {
        "ok": not issues,
        "path": display_path(ctx.data_path) if ctx.data_path is not None else "",
        "origin": ctx.origin,
        "issues": issues,
        "counts": {"boxes": box_count, "issues": len(issues), "byCode": by_code},
    }
    if issues:
        lines = [f"{payload['path']}: {len(issues)} issue(s)"]
        lines.extend(f"  {issue['code']}: {issue['message']}" for issue in issues)
        return emit(payload, args.json, "\n".join(lines), ok=False)
    return emit(payload, args.json, f"ok {payload['path']} boxes={box_count}")


def cmd_review(args: argparse.Namespace) -> int:
    ctx = Context(args, need_boxes=True, need_review=True)
    _require_column(ctx, args.plate, args.col)
    note = _clean_note(args.note)
    if args.unresolved and not note:
        raise CliError("unresolved requires --note")
    if args.reviewed:
        entry = set_reviewed(ctx.review, args.plate, args.col, note=note)
        action = "reviewed"
    elif args.unresolved:
        entry = set_unresolved(ctx.review, args.plate, args.col, note=note)
        action = "unresolved"
    else:
        entry = clear_review(ctx.review, args.plate, args.col)
        action = "clear"
    save_review(ctx.review_path, ctx.review)
    payload = {
        "ok": True,
        "action": action,
        "plate": args.plate,
        "col": args.col,
        "review": display_path(ctx.review_path),
        "entry": entry,
    }
    text = f"{args.plate} col {args.col}: {action}"
    if entry.get("note"):
        text += f" ({entry['note']})"
    if entry.get("approved"):
        text += " (approved, unchanged)"
    return emit(payload, args.json, text)


def cmd_cut(args: argparse.Namespace) -> int:
    from PIL import Image

    from tools.column_cut import cut_image

    settings = load_settings(args)
    plates = discover_plates(settings.plates_dir, settings.plate_glob)
    if not plates:
        raise CliError(f"no plates in {display_path(settings.plates_dir)}")
    plate_id, path = plates[0]
    if args.plate:
        match = [item for item in plates if item[0] == args.plate]
        if not match:
            raise CliError(f"unknown plate {args.plate}")
        plate_id, path = match[0]
    if args.per_column < 1:
        raise CliError("per-column must be at least 1")
    try:
        with Image.open(path) as image:
            image.load()
            boxes = cut_image(image, args.per_column)
    except OSError as exc:
        raise CliError(f"cannot read {display_path(path)}: {exc}") from exc
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    if not boxes:
        return fail(f"{plate_id}: no columns cut", args.json)
    grouped: dict[int, list[dict]] = {}
    for box in boxes:
        grouped.setdefault(int(box["col"]), []).append(box)
    columns = []
    for col in sorted(grouped):
        items = sorted(grouped[col], key=lambda box: (box["y"], box["x"]))
        spans = [
            [int(box["y"]), int(box["y"] + box["h"]), int(box["x"]), int(box["w"])]
            for box in items
        ]
        columns.append({"col": col, "boxes": len(items), "spans": spans})
    payload = {
        "ok": True,
        "plate": plate_id,
        "perColumn": args.per_column,
        "boxCount": len(boxes),
        "columnCount": len(columns),
        "columns": columns,
        "wrote": False,
    }
    lines = [f"{plate_id}: {len(boxes)} boxes in {len(columns)} column(s)"]
    for column in columns:
        lines.append(f"  col {column['col']} boxes={column['boxes']}")
    lines.append("cut is a proposal; write a spec and apply-spec to save it")
    return emit(payload, args.json, "\n".join(lines))


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the annotator. Blocks until the process is interrupted."""
    from serve import main as serve_main

    forwarded: list[str] = []
    if args.data_dir:
        forwarded += ["--data-dir", args.data_dir]
    if args.config:
        forwarded += ["--config", args.config]
    if args.plates_dir:
        forwarded += ["--plates-dir", args.plates_dir]
    if args.boxes:
        forwarded += ["--boxes", args.boxes]
    if args.text:
        forwarded += ["--text", args.text]
    if args.plate_glob:
        forwarded += ["--plate-glob", args.plate_glob]
    if args.host:
        forwarded += ["--host", args.host]
    if args.port is not None:
        forwarded += ["--port", str(args.port)]
    try:
        serve_main(forwarded)
    except KeyboardInterrupt:
        return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cba",
        description="Agent CLI for per-column calligraphy boxes. Does not call a model API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_shared(target: argparse.ArgumentParser) -> None:
        add_common_arguments(target, crop=False)
        target.add_argument("--json", action="store_true", help="print one JSON object on stdout")

    status = sub.add_parser("status", help="per-plate and per-column counts, plus unreviewed columns")
    add_shared(status)
    status.add_argument("--plate", help="only this plate id")

    export = sub.add_parser(
        "export-column",
        help="write one column image with numbered boxes and that column's transcription",
    )
    add_shared(export)
    export.add_argument("--plate", required=True, help="plate id")
    export.add_argument("--col", required=True, type=int, help="column number, 1 = rightmost")
    export.add_argument("--out", default="review", help="output directory (default: review)")
    export.add_argument("--margin", type=int, default=24, help="pixels of context around the column")

    apply_cmd = sub.add_parser("apply-spec", help="apply a column spec JSON onto boxes.json")
    add_shared(apply_cmd)
    apply_cmd.add_argument("--spec", required=True, help="spec JSON path")
    apply_cmd.add_argument("--label", default="spec", help="backup suffix: boxes.json.pre_<label>")
    apply_cmd.add_argument("--dry-run", action="store_true", help="do not write boxes.json")

    sheet = sub.add_parser("contact-sheet", help="write per-plate contact sheets")
    add_shared(sheet)
    sheet.add_argument("--out", default="sheets", help="output directory (default: sheets)")
    sheet.add_argument("--font", dest="cjk_font", help="CJK font file")
    sheet.add_argument("--low-cols", help="comma-separated low-confidence column ids")
    sheet.add_argument("--lowconf", help="JSON list or object of low-confidence column ids")

    check = sub.add_parser(
        "validate",
        help="check boxes for overlaps, empty chars, ti gaps/duplicates, and transcription mismatches",
    )
    add_shared(check)

    review = sub.add_parser("review", help="mark a column reviewed or unresolved so a later run can resume")
    add_shared(review)
    review.add_argument("--plate", required=True)
    review.add_argument("--col", required=True, type=int)
    mode = review.add_mutually_exclusive_group(required=True)
    mode.add_argument("--reviewed", action="store_true", help="column checks out; a person is not needed")
    mode.add_argument("--unresolved", action="store_true", help="escalate to a person; requires --note")
    mode.add_argument("--clear", action="store_true", help="drop reviewed/unresolved; keep approved")
    review.add_argument("--note", default="", help="reason, required with --unresolved")

    cut = sub.add_parser("cut", help="propose N boxes per ink column; does not write boxes.json")
    add_shared(cut)
    cut.add_argument("--per-column", type=int, default=4, help="exact box count inside each column")
    cut.add_argument("--plate", help="plate id (default: the first plate)")

    serve = sub.add_parser("serve", help="launch the annotator web UI")
    add_shared(serve)
    return parser


_HANDLERS = {
    "status": cmd_status,
    "export-column": cmd_export_column,
    "apply-spec": cmd_apply_spec,
    "contact-sheet": cmd_contact_sheet,
    "validate": cmd_validate,
    "review": cmd_review,
    "cut": cmd_cut,
    "serve": cmd_serve,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 2
    try:
        return _HANDLERS[args.command](args)
    except CliError as exc:
        return fail(str(exc), getattr(args, "json", False), **exc.extra)
    except ReviewError as exc:
        return fail(str(exc), getattr(args, "json", False))
    except SystemExit as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": "command failed"}, ensure_ascii=False, indent=2))
        code = exc.code
        return int(code) if isinstance(code, int) else 1
