"""Tests for config, plate discovery, boxes, crop, and the local API."""

from __future__ import annotations

import argparse
import json
import os
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

import boxannotator  # noqa: E402
from boxannotator import (  # noqa: E402
    clean_box,
    crop_plates,
    discover_plates,
    load_settings,
    load_text_chars,
    normalize_boxes,
    save_boxes,
)
from serve import Server, build_handler  # noqa: E402

CBA_ENV = (
    "CBA_CONFIG",
    "CBA_DATA_DIR",
    "CBA_HOST",
    "CBA_PORT",
    "CBA_PLATES_DIR",
    "CBA_BOXES",
    "CBA_TEXT",
    "CBA_PLATE_GLOB",
    "CBA_SOURCE",
    "CBA_CROP_OUT",
)


def ns(**kwargs) -> argparse.Namespace:
    base = {
        "config": None,
        "plates_dir": None,
        "boxes": None,
        "text": None,
        "plate_glob": None,
        "host": None,
        "port": None,
        "crop_out": None,
        "source": None,
        "data_dir": None,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


class EnvGuard(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {key: os.environ.get(key) for key in CBA_ENV}
        for key in CBA_ENV:
            os.environ.pop(key, None)
        self._cwd = Path.cwd()

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class DemoDataTests(EnvGuard):
    def test_example_config_points_at_synthetic_plates(self) -> None:
        settings = load_settings(ns())
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8765)
        self.assertEqual(settings.plates_dir, (ROOT / "examples/demo/plates").resolve())
        self.assertEqual(settings.text_path, (ROOT / "examples/demo/transcription.txt").resolve())
        self.assertEqual(settings.plate_glob, "*.png")
        ids = [plate_id for plate_id, _ in discover_plates(settings.plates_dir, settings.plate_glob)]
        self.assertEqual(ids, ["plate-01", "plate-02"])

    def test_transcription_is_sixteen_cjk_chars(self) -> None:
        chars = load_text_chars(ROOT / "examples/demo/transcription.txt")
        self.assertEqual("".join(chars), "甲乙丙丁戊己庚辛子丑寅卯辰巳午未")

    def test_demo_plates_are_small_synthetic_pngs(self) -> None:
        from PIL import Image

        for name in ("plate-01.png", "plate-02.png"):
            path = ROOT / "examples/demo/plates" / name
            with Image.open(path) as image:
                self.assertEqual(image.size, (560, 760))
                self.assertEqual(image.format, "PNG")
                dark, light = image.convert("L").getextrema()
            self.assertLess(dark, 80)
            self.assertGreater(light, 200)
            self.assertLess(path.stat().st_size, 400_000)


class TextAndBoxTests(unittest.TestCase):
    def test_comments_and_punctuation_are_skipped(self) -> None:
        tmp = ROOT / "tests" / "_text_tmp.txt"
        try:
            tmp.write_text("# 注释甲\n永，和。\nABC\n", encoding="utf-8")
            self.assertEqual(load_text_chars(tmp), ["永", "和"])
        finally:
            tmp.unlink(missing_ok=True)

    def test_clean_box_keeps_one_cjk_char(self) -> None:
        self.assertIsNone(clean_box({"id": True, "x": 0, "y": 0, "w": 1, "h": 1}))
        kept = clean_box({"id": 2, "x": 1.0, "y": 2, "w": "3", "h": 4, "char": "永"})
        self.assertEqual(kept, {"id": 2, "x": 1, "y": 2, "w": 3, "h": 4, "char": "永"})
        dropped = clean_box({"id": 2, "x": 0, "y": 0, "w": 10, "h": 10, "char": "AB"})
        self.assertNotIn("char", dropped)

    def test_normalize_keeps_known_plates_only(self) -> None:
        raw = {
            "source": "manual",
            "textOffsetByPlate": {"b": "3", "missing": 9},
            "plates": {
                "b": [{"id": 1, "x": 0, "y": 0, "w": 5, "h": 5, "char": "乙"}],
                "missing": [{"id": 1, "x": 0, "y": 0, "w": 5, "h": 5}],
            },
        }
        clean = normalize_boxes(raw, ["a", "b"], "manual")
        self.assertEqual(list(clean["plates"]), ["a", "b"])
        self.assertEqual(clean["textOffsetByPlate"], {"a": 0, "b": 3})
        self.assertEqual(clean["plates"]["b"][0]["char"], "乙")


class DiscoverTests(unittest.TestCase):
    def test_natural_order_suffix_and_glob(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ("plate-10.png", "plate-2.jpg", "plate-1.jpeg", "notes.txt", "skip.webp"):
                (folder / name).write_bytes(b"x")
            (folder / "plate-2.png").write_bytes(b"png")
            ids = [plate_id for plate_id, path in discover_plates(folder, None)]
            self.assertEqual(ids, ["plate-1", "plate-2", "plate-10"])
            by_id = dict(discover_plates(folder, None))
            self.assertEqual(by_id["plate-2"].suffix, ".jpg")
            self.assertEqual([plate_id for plate_id, _ in discover_plates(folder, "*.png")], ["plate-2", "plate-10"])

    def test_glob_cannot_escape_the_directory(self) -> None:
        with self.assertRaises(SystemExit):
            discover_plates(ROOT, "../*.py")


class ConfigTests(EnvGuard):
    def test_cli_overrides_env_overrides_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config = folder / "config.json"
            config.write_text(json.dumps({"port": 1111, "host": "127.0.0.1"}), encoding="utf-8")
            from_file = load_settings(ns(config=str(config)))
            self.assertEqual(from_file.port, 1111)

            os.environ["CBA_PORT"] = "2222"
            from_env = load_settings(ns(config=str(config)))
            self.assertEqual(from_env.port, 2222)

            from_cli = load_settings(ns(config=str(config), port=3333))
            self.assertEqual(from_cli.port, 3333)

    def test_config_relative_paths_follow_the_config_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "project"
            plates = folder / "plates"
            plates.mkdir(parents=True)
            (plates / "a.png").write_bytes(b"x")
            (folder / "config.json").write_text(
                json.dumps(
                    {
                        "plates_dir": "plates",
                        "boxes": "boxes.json",
                        "text": "text.txt",
                        "crop_out": "out",
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(Path(tmp))
            settings = load_settings(ns(config=str(folder / "config.json")))
            self.assertEqual(settings.plates_dir, plates.resolve())
            self.assertEqual(settings.boxes_path, (folder / "boxes.json").resolve())
            self.assertEqual([plate_id for plate_id, _ in discover_plates(settings.plates_dir, None)], ["a"])

    def test_defaults_ignore_the_working_directory(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            settings = load_settings(ns())
        self.assertEqual(settings.plates_dir, (ROOT / "examples/demo/plates").resolve())


class CropTests(EnvGuard):
    def test_crop_writes_plate_index_and_char(self) -> None:
        import tempfile

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plates = folder / "plates"
            plates.mkdir()
            image = Image.new("RGB", (40, 30), "white")
            for x in range(5, 15):
                for y in range(4, 14):
                    image.putpixel((x, y), (10, 20, 30))
            image.save(plates / "plate-01.png")
            boxes = {
                "source": "manual",
                "textOffsetByPlate": {"plate-01": 0},
                "plates": {
                    "plate-01": [
                        {"id": 7, "x": 5, "y": 4, "w": 10, "h": 10, "char": "甲"},
                        {"id": 8, "x": 100, "y": 100, "w": 5, "h": 5},
                        {"id": 9, "x": 0, "y": 0, "w": 4, "h": 3, "char": "乙"},
                    ]
                },
            }
            boxes_path = folder / "boxes.json"
            save_boxes(boxes_path, boxes)
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
            self.assertEqual(names, ["plate-01_0007_甲.png", "plate-01_0009_乙.png"])
            self.assertEqual(report.skipped, 1)
            with Image.open(folder / "cropped" / "plate-01_0007_甲.png") as glyph:
                self.assertEqual(glyph.size, (10, 10))
                self.assertEqual(glyph.getpixel((0, 0)), (10, 20, 30))
            with Image.open(folder / "cropped" / "plate-01_0009_乙.png") as glyph:
                self.assertEqual(glyph.size, (4, 3))


class ServerTests(EnvGuard):
    def test_api_round_trip(self) -> None:
        import tempfile

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plates = folder / "plates"
            plates.mkdir()
            Image.new("RGB", (12, 8), (240, 230, 210)).save(plates / "图一.png")
            (folder / "text.txt").write_text("# 跳过\n甲乙\n", encoding="utf-8")
            config = {
                "host": "127.0.0.1",
                "port": 9,
                "plates_dir": "plates",
                "boxes": "boxes.json",
                "text": "text.txt",
                "source": "demo",
                "crop_out": "cropped",
            }
            (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
            settings = load_settings(ns(config=str(folder / "config.json")))
            httpd = Server(("127.0.0.1", 0), build_handler(settings))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            port = httpd.server_address[1]
            base = f"http://127.0.0.1:{port}"
            try:
                with urllib.request.urlopen(base + "/api/plates") as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["plates"], ["图一"])

                with urllib.request.urlopen(base + "/api/text") as response:
                    text = json.loads(response.read().decode("utf-8"))
                self.assertEqual(text["chars"], ["甲", "乙"])

                with urllib.request.urlopen(base + "/api/plate/" + urllib.parse.quote("图一")) as response:
                    body = response.read()
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertEqual(body, (plates / "图一.png").read_bytes())

                with urllib.request.urlopen(base + "/") as response:
                    html = response.read().decode("utf-8")
                self.assertIn("字形框选", html)

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + "/config.example.json")
                self.assertEqual(caught.exception.code, 404)

                posted = {
                    "source": "ignored-by-server",
                    "textOffsetByPlate": {"图一": 1, "other": 4},
                    "plates": {
                        "图一": [
                            {"id": 1, "x": 0, "y": 0, "w": 6, "h": 4, "char": "乙"},
                            {"id": 2, "x": 0, "y": 0, "w": 2, "h": 2, "char": "no"},
                        ],
                        "other": [{"id": 9, "x": 0, "y": 0, "w": 1, "h": 1, "char": "甲"}],
                    },
                }
                request = urllib.request.Request(
                    base + "/api/boxes",
                    data=json.dumps(posted).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                self.assertTrue(saved["ok"])
                self.assertEqual(saved["counts"], {"图一": 2})
                on_disk = json.loads((folder / "boxes.json").read_text(encoding="utf-8"))
                self.assertEqual(on_disk["source"], "demo")
                self.assertEqual(on_disk["textOffsetByPlate"], {"图一": 1})
                self.assertEqual(list(on_disk["plates"]), ["图一"])
                self.assertNotIn("char", on_disk["plates"]["图一"][1])
                self.assertEqual(on_disk["plates"]["图一"][0]["char"], "乙")
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
