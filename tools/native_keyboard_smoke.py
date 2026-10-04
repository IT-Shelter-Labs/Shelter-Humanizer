"""Windows-only acceptance check: send keys only to this process's own Tk window."""

import ctypes
import json
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shelter_humanizer.clipboard import read as read_clipboard
from shelter_humanizer.gui import App


def main():
    if sys.platform != "win32":
        raise SystemExit("This acceptance test requires Windows")
    user = ctypes.windll.user32
    user.GetKeyboardLayout.restype = ctypes.c_void_p
    user.GetForegroundWindow.restype = ctypes.c_void_p
    user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.GetAncestor.restype = ctypes.c_void_p
    user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user.SetForegroundWindow.restype = ctypes.c_int
    user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user.LoadKeyboardLayoutW.restype = ctypes.c_void_p
    user.ActivateKeyboardLayout.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    original = user.GetKeyboardLayout(0)
    root = tk.Tk()
    app = App(root, auto_connect=False)
    seen = []
    app.source.bind("<KeyPress>", lambda e: seen.append((e.keycode, e.keysym, e.state)), add="+")

    def pump():
        until = time.monotonic() + 0.15
        while time.monotonic() < until:
            root.update()
            time.sleep(0.01)

    def chord(code):
        pid = ctypes.c_ulong()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
        if pid.value != os.getpid():
            raise RuntimeError("Own window is not foreground: refusing to send keys")
        # Real Win32 key messages, rather than Tk event_generate with an invented state.
        user.keybd_event(0x11, 0, 0, 0)
        user.keybd_event(code, 0, 0, 0)
        pump()
        user.keybd_event(code, 0, 2, 0)
        user.keybd_event(0x11, 0, 2, 0)
        pump()

    def focus_owned(widget):
        widget.update()
        handle = user.GetAncestor(widget.winfo_id(), 2)
        pid = ctypes.c_ulong()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value != os.getpid():
            raise RuntimeError("Refusing to activate another process's window")
        user.SetForegroundWindow(handle)
        widget.focus_force()
        pump()

    try:
        results = {}
        for layout in ("00000409", "00000419"):
            handle = user.LoadKeyboardLayoutW(layout, 0)
            if not handle:
                raise RuntimeError("Keyboard layout unavailable")
            user.ActivateKeyboardLayout(handle, 0)
            app.set_source("")
            root.deiconify()
            root.lift()
            focus_owned(root)
            app.source.focus_force()
            pump()
            app.clipboard("Проверка русской вставки 👩‍💻")
            chord(86)
            assert app.get_text(app.source) == "Проверка русской вставки 👩‍💻", (
                layout,
                seen,
                app.get_text(app.source),
            )
            chord(65)
            selection = str(app.source.tag_ranges("sel"))
            chord(67)
            assert read_clipboard(root) == "Проверка русской вставки 👩‍💻", (
                layout,
                selection,
                repr(read_clipboard(root)),
                repr(app.get_text(app.source)),
                seen,
            )
            chord(88)
            assert app.get_text(app.source) == ""
            chord(86)
            assert app.get_text(app.source) == "Проверка русской вставки 👩‍💻"
            app.mode.set("api")
            app.settings()
            dialog = next(
                w
                for w in root.winfo_children()
                if isinstance(w, tk.Toplevel) and w is not app.review_window
            )

            def entries(widget):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Entry):
                        yield child
                    yield from entries(child)

            entry = next(entries(dialog))
            dialog.lift()
            focus_owned(dialog)
            entry.focus_force()
            entry.delete(0, "end")
            pump()
            chord(86)
            assert entry.get() == "Проверка русской вставки 👩‍💻", (
                layout,
                entry.get(),
                str(root.focus_get()),
            )
            chord(65)
            chord(67)
            assert read_clipboard(root) == "Проверка русской вставки 👩‍💻"
            chord(88)
            assert entry.get() == ""
            chord(66)
            assert entry.get() == "Проверка русской вставки 👩‍💻"
            dialog.destroy()
            results[layout] = "Text + Entry: paste/select/copy/cut/paste passed"
        print(json.dumps(results, ensure_ascii=False))
    finally:
        user.keybd_event(0x11, 0, 2, 0)
        if original:
            user.ActivateKeyboardLayout(original, 0)
        app.close()


if __name__ == "__main__":
    main()
