#!/usr/bin/env python3
"""Local web UI for boxing characters on calligraphy plate images."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from boxannotator import (
    add_common_arguments,
    demo_dir,
    discover_plates,
    empty_boxes,
    load_boxes,
    load_settings,
    load_text_chars,
    normalize_boxes,
    save_boxes,
    web_dir,
)

WEB_ROOT = web_dir()
STATIC_FILES = {"index.html", "app.js", "style.css"}
MAX_BODY = 20 * 1024 * 1024
_SAVE_LOCK = threading.Lock()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def build_handler(settings):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            print(f"[serve] {self.address_string()} - {fmt % args}")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: object) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

        def _plate_ids(self) -> list[str]:
            return [plate_id for plate_id, _ in discover_plates(settings.plates_dir, settings.plate_glob)]

        def _plate_map(self) -> dict[str, Path]:
            return dict(discover_plates(settings.plates_dir, settings.plate_glob))

        def _request_path(self) -> str:
            return unquote(self.path.split("?", 1)[0])

        def do_GET(self) -> None:  # noqa: N802
            path = self._request_path()
            if path == "/api/plates":
                self._json(200, {"plates": self._plate_ids()})
                return
            if path == "/api/boxes":
                self._json(200, load_boxes(settings.boxes_path, self._plate_ids(), settings.source))
                return
            if path == "/api/text":
                chars = load_text_chars(settings.text_path)
                self._json(200, {"chars": chars, "length": len(chars)})
                return
            if path.startswith("/api/plate/"):
                plate_id = path[len("/api/plate/") :]
                image = self._plate_map().get(plate_id)
                if image is None or not image.is_file():
                    self._json(404, {"error": "unknown plate"})
                    return
                content_type = CONTENT_TYPES.get(image.suffix.lower(), "application/octet-stream")
                self._send(200, image.read_bytes(), content_type)
                return
            if path.startswith("/api/"):
                self._json(404, {"error": "not found"})
                return
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            if rel not in STATIC_FILES:
                self._json(404, {"error": "not found"})
                return
            target = (WEB_ROOT / rel).resolve()
            try:
                target.relative_to(WEB_ROOT.resolve())
            except ValueError:
                self._json(404, {"error": "not found"})
                return
            if not target.is_file():
                self._json(404, {"error": "not found"})
                return
            content_type = CONTENT_TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), content_type)

        def do_POST(self) -> None:  # noqa: N802
            path = self._request_path()
            if path != "/api/boxes":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._json(400, {"error": "invalid content length"})
                return
            if length < 0 or length > MAX_BODY:
                self._json(413, {"error": "body too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": f"invalid json: {exc}"})
                return
            if not isinstance(data, dict) or "plates" not in data:
                self._json(400, {"error": "expected {source, textOffsetByPlate, plates}"})
                return
            plate_ids = self._plate_ids()
            clean = normalize_boxes(data, plate_ids, settings.source)
            clean["source"] = settings.source
            with _SAVE_LOCK:
                save_boxes(settings.boxes_path, clean)
            self._json(
                200,
                {
                    "ok": True,
                    "path": str(settings.boxes_path),
                    "counts": {plate_id: len(boxes) for plate_id, boxes in clean["plates"].items()},
                },
            )

    return Handler


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _example_box_files(settings) -> list[Path]:
    sibling = settings.boxes_path.with_name("boxes.example.json")
    bundled = demo_dir() / "boxes.example.json"
    found: list[Path] = []
    for path in (sibling, bundled):
        if path.is_file() and path not in found:
            found.append(path)
    return found


def ensure_boxes_file(settings) -> None:
    if settings.boxes_path.exists():
        return
    plate_ids = [plate_id for plate_id, _ in discover_plates(settings.plates_dir, settings.plate_glob)]
    for example in _example_box_files(settings):
        try:
            raw = json.loads(example.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: cannot read {example}: {exc}")
            continue
        save_boxes(settings.boxes_path, normalize_boxes(raw, plate_ids, settings.source))
        print(f"seeded boxes from {example.name}")
        return
    save_boxes(settings.boxes_path, empty_boxes(plate_ids, settings.source))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the calligraphy glyph boxing UI and boxes.json API.",
    )
    add_common_arguments(parser, crop=False)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    settings = load_settings(parse_args(argv))
    plates = discover_plates(settings.plates_dir, settings.plate_glob)
    chars = load_text_chars(settings.text_path)
    if not settings.plates_dir.is_dir():
        print(f"warning: plates directory does not exist: {settings.plates_dir}")
    ensure_boxes_file(settings)
    handler = build_handler(settings)
    try:
        httpd = Server((settings.host, settings.port), handler)
    except OSError as exc:
        print(f"cannot bind {settings.host}:{settings.port}: {exc}")
        raise SystemExit(1) from exc
    print(f"Calligraphy box annotator: http://{settings.host}:{settings.port}/")
    print(f"plates: {settings.plates_dir} ({len(plates)})")
    print(f"boxes:  {settings.boxes_path}")
    print(f"text:   {settings.text_path} ({len(chars)} CJK chars)")
    if settings.config_path:
        print(f"config: {settings.config_path}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
