"""Config, plate discovery, boxes.json, and glyph cropping.

Paths in a config file are resolved relative to that file. Relative CLI flags
and environment variables are resolved relative to the current working directory.
Built-in defaults are resolved relative to the repository root.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
PATH_KEYS = ("plates_dir", "boxes", "text", "crop_out")

DEFAULTS: dict[str, object] = {
    "host": "127.0.0.1",
    "port": 8765,
    "plates_dir": "examples/demo/plates",
    "boxes": "examples/demo/boxes.json",
    "text": "examples/demo/transcription.txt",
    "plate_glob": "",
    "source": "manual",
    "crop_out": "cropped",
    "cjk_font": "",
}

ENV_KEYS = {
    "host": "CBA_HOST",
    "port": "CBA_PORT",
    "plates_dir": "CBA_PLATES_DIR",
    "boxes": "CBA_BOXES",
    "text": "CBA_TEXT",
    "plate_glob": "CBA_PLATE_GLOB",
    "source": "CBA_SOURCE",
    "crop_out": "CBA_CROP_OUT",
    "cjk_font": "CBA_FONT",
}

NO_CARD_REASONS = ("repair", "blank", "damaged")
REPEAT_NOTE = (
    "repeatMark boxes keep their label but must not be used as an alternate writing"
)
NOCARD_NOTE = (
    "noCard boxes keep their box and label but are not carded; "
    "noCardReason is repair, blank, or damaged"
)

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    plates_dir: Path
    boxes_path: Path
    text_path: Path
    plate_glob: str | None
    source: str
    crop_out: Path
    cjk_font: Path | None
    config_path: Path | None


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def read_config_file(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        die(f"config not found: {path}")
    except OSError as exc:
        die(f"cannot read config {path}: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"invalid config JSON {path}: {exc}")
    if not isinstance(data, dict):
        die(f"config must be a JSON object: {path}")
    return data


def _resolve_path(value: object, base: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        die(f"{label} must be a non-empty path")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _as_port(value: object) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        die(f"port must be an integer, got {value!r}")
    if not 1 <= port <= 65535:
        die(f"port out of range: {port}")
    return port


def _as_source(value: object) -> str:
    if not isinstance(value, str):
        return str(DEFAULTS["source"])
    text = value.strip()
    if not text or len(text) > 80 or any(c in text for c in "\r\n\x00"):
        return str(DEFAULTS["source"])
    return text


def _pick(key: str, data: dict, args: argparse.Namespace | None, config_base: Path):
    cli_value = getattr(args, key, None) if args is not None else None
    if cli_value is not None and cli_value != "":
        return cli_value, Path.cwd().resolve()
    env_value = os.environ.get(ENV_KEYS[key])
    if env_value not in (None, ""):
        return env_value, Path.cwd().resolve()
    if key in data and data[key] not in (None, ""):
        return data[key], config_base
    return DEFAULTS[key], ROOT


def load_settings(args: argparse.Namespace | None = None) -> Settings:
    """CLI flags override environment variables, which override the config file."""
    config_arg = getattr(args, "config", None) if args is not None else None
    env_config = os.environ.get("CBA_CONFIG")
    config_path: Path | None = None
    data: dict = {}
    config_base = ROOT

    chosen = config_arg or env_config
    if chosen:
        config_path = _resolve_path(chosen, Path.cwd(), "config")
        data = read_config_file(config_path)
        config_base = config_path.parent
    else:
        local = ROOT / "config.json"
        example = ROOT / "config.example.json"
        if local.is_file():
            config_path = local
            data = read_config_file(local)
            config_base = local.parent
        elif example.is_file():
            config_path = example
            data = read_config_file(example)
            config_base = example.parent

    values: dict[str, object] = {}
    bases: dict[str, Path] = {}
    for key in DEFAULTS:
        values[key], bases[key] = _pick(key, data, args, config_base)

    glob = values["plate_glob"]
    plate_glob = glob.strip() if isinstance(glob, str) and glob.strip() else None
    host = values["host"]
    if not isinstance(host, str) or not host.strip():
        die("host must be a non-empty string")

    font_value = values["cjk_font"]
    cjk_font = None
    if isinstance(font_value, str) and font_value.strip():
        cjk_font = _resolve_path(font_value, bases["cjk_font"], "cjk_font")

    return Settings(
        host=host.strip(),
        port=_as_port(values["port"]),
        plates_dir=_resolve_path(values["plates_dir"], bases["plates_dir"], "plates_dir"),
        boxes_path=_resolve_path(values["boxes"], bases["boxes"], "boxes"),
        text_path=_resolve_path(values["text"], bases["text"], "text"),
        plate_glob=plate_glob,
        source=_as_source(values["source"]),
        crop_out=_resolve_path(values["crop_out"], bases["crop_out"], "crop_out"),
        cjk_font=cjk_font,
        config_path=config_path,
    )


def find_cjk_font(explicit: Path | None = None) -> str:
    """First existing CJK font: explicit path, CBA_FONT, CBA_DEMO_FONT, then common system paths."""
    candidates: list[str] = []
    if explicit is not None:
        candidates.append(str(explicit))
    for key in ("CBA_FONT", "CBA_DEMO_FONT"):
        env = os.environ.get(key)
        if env:
            candidates.append(env)
    candidates.extend(FONT_CANDIDATES)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    die("No CJK font found. Set cjk_font in the config, or CBA_FONT to a font file.")


def add_common_arguments(parser: argparse.ArgumentParser, *, crop: bool = False) -> None:
    parser.add_argument(
        "--config",
        help="config JSON (default: ./config.json if it exists, otherwise config.example.json)",
    )
    parser.add_argument("--plates-dir", dest="plates_dir", help="directory of plate images")
    parser.add_argument("--boxes", help="path to boxes.json")
    parser.add_argument(
        "--plate-glob",
        dest="plate_glob",
        help="glob under the plates directory, for example '*.jpg' (default: all JPG/PNG)",
    )
    if crop:
        parser.add_argument("--out", dest="crop_out", help="directory for cropped glyph PNGs")
    else:
        parser.add_argument("--text", help="plain-text transcription used for CJK labels")
        parser.add_argument("--host", help="bind address (default 127.0.0.1)")
        parser.add_argument("--port", type=int, help="bind port (default 8765)")


def natural_key(text: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def safe_plate_id(stem: str) -> bool:
    if not stem or stem in {".", ".."} or stem.startswith("."):
        return False
    return not any(ch in stem for ch in "/\\\x00")


def discover_plates(plates_dir: Path, plate_glob: str | None = None) -> list[tuple[str, Path]]:
    """Return (plate id, image path) pairs sorted naturally by id.

    The plate id is the filename stem. Duplicate stems keep the first file in
    suffix order (.jpeg, .jpg, .png). Paths that escape plates_dir are ignored.
    """
    root = plates_dir.resolve()
    if not root.is_dir():
        return []
    if plate_glob:
        pattern = Path(plate_glob)
        if pattern.is_absolute() or ".." in pattern.parts:
            die("plate_glob must be a relative pattern inside the plates directory")
        candidates = [
            path
            for path in root.glob(plate_glob)
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
    else:
        candidates = [
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
    candidates.sort(key=lambda path: (natural_key(path.stem), path.suffix.lower(), path.name))
    found: dict[str, Path] = {}
    for path in candidates:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        plate_id = path.stem
        if not safe_plate_id(plate_id) or plate_id in found:
            continue
        found[plate_id] = resolved
    return sorted(found.items(), key=lambda item: natural_key(item[0]))


def load_text_chars(path: Path) -> list[str]:
    """CJK Unified Ideographs from a transcription file.

    Lines whose first non-whitespace character is '#' are comments and are
    skipped, so a transcription can carry notes without becoming labels.
    """
    if not path.is_file():
        return []
    chars: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith("#"):
            continue
        chars.extend(CJK_RE.findall(line))
    return chars


def _json_number(value: float) -> int | float:
    if value.is_integer():
        return int(value)
    return value


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"not a number: {value!r}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"not a finite number: {value!r}")
    return number


def clean_box(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    try:
        box_id = raw["id"]
        if isinstance(box_id, bool):
            return None
        if isinstance(box_id, float) and not box_id.is_integer():
            return None
        item: dict = {
            "id": int(box_id),
            "x": _json_number(_as_float(raw["x"])),
            "y": _json_number(_as_float(raw["y"])),
            "w": _json_number(_as_float(raw["w"])),
            "h": _json_number(_as_float(raw["h"])),
        }
    except (KeyError, TypeError, ValueError):
        return None
    char = raw.get("char")
    if isinstance(char, str) and len(char) == 1 and CJK_RE.fullmatch(char):
        item["char"] = char
    for key in ("col", "ti"):
        parsed = _as_int_field(raw.get(key))
        if parsed is not None:
            item[key] = parsed
    if raw.get("repeatMark") is True:
        item["repeatMark"] = True
    if raw.get("noCard") is True:
        item["noCard"] = True
        reason = raw.get("noCardReason")
        if reason in NO_CARD_REASONS:
            item["noCardReason"] = reason
    return item


def _as_int_field(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def label_mode_of(data: object) -> str:
    if isinstance(data, dict) and data.get("labelMode") == "fixed":
        return "fixed"
    return "offset"


def _offset(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def empty_boxes(plate_ids: list[str], source: str, label_mode: str = "offset") -> dict:
    return {
        "source": source,
        "labelMode": "fixed" if label_mode == "fixed" else "offset",
        "repeatMarkNote": REPEAT_NOTE,
        "noCardNote": NOCARD_NOTE,
        "textOffsetByPlate": {plate_id: 0 for plate_id in plate_ids},
        "plates": {plate_id: [] for plate_id in plate_ids},
    }


def normalize_boxes(data: object, plate_ids: list[str], source: str) -> dict:
    """Keep only known plates, in discovery order, with validated boxes."""
    raw = data if isinstance(data, dict) else {}
    raw_plates = raw.get("plates") if isinstance(raw.get("plates"), dict) else {}
    raw_offsets = raw.get("textOffsetByPlate") if isinstance(raw.get("textOffsetByPlate"), dict) else {}
    file_source = raw.get("source")
    plates: dict[str, list] = {}
    offsets: dict[str, int] = {}
    for plate_id in plate_ids:
        items = raw_plates.get(plate_id, [])
        cleaned: list[dict] = []
        if isinstance(items, list):
            for item in items:
                box = clean_box(item)
                if box is not None:
                    cleaned.append(box)
        plates[plate_id] = cleaned
        offsets[plate_id] = _offset(raw_offsets.get(plate_id, 0))
    return {
        "source": _as_source(file_source) if isinstance(file_source, str) and file_source.strip() else source,
        "labelMode": label_mode_of(raw),
        "repeatMarkNote": REPEAT_NOTE,
        "noCardNote": NOCARD_NOTE,
        "textOffsetByPlate": offsets,
        "plates": plates,
    }


def load_boxes(path: Path, plate_ids: list[str], source: str) -> dict:
    if not path.is_file():
        return empty_boxes(plate_ids, source)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return empty_boxes(plate_ids, source)
    return normalize_boxes(data, plate_ids, source)


def save_boxes(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def glyph_filename(
    plate_id: str,
    box_id: int,
    char: str | None,
    used: set[str],
    repeat: bool = False,
) -> str:
    safe = re.sub(r"[^\w.\-]+", "_", plate_id, flags=re.UNICODE).strip("._") or "plate"
    suffix = f"_{char}" if char else ""
    mark = "_repeat" if repeat else ""
    name = f"{safe}_{int(box_id):04d}{suffix}{mark}.png"
    if name not in used:
        used.add(name)
        return name
    serial = 2
    while True:
        candidate = f"{safe}_{int(box_id):04d}{suffix}{mark}_{serial}.png"
        if candidate not in used:
            used.add(candidate)
            return candidate
        serial += 1


def crop_image_box(image, box: dict):
    """Crop one box in full-image pixel space. Returns None if the area is empty."""
    width, height = image.size
    x = int(round(float(box["x"])))
    y = int(round(float(box["y"])))
    w = int(round(float(box["w"])))
    h = int(round(float(box["h"])))
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    left = max(0, min(width, x))
    top = max(0, min(height, y))
    right = max(0, min(width, x + w))
    bottom = max(0, min(height, y + h))
    if right <= left or bottom <= top:
        return None
    return image.crop((left, top, right, bottom))


@dataclass(frozen=True)
class CropReport:
    written: list[Path]
    skipped: int
    missing_plates: list[str]
    skipped_nocard: int = 0
    skipped_repeat: int = 0


def crop_plates(
    settings: Settings,
    boxes: dict | None = None,
    *,
    include_nocard: bool = False,
    skip_repeat: bool = False,
) -> CropReport:
    try:
        from PIL import Image
    except ImportError:
        die("Pillow is required to crop glyphs. Install it with: python -m pip install -r requirements.txt")

    pairs = discover_plates(settings.plates_dir, settings.plate_glob)
    plate_paths = dict(pairs)
    plate_ids = [plate_id for plate_id, _ in pairs]
    document = boxes if boxes is not None else load_boxes(settings.boxes_path, plate_ids, settings.source)
    out_dir = settings.crop_out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped = 0
    skipped_nocard = 0
    skipped_repeat = 0
    missing: list[str] = []
    used: set[str] = set()
    plates = document.get("plates") if isinstance(document.get("plates"), dict) else {}
    for plate_id in plate_ids:
        image_path = plate_paths.get(plate_id)
        items = plates.get(plate_id) or []
        if not items:
            continue
        if image_path is None or not image_path.is_file():
            missing.append(plate_id)
            skipped += len(items)
            continue
        try:
            with Image.open(image_path) as image:
                image.load()
                for raw in items:
                    box = clean_box(raw)
                    if box is None:
                        skipped += 1
                        continue
                    if box.get("noCard") is True and not include_nocard:
                        skipped += 1
                        skipped_nocard += 1
                        continue
                    if box.get("repeatMark") is True and skip_repeat:
                        skipped += 1
                        skipped_repeat += 1
                        continue
                    glyph = crop_image_box(image, box)
                    if glyph is None:
                        skipped += 1
                        continue
                    char = box.get("char") if isinstance(box.get("char"), str) else None
                    name = glyph_filename(
                        plate_id,
                        int(box["id"]),
                        char,
                        used,
                        repeat=box.get("repeatMark") is True,
                    )
                    dest = out_dir / name
                    if not dest.resolve().is_relative_to(out_dir):
                        skipped += 1
                        continue
                    glyph.save(dest, format="PNG")
                    written.append(dest)
        except OSError as exc:
            print(f"warning: cannot read {image_path}: {exc}", file=sys.stderr)
            missing.append(plate_id)
            skipped += len(items)
    return CropReport(
        written=written,
        skipped=skipped,
        missing_plates=missing,
        skipped_nocard=skipped_nocard,
        skipped_repeat=skipped_repeat,
    )
