"""Per-column review sidecar so an agent can stop and resume.

The file lives next to the configured boxes path as ``<stem>.review.json``
(for ``boxes.json`` that is ``boxes.review.json``). It is local state.

``approved`` is set by a person, in this file or as ``approved: true`` on a
box. The review subcommand never changes it. ``reviewed`` and ``unresolved``
are what an agent writes after looking at a column.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.apply_column_spec import spec_targets


class ReviewError(Exception):
    """The sidecar is unreadable. Callers should fail instead of guessing."""


def review_sidecar(boxes_path: Path) -> Path:
    return boxes_path.with_name(boxes_path.stem + ".review.json")


def empty_review() -> dict:
    return {"version": 1, "columns": {}}


def _blank_entry() -> dict:
    return {"reviewed": False, "unresolved": False, "approved": False, "note": ""}


def _parse_col(value: object) -> int:
    if isinstance(value, bool):
        raise ReviewError(f"invalid column key {value!r}")
    if isinstance(value, int):
        col = value
    elif isinstance(value, str) and value.isdigit():
        col = int(value)
    else:
        raise ReviewError(f"invalid column key {value!r}")
    if col < 1:
        raise ReviewError(f"invalid column key {value!r}")
    return col


def _entry_from(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ReviewError("column entry must be an object")
    note = raw.get("note", "")
    if not isinstance(note, str):
        raise ReviewError("note must be a string")
    return {
        "reviewed": raw.get("reviewed") is True,
        "unresolved": raw.get("unresolved") is True,
        "approved": raw.get("approved") is True,
        "note": note,
    }


def load_review(path: Path) -> dict:
    """Return the sidecar, or an empty state when the file is absent.

    A present but unreadable file raises ``ReviewError`` so a broken sidecar
    cannot be mistaken for "nothing reviewed".
    """
    if not path.exists():
        return empty_review()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"invalid review file {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewError(f"invalid review file {path.name}: expected an object")
    columns_raw = data.get("columns", {})
    if columns_raw is None:
        columns_raw = {}
    if not isinstance(columns_raw, dict):
        raise ReviewError("review columns must be an object")
    columns: dict[str, dict[str, dict]] = {}
    for plate_id, cols in columns_raw.items():
        if not isinstance(plate_id, str) or not isinstance(cols, dict):
            raise ReviewError("review columns must be plate -> column -> entry")
        plate_cols: dict[str, dict] = {}
        for col_key, entry in cols.items():
            plate_cols[str(_parse_col(col_key))] = _entry_from(entry)
        columns[plate_id] = plate_cols
    return {"version": 1, "columns": columns}


def column_entry(state: dict, plate: str, col: int) -> dict:
    cols = state.get("columns") if isinstance(state.get("columns"), dict) else {}
    plate_cols = cols.get(plate) if isinstance(cols, dict) else None
    if not isinstance(plate_cols, dict):
        return _blank_entry()
    entry = plate_cols.get(str(int(col)))
    if not isinstance(entry, dict):
        return _blank_entry()
    return {
        "reviewed": entry.get("reviewed") is True,
        "unresolved": entry.get("unresolved") is True,
        "approved": entry.get("approved") is True,
        "note": entry.get("note") if isinstance(entry.get("note"), str) else "",
    }


def save_review(path: Path, state: dict) -> None:
    from boxannotator import ensure_outside_package

    ensure_outside_package(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "columns": state.get("columns") or {}}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _ensure(state: dict, plate: str, col: int) -> dict:
    columns = state.setdefault("columns", {})
    plate_cols = columns.setdefault(plate, {})
    key = str(int(col))
    entry = plate_cols.get(key)
    if not isinstance(entry, dict):
        entry = _blank_entry()
        plate_cols[key] = entry
    return entry


def _prune(state: dict, plate: str, col: int) -> None:
    entry = column_entry(state, plate, col)
    if entry["reviewed"] or entry["unresolved"] or entry["approved"] or entry["note"]:
        return
    columns = state.get("columns") or {}
    plate_cols = columns.get(plate) or {}
    plate_cols.pop(str(int(col)), None)
    if not plate_cols and plate in columns:
        columns.pop(plate, None)


def set_reviewed(state: dict, plate: str, col: int, *, note: str = "") -> dict:
    entry = _ensure(state, plate, col)
    entry["reviewed"] = True
    entry["unresolved"] = False
    entry["note"] = note
    return dict(entry)


def set_unresolved(state: dict, plate: str, col: int, *, note: str) -> dict:
    entry = _ensure(state, plate, col)
    entry["reviewed"] = False
    entry["unresolved"] = True
    entry["note"] = note
    return dict(entry)


def clear_review(state: dict, plate: str, col: int) -> dict:
    entry = _ensure(state, plate, col)
    entry["reviewed"] = False
    entry["unresolved"] = False
    entry["note"] = ""
    _prune(state, plate, col)
    return column_entry(state, plate, col)


def _box_col(box: dict) -> int | None:
    value = box.get("col")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def is_protected(document: dict, review: dict | None, plate: str, col: int) -> bool:
    """True when a person has approved this column or a box inside it.

    An approved box that has no ``col`` protects every column on that plate,
    because the tool cannot tell which column it belongs to.
    """
    if column_entry(review or empty_review(), plate, col)["approved"]:
        return True
    plates = document.get("plates") if isinstance(document, dict) else None
    boxes = plates.get(plate) if isinstance(plates, dict) else None
    if not isinstance(boxes, list):
        return False
    for box in boxes:
        if not isinstance(box, dict) or box.get("approved") is not True:
            continue
        box_col = _box_col(box)
        if box_col is None or box_col == col:
            return True
    return False


def targeted_protected(document: dict, spec: dict, review: dict | None) -> list[dict]:
    """Approved columns named by ``spec``. Empty when the spec may be applied."""
    blocked = []
    for plate, col in spec_targets(spec):
        if is_protected(document, review, plate, col):
            blocked.append({"plate": plate, "col": col})
    return blocked
