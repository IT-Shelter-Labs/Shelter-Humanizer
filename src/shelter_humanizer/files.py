"""Explicit bounded UTF-8 import and atomic export. No automatic history."""

import json
import os
import tempfile
from pathlib import Path

from .models import MAX_TEXT, validate_text


def read_text(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_TEXT * 4 + 4)
    if len(raw) > MAX_TEXT * 4 + 3:
        raise ValueError("Файл слишком большой.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Нужен TXT-файл в UTF-8. Сохраните его с этой кодировкой.") from None
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
