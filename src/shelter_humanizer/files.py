"""Explicit bounded UTF-8 import and atomic export. No automatic history."""

import codecs
import json
import os
import tempfile
from pathlib import Path

from .models import MAX_TEXT, validate_text


class TextEncodingError(ValueError):
    pass


def read_text(path: str | Path, encoding="auto") -> str:
    if encoding not in ("auto", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1251"):
        raise ValueError("Неподдерживаемая кодировка TXT.")
    maximum = MAX_TEXT * 4 + 4
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Файл слишком большой.")
    if encoding == "auto":
        encoding = "utf-8-sig"
        if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = "utf-32"
        elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        raise TextEncodingError(
            "Не удалось прочитать TXT. Выберите кодировку с предпросмотром или сохраните файл в UTF-8."
        ) from None
    validate_text(text)
    return text


def write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    encoded = text.encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".shelter-", delete=False
        ) as stream:
            temporary = stream.name
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def write_report(path: str | Path, result) -> None:
    write_text(path, json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n")
