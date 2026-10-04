"""Packaged-app smoke test: no network, no dialogs, explicit receipt file only."""

import json
import tkinter as tk
from pathlib import Path

from . import __version__
from .files import write_text
from .gui import SAMPLE, App
from .service import offline, rewrite

SMOKE_TEXT = (
    "Важно отметить, что сервис осуществляет проверку.\n"
    "Цена — 2 000 ₽. «Точная цитата» https://example.org/docs\n"
    "про\u200bверка. пpоверка. 👩‍💻."
)


def run(path: str) -> int:
    receipt = {"version": __version__, "ok": False, "network_used": False}
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = App(root, auto_connect=False)
        app.set_source(SAMPLE)
        result = offline(SAMPLE)
        app.show_result(result)
        root.update()
        assert app.get_text(app.source) == SAMPLE
        assert app.get_text(app.output) == SAMPLE
        app.set_source(SMOKE_TEXT)
        result = offline(SMOKE_TEXT)
        app.show_result(result)
        root.update()
        assert "про\u200bверка" not in app.get_text(app.output)
        assert "2 000 ₽" in app.get_text(app.output)
        assert "👩‍💻" in app.get_text(app.output)
        assert app.logo.width() > 0

        class SyntheticAI:
            def complete(self, messages):
                return "Про\u200bверка\u00a0текста. «Точная цитата» 👩‍💻"

        generated = rewrite(
            "Проверка текста. «Точная цитата» 👩‍💻", SyntheticAI(), second_pass=False
        )
        assert generated.text == "Проверка\u00a0текста. «Точная цитата» 👩‍💻"
        assert generated.original == "Проверка текста. «Точная цитата» 👩‍💻"
        for theme in ("dark", "light"):
            app.apply_theme(theme)
            root.update()
            assert app.get_text(app.source) == SMOKE_TEXT
            assert app.get_text(app.output) == result.text
        receipt.update(
            ok=True,
            tk=str(root.tk.call("info", "patchlevel")),
            source_preserved=True,
            unicode_removed=True,
            numbers_preserved=True,
            emoji_preserved=True,
            themes_preserve_text=True,
            logo_loaded=True,
            neutral_example_loaded=True,
            post_ai_cleanup=True,
            ai_generation_used=False,
        )
        app.close()
        root = None
    except Exception:
        receipt["error"] = "Packaged GUI self-test failed"
    finally:
        if root is not None:
            root.destroy()
    write_text(Path(path), json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return 0 if receipt["ok"] else 1
