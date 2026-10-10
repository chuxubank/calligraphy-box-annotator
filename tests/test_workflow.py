"""Flags, fixed labels, crop policy, column cuts, and contact sheets."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import boxannotator  # noqa: E402
from boxannotator import clean_box, crop_plates, load_settings, normalize_boxes  # noqa: E402
from serve import ensure_boxes_file  # noqa: E402
from tools.apply_column_spec import apply_spec  # noqa: E402
from tools.column_cut import cut_image  # noqa: E402
from tools.contact_sheet import group_columns, load_low_cols, render_plate  # noqa: E402
from tests.test_boxannotator import EnvGuard, ns  # noqa: E402


class FlagTests(unittest.TestCase):
    def test_clean_box_keeps_column_and_card_flags(self) -> None:
        kept = clean_box(
            {
                "id": 4,
                "x": 1,
                "y": 2,
                "w": 3,
                "h": 4,
                "char": "乙",
                "col": 1,
                "ti": 8,
                "repeatMark": True,
                "noCard": True,
                "noCardReason": "repair",
                "noCardReasonIgnored": "nope",
            }
        )
        self.assertEqual(kept["col"], 1)
        self.assertEqual(kept["ti"], 8)
        self.assertTrue(kept["repeatMark"])
        self.assertEqual(kept["noCardReason"], "repair")
        dropped = clean_box(
            {"id": 1, "x": 0, "y": 0, "w": 1, "h": 1, "char": "甲", "repeatMark": False, "noCardReason": "blank"}
        )
        self.assertNotIn("repeatMark", dropped)
        self.assertNotIn("noCard", dropped)

    def test_fixed_label_mode_survives_normalize(self) -> None:
        raw = {
            "labelMode": "fixed",
            "textOffsetByPlate": {"plate-01": 3},
            "plates": {
                "plate-01": [
                    {"id": 2, "x": 10, "y": 10, "w": 20, "h": 20, "char": "乙", "col": 1, "ti": 1, "repeatMark": True}
                ]
            },
        }
        clean = normalize_boxes(raw, ["plate-01"], "manual")
        self.assertEqual(clean["labelMode"], "fixed")
        box = clean["plates"]["plate-01"][0]
        self.assertEqual(box["char"], "乙")
        self.assertEqual(box["ti"], 1)
        self.assertTrue(box["repeatMark"])
        offset_mode = normalize_boxes({"plates": {"plate-01": []}}, ["plate-01"], "manual")
        self.assertEqual(offset_mode["labelMode"], "offset")


class CropFlagTests(EnvGuard):
    def test_skip_nocard_and_mark_repeat(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plates = folder / "plates"
            plates.mkdir()
            Image.new("RGB", (30, 20), "white").save(plates / "plate-01.png")
            boxes = {
                "labelMode": "fixed",
                "plates": {
                    "plate-01": [
                        {"id": 1, "x": 0, "y": 0, "w": 8, "h": 8, "char": "甲", "repeatMark": True},
                        {"id": 2, "x": 10, "y": 0, "w": 8, "h": 8, "char": "乙", "noCard": True, "noCardReason": "blank"},
                        {"id": 3, "x": 0, "y": 10, "w": 8, "h": 8, "char": "丙"},
                    ]
                },
            }
            boxes_path = folder / "boxes.json"
            boxannotator.save_boxes(boxes_path, boxes)
            settings = load_settings(
                ns(
                    plates_dir=str(plates),
                    boxes=str(boxes_path),
                    text=str(folder / "missing.txt"),
                    crop_out=str(folder / "cropped"),
                    plate_glob="*.png",
                )
            )
            report = crop_plates(settings)
            names = sorted(path.name for path in report.written)
            self.assertEqual(names, ["plate-01_0001_甲_repeat.png", "plate-01_0003_丙.png"])
            self.assertEqual(report.skipped_nocard, 1)
            skipped = crop_plates(settings, skip_repeat=True)
            self.assertEqual(sorted(path.name for path in skipped.written), ["plate-01_0003_丙.png"])
            self.assertEqual(skipped.skipped_repeat, 1)
            included = crop_plates(settings, include_nocard=True)
            self.assertIn("plate-01_0002_乙.png", [path.name for path in included.written])


class SeedTests(EnvGuard):
    def test_missing_boxes_file_is_seeded_from_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plates = folder / "plates"
            plates.mkdir()
            (plates / "plate-01.png").write_bytes(b"x")
            example = {
                "labelMode": "fixed",
                "plates": {
                    "plate-01": [
                        {
                            "id": 1,
                            "x": 0,
                            "y": 0,
                            "w": 4,
                            "h": 4,
                            "char": "甲",
                            "col": 1,
                            "ti": 0,
                            "repeatMark": True,
                        }
                    ]
                },
            }
            (folder / "boxes.example.json").write_text(json.dumps(example), encoding="utf-8")
            settings = load_settings(
                ns(
                    plates_dir=str(plates),
                    boxes=str(folder / "boxes.json"),
                    text=str(folder / "t.txt"),
                    crop_out=str(folder / "out"),
                )
            )
            ensure_boxes_file(settings)
            saved = json.loads((folder / "boxes.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["labelMode"], "fixed")
            self.assertTrue(saved["plates"]["plate-01"][0]["repeatMark"])


class ColumnAndSheetTests(unittest.TestCase):
    def test_demo_plate_cuts_into_two_columns_of_four(self) -> None:
        from PIL import Image

        with Image.open(ROOT / "examples/demo/plates/plate-01.png") as image:
            boxes = cut_image(image, per_column=4)
        self.assertEqual(len(boxes), 8)
        by_col: dict[int, list[dict]] = {}
        for box in boxes:
            by_col.setdefault(box["col"], []).append(box)
        self.assertEqual(sorted(by_col), [1, 2])
        right = sorted(by_col[1], key=lambda box: box["y"])
        left = sorted(by_col[2], key=lambda box: box["y"])
        self.assertEqual(len(right), 4)
        self.assertEqual(len(left), 4)
        self.assertGreater(sum(box["x"] for box in right) / 4, sum(box["x"] for box in left) / 4)
        for column in (right, left):
            for earlier, later in zip(column, column[1:]):
                self.assertLess(earlier["y"] + earlier["h"] * 0.5, later["y"])

    def test_contact_sheet_marks_low_column(self) -> None:
        from PIL import Image

        document = json.loads((ROOT / "examples/demo/boxes.example.json").read_text(encoding="utf-8"))
        columns = group_columns(document["plates"]["plate-01"])
        self.assertEqual([col for col, _ in columns], [1, 2])
        self.assertTrue(columns[0][1][1]["repeatMark"])
        low = load_low_cols(ROOT / "examples/demo/lowconf.example.json", None)
        self.assertIn(2, low)
        with Image.open(ROOT / "examples/demo/plates/plate-01.png") as image:
            sheet = render_plate(image, document["plates"]["plate-01"], low, boxannotator.find_cjk_font(None), "demo")
        pixels = sheet.get_flattened_data() if hasattr(sheet, "get_flattened_data") else sheet.getdata()
        red = 0
        for pixel in pixels:
            if pixel[0] > 200 and pixel[1] < 40 and pixel[2] < 40:
                red += 1
                break
        self.assertGreater(red, 0)
        self.assertGreater(sheet.size[0], 200)

    def test_spec_replaces_one_column_and_flags_repeat(self) -> None:
        document = {
            "labelMode": "fixed",
            "plates": {
                "plate-01": [
                    {"id": 1, "col": 1, "ti": 0, "x": 10, "y": 10, "w": 20, "h": 20, "char": "甲"},
                    {"id": 2, "col": 2, "ti": 4, "x": 80, "y": 10, "w": 20, "h": 20, "char": "戊"},
                ]
            },
        }
        spec = {
            "plates": {
                "plate-01": {
                    "1": {
                        "t0": 0,
                        "x": 10,
                        "w": 20,
                        "spans": [[10, 40], [40, 70]],
                        "repeat": [1],
                    }
                }
            }
        }
        updated = apply_spec(document, spec, list("甲乙丙丁"))
        column = [box for box in updated["plates"]["plate-01"] if box["col"] == 1]
        self.assertEqual([box["char"] for box in column], ["甲", "乙"])
        self.assertTrue(column[1]["repeatMark"])
        self.assertEqual([box["char"] for box in updated["plates"]["plate-01"] if box["col"] == 2], ["戊"])
        self.assertEqual(updated["labelMode"], "fixed")


if __name__ == "__main__":
    unittest.main()
