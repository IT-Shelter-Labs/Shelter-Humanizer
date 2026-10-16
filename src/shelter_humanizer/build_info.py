"""Public build identity only; no local paths or user information."""

import json
from pathlib import Path

from . import __version__


def build_info():
    try:
        path = Path(__file__).with_name("build-info.json")
        if path.stat().st_size > 8192:
            raise ValueError("invalid metadata")
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            key: data[key]
            for key in ("version", "build_id", "commit", "source_dirty", "built_at")
            if key in data
        }
    except (OSError, ValueError, TypeError):
        return {"version": __version__, "build_id": "source"}
