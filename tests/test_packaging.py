"""Installable console script, data directory, and package write guard."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

from boxannotator import demo_dir, load_settings, save_boxes, source_checkout, web_dir  # noqa: E402
from cba.cli import main  # noqa: E402
from serve import ensure_boxes_file  # noqa: E402
from tests.test_boxannotator import EnvGuard, ns  # noqa: E402
from tests.test_cli import run  # noqa: E402


class EntryPointTests(EnvGuard):
    def test_pyproject_console_script_is_cba(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "calligraphy-box-annotator"', text)
        self.assertIn('requires-python = ">=3.9"', text)
        self.assertIn('license = "MIT"', text)
        self.assertIn('cba = "cba.cli:main"', text)
        self.assertIn("pillow", text.lower())
        self.assertNotIn("numpy", text.lower())
        self.assertTrue(callable(main))

    def test_help_lists_serve_and_module_entry_still_works(self) -> None:
        code, out, err = run(["--help"])
        self.assertEqual(code, 0, err)
        self.assertIn("serve", out)
        self.assertIn("status", out)
        self.assertIn("validate", out)

        code, out, err = run(["serve", "--help"])
        self.assertEqual(code, 0, err)
        self.assertIn("--data-dir", out)
        self.assertIn("--port", out)

        env = os.environ.copy()
        for key in list(env):
            if key.startswith("CBA_"):
                env.pop(key)
        completed = subprocess.run(
            [sys.executable, "-m", "cba", "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("serve", completed.stdout)
        self.assertIn("status", completed.stdout)

    def test_serve_forwards_data_dir_and_port(self) -> None:
        with patch("serve.main") as serve_main:
            code = main(["serve", "--data-dir", "/tmp/cba-data", "--port", "9876", "--host", "127.0.0.1"])
        self.assertEqual(code, 0)
        serve_main.assert_called_once_with(
            ["--data-dir", "/tmp/cba-data", "--host", "127.0.0.1", "--port", "9876"]
        )


class DataDirTests(EnvGuard):
    def test_checkout_still_exposes_demo_and_web(self) -> None:
        self.assertTrue(source_checkout())
        self.assertEqual(demo_dir(), ROOT / "examples" / "demo")
        self.assertEqual(web_dir(), ROOT / "web")
        self.assertTrue((web_dir() / "index.html").is_file())
        self.assertTrue((demo_dir() / "boxes.example.json").is_file())

    def test_data_dir_reads_bundled_demo_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            settings = load_settings(ns(data_dir=str(folder)))
            self.assertEqual(settings.boxes_path, (folder / "boxes.json").resolve())
            self.assertEqual(settings.crop_out, (folder / "cropped").resolve())
            self.assertEqual(settings.plates_dir, (ROOT / "examples" / "demo" / "plates").resolve())
            self.assertEqual(settings.text_path, (ROOT / "examples" / "demo" / "transcription.txt").resolve())
            self.assertEqual(settings.plate_glob, "*.png")

            code, out, err = run(["status", "--data-dir", str(folder), "--json"])
            self.assertEqual(code, 0, err)
            status = json.loads(out)
            self.assertTrue(status["ok"])
            self.assertEqual(status["origin"], "example")
            self.assertEqual(status["counts"]["unreviewed"], 3)
            self.assertEqual(status["counts"]["boxes"], 9)
            self.assertFalse((folder / "boxes.json").exists())
            self.assertFalse((ROOT / "examples" / "demo" / "boxes.json").exists())

            code, out, err = run(["validate", "--data-dir", str(folder), "--json"])
            self.assertEqual(code, 0, err)
            report = json.loads(out)
            self.assertTrue(report["ok"])
            self.assertEqual(report["issues"], [])
            self.assertFalse((folder / "boxes.json").exists())

    def test_env_data_dir_overrides_checkout_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            os.environ["CBA_DATA_DIR"] = str(folder)
            settings = load_settings(ns())
            self.assertEqual(settings.boxes_path, (folder / "boxes.json").resolve())
            self.assertNotEqual(settings.config_path, ROOT / "config.example.json")

    def test_serve_seed_writes_boxes_into_the_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            settings = load_settings(ns(data_dir=str(folder)))
            ensure_boxes_file(settings)
            written = folder / "boxes.json"
            self.assertTrue(written.is_file())
            data = json.loads(written.read_text(encoding="utf-8"))
            self.assertIn("甲", json.dumps(data, ensure_ascii=False))
            self.assertIn("plate-01", data["plates"])
            self.assertFalse((ROOT / "examples" / "demo" / "boxes.json").exists())

    def test_installed_mode_refuses_writes_inside_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch("boxannotator.source_checkout", return_value=False):
                with self.assertRaises(SystemExit):
                    with contextlib.redirect_stderr(io.StringIO()):
                        save_boxes(ROOT / "cba" / "do-not-write.json", {"plates": {}})
                save_boxes(folder / "boxes.json", {"plates": {}})
            self.assertTrue((folder / "boxes.json").is_file())
            self.assertFalse((ROOT / "cba" / "do-not-write.json").exists())
