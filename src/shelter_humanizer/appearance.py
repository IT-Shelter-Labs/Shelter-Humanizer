"""One palette per theme; no changes to document or connection state."""

from tkinter import ttk

PALETTES = {
    "light": {
        "bg": "#f3f2f8",
        "card": "#ffffff",
        "ink": "#252238",
        "muted": "#625d76",
        "border": "#d8d3e6",
        "hover": "#e9e4f4",
        "accent": "#7044c6",
        "accent_hover": "#5c34ab",
        "selection": "#ddd0f5",
        "selected_ink": "#252238",
        "disabled": "#ebe8f1",
        "disabled_ink": "#777184",
        "heading": "#eeeaf6",
        "added": "#187342",
        "removed": "#b12f48",
        "finding": "#ffedb2",
        "finding_ink": "#493506",
    },
    "dark": {
        "bg": "#14121d",
        "card": "#211e2e",
        "ink": "#f3effb",
        "muted": "#b4aac8",
        "border": "#474058",
        "hover": "#373044",
        "accent": "#7950ca",
        "accent_hover": "#8b5fdb",
        "selection": "#553980",
        "selected_ink": "#ffffff",
        "disabled": "#2b2638",
        "disabled_ink": "#9a90ae",
        "heading": "#2d273d",
        "added": "#83dca4",
        "removed": "#ffa0ae",
        "finding": "#584421",
        "finding_ink": "#ffe2a3",
    },
}


def configure_styles(root, theme):
    p = PALETTES[theme]
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    style.configure(".", background=p["bg"], foreground=p["ink"], font=("Segoe UI", 10))
    style.configure("TFrame", background=p["bg"])
    style.configure("Card.TFrame", background=p["card"])
    style.configure("TLabel", background=p["bg"], foreground=p["ink"])
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Card.TLabel", background=p["card"], foreground=p["ink"])
    style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
    style.configure("Heading.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Brand.TLabel", foreground=p["muted"], font=("Segoe UI", 9, "bold"))
    style.configure("Editor.TLabel", background=p["card"], font=("Segoe UI", 11, "bold"))
    style.configure(
        "Hint.TLabel", background=p["card"], foreground=p["muted"], font=("Segoe UI", 9)
    )
    style.configure(
        "TButton",
        padding=(12, 8),
        background=p["card"],
        foreground=p["ink"],
        borderwidth=1,
        bordercolor=p["border"],
        lightcolor=p["card"],
        darkcolor=p["card"],
        focuscolor=p["accent"],
    )
    style.map(
        "TButton",
        background=[("disabled", p["disabled"]), ("active", p["hover"])],
        foreground=[("disabled", p["disabled_ink"])],
    )
    style.configure(
        "Accent.TButton",
        background=p["accent"],
        foreground="#ffffff",
        bordercolor=p["accent"],
        lightcolor=p["accent"],
        darkcolor=p["accent"],
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", p["disabled"]), ("active", p["accent_hover"])],
        foreground=[("disabled", p["disabled_ink"]), ("!disabled", "#ffffff")],
    )
    for widget in ("TRadiobutton", "TCheckbutton"):
        style.configure(
            widget,
            background=p["bg"],
            foreground=p["ink"],
            padding=(3, 5),
            indicatorbackground=p["card"],
            indicatorforeground=p["accent"],
            bordercolor=p["border"],
            focuscolor=p["accent"],
        )
        style.map(
            widget,
            background=[("active", p["bg"])],
            foreground=[("disabled", p["disabled_ink"])],
            indicatorbackground=[("selected", p["accent"]), ("active", p["hover"])],
        )
    style.configure(
        "Theme.Toolbutton",
        padding=(10, 7),
        background=p["card"],
        foreground=p["muted"],
        borderwidth=0,
        focuscolor=p["accent"],
    )
    style.map(
        "Theme.Toolbutton",
        background=[("selected", p["selection"]), ("active", p["hover"])],
        foreground=[("selected", p["selected_ink"])],
    )
    for widget in ("TEntry", "TCombobox"):
        style.configure(
            widget,
            padding=7,
            fieldbackground=p["card"],
            foreground=p["ink"],
            background=p["heading"],
            bordercolor=p["border"],
            lightcolor=p["card"],
            darkcolor=p["card"],
            insertcolor=p["ink"],
            arrowcolor=p["ink"],
            selectbackground=p["selection"],
            selectforeground=p["selected_ink"],
        )
        style.map(
            widget,
            fieldbackground=[("disabled", p["disabled"]), ("readonly", p["card"])],
            foreground=[("disabled", p["disabled_ink"]), ("readonly", p["ink"])],
            background=[("active", p["hover"])],
        )
    style.configure(
        "Treeview",
        font=("Segoe UI", 9),
        rowheight=31,
        background=p["card"],
        fieldbackground=p["card"],
        foreground=p["ink"],
        bordercolor=p["border"],
        lightcolor=p["border"],
        darkcolor=p["border"],
    )
    style.map(
        "Treeview",
        background=[("selected", p["selection"])],
        foreground=[("selected", p["selected_ink"])],
    )
    style.configure(
        "Treeview.Heading",
        font=("Segoe UI", 9, "bold"),
        padding=(7, 8),
        background=p["heading"],
        foreground=p["muted"],
        bordercolor=p["border"],
        lightcolor=p["heading"],
        darkcolor=p["heading"],
    )
    style.map("Treeview.Heading", background=[("active", p["hover"])])
    style.configure(
        "TNotebook",
        background=p["bg"],
        borderwidth=0,
        bordercolor=p["border"],
        lightcolor=p["bg"],
        darkcolor=p["bg"],
    )
    style.configure(
        "TNotebook.Tab",
        padding=(16, 9),
        background=p["bg"],
        foreground=p["muted"],
        borderwidth=0,
        bordercolor=p["border"],
        lightcolor=p["bg"],
        darkcolor=p["bg"],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", p["card"]), ("active", p["hover"])],
        foreground=[("selected", p["ink"])],
    )
    style.configure("TSeparator", background=p["border"])
    style.configure("TPanedwindow", background=p["bg"])
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=p["heading"],
            troughcolor=p["bg"],
            bordercolor=p["bg"],
            arrowcolor=p["muted"],
            lightcolor=p["heading"],
            darkcolor=p["heading"],
            borderwidth=0,
            arrowsize=12,
        )
        style.map(f"{orient}.TScrollbar", background=[("active", p["border"])])
    style.configure(
        "Horizontal.TProgressbar",
        background=p["accent"],
        troughcolor=p["heading"],
        bordercolor=p["bg"],
        lightcolor=p["accent"],
        darkcolor=p["accent"],
    )
    # Tk's combobox popdown is a Listbox, not a ttk widget.
    for option, value in (
        ("background", p["card"]),
        ("foreground", p["ink"]),
        ("selectBackground", p["selection"]),
        ("selectForeground", p["selected_ink"]),
    ):
        root.option_add(f"*TCombobox*Listbox.{option}", value)
    return p
