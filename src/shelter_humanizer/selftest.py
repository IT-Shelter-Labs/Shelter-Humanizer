"""Packaged-app smoke test: offline, no user interaction, explicit receipt file only."""

import hashlib
import json
import tempfile
import tkinter as tk
from pathlib import Path

from . import __version__
from .files import write_text
from .gui import SAMPLE, App
from .local_setup import LocalSetup
from .managed_runtime import ManagedRuntime
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
        assert app.depth_key() == "edit"
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            wizard = LocalSetup(root, lambda: app.colors, lambda model, url: None, runtime)
            default_model = wizard.choice.get()
            assert default_model == "qwen3.5:4b"
            assert runtime.selected_model() == "" and runtime.process is None
            assert not (Path(directory) / "selection.json").exists()
            wizard.close()
            runtime.save_model("deepseek-r1:8b-0528-qwen3-q4_K_M")
            wizard = LocalSetup(root, lambda: app.colors, lambda model, url: None, runtime)
            assert wizard.choice.get() == "deepseek-r1:8b-0528-qwen3-q4_K_M"
            wizard.close()
            runtime.close()
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
                self.system_sha256 = hashlib.sha256(messages[0]["content"].encode()).hexdigest()
                return "Про\u200bверка\u00a0текста. «Точная цитата» 👩‍💻"

        synthetic_ai = SyntheticAI()
        generated = rewrite(
            "Проверка текста. «Точная цитата» 👩‍💻", synthetic_ai, second_pass=False
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
            default_depth=app.depth_key(),
            default_model=default_model,
            saved_model_preserved=True,
            ai_system_sha256=synthetic_ai.system_sha256,
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
