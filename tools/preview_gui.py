"""Capture this application's own window, never the full desktop (Windows only)."""

import sys
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shelter_humanizer.gui import SAMPLE, App
from shelter_humanizer.service import offline


def main():
    from PIL import ImageGrab

    root = tk.Tk()
    app = App(root, auto_connect=False)
    try:
        app.set_source(SAMPLE)
        app.show_result(offline(SAMPLE))
        for theme, filename in (("light", "desktop.png"), ("dark", "desktop-dark.png")):
            app.apply_theme(theme)
            root.update_idletasks()
            root.update()
            path = Path(__file__).resolve().parents[1] / "docs" / "images" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            snapshot = ImageGrab.grab(window=root.winfo_id())
            snapshot.save(path)
            print(f"Own-window preview ({theme}): {snapshot.size}")
        # Show a first-time setup, independent of this machine's saved model choice.
        with patch.object(app.runtime, "selected_model", return_value=""):
            app.setup_local()
        root.update()
        wizard = app.local_wizard
        ImageGrab.grab(window=wizard.window.winfo_id()).save(path.parent / "local-setup.png")
        wizard.close()
        for method, filename in (
            (app.text_settings, "text-settings.png"),
            (app.processing_settings, "processing-settings.png"),
            (app.chat_workflow, "chat-workflow.png"),
        ):
            method()
            root.update()
            dialog = next(
                w
                for w in root.winfo_children()
                if isinstance(w, tk.Toplevel) and w is not app.review_window
            )
            ImageGrab.grab(window=dialog.winfo_id()).save(path.parent / filename)
            dialog.destroy()
        app.toggle_details(True)
        root.update()
        ImageGrab.grab(window=app.review_window.winfo_id()).save(path.parent / "desktop-review.png")
    finally:
        app.close()


if __name__ == "__main__":
    main()
