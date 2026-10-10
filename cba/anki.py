"""Build an Anki package from plate images and boxes.json.

One note per character. Note fields are data (character, ``<img>`` tags,
captions, count). Card layout lives in ``cba/anki_templates`` or a directory
passed as ``--template-dir``. ``repeatMark`` and ``noCard`` boxes are skipped.
The deck id comes from the deck name. The model id comes from the deck name
and the template set name. The note GUID comes from the deck name and the
character, so importing the same deck again updates existing notes.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

PAD_PX = 8
MAX_SIDE = 400
JPEG_QUALITY = 85
# Fixed package clock. Note identity on re-import is the GUID, not this value.
PACKAGE_TIMESTAMP = 1_704_067_200.0
_ID_SPACE = 2_000_000_000
_CAPTION_DOT = "\u00b7"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
NOTE_FIELDS = ("Char", "Image", "Variants", "Sources", "Count")
_ANKI_SPECIAL = {"FrontSide", "Tags", "Type", "Deck", "Card", "Subdeck", "CardFlag"}
_TAG = re.compile(r"\{\{([^{}]+)\}\}")
_TAG_PARTS = re.compile(
    r"^\s*([#/^])?\s*(?:(text|hint|type|furigana|kana|kanji):)?\s*([A-Za-z][A-Za-z0-9_]*)\s*$"
)
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class DeckError(Exception):
    def __init__(self, message: str, **extra: object) -> None:
        super().__init__(message)
        self.extra = extra


def stable_id(key: str) -> int:
    """Positive id in ``2 .. 2_000_000_000``, stable for ``key``.

    The lower bound stays off Anki's built-in deck id 1. The upper bound fits
    in a signed 32-bit integer.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") % (_ID_SPACE - 1)) + 2


def deck_id_for(deck_name: str) -> int:
    return stable_id(f"cba\x1fdeck\x1f{deck_name}")


def model_id_for(deck_name: str, template_name: str = "default") -> int:
    """Model id for one deck and one template set. ``--front`` is not part of it."""
    model = stable_id(f"cba\x1fmodel\x1f{deck_name}\x1f{template_name}")
    deck = deck_id_for(deck_name)
    if model != deck:
        return model
    return 2 if deck != 2 else 3


def note_guid(deck_name: str, char: str) -> str:
    """Anki GUID for one character in one deck. Same inputs always match."""
    import genanki

    return genanki.guid_for(deck_name, char)


def deck_filename(deck_name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", deck_name).strip("._")
    return f"{cleaned or 'deck'}.apkg"


def crop_glyph(image, box: dict, *, pad: int = PAD_PX, max_side: int = MAX_SIDE):
    """Crop one box with a small pad, then shrink so the long side is at most ``max_side``.

    Returns an RGB image, or None when the crop has no pixels.
    """
    from PIL import Image

    width, height = image.size
    x = int(round(float(box["x"])))
    y = int(round(float(box["y"])))
    w = int(round(float(box["w"])))
    h = int(round(float(box["h"])))
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    left = max(0, x - pad)
    top = max(0, y - pad)
    right = min(width, x + w + pad)
    bottom = min(height, y + h + pad)
    if right <= left or bottom <= top:
        return None
    crop = image.crop((left, top, right, bottom))
    crop = _to_rgb(crop)
    longest = max(crop.size)
    if longest > max_side:
        scale = max_side / float(longest)
        new_size = (max(1, int(round(crop.width * scale))), max(1, int(round(crop.height * scale))))
        resample = Image.Resampling.LANCZOS
        crop = crop.resize(new_size, resample)
    return crop


def _to_rgb(image):
    from PIL import Image

    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def default_template_dir() -> Path:
    """Bundled card templates, next to this module in a checkout or an install."""
    return Path(__file__).resolve().parent / "anki_templates" / "default"


@dataclass(frozen=True)
class CardTemplate:
    set_id: str
    model_name: str
    card_name: str
    fields: tuple[str, ...]
    front_html: str
    back_html: str
    css: str
    front: str


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DeckError(f"cannot read template file {path.name}: {exc}", code="template") from exc


def _caption(plate: str, ti: int | None) -> str:
    if ti is None:
        return plate
    return f"{plate}{_CAPTION_DOT}{ti}"


def _img_tag(filename: str, char: str) -> str:
    src = html.escape(filename, quote=True)
    alt = html.escape(char, quote=True)
    return f'<img src="{src}" alt="{alt}">'


def referenced_fields(*parts: str) -> set[str]:
    """Field names used by Anki template tags. Special names such as FrontSide are omitted."""
    found: set[str] = set()
    for part in parts:
        for raw in _TAG.findall(part):
            match = _TAG_PARTS.match(raw)
            if match is None:
                raise DeckError(
                    f"unrecognized template tag {{{{{raw.strip()}}}}}",
                    code="template",
                )
            name = match.group(3)
            if name not in _ANKI_SPECIAL:
                found.add(name)
    return found


def _validate_references(label: str, text: str, fields: tuple[str, ...]) -> None:
    try:
        used = referenced_fields(text)
    except DeckError as exc:
        raise DeckError(f"{label}: {exc}", code="template", **exc.extra) from exc
    unknown = sorted(name for name in used if name not in fields)
    if unknown:
        names = ", ".join(unknown)
        allowed = ", ".join(fields) if fields else "(none)"
        raise DeckError(
            f"{label} references unknown field(s): {names}; fields are {allowed}",
            code="template",
            unknown=unknown,
            file=label,
        )


def load_template(directory: Path | None, front: str) -> CardTemplate:
    """Load ``front.html`` / ``front-char.html``, ``back.html``, ``style.css``, and optional ``template.json``."""
    if front not in ("image", "char"):
        raise DeckError("front must be image or char")
    folder = default_template_dir() if directory is None else Path(directory)
    if not folder.is_dir():
        raise DeckError(f"template directory not found: {folder.name}", code="template")

    meta: dict = {}
    spec_path = folder / "template.json"
    if spec_path.is_file():
        try:
            parsed = json.loads(_read_text(spec_path))
        except json.JSONDecodeError as exc:
            raise DeckError(f"invalid template.json: {exc}", code="template") from exc
        if not isinstance(parsed, dict):
            raise DeckError("template.json must be an object", code="template")
        meta = parsed

    set_id = meta.get("id")
    if set_id in (None, ""):
        set_id = folder.name
    if not isinstance(set_id, str) or not set_id.strip() or any(ch in set_id for ch in "\r\n\x00"):
        raise DeckError("template id must be a non-empty single-line string", code="template")
    set_id = set_id.strip()

    model_name = meta.get("name")
    if model_name in (None, ""):
        model_name = "cba glyphs" if set_id == "default" else set_id
    if not isinstance(model_name, str) or not model_name.strip() or any(ch in model_name for ch in "\r\n\x00"):
        raise DeckError("template name must be a non-empty single-line string", code="template")
    model_name = model_name.strip()

    card_name = meta.get("card") or "Glyph"
    if not isinstance(card_name, str) or not card_name.strip():
        raise DeckError("template card name must be a non-empty string", code="template")

    if "fields" in meta and meta["fields"] not in (None, ""):
        raw_fields = meta["fields"]
        if not isinstance(raw_fields, list) or not raw_fields:
            raise DeckError("template fields must be a non-empty list", code="template")
        fields_list: list[str] = []
        for item in raw_fields:
            if not isinstance(item, str) or not _FIELD_NAME.fullmatch(item):
                raise DeckError(f"invalid template field name: {item!r}", code="template")
            if item in fields_list:
                raise DeckError(f"duplicate template field: {item}", code="template")
            fields_list.append(item)
        fields = tuple(fields_list)
    else:
        fields = NOTE_FIELDS
    unknown_fields = [name for name in fields if name not in NOTE_FIELDS]
    if unknown_fields:
        names = ", ".join(unknown_fields)
        allowed = ", ".join(NOTE_FIELDS)
        raise DeckError(
            f"template field(s) not produced by cba anki: {names}; available fields are {allowed}",
            code="template",
            unknown=unknown_fields,
        )

    fronts = meta.get("fronts") or {"image": "front.html", "char": "front-char.html"}
    if not isinstance(fronts, dict):
        raise DeckError("template fronts must be an object", code="template")
    front_file = fronts.get(front) or ("front.html" if front == "image" else "front-char.html")
    if not isinstance(front_file, str) or not front_file.strip() or Path(front_file).name != front_file:
        raise DeckError(f"template front for {front} must be a file name in the template directory", code="template")
    back_file = meta.get("back") or "back.html"
    css_file = meta.get("css") or "style.css"
    for label, filename in (("back", back_file), ("css", css_file)):
        if not isinstance(filename, str) or not filename.strip() or Path(filename).name != filename:
            raise DeckError(f"template {label} must be a file name in the template directory", code="template")

    front_path = folder / front_file
    back_path = folder / back_file
    css_path = folder / css_file
    missing = [path.name for path in (front_path, back_path, css_path) if not path.is_file()]
    if missing:
        raise DeckError(
            f"template is missing {', '.join(missing)} (front {front} uses {front_file})",
            code="template",
            missing=missing,
        )
    front_html = _read_text(front_path).strip()
    back_html = _read_text(back_path).strip()
    css = _read_text(css_path).strip()
    if not front_html:
        raise DeckError(f"{front_file} is empty", code="template")
    if not back_html:
        raise DeckError(f"{back_file} is empty", code="template")
    _validate_references(front_file, front_html, fields)
    _validate_references(back_file, back_html, fields)
    return CardTemplate(
        set_id=set_id,
        model_name=model_name,
        card_name=card_name.strip(),
        fields=fields,
        front_html=front_html,
        back_html=back_html,
        css=css,
        front=front,
    )


def _note_values(char: str, named: list[tuple[object, str]]) -> dict[str, str]:
    images = [_img_tag(filename, char) for _item, filename in named]
    sources = [html.escape(_caption(item.plate, item.ti)) for item, _filename in named]
    return {
        "Char": html.escape(char),
        "Image": images[0],
        "Variants": "\n".join(images),
        "Sources": "\n".join(sources),
        "Count": str(len(named)),
    }


def _media_name(plate: str, box_id: int, ti: int | None, used: set[str]) -> str:
    safe = _SAFE_NAME.sub("_", plate).strip("._") or "plate"
    if safe != plate:
        digest = hashlib.sha256(plate.encode("utf-8")).hexdigest()[:6]
        safe = f"{safe}_{digest}" if safe != "plate" else f"plate_{digest}"
    ti_part = "na" if ti is None else str(ti)
    stem = f"{safe}_{int(box_id):04d}_{ti_part}"
    name = f"{stem}.jpg"
    serial = 2
    while name in used:
        name = f"{stem}_{serial}.jpg"
        serial += 1
    if "/" in name or "\\" in name or name.startswith("."):
        raise DeckError("media filename must be a plain file name")
    used.add(name)
    return name


@dataclass
class _Kept:
    plate: str
    plate_index: int
    box_id: int
    ti: int | None
    y: float
    char: str
    box: dict


def _sort_key(item: _Kept) -> tuple:
    ti = item.ti if item.ti is not None else 10**12
    return (item.plate_index, ti, item.y, item.box_id)


def _classify(plates: list[tuple[str, Path]], document: dict) -> tuple[list[_Kept], dict]:
    from boxannotator import clean_box

    skipped_repeat = 0
    skipped_nocard = 0
    skipped_other = 0
    missing: list[str] = []
    kept: list[_Kept] = []
    raw_plates = document.get("plates") if isinstance(document.get("plates"), dict) else {}
    for plate_index, (plate_id, image_path) in enumerate(plates):
        items = raw_plates.get(plate_id) or []
        if not isinstance(items, list):
            continue
        image_ok = image_path is not None and Path(image_path).is_file()
        for raw in items:
            box = clean_box(raw)
            if box is None:
                skipped_other += 1
                continue
            repeat = box.get("repeatMark") is True
            nocard = box.get("noCard") is True
            if repeat:
                skipped_repeat += 1
            if nocard:
                skipped_nocard += 1
            if repeat or nocard:
                continue
            char = box.get("char")
            if not isinstance(char, str) or len(char) != 1:
                skipped_other += 1
                continue
            if not image_ok:
                if plate_id not in missing:
                    missing.append(plate_id)
                skipped_other += 1
                continue
            ti = box.get("ti")
            kept.append(
                _Kept(
                    plate=plate_id,
                    plate_index=plate_index,
                    box_id=int(box["id"]),
                    ti=ti if isinstance(ti, int) and not isinstance(ti, bool) else None,
                    y=float(box["y"]),
                    char=char,
                    box=box,
                )
            )
    counts = {
        "skippedRepeat": skipped_repeat,
        "skippedNoCard": skipped_nocard,
        "skippedOther": skipped_other,
        "missingPlates": missing,
    }
    return kept, counts


def _model(deck_name: str, template: CardTemplate):
    import genanki

    return genanki.Model(
        model_id_for(deck_name, template.set_id),
        template.model_name,
        fields=[{"name": name} for name in template.fields],
        templates=[
            {
                "name": template.card_name,
                "qfmt": template.front_html,
                "afmt": template.back_html,
            }
        ],
        css=template.css,
        sort_field_index=template.fields.index("Char") if "Char" in template.fields else 0,
    )


def _save_jpeg(image, path: Path) -> None:
    image.save(path, format="JPEG", quality=JPEG_QUALITY, optimize=True)


def build_apkg(
    plates: list[tuple[str, Path]],
    document: dict,
    deck_name: str,
    out_path: Path,
    front: str = "image",
    template_dir: Path | None = None,
) -> dict:
    """Write one ``.apkg``. Raises ``DeckError`` when there is nothing to card."""
    from boxannotator import ensure_outside_package

    name = deck_name.strip()
    if not name or any(ch in name for ch in "\r\n\x00"):
        raise DeckError("deck name must be a non-empty single-line name")
    template = load_template(template_dir, front)
    out_path = Path(out_path)
    if out_path.exists() and out_path.is_dir():
        raise DeckError("output path is a directory")
    ensure_outside_package(out_path)

    try:
        from PIL import Image
    except ImportError as exc:
        raise DeckError("Pillow is required to crop glyphs") from exc
    try:
        import genanki
    except ImportError as exc:
        raise DeckError("genanki is required to build an Anki deck") from exc

    kept, counts = _classify(plates, document)
    grouped: dict[str, list[_Kept]] = {}
    for item in kept:
        grouped.setdefault(item.char, []).append(item)

    used_names: set[str] = set()
    plan: list[tuple[str, list[tuple[_Kept, str]]]] = []
    for char in sorted(grouped):
        ordered = sorted(grouped[char], key=_sort_key)
        named = [(item, _media_name(item.plate, item.box_id, item.ti, used_names)) for item in ordered]
        plan.append((char, named))

    by_plate: dict[str, list[tuple[_Kept, str]]] = {}
    for _char, named in plan:
        for item, filename in named:
            by_plate.setdefault(item.plate, []).append((item, filename))

    saved: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="cba-anki-") as tmp:
        tmp_dir = Path(tmp)
        plate_paths = dict(plates)
        for plate_id, named in by_plate.items():
            image_path = plate_paths.get(plate_id)
            try:
                opened = Image.open(image_path)
            except OSError:
                if plate_id not in counts["missingPlates"]:
                    counts["missingPlates"].append(plate_id)
                counts["skippedOther"] += len(named)
                continue
            with opened as image:
                image.load()
                for item, filename in named:
                    glyph = crop_glyph(image, item.box)
                    if glyph is None:
                        counts["skippedOther"] += 1
                        continue
                    dest = tmp_dir / filename
                    try:
                        _save_jpeg(glyph, dest)
                    except OSError:
                        counts["skippedOther"] += 1
                        continue
                    saved[filename] = dest

        notes: list[tuple[str, list[tuple[_Kept, str]]]] = []
        for char, named in plan:
            ok = [(item, filename) for item, filename in named if filename in saved]
            if ok:
                notes.append((char, ok))
        if not notes:
            raise DeckError(
                "no glyphs to card; repeatMark and noCard boxes are skipped",
                notes=0,
                media=0,
                deck=name,
                front=front,
                **counts,
            )

        model = _model(name, template)
        deck = genanki.Deck(
            deck_id_for(name),
            name,
            description="One note per character. Glyph JPEGs are package media files.",
        )
        media_paths: list[str] = []
        for char, named in notes:
            values = _note_values(char, named)
            fields = [values[field_name] for field_name in template.fields]
            note = genanki.Note(model=model, fields=fields, guid=note_guid(name, char))
            deck.add_note(note)
            for _item, filename in named:
                media_paths.append(str(saved[filename]))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        partial = out_path.with_name(out_path.name + ".partial")
        ensure_outside_package(partial)
        try:
            package = genanki.Package(deck, media_paths)
            package.media_files = media_paths
            package.write_to_file(str(partial), timestamp=PACKAGE_TIMESTAMP)
            partial.replace(out_path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    return {
        "deck": name,
        "front": front,
        "template": template.set_id,
        "notes": len(notes),
        "media": len(media_paths),
        "skippedRepeat": counts["skippedRepeat"],
        "skippedNoCard": counts["skippedNoCard"],
        "skippedOther": counts["skippedOther"],
        "missingPlates": counts["missingPlates"],
        "bytes": out_path.stat().st_size,
        "deckId": deck_id_for(name),
        "modelId": model_id_for(name, template.set_id),
    }
