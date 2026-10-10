"""Agent CLI: status, validate, export, review resume, and approved columns."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

from boxannotator import clean_box  # noqa: E402
from cba.cli import main  # noqa: E402
from cba.validate import validate_document  # noqa: E402
from tests.test_boxannotator import EnvGuard  # noqa: E402
from tools.apply_column_spec import main as apply_main  # noqa: E402


def run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def as_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _box(box_id: int, y: int, ti: int | None, char: str, *, x: int = 0, h: int = 10) -> dict:
    item = {"id": box_id, "col": 1, "x": x, "y": y, "w": 10, "h": h}
    if ti is not None:
        item["ti"] = ti
    if char:
        item["char"] = char
    return item


class DemoCliTests(EnvGuard):
    def setUp(self) -> None:
        super().setUp()
        os.chdir(ROOT)

    def test_status_and_validate_json_on_demo_are_read_only(self) -> None:
        demo = ROOT / "examples" / "demo"
        before = sorted(path.name for path in demo.iterdir())
        code, out, err = run(["status", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        status = json.loads(out)
        self.assertTrue(status["ok"])
        self.assertEqual(status["origin"], "example")
        self.assertEqual(status["boxes"], "examples/demo/boxes.example.json")
        self.assertFalse(status["reviewExists"])
        self.assertEqual(status["textLength"], 16)
        self.assertEqual(status["counts"]["boxes"], 9)
        self.assertEqual(status["counts"]["columns"], 3)
        self.assertEqual(status["counts"]["unreviewed"], 3)
        self.assertEqual(status["counts"]["unresolved"], 0)
        self.assertEqual(status["counts"]["droppedBoxes"], 0)
        by_plate = {plate["id"]: plate for plate in status["plates"]}
        self.assertEqual([column["boxes"] for column in by_plate["plate-01"]["columns"]], [4, 4])
        self.assertEqual(by_plate["plate-01"]["columns"][0]["chars"], "甲乙丙丁")
        self.assertEqual(by_plate["plate-01"]["columns"][1]["chars"], "戊己庚辛")
        self.assertEqual(by_plate["plate-02"]["columns"][0]["chars"], "子")
        self.assertEqual(
            status["unreviewed"],
            [
                {"plate": "plate-01", "col": 1},
                {"plate": "plate-01", "col": 2},
                {"plate": "plate-02", "col": 1},
            ],
        )

        code, out, err = run(["validate", "--json"])
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertTrue(report["ok"])
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["counts"]["issues"], 0)
        self.assertEqual(report["path"], "examples/demo/boxes.example.json")
        self.assertEqual(sorted(path.name for path in demo.iterdir()), before)
        self.assertFalse((demo / "boxes.json").exists())
        self.assertFalse((demo / "boxes.review.json").exists())

    def test_module_entrypoint_matches_status(self) -> None:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("CBA_"):
                env.pop(key)
        completed = subprocess.run(
            [sys.executable, "-m", "cba", "status", "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["counts"]["unreviewed"], 3)
        self.assertNotIn("password", completed.stdout.lower())


class ValidateTests(unittest.TestCase):
    def test_shared_edge_is_not_an_overlap(self) -> None:
        data = {"plates": {"p": [_box(1, 0, 0, "甲"), _box(2, 10, 1, "乙")]}}
        self.assertEqual(validate_document(data, list("甲乙")), [])

    def test_overlap_empty_char_gap_duplicate_mismatch_and_order(self) -> None:
        overlapping = {"plates": {"p": [_box(1, 0, 0, "甲"), _box(2, 5, 1, "乙")]}}
        codes = {issue["code"] for issue in validate_document(overlapping, list("甲乙"))}
        self.assertEqual(codes, {"overlap"})

        empty = {"plates": {"p": [_box(1, 0, 0, "")]}}
        self.assertEqual(validate_document(empty, list("甲"))[0]["code"], "empty_char")

        gap = {"plates": {"p": [_box(1, 0, 0, "甲"), _box(2, 20, 2, "丙")]}}
        gap_codes = {issue["code"] for issue in validate_document(gap, list("甲乙丙"))}
        self.assertIn("ti_gap", gap_codes)
        self.assertNotIn("char_mismatch", gap_codes)

        mismatch = {"plates": {"p": [_box(1, 0, 1, "甲")]}}
        mismatch_codes = {issue["code"] for issue in validate_document(mismatch, list("甲乙"))}
        self.assertEqual(mismatch_codes, {"char_mismatch"})

        backwards = {
            "plates": {
                "p": [_box(1, 0, 1, "乙"), _box(2, 20, 0, "甲")],
            }
        }
        order_codes = {issue["code"] for issue in validate_document(backwards, list("甲乙"))}
        self.assertIn("ti_order", order_codes)
        self.assertIn("duplicate_ti", {issue["code"] for issue in validate_document(
            {"plates": {"p": [_box(1, 0, 0, "甲"), _box(2, 20, 0, "甲")]}},
            list("甲"),
        )})

    def test_cli_validate_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            boxes = folder / "boxes.json"
            boxes.write_text(
                json.dumps({"plates": {"plate-01": [_box(1, 0, 0, ""), _box(2, 4, 0, "甲")]}}),
                encoding="utf-8",
            )
            code, out, _err = run(
                [
                    "validate",
                    "--json",
                    "--boxes",
                    str(boxes),
                    "--plates-dir",
                    str(ROOT / "examples" / "demo" / "plates"),
                    "--text",
                    str(ROOT / "examples" / "demo" / "transcription.txt"),
                    "--plate-glob",
                    "*.png",
                ]
            )
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        codes = {issue["code"] for issue in payload["issues"]}
        self.assertIn("empty_char", codes)
        self.assertIn("overlap", codes)
        self.assertIn("duplicate_ti", codes)


class _Project(EnvGuard):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.boxes = self.folder / "boxes.json"
        shutil.copy(ROOT / "examples" / "demo" / "boxes.example.json", self.boxes)
        self.common = [
            "--boxes",
            str(self.boxes),
            "--plates-dir",
            str(ROOT / "examples" / "demo" / "plates"),
            "--text",
            str(ROOT / "examples" / "demo" / "transcription.txt"),
            "--plate-glob",
            "*.png",
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()
        super().tearDown()


class ExportReviewTests(_Project):
    def test_export_column_numbers_match_transcription(self) -> None:
        code, out, err = run(
            ["export-column", "--json", "--plate", "plate-01", "--col", "1", "--out", str(self.folder / "out")]
            + self.common
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["transcription"], "甲乙丙丁")
        self.assertEqual([box["n"] for box in payload["boxes"]], [1, 2, 3, 4])
        self.assertEqual(payload["boxes"][1]["flags"], "repeat")
        text = as_path(payload["text"]).read_text(encoding="utf-8")
        self.assertIn("transcription: 甲乙丙丁", text)
        self.assertIn("rules: docs/CURSIVE_RULES.md", text)
        self.assertIn("\t乙\t乙\t", text)
        image = as_path(payload["image"])
        self.assertTrue(image.is_file())
        from PIL import Image

        with Image.open(image) as rendered:
            self.assertGreater(rendered.size[0], 40)
            self.assertGreater(rendered.size[1], 200)
            pixels = rendered.get_flattened_data() if hasattr(rendered, "get_flattened_data") else rendered.getdata()
            red = sum(1 for pixel in pixels if pixel[0] > 150 and pixel[1] < 40 and pixel[2] < 40)
        self.assertGreater(red, 0)

        code, out, err = run(
            ["export-column", "--json", "--plate", "plate-01", "--col", "2", "--out", str(self.folder / "out")]
            + self.common
        )
        self.assertEqual(code, 0, err)
        second = json.loads(out)
        self.assertEqual(second["transcription"], "戊己庚辛")
        self.assertEqual(second["boxes"][0]["flags"], "noCard:blank")

    def test_review_state_resumes_and_keeps_approval(self) -> None:
        sidecar = self.boxes.with_name("boxes.review.json")
        sidecar.write_text(
            json.dumps(
                {
                    "version": 1,
                    "columns": {
                        "plate-01": {
                            "1": {"reviewed": False, "unresolved": False, "approved": True, "note": ""}
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        code, out, err = run(["review", "--json", "--plate", "plate-01", "--col", "2", "--reviewed"] + self.common)
        self.assertEqual(code, 0, err)
        code, out, err = run(
            ["review", "--json", "--plate", "plate-02", "--col", "1", "--unresolved", "--note", "顶边是空白"]
            + self.common
        )
        self.assertEqual(code, 0, err)
        code, out, err = run(["status", "--json"] + self.common)
        status = json.loads(out)
        self.assertEqual(status["unreviewed"], [])
        self.assertEqual(status["unresolved"], [{"plate": "plate-02", "col": 1, "note": "顶边是空白"}])
        self.assertEqual(status["approved"], [{"plate": "plate-01", "col": 1}])
        code, out, err = run(["review", "--json", "--plate", "plate-01", "--col", "1", "--clear"] + self.common)
        self.assertEqual(code, 0, err)
        entry = json.loads(out)["entry"]
        self.assertTrue(entry["approved"])
        self.assertFalse(entry["reviewed"])
        code, out, err = run(["status", "--json"] + self.common)
        status = json.loads(out)
        self.assertEqual(status["approved"], [{"plate": "plate-01", "col": 1}])
        self.assertNotIn({"plate": "plate-01", "col": 1}, status["unreviewed"])
        code, out, err = run(["review", "--json", "--plate", "plate-01", "--col", "2", "--unresolved"] + self.common)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_unknown_column_is_json_on_stdout(self) -> None:
        code, out, err = run(["export-column", "--json", "--plate", "plate-01", "--col", "9", "--out", str(self.folder)] + self.common)
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        self.assertFalse(json.loads(out)["ok"])


class ApplyAndCutTests(_Project):
    def _spec(self, name: str, body: dict) -> Path:
        path = self.folder / name
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_apply_refuses_approved_box_and_sidecar(self) -> None:
        document = json.loads(self.boxes.read_text(encoding="utf-8"))
        document["plates"]["plate-01"][0]["approved"] = True
        self.boxes.write_text(json.dumps(document), encoding="utf-8")
        original = self.boxes.read_bytes()
        spec = self._spec(
            "col1.json",
            {
                "plates": {
                    "plate-01": {
                        "1": {"t0": 0, "x": 10, "w": 20, "spans": [[1, 20], [21, 40], [41, 60], [61, 80]]}
                    }
                }
            },
        )
        code, out, _err = run(["apply-spec", "--json", "--spec", str(spec), "--label", "nope"] + self.common)
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["code"], "approved")
        self.assertEqual(payload["columns"], [{"plate": "plate-01", "col": 1}])
        self.assertEqual(self.boxes.read_bytes(), original)
        self.assertEqual(list(self.folder.glob("*.pre_*")), [])

        document["plates"]["plate-01"][0].pop("approved")
        self.boxes.write_text(json.dumps(document), encoding="utf-8")
        original = self.boxes.read_bytes()
        sidecar = self.boxes.with_name("boxes.review.json")
        sidecar.write_text(
            json.dumps({"version": 1, "columns": {"plate-01": {"2": {"approved": True}}}}),
            encoding="utf-8",
        )
        spec = self._spec(
            "col2.json",
            {"plates": {"plate-01": {"2": {"t0": 4, "x": 1, "w": 2, "spans": [[1, 10]]}}}},
        )
        code, out, err = run(["apply-spec", "--json", "--spec", str(spec)] + self.common)
        self.assertEqual(code, 1, err)
        self.assertEqual(self.boxes.read_bytes(), original)
        kept = clean_box({"id": 1, "x": 0, "y": 0, "w": 1, "h": 1, "char": "甲", "approved": True})
        self.assertTrue(kept["approved"])

    def test_apply_writes_unapproved_column_and_backup(self) -> None:
        spec = self._spec(
            "move.json",
            {
                "plates": {
                    "plate-01": {
                        "2": {
                            "t0": 4,
                            "x": 90,
                            "w": 40,
                            "spans": [[10, 30], [40, 60], [70, 90], [100, 120]],
                            "noCard": {"4": "blank"},
                        }
                    }
                }
            },
        )
        before = json.loads(self.boxes.read_text(encoding="utf-8"))
        code, out, err = run(["apply-spec", "--json", "--spec", str(spec), "--label", "fix-1", "--dry-run"] + self.common)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.boxes.read_text(encoding="utf-8")), before)
        code, out, err = run(["apply-spec", "--json", "--spec", str(spec), "--label", "fix-1"] + self.common)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["applied"], [{"plate": "plate-01", "col": 2, "boxes": 4}])
        self.assertTrue(as_path(payload["backup"]).is_file())
        updated = json.loads(self.boxes.read_text(encoding="utf-8"))
        column1 = [box for box in updated["plates"]["plate-01"] if box["col"] == 1]
        column2 = [box for box in updated["plates"]["plate-01"] if box["col"] == 2]
        self.assertEqual([box["id"] for box in column1], [1, 2, 3, 4])
        self.assertEqual([box["char"] for box in column2], ["戊", "己", "庚", "辛"])
        self.assertTrue(column2[0]["noCard"])
        self.assertEqual(column2[0]["y"], 10)
        self.assertEqual(updated["labelMode"], "fixed")

    def test_raw_tool_refuses_approved_and_still_applies(self) -> None:
        document = json.loads(self.boxes.read_text(encoding="utf-8"))
        document["plates"]["plate-01"][0]["approved"] = True
        self.boxes.write_text(json.dumps(document), encoding="utf-8")
        spec = self._spec(
            "raw.json",
            {"plates": {"plate-01": {"1": {"t0": 0, "spans": [[1, 10], [11, 20], [21, 30], [31, 40]]}}}},
        )
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = apply_main(["--spec", str(spec), "--label", "raw"] + self.common)
        self.assertEqual(code, 1)
        self.assertIn("approved", err.getvalue())
        self.assertTrue(json.loads(self.boxes.read_text(encoding="utf-8"))["plates"]["plate-01"][0]["approved"])

        document["plates"]["plate-01"][0].pop("approved")
        self.boxes.write_text(json.dumps(document), encoding="utf-8")
        spec = self._spec(
            "raw-ok.json",
            {"plates": {"plate-01": {"2": {"t0": 4, "x": 12, "w": 12, "spans": [[1, 11], [12, 22], [23, 33], [34, 44]]}}}},
        )
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = apply_main(["--spec", str(spec), "--label", "raw-ok"] + self.common)
        self.assertEqual(code, 0, err.getvalue())
        updated = json.loads(self.boxes.read_text(encoding="utf-8"))
        column2 = [box for box in updated["plates"]["plate-01"] if box["col"] == 2]
        self.assertEqual(column2[0]["x"], 12)

    def test_cut_is_a_proposal(self) -> None:
        code, out, err = run(["cut", "--json", "--plate", "plate-01", "--per-column", "4"] + self.common)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["boxCount"], 8)
        self.assertEqual(payload["columnCount"], 2)
        self.assertFalse(payload["wrote"])
        self.assertEqual([column["boxes"] for column in payload["columns"]], [4, 4])
        self.assertEqual(len(payload["columns"][0]["spans"][0]), 4)

    def test_contact_sheet_json(self) -> None:
        code, out, err = run(
            [
                "contact-sheet",
                "--json",
                "--out",
                str(self.folder / "sheets"),
                "--lowconf",
                str(ROOT / "examples" / "demo" / "lowconf.example.json"),
            ]
            + self.common
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(len(payload["sheets"]), 2)
        for sheet in payload["sheets"]:
            self.assertTrue(as_path(sheet).is_file())


if __name__ == "__main__":
    unittest.main()
