"""Opt-in local editor preferences; never store texts, endpoints or API keys."""

import json
import os
from pathlib import Path

from .files import write_text

MAX_INSTRUCTION = 12000


def validate_instruction(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_INSTRUCTION:
        raise ValueError(f"Инструкция должна содержать от 1 до {MAX_INSTRUCTION} символов.")
    if "\x00" in value:
        raise ValueError("В инструкции есть недопустимый нулевой символ.")
    return value


class Preferences:
    def __init__(self, path=None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
        self.path = Path(path) if path else base / "ShelterHumanizer/editor-preferences.json"

    def load(self):
        try:
            if self.path.stat().st_size > 100000:
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            value = data.get("instruction")
            return {"instruction": validate_instruction(value)} if value else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def save(self, instruction):
        if instruction is None:
            self.path.unlink(missing_ok=True)
            return
        instruction = validate_instruction(instruction)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text(self.path, json.dumps({"instruction": instruction}, ensure_ascii=False) + "\n")
