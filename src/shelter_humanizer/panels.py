"""Scrollable dialog bodies; actions stay outside the scrolling content."""

import tkinter as tk
from tkinter import ttk


def fit_dialog(window, width=720, height=650):
    width = min(width, max(380, window.winfo_screenwidth() - 60))
    height = min(height, max(300, window.winfo_screenheight() - 100))
    window.geometry(f"{width}x{height}")
    window.minsize(min(440, width), min(340, height))
    window.resizable(True, True)


class ScrollArea(ttk.Frame):
    def __init__(self, parent, *, padding=18):
        super().__init__(parent)
        color = ttk.Style(self).lookup("TFrame", "background") or "#f4f2fa"
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=color)
        scrollbar = ttk.Scrollbar(self, command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.body = ttk.Frame(self.canvas, padding=padding)
        self.item = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._layout)
        self.canvas.bind("<Configure>", self._resize)
        owner = self.winfo_toplevel()
        self.owner = owner
        self.bindings = []
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<FocusIn>"):
            callback = self._focus if event == "<FocusIn>" else self._wheel
            self.bindings.append((event, owner.bind(event, callback, add="+")))
        self.bind("<Destroy>", self._destroy, add="+")

    def _inside(self, widget):
        while widget is not None:
            if widget is self.body or widget is self.canvas:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _layout(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)

        def wrap(parent):
            for child in parent.winfo_children():
                if isinstance(child, ttk.Label) and int(child.cget("wraplength") or 0) > 0:
                    child.configure(wraplength=max(180, event.width - 64))
                wrap(child)

        wrap(self.body)

    def _wheel(self, event):
        if not self._inside(event.widget) or isinstance(event.widget, (tk.Text, ttk.Treeview)):
            return
        if self.body.winfo_reqheight() <= self.canvas.winfo_height():
            return
        direction = -1 if getattr(event, "num", None) == 4 else 1
        if getattr(event, "delta", 0):
            direction = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(direction * 3, "units")
        return "break"

    def _focus(self, event):
        if not self._inside(event.widget) or event.widget is self.canvas:
            return
        top = event.widget.winfo_rooty() - self.body.winfo_rooty()
        first, last = self.canvas.yview()
        height = max(1, self.body.winfo_reqheight())
        if top < first * height or top + event.widget.winfo_height() > last * height:
            self.canvas.yview_moveto(max(0, (top - 15) / height))

    def _destroy(self, event):
        if event.widget is not self:
            return
        owner = self.owner
        for sequence, identity in self.bindings:
            try:
                owner.unbind(sequence, identity)
            except tk.TclError:
                pass
