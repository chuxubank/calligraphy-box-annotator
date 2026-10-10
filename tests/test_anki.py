"""Anki deck export: media files, skip rules, and stable ids."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

import genanki  # noqa: E402

from cba.anki import (  # noqa: E402
    JPEG_QUALITY,
    MAX_SIDE,
    PAD_PX,
    DeckError,
    build_apkg,
    crop_glyph,
    deck_id_for,
    default_template_dir,
    load_template,
    model_id_for,
    note_guid,
)
from tests.test_boxannotator import EnvGuard  # noqa: E402
from tests.test_cli import run  # noqa: E402


def _png(size: tuple[int, int], boxes: list[tuple[int, int, int, int, tuple[int, int, int]]]) -> bytes:
    image = Image.new("RGB", size, (245, 240, 230))
    for x, y, w, h, color in boxes:
        image.paste(Image.new("RGB", (w, h), color), (x, y))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _box(box_id: int, x: int, y: int, w: int, h: int, char: str, ti: int, **flags: object) -> dict:
    item = {"id": box_id, "col": 1, "x": x, "y": y, "w": w, "h": h, "ti": ti, "char": char}
    item.update(flags)
    return item


def read_apkg(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        collection_name = next(name for name in names if name.startswith("collection"))
        media = json.loads(archive.read("media"))
        collection = archive.read(collection_name)
        blobs = {str(key): archive.read(str(key)) for key in media}
        file_names = list(names)
    db_path = path.with_suffix(".anki2")
    db_path.write_bytes(collection)
    try:
        conn = sqlite3.connect(db_path)
        notes = conn.execute("select guid, flds, sfld from notes order by sfld").fetchall()
        card_count = conn.execute("select count(*) from cards").fetchone()[0]
        models = json.loads(conn.execute("select models from col").fetchone()[0])
        decks = json.loads(conn.execute("select decks from col").fetchone()[0])
        conn.close()
    finally:
        db_path.unlink(missing_ok=True)
    return {
        "names": file_names,
        "collection_name": collection_name,
        "collection": collection,
        "media": media,
        "blobs": blobs,
        "notes": [
            {"guid": guid, "fields": flds.split("\x1f"), "sort": sfld}
            for guid, flds, sfld in notes
        ],
        "cards": card_count,
        "models": models,
        "decks": decks,
    }


class IdTests(unittest.TestCase):
    def test_ids_depend_only_on_the_deck_name_and_character(self) -> None:
        self.assertEqual(deck_id_for("calligraphy"), deck_id_for("calligraphy"))
        self.assertNotEqual(deck_id_for("calligraphy"), deck_id_for("other"))
        self.assertEqual(model_id_for("calligraphy"), model_id_for("calligraphy", "default"))
        self.assertNotEqual(model_id_for("calligraphy", "default"), model_id_for("calligraphy", "plain"))
        self.assertNotEqual(deck_id_for("calligraphy"), model_id_for("calligraphy", "default"))
        for name in ("calligraphy", "书谱", "a"):
            for value in (deck_id_for(name), model_id_for(name)):
                self.assertIsInstance(value, int)
                self.assertGreaterEqual(value, 2)
                self.assertLessEqual(value, 2_000_000_000)
        self.assertEqual(note_guid("calligraphy", "甲"), genanki.guid_for("calligraphy", "甲"))
        self.assertNotEqual(note_guid("calligraphy", "甲"), note_guid("other", "甲"))
        self.assertNotEqual(note_guid("calligraphy", "甲"), note_guid("calligraphy", "乙"))
        self.assertEqual(JPEG_QUALITY, 85)
        self.assertEqual(MAX_SIDE, 400)
        self.assertEqual(PAD_PX, 8)


class CropTests(unittest.TestCase):
    def test_padding_is_small_and_long_side_is_capped(self) -> None:
        image = Image.new("RGB", (100, 100), (255, 255, 255))
        image.paste(Image.new("RGB", (30, 30), (0, 0, 0)), (20, 20))
        glyph = crop_glyph(image, {"x": 20, "y": 20, "w": 30, "h": 30})
        self.assertEqual(glyph.size, (30 + PAD_PX * 2, 30 + PAD_PX * 2))
        self.assertEqual(glyph.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(glyph.getpixel((PAD_PX + 15, PAD_PX + 15)), (0, 0, 0))

        wide = Image.new("RGB", (900, 100), (10, 20, 30))
        fitted = crop_glyph(wide, {"x": 0, "y": 0, "w": 900, "h": 100}, pad=0)
        self.assertLessEqual(max(fitted.size), MAX_SIDE)
        self.assertEqual(fitted.size[0], MAX_SIDE)


class PackageTests(EnvGuard):
    def _build(
        self,
        folder: Path,
        boxes: list[dict],
        *,
        deck_name: str = "glyphs",
        front: str = "image",
        color: tuple[int, int, int] = (20, 20, 20),
    ) -> tuple[Path, dict]:
        image_path = folder / "plate-a.png"
        image_path.write_bytes(
            _png(
                (240, 240),
                [
                    (10, 10, 40, 40, color),
                    (10, 80, 40, 40, color),
                    (80, 10, 40, 40, (60, 60, 60)),
                    (80, 80, 50, 50, (80, 80, 80)),
                ],
            )
        )
        document = {"plates": {"plate-a": boxes}}
        out = folder / "out.apkg"
        summary = build_apkg([("plate-a", image_path)], document, deck_name, out, front=front)
        return out, summary

    def test_groups_variants_skips_repeat_and_nocard_and_stores_media_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            boxes = [
                _box(1, 10, 10, 40, 40, "甲", 0),
                _box(2, 10, 80, 40, 40, "甲", 1),
                _box(3, 80, 10, 40, 40, "乙", 2, repeatMark=True),
                _box(4, 80, 80, 40, 40, "丙", 3, noCard=True, noCardReason="blank"),
                _box(5, 150, 10, 40, 40, "丁", 4, repeatMark=True, noCard=True, noCardReason="repair"),
                {"id": 6, "x": 150, "y": 80, "w": 20, "h": 20, "ti": 5},
            ]
            out, summary = self._build(folder, boxes, deck_name="group")
            self.assertEqual(summary["notes"], 1)
            self.assertEqual(summary["media"], 2)
            self.assertEqual(summary["skippedRepeat"], 2)
            self.assertEqual(summary["skippedNoCard"], 2)
            self.assertEqual(summary["skippedOther"], 1)
            self.assertEqual(summary["bytes"], out.stat().st_size)
            self.assertGreater(summary["bytes"], 0)

            package = read_apkg(out)
            self.assertEqual(package["collection_name"], "collection.anki2")
            self.assertTrue(package["collection"].startswith(b"SQLite format 3"))
            self.assertNotIn(b"data:image", package["collection"])
            self.assertNotIn(b"cba-anki-", package["collection"])
            self.assertIsInstance(package["media"], dict)
            self.assertEqual(len(package["media"]), 2)
            self.assertEqual(package["cards"], 1)
            self.assertEqual(len(package["notes"]), 1)
            note = package["notes"][0]
            self.assertEqual(note["guid"], note_guid("group", "甲"))
            self.assertEqual(note["sort"], "甲")
            char, image, variants, sources, count = note["fields"]
            self.assertEqual(char, "甲")
            self.assertEqual(image.count("<img "), 1)
            self.assertEqual(variants.count("<img "), 2)
            self.assertNotIn("<figure>", "\n".join(note["fields"]))
            self.assertNotIn("乙", char + image + variants + sources)
            self.assertNotIn("丙", char + image + variants + sources)
            self.assertIn("plate-a\u00b70", sources)
            self.assertIn("plate-a\u00b71", sources)
            self.assertEqual(count, "2")
            model = package["models"][str(summary["modelId"])]
            self.assertIn("{{Image}}", model["tmpls"][0]["qfmt"])
            self.assertIn("{{Variants}}", model["tmpls"][0]["afmt"])
            self.assertIn("--paper: #f4ecd8", model["css"])
            self.assertEqual([field["name"] for field in model["flds"]], ["Char", "Image", "Variants", "Sources", "Count"])
            srcs = []
            for field in note["fields"]:
                self.assertNotIn("data:image", field)
                self.assertNotIn("base64", field)
                self.assertNotIn("/tmp", field)
                self.assertNotIn("/home", field)
                self.assertNotIn("src=\"/", field)
                srcs.extend(
                    part.split('"', 1)[0]
                    for part in field.split('src="')[1:]
                )
            self.assertEqual(set(srcs), set(package["media"].values()))
            self.assertEqual(len(srcs), 3)
            for key, filename in package["media"].items():
                self.assertTrue(key.isdigit())
                self.assertNotIn("/", filename)
                self.assertNotIn("\\", filename)
                self.assertTrue(filename.endswith(".jpg"))
                blob = package["blobs"][str(key)]
                self.assertTrue(blob.startswith(b"\xff\xd8"))
                with Image.open(io.BytesIO(blob)) as glyph:
                    self.assertLessEqual(max(glyph.size), MAX_SIDE)
                    self.assertEqual(glyph.format, "JPEG")
            self.assertEqual(package["decks"][str(summary["deckId"])]["name"], "group")
            self.assertEqual(int(package["models"][str(summary["modelId"])]["id"]), summary["modelId"])
            numeric = [name for name in package["names"] if name not in {"collection.anki2", "media"}]
            self.assertEqual(sorted(numeric), sorted(package["media"]))

    def test_guid_stays_when_pixels_or_variants_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = [_box(1, 10, 10, 40, 40, "甲", 0)]
            out1, summary1 = self._build(folder, first, deck_name="stable", front="char", color=(1, 2, 3))
            original = read_apkg(out1)
            fields = original["notes"][0]["fields"]
            self.assertEqual(fields[0], "甲")
            self.assertIn("<img ", fields[1])
            self.assertIn("plate-a\u00b70", fields[3])
            char_model = original["models"][str(summary1["modelId"])]
            self.assertIn("{{Char}}", char_model["tmpls"][0]["qfmt"])
            self.assertNotIn("{{Image}}", char_model["tmpls"][0]["qfmt"])
            second = [
                _box(1, 10, 10, 40, 40, "甲", 0),
                _box(2, 10, 80, 40, 40, "甲", 1),
            ]
            out2, summary2 = self._build(folder, second, deck_name="stable", front="image", color=(9, 9, 9))
            package = read_apkg(out2)
            self.assertEqual(package["notes"][0]["guid"], original["notes"][0]["guid"])
            self.assertEqual(summary1["deckId"], summary2["deckId"])
            self.assertEqual(summary1["modelId"], summary2["modelId"])
            self.assertEqual(package["notes"][0]["guid"], note_guid("stable", "甲"))
            self.assertEqual(package["notes"][0]["fields"][1].count("<img "), 1)
            self.assertEqual(package["notes"][0]["fields"][2].count("<img "), 2)
            self.assertIn("{{Image}}", package["models"][str(summary2["modelId"])]["tmpls"][0]["qfmt"])
            self.assertNotEqual(note_guid("stable", "甲"), note_guid("other-deck", "甲"))

    def test_wide_crop_is_resized_and_cjk_plate_names_stay_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plate = folder / "甲卷.png"
            image = Image.new("RGB", (900, 120), (240, 236, 220))
            image.paste(Image.new("RGB", (900, 80), (0, 0, 0)), (0, 20))
            image.save(plate, format="PNG")
            document = {"plates": {"甲卷": [_box(7, 0, 20, 900, 80, "戊", 3, approved=True)]}}
            out = folder / "wide.apkg"
            summary = build_apkg([("甲卷", plate)], document, "wide", out, front="image")
            self.assertEqual(summary["notes"], 1)
            self.assertEqual(summary["media"], 1)
            package = read_apkg(out)
            filename = next(iter(package["media"].values()))
            self.assertRegex(filename, r"^[A-Za-z0-9._-]+\.jpg$")
            self.assertNotIn("甲", filename)
            self.assertIn("甲卷\u00b73", package["notes"][0]["fields"][3])
            blob = next(iter(package["blobs"].values()))
            with Image.open(io.BytesIO(blob)) as glyph:
                self.assertEqual(glyph.size[0], MAX_SIDE)
                self.assertLess(glyph.size[1], MAX_SIDE)


class DemoAnkiTests(EnvGuard):
    def setUp(self) -> None:
        super().setUp()
        os.chdir(ROOT)

    def test_help_lists_anki_options(self) -> None:
        code, out, err = run(["anki", "--help"])
        self.assertEqual(code, 0, err)
        self.assertIn("--deck-name", out)
        self.assertIn("--front", out)
        self.assertIn("--out", out)
        self.assertIn("--data-dir", out)
        self.assertIn("--json", out)
        self.assertIn("--template-dir", out)
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("genanki", text.lower())
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertIn("genanki", requirements)

    def test_default_anki_json_on_the_bundled_demo(self) -> None:
        demo = ROOT / "examples" / "demo"
        before = sorted(path.name for path in demo.iterdir())
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            code, out, err = run(["anki", "--json"])
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["deck"], "calligraphy")
            self.assertEqual(payload["front"], "image")
            self.assertEqual(payload["template"], "default")
            self.assertEqual(payload["out"], "calligraphy.apkg")
            self.assertEqual(payload["notes"], 6)
            self.assertEqual(payload["media"], 6)
            self.assertEqual(payload["skippedRepeat"], 1)
            self.assertEqual(payload["skippedNoCard"], 2)
            self.assertTrue((Path(tmp) / "calligraphy.apkg").is_file())
            self.assertNotIn("data:image", out)
        self.assertEqual(sorted(path.name for path in demo.iterdir()), before)

    def test_demo_json_skips_repeat_and_nocard(self) -> None:
        demo = ROOT / "examples" / "demo"
        before = sorted(path.name for path in demo.iterdir())
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            code, out, err = run(["anki", "--json", "--out", "demo.apkg", "--deck-name", "demo-glyphs"])
            self.assertEqual(code, 0, err)
            self.assertEqual(err, "")
            payload = json.loads(out)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["out"], "demo.apkg")
            self.assertEqual(payload["deck"], "demo-glyphs")
            self.assertEqual(payload["front"], "image")
            self.assertEqual(payload["notes"], 6)
            self.assertEqual(payload["media"], 6)
            self.assertEqual(payload["skippedRepeat"], 1)
            self.assertEqual(payload["skippedNoCard"], 2)
            self.assertEqual(payload["skippedOther"], 0)
            self.assertEqual(payload["missingPlates"], [])
            self.assertEqual(payload["bytes"], (Path(tmp) / "demo.apkg").stat().st_size)
            self.assertNotIn("/tmp", out)
            self.assertNotIn("/home", out)
            self.assertNotIn("/workspace", out)
            self.assertNotIn("data:image", out)
            package = read_apkg(Path(tmp) / "demo.apkg")
            chars = sorted(note["sort"] for note in package["notes"])
            self.assertEqual(chars, ["丁", "丙", "己", "庚", "甲", "辛"])
            joined = "\n".join("".join(note["fields"]) for note in package["notes"])
            for skipped in ("乙", "戊", "子"):
                self.assertNotIn(skipped, joined)
            self.assertIn("plate-01\u00b70", joined)
            self.assertIn("plate-01\u00b72", joined)
            self.assertNotIn("data:image", joined)
            self.assertEqual(len(package["media"]), 6)
            for filename in package["media"].values():
                self.assertRegex(filename, r"^plate-0[12]_\d{4}_\d+\.jpg$")
            filenames = set(package["media"].values())
            self.assertIn("plate-01_0001_0.jpg", filenames)
            self.assertNotIn("plate-01_0002_1.jpg", filenames)
            self.assertNotIn("plate-01_0005_4.jpg", filenames)
            self.assertFalse(any(name.startswith("plate-02_") for name in filenames))
            for blob in package["blobs"].values():
                with Image.open(io.BytesIO(blob)) as glyph:
                    self.assertEqual(glyph.size, (150 + PAD_PX * 2, 148 + PAD_PX * 2))
            self.assertEqual({note["guid"] for note in package["notes"]}, {note_guid("demo-glyphs", ch) for ch in chars})
        self.assertEqual(sorted(path.name for path in demo.iterdir()), before)
        self.assertFalse((demo / "boxes.json").exists())

    def test_all_skipped_boxes_fail_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = folder / "plate-01.png"
            Image.new("RGB", (40, 40), (255, 255, 255)).save(image, format="PNG")
            boxes = folder / "boxes.json"
            boxes.write_text(
                json.dumps(
                    {
                        "plates": {
                            "plate-01": [
                                _box(1, 0, 0, 20, 20, "甲", 0, repeatMark=True),
                                _box(2, 0, 20, 20, 20, "乙", 1, noCard=True, noCardReason="damaged"),
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            dest = folder / "empty.apkg"
            code, out, err = run(
                [
                    "anki",
                    "--json",
                    "--boxes",
                    str(boxes),
                    "--plates-dir",
                    str(folder),
                    "--plate-glob",
                    "*.png",
                    "--out",
                    str(dest),
                ]
            )
            self.assertEqual(code, 1, out)
            payload = json.loads(out)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["skippedRepeat"], 1)
            self.assertEqual(payload["skippedNoCard"], 1)
            self.assertEqual(payload["notes"], 0)
            self.assertFalse(dest.exists())

    def test_front_char_puts_the_character_on_the_front(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            code, out, err = run(["anki", "--json", "--front", "char", "--out", "chars.apkg", "--deck-name", "chars"])
            self.assertEqual(code, 0, err)
            package = read_apkg(Path(tmp) / "chars.apkg")
            jia = next(note for note in package["notes"] if note["sort"] == "甲")
            self.assertEqual(jia["fields"][0], "甲")
            self.assertIn("<img ", jia["fields"][1])
            self.assertIn("plate-01\u00b70", jia["fields"][3])
            model = next(iter(package["models"].values()))
            self.assertIn("{{Char}}", model["tmpls"][0]["qfmt"])
            self.assertNotIn("{{Image}}", model["tmpls"][0]["qfmt"])
            self.assertNotIn("data:image", "".join(jia["fields"]))
            payload = json.loads(out)
            self.assertEqual(payload["front"], "char")
            self.assertEqual(payload["template"], "default")
            self.assertEqual(payload["notes"], 6)


def _write_template(folder: Path, *, front: str, back: str, css: str, spec: dict | None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "front.html").write_text(front, encoding="utf-8")
    (folder / "back.html").write_text(back, encoding="utf-8")
    (folder / "style.css").write_text(css, encoding="utf-8")
    if spec is not None:
        (folder / "template.json").write_text(json.dumps(spec), encoding="utf-8")


class TemplateTests(EnvGuard):
    def test_bundled_template_is_the_paper_card(self) -> None:
        folder = default_template_dir()
        self.assertTrue((folder / "front.html").is_file())
        self.assertTrue((folder / "front-char.html").is_file())
        self.assertTrue((folder / "back.html").is_file())
        self.assertTrue((folder / "style.css").is_file())
        image = load_template(None, "image")
        char = load_template(None, "char")
        self.assertEqual(image.set_id, "default")
        self.assertEqual(image.fields, ("Char", "Image", "Variants", "Sources", "Count"))
        self.assertIn("{{Image}}", image.front_html)
        self.assertIn("{{Char}}", char.front_html)
        self.assertNotIn("{{Image}}", char.front_html)
        self.assertIn("--paper: #f4ecd8", image.css)
        self.assertNotIn("data:image", image.front_html + image.back_html + image.css)
        self.assertNotIn("米芾", image.front_html + image.back_html + char.front_html)

    def test_custom_template_dir_and_unknown_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            custom = folder / "plain"
            _write_template(
                custom,
                front='<div class="only">{{Char}}</div>',
                back="<div>{{Image}}</div>",
                css=".only { color: #123456; }",
                spec={
                    "id": "plain",
                    "name": "Plain glyphs",
                    "fields": ["Char", "Image"],
                    "fronts": {"image": "front.html", "char": "front.html"},
                },
            )
            image = folder / "plate-a.png"
            image.write_bytes(_png((80, 80), [(10, 10, 30, 30, (0, 0, 0))]))
            document = {"plates": {"plate-a": [_box(1, 10, 10, 30, 30, "甲", 0)]}}
            out = folder / "custom.apkg"
            summary = build_apkg(
                [("plate-a", image)],
                document,
                "glyphs",
                out,
                front="image",
                template_dir=custom,
            )
            self.assertEqual(summary["template"], "plain")
            self.assertEqual(summary["modelId"], model_id_for("glyphs", "plain"))
            self.assertNotEqual(summary["modelId"], model_id_for("glyphs", "default"))
            package = read_apkg(out)
            note = package["notes"][0]
            self.assertEqual(note["guid"], note_guid("glyphs", "甲"))
            self.assertEqual(note["fields"], ["甲", note["fields"][1]])
            self.assertIn("<img ", note["fields"][1])
            self.assertNotIn("data:image", "".join(note["fields"]))
            model = package["models"][str(summary["modelId"])]
            self.assertEqual(model["name"], "Plain glyphs")
            self.assertEqual(model["tmpls"][0]["qfmt"], '<div class="only">{{Char}}</div>')
            self.assertIn("#123456", model["css"])
            self.assertEqual([field["name"] for field in model["flds"]], ["Char", "Image"])

            broken = folder / "broken"
            _write_template(
                broken,
                front="{{Char}} {{Nope}}",
                back="{{Image}}",
                css=".card {}",
                spec={"id": "broken", "fields": ["Char", "Image"]},
            )
            with self.assertRaises(DeckError) as raised:
                load_template(broken, "image")
            self.assertIn("Nope", str(raised.exception))
            self.assertIn("front.html", str(raised.exception))
            self.assertEqual(raised.exception.extra.get("code"), "template")

            boxes = folder / "boxes.json"
            boxes.write_text(json.dumps(document), encoding="utf-8")
            code, out_text, _err = run(
                [
                    "anki",
                    "--json",
                    "--deck-name",
                    "glyphs",
                    "--boxes",
                    str(boxes),
                    "--plates-dir",
                    str(folder),
                    "--plate-glob",
                    "*.png",
                    "--template-dir",
                    str(broken),
                    "--out",
                    str(folder / "nope.apkg"),
                ]
            )
            self.assertEqual(code, 1, out_text)
            payload = json.loads(out_text)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["code"], "template")
            self.assertIn("Nope", payload["error"])
            self.assertIn("front.html", payload["error"])
            self.assertFalse((folder / "nope.apkg").exists())
            self.assertNotIn("data:image", out_text)
