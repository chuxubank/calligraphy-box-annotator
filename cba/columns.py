"""Group boxes into reading-order columns.

Column 1 is the rightmost column. When every box has an integer ``col``,
that field is the column id. Otherwise boxes are clustered on x, the same
way the contact sheet does it.
"""

from __future__ import annotations


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def split_columns(boxes: list[dict]) -> list[tuple[int, list[dict]]]:
    if not boxes:
        return []
    if all(is_int(box.get("col")) and box["col"] >= 1 for box in boxes):
        grouped: dict[int, list[dict]] = {}
        for box in boxes:
            grouped.setdefault(int(box["col"]), []).append(box)
        columns = []
        for col in sorted(grouped):
            items = sorted(grouped[col], key=lambda box: (float(box["y"]), int(box["id"])))
            columns.append((col, items))
        return columns
    from tools.contact_sheet import group_columns

    columns = []
    for col, items in group_columns(boxes):
        ordered = sorted(items, key=lambda box: (float(box["y"]), int(box["id"])))
        columns.append((col, ordered))
    return columns


def column_boxes(boxes: list[dict], col: int) -> list[dict] | None:
    """Boxes in ``col`` sorted top to bottom, or None when that column is absent."""
    found = dict(split_columns(boxes))
    items = found.get(col)
    if items is None:
        return None
    return items
