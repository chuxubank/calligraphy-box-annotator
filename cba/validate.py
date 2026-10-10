"""Schema and alignment checks for a boxes document.

Issues are stable records: ``code``, a human ``message``, and the plate / box
ids when they apply. ``validate_document`` does not read the filesystem.
"""

from __future__ import annotations

from boxannotator import CJK_RE, NO_CARD_REASONS

from cba.columns import is_int, split_columns


def _issue(code: str, message: str, **fields: object) -> dict:
    item = {"code": code, "message": message}
    item.update(fields)
    return item


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _sort_key(issue: dict) -> tuple:
    def ident(key: str) -> int:
        value = issue.get(key)
        return value if isinstance(value, int) else -1

    return (
        str(issue.get("plate") or ""),
        str(issue.get("code") or ""),
        ident("index"),
        ident("id"),
        ident("otherId"),
        ident("ti"),
        str(issue.get("message") or ""),
    )


def _parse_box(raw: object, plate: str, index: int) -> tuple[dict | None, list[dict]]:
    issues: list[dict] = []
    where = {"plate": plate, "index": index}
    if not isinstance(raw, dict):
        issues.append(_issue("schema", f"{plate}[{index}] is not an object", **where))
        return None, issues
    box_id = _as_int(raw.get("id"))
    if box_id is None:
        issues.append(_issue("schema", f"{plate}[{index}] needs an integer id", **where))
    else:
        where["id"] = box_id
    numbers = {}
    for key in ("x", "y", "w", "h"):
        number = _as_float(raw.get(key))
        if number is None:
            issues.append(_issue("schema", f"{plate}[{index}] field {key} must be a finite number", **where))
        else:
            numbers[key] = number
    if "w" in numbers and numbers["w"] <= 0:
        issues.append(_issue("schema", f"{plate}[{index}] width must be positive", **where))
    if "h" in numbers and numbers["h"] <= 0:
        issues.append(_issue("schema", f"{plate}[{index}] height must be positive", **where))
    if "col" in raw and raw.get("col") is not None and not is_int(raw.get("col")):
        # Integral floats are accepted as column ids.
        if _as_int(raw.get("col")) is None:
            issues.append(_issue("schema", f"{plate}[{index}] col must be an integer", **where))
    if "ti" in raw and raw.get("ti") is not None and _as_int(raw.get("ti")) is None:
        issues.append(_issue("schema", f"{plate}[{index}] ti must be an integer", **where))
    if "repeatMark" in raw and not isinstance(raw.get("repeatMark"), bool):
        issues.append(_issue("schema", f"{plate}[{index}] repeatMark must be a boolean", **where))
    if "noCard" in raw and not isinstance(raw.get("noCard"), bool):
        issues.append(_issue("schema", f"{plate}[{index}] noCard must be a boolean", **where))
    if "approved" in raw and not isinstance(raw.get("approved"), bool):
        issues.append(_issue("schema", f"{plate}[{index}] approved must be a boolean", **where))
    if issues:
        return None, issues
    box = {
        "id": box_id,
        "x": numbers["x"],
        "y": numbers["y"],
        "w": numbers["w"],
        "h": numbers["h"],
    }
    col = _as_int(raw.get("col"))
    if col is not None:
        box["col"] = col
    ti = _as_int(raw.get("ti"))
    if ti is not None:
        box["ti"] = ti
    if "char" in raw and raw.get("char") not in (None, ""):
        box["char"] = raw.get("char")
    if raw.get("repeatMark") is True:
        box["repeatMark"] = True
    if raw.get("noCard") is True:
        box["noCard"] = True
        box["noCardReason"] = raw.get("noCardReason")
    if raw.get("approved") is True:
        box["approved"] = True
    return box, []


def _intersection(a: dict, b: dict) -> float:
    x0 = max(a["x"], b["x"])
    y0 = max(a["y"], b["y"])
    x1 = min(a["x"] + a["w"], b["x"] + b["w"])
    y1 = min(a["y"] + a["h"], b["y"] + b["h"])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _semantic(plate: str, boxes: list[dict], chars: list[str]) -> list[dict]:
    issues: list[dict] = []
    seen_ids: dict[int, int] = {}
    for box in boxes:
        box_id = box["id"]
        if box_id in seen_ids:
            issues.append(
                _issue(
                    "duplicate_id",
                    f"{plate} id {box_id} is used more than once",
                    plate=plate,
                    id=box_id,
                )
            )
        else:
            seen_ids[box_id] = box_id
        char = box.get("char")
        if not isinstance(char, str) or char == "":
            issues.append(_issue("empty_char", f"{plate} box {box_id} has no character", plate=plate, id=box_id))
        elif not (len(char) == 1 and CJK_RE.fullmatch(char)):
            issues.append(
                _issue("bad_char", f"{plate} box {box_id} character {char!r} is not one CJK ideograph", plate=plate, id=box_id)
            )
        if "ti" not in box:
            issues.append(_issue("missing_ti", f"{plate} box {box_id} has no ti", plate=plate, id=box_id))
        else:
            ti = box["ti"]
            if ti < 0 or ti >= len(chars):
                issues.append(
                    _issue(
                        "ti_out_of_range",
                        f"{plate} box {box_id} ti {ti} is outside the transcription (length {len(chars)})",
                        plate=plate,
                        id=box_id,
                        ti=ti,
                    )
                )
            elif isinstance(char, str) and len(char) == 1 and CJK_RE.fullmatch(char) and chars[ti] != char:
                issues.append(
                    _issue(
                        "char_mismatch",
                        f"{plate} box {box_id} char {char} != transcription[{ti}] {chars[ti]}",
                        plate=plate,
                        id=box_id,
                        ti=ti,
                    )
                )
        if box.get("noCard") is True and box.get("noCardReason") not in NO_CARD_REASONS:
            issues.append(
                _issue(
                    "no_card_reason",
                    f"{plate} box {box_id} noCard needs a reason repair, blank, or damaged",
                    plate=plate,
                    id=box_id,
                )
            )

    seen_pair: set[tuple[int, int]] = set()
    for left_index, left in enumerate(boxes):
        for right in boxes[left_index + 1 :]:
            pair = tuple(sorted((int(left["id"]), int(right["id"]))))
            if pair in seen_pair:
                continue
            if _intersection(left, right) <= 0:
                continue
            seen_pair.add(pair)
            issues.append(
                _issue(
                    "overlap",
                    f"{plate} boxes {pair[0]} and {pair[1]} overlap",
                    plate=plate,
                    id=pair[0],
                    otherId=pair[1],
                )
            )

    column_holes: set[int] = set()
    for col, items in split_columns(boxes):
        tis = [item["ti"] for item in items if "ti" in item]
        ordered = sorted(tis)
        for earlier, later in zip(ordered, ordered[1:]):
            if later - earlier <= 1:
                continue
            for missing in range(earlier + 1, later):
                column_holes.add(missing)
                issues.append(
                    _issue(
                        "ti_gap",
                        f"{plate} column {col} is missing ti {missing}",
                        plate=plate,
                        col=col,
                        ti=missing,
                        scope="column",
                    )
                )
        last_ti: int | None = None
        for item in items:
            if "ti" not in item:
                continue
            ti = item["ti"]
            if last_ti is not None and ti <= last_ti:
                issues.append(
                    _issue(
                        "ti_order",
                        f"{plate} column {col} ti goes backwards at box {item['id']} (ti {ti} after {last_ti})",
                        plate=plate,
                        id=item["id"],
                        col=col,
                        ti=ti,
                    )
                )
            last_ti = ti

    plate_tis = sorted({box["ti"] for box in boxes if "ti" in box})
    for earlier, later in zip(plate_tis, plate_tis[1:]):
        if later - earlier <= 1:
            continue
        for missing in range(earlier + 1, later):
            if missing in column_holes:
                continue
            issues.append(
                _issue(
                    "ti_gap",
                    f"{plate} is missing ti {missing} between columns",
                    plate=plate,
                    ti=missing,
                    scope="plate",
                )
            )
    return issues


def validate_document(data: object, chars: list[str]) -> list[dict]:
    """Return every schema and alignment issue in ``data``.

    ``chars`` is the transcription as CJK characters, index 0 first.
    Overlapping means the rectangles have positive area in common; shared
    edges are not overlaps. Within a column, ``ti`` must increase top to
    bottom and match ``chars[ti]``.
    """
    if not isinstance(data, dict):
        return [_issue("schema", "boxes document must be a JSON object")]
    plates = data.get("plates")
    if not isinstance(plates, dict):
        return [_issue("schema", "plates must be an object")]

    issues: list[dict] = []
    seen_global: dict[int, tuple[str, int]] = {}
    seen_ti: dict[int, tuple[str, int]] = {}
    for plate, items in plates.items():
        plate_id = str(plate)
        if not isinstance(items, list):
            issues.append(_issue("schema", f"{plate_id} boxes must be a list", plate=plate_id))
            continue
        parsed: list[dict] = []
        for index, raw in enumerate(items):
            box, box_issues = _parse_box(raw, plate_id, index)
            issues.extend(box_issues)
            if box is None:
                continue
            parsed.append(box)
            previous = seen_global.get(box["id"])
            if previous is not None and previous[0] != plate_id:
                issues.append(
                    _issue(
                        "duplicate_id",
                        f"id {box['id']} is used on {previous[0]} and {plate_id}",
                        plate=plate_id,
                        id=box["id"],
                    )
                )
            else:
                seen_global.setdefault(box["id"], (plate_id, box["id"]))
            if "ti" in box:
                prior = seen_ti.get(box["ti"])
                if prior is not None:
                    issues.append(
                        _issue(
                            "duplicate_ti",
                            f"ti {box['ti']} is on {prior[0]} box {prior[1]} and {plate_id} box {box['id']}",
                            plate=plate_id,
                            id=box["id"],
                            ti=box["ti"],
                        )
                    )
                else:
                    seen_ti[box["ti"]] = (plate_id, box["id"])
        issues.extend(_semantic(plate_id, parsed, chars))
    issues.sort(key=_sort_key)
    return issues
