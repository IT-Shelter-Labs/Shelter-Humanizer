"""Native Tk desktop. Workers communicate only through a queue, never with widgets."""

from __future__ import annotations

import difflib
import queue
import threading
import tkinter as tk
import urllib.parse
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .appearance import PALETTES, configure_styles
from .clipboard import read as read_clipboard
from .clipboard import write as write_clipboard
from .files import read_text, write_report, write_text
from .local_setup import LocalSetup
from .managed_runtime import ManagedRuntime
from .models import Result, validate_text
from .prompts import copy_prompt
from .providers import Client, ProviderConfig
from .service import Cancelled, analyze, compare, offline, rewrite
from .style import GENRES
from .ui_helpers import (
    attach_help,
    bind_editing,
    hide_tooltips,
    install_shortcuts,
)
from .unicode_check import CleanOptions, clean_unicode

EDIT_CHOICES = {"edit": "Небольшие правки", "rephrase": "Переписать формулировки"}

TELEGRAM_URL = "https://t.me/+txLM4MYbMh9lZDli"
LOGO_PATH = Path(__file__).parent / "assets" / "it-shelter-logo.png"
SAMPLE = (
    "В субботу в городской библиотеке состоится встреча книжного клуба. "
    "Участники обсудят прочитанные книги и выберут тему следующего разговора.\n\n"
    "Встреча начнётся в 15:00. Вход свободный, но количество мест ограничено. "
    "Для участия нужно записаться у библиотекаря до пятницы.\n\n"
    "Можно принести книгу, о которой хочется рассказать. "
    "Специальная подготовка не требуется."
)


class App:
    def __init__(self, root: tk.Tk, *, auto_connect=True):
        self.root = root
        root.title(f"Shelter Humanizer · {__version__}")
        width = max(960, min(1260, root.winfo_screenwidth() - 80))
        height = max(620, min(860, root.winfo_screenheight() - 100))
        root.geometry(f"{width}x{height}")
        root.minsize(960, 620)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.closed = False
        self.events = queue.Queue()
        self.model_requests = {}
        self.local_wizard = None
        self.runtime = ManagedRuntime()
        self.managed_connection = False
        self.cancel = threading.Event()
        self.busy = False
        self.revision = 0
        self.result = None
        self.analysis_snapshot = None
        self.findings = {}
        self.configs = {
            "ollama": ProviderConfig(),
            "api": ProviderConfig("api", "https://openrouter.ai/api/v1"),
        }
        self.voice = ""
        self.terms = ()
        self.last_chat_prompt = None
        self.last_chat_source = None
        self.mode = tk.StringVar(value="offline")
        self.connection_note = tk.StringVar(value="")
        self.genre = tk.StringVar(value=GENRES["plain"])
        self.depth = tk.StringVar(value=EDIT_CHOICES["edit"])
        self.second_pass = tk.BooleanVar(value=True)
        self.spaces = tk.BooleanVar(value=True)
        self.confusables = tk.BooleanVar(value=False)
        self.nfc = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Готово. Вставьте текст или откройте пример.")
        self.count = tk.StringVar(value="0 символов")
        self.theme = tk.StringVar(value="light")
        self.result_note = tk.StringVar(value="Готовый текст появится здесь после обработки.")
        install_shortcuts(root)
        self._styles()
        self._build()
        self.apply_theme()
        attach_help(root, lambda: self.colors)
        self.source.bind("<<Modified>>", self._source_changed)
        self.poll_id = root.after(80, self._poll)
        if auto_connect and self.runtime.selected_model():
            self.mode.set("ollama")
            root.after_idle(self.select_mode)

    def _styles(self):
        self.colors = configure_styles(self.root, self.theme.get())

    def apply_theme(self, theme=None):
        if theme is not None:
            if theme not in PALETTES:
                raise ValueError("Unknown theme")
            self.theme.set(theme)
        self._styles()
        p = self.colors

        def recolor(widget):
            if hasattr(widget, "shelter_tip"):
                widget.shelter_tip.hide()
            if isinstance(widget, (tk.Tk, tk.Toplevel, tk.Canvas)):
                widget.configure(background=p["bg"])
            elif isinstance(widget, tk.Text):
                self._text_colors(widget)
            elif isinstance(widget, ttk.Combobox):
                # Update a popdown that has already been opened in the previous theme.
                popdown = widget.tk.call("ttk::combobox::PopdownWindow", str(widget))
                widget.tk.call(
                    str(popdown) + ".f.l",
                    "configure",
                    "-background",
                    p["card"],
                    "-foreground",
                    p["ink"],
                    "-selectbackground",
                    p["selection"],
                    "-selectforeground",
                    p["selected_ink"],
                )
            for child in widget.winfo_children():
                recolor(child)

        recolor(self.root)
        self.source.tag_configure("finding", background=p["finding"], foreground=p["finding_ink"])
        self.diff.tag_configure("added", foreground=p["added"])
        self.diff.tag_configure("removed", foreground=p["removed"])

    def _text_colors(self, box):
        p = self.colors
        box.configure(
            bg=p["card"],
            fg=p["ink"],
            insertbackground=p["ink"],
            selectbackground=p["selection"],
            selectforeground=p["selected_ink"],
        )

    def open_telegram(self):
        try:
            if not webbrowser.open(TELEGRAM_URL, new=2):
                self.status.set("Не удалось открыть браузер. Ссылка на Telegram есть в README.")
        except OSError:
            self.error("Не удалось открыть браузер. Ссылка на Telegram есть в README.")

    def _build(self):
        header = ttk.Frame(self.root, padding=(24, 12, 24, 12))
        header.pack(fill="x")
        self.logo_original = tk.PhotoImage(master=self.root, file=str(LOGO_PATH))
        factor = max(1, (max(self.logo_original.width(), self.logo_original.height()) + 63) // 64)
        self.logo = self.logo_original.subsample(factor, factor)
        self.root.iconphoto(True, self.logo)
        ttk.Label(header, image=self.logo).pack(side="left", padx=(0, 14))
        title = ttk.Frame(header)
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text="IT SHELTER", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(title, text="Shelter Humanizer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title,
            text="Естественный текст. Ваш стиль.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        links = ttk.Frame(header)
        links.pack(side="right")
        switch = ttk.Frame(links)
        switch.pack(anchor="e", pady=(0, 9))
        for value, label in (("light", "Светлая"), ("dark", "Тёмная")):
            ttk.Radiobutton(
                switch,
                text=label,
                value=value,
                variable=self.theme,
                style="Theme.Toolbutton",
                command=self.apply_theme,
            ).pack(side="left")
        ttk.Button(links, text="Telegram IT Shelter ↗", command=self.open_telegram).pack(
            side="right"
        )
        ttk.Button(links, text="Помощь", command=self.help).pack(side="right", padx=(0, 8))
        body = ttk.Frame(self.root, padding=(24, 0, 24, 0))
        body.pack(fill="both", expand=True)
        content = self.content = ttk.Frame(body)
        content.pack(fill="both", expand=True)
        connection = ttk.Frame(content, style="Card.TFrame", padding=(12, 10))
        connection.pack(fill="x", pady=(0, 12))
        self.mode_note = ttk.Label(connection, style="Hint.TLabel", wraplength=590)
        self.mode_note.pack(side="left", fill="x", expand=True)
        self.settings_button = ttk.Button(
            connection, text="Выбрать ИИ", command=self.processing_settings
        )
        self.settings_button.pack(side="right")
        self.text_settings_button = ttk.Button(
            connection, text="Настройки текста", command=self.text_settings
        )
        self.text_settings_button.pack(side="right", padx=(8, 8))
        connection.bind(
            "<Configure>",
            lambda event: self.mode_note.configure(wraplength=max(230, event.width - 355)),
        )
        toolbar = ttk.Frame(content)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Вставить", command=self.paste).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Открыть TXT", command=self.load).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Пример", command=lambda: self.set_source(SAMPLE)).pack(
            side="left", padx=6
        )
        ttk.Label(toolbar, textvariable=self.count, style="Muted.TLabel").pack(side="right")
        editors = self.editors = ttk.Panedwindow(content, orient="horizontal")
        editors.pack(fill="both", expand=True)
        left = ttk.Frame(editors, style="Card.TFrame", padding=12)
        right = ttk.Frame(editors, style="Card.TFrame", padding=12)
        editors.add(left, weight=1)
        editors.add(right, weight=1)
        editors.bind("<Configure>", lambda event: editors.sashpos(0, event.width // 2))
        source_heading = ttk.Frame(left, style="Card.TFrame", height=40)
        source_heading.pack(fill="x", pady=(0, 8))
        source_heading.pack_propagate(False)
        ttk.Label(source_heading, text="01  /  Исходный текст", style="Editor.TLabel").pack(
            side="left"
        )
        result_heading = ttk.Frame(right, style="Card.TFrame", height=40)
        result_heading.pack(fill="x", pady=(0, 8))
        result_heading.pack_propagate(False)
        self.copy_button = ttk.Button(result_heading, text="Копировать", command=self.copy_result)
        self.copy_button.pack(side="right")
        ttk.Label(result_heading, text="02  /  Результат", style="Editor.TLabel").pack(side="left")
        ttk.Label(left, text="Оригинал останется здесь", style="Hint.TLabel").pack(
            anchor="w", pady=(0, 9)
        )
        result_hint = ttk.Label(
            right, textvariable=self.result_note, wraplength=360, style="Hint.TLabel"
        )
        result_hint.pack(anchor="w", pady=(0, 9))
        right.bind(
            "<Configure>",
            lambda event: result_hint.configure(wraplength=max(160, event.width - 24)),
        )
        self.source = self.text_box(left)
        self.output = self.text_box(right)
        actions = self.actions = ttk.Frame(content)
        actions.pack(fill="x", pady=10)
        self.run_button = ttk.Button(
            actions,
            text="Улучшить текст",
            style="Accent.TButton",
            command=lambda: self.run("edit"),
        )
        self.run_button.pack(side="left", padx=(0, 6))
        self.analyze_button = ttk.Button(
            actions, text="Проверить текст", command=lambda: self.run("analyze")
        )
        self.analyze_button.pack(side="left", padx=4)
        self.clean_button = ttk.Button(
            actions, text="Очистить символы", command=lambda: self.run("clean")
        )
        self.clean_button.pack(side="left", padx=4)
        self.cancel_button = ttk.Button(
            actions, text="Отмена", command=self.cancel_run, state="disabled"
        )
        self.cancel_button.pack(side="right")
        self.review_window = tk.Toplevel(self.root)
        self.review_window.withdraw()
        self.review_window.title("Проверки и изменения")
        self.review_window.geometry("900x460")
        self.review_window.minsize(640, 340)
        self.review_window.transient(self.root)
        self.review_window.protocol("WM_DELETE_WINDOW", lambda: self.toggle_details(False))
        self.tabs = ttk.Notebook(self.review_window)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        self.details_visible = False
        self.unicode_tree = self.findings_tab("Символы")
        self.style_tree = self.findings_tab("Замечания")
        diff_frame = ttk.Frame(self.tabs)
        self.tabs.add(diff_frame, text="Изменения")
        self.diff = self.text_box(diff_frame, height=6)
        self.diff.configure(state="disabled", font=("Consolas", 10))
        self.diff.tag_configure("added", foreground="#087f50")
        self.diff.tag_configure("removed", foreground="#b13333")
        summary_frame = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(summary_frame, text="Проверка смысла")
        self.summary = self.text_box(summary_frame, height=6)
        self.summary.configure(state="disabled")
        bottom = ttk.Frame(content)
        bottom.pack(fill="x", pady=(5, 0))
        ttk.Button(bottom, text="Сохранить TXT", command=self.save).pack(side="left", padx=4)
        self.chat_button = ttk.Button(
            bottom, text="Обработать в ИИ-чате…", command=self.chat_workflow
        )
        self.chat_button.pack(side="left", padx=4)
        self.details_button = ttk.Button(
            bottom, text="Проверки и изменения…", command=self.toggle_details
        )
        self.details_button.pack(side="right")
        details_actions = ttk.Frame(self.review_window, padding=12)
        details_actions.pack(side="bottom", fill="x", before=self.tabs)
        ttk.Button(details_actions, text="Отчёт JSON", command=self.report).pack(side="left")
        ttk.Button(
            details_actions, text="Закрыть", command=lambda: self.toggle_details(False)
        ).pack(side="right")
        ttk.Button(details_actions, text="Проверить правки", command=self.review_result).pack(
            side="right", padx=8
        )
        # Reserve controls before allowing the editors to consume remaining height.
        bottom.pack_configure(side="bottom", before=editors)
        actions.pack_configure(side="bottom", before=editors)
        footer = ttk.Frame(self.root, padding=(24, 10, 24, 14))
        footer.pack(side="bottom", fill="x", before=body)
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel", wraplength=1020).pack(
            side="left"
        )
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=110)
        self.progress.pack(side="right")
        self._mode_changed()

    def text_box(self, parent, height=10):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="both", expand=True)
        box = tk.Text(
            frame,
            wrap="word",
            undo=True,
            height=height,
            width=20,
            font=("Segoe UI", 11),
            relief="flat",
            padx=10,
            pady=10,
            spacing1=2,
            spacing3=5,
        )
        self._text_colors(box)
        scroll = ttk.Scrollbar(frame, command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        box.pack(fill="both", expand=True)
        bind_editing(box)
        return box

    def findings_tab(self, label):
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text=label)
        tree = ttk.Treeview(
            frame, columns=("location", "code", "message"), show="headings", height=4
        )
        for name, title, width in (
            ("location", "Стр.:симв.", 80),
            ("code", "Находка", 105),
            ("message", "Что проверить · нажмите для выделения", 550),
        ):
            tree.heading(name, text=title)
            tree.column(name, width=width, minwidth=60, stretch=name == "message")
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        tree.bind("<<TreeviewSelect>>", lambda event: self.highlight(tree))
        return tree

    def genre_key(self):
        return next(key for key, value in GENRES.items() if value == self.genre.get())

    def depth_key(self):
        return next(key for key, value in EDIT_CHOICES.items() if value == self.depth.get())

    @staticmethod
    def get_text(widget):
        return widget.get("1.0", "end-1c")

    def set_source(self, text):
        self.source.delete("1.0", "end")
        self.source.insert("1.0", text)
        self.source.edit_reset()
        self.source.edit_modified(False)
        self.revision += 1
        self.findings.clear()
        self.analysis_snapshot = None
        self.count.set(f"{len(text):,} символов")
        self.status.set("Исходник обновлён. Запустите обработку.")

    def _source_changed(self, event=None):
        if self.source.edit_modified():
            self.source.edit_modified(False)
            self.revision += 1
            self.count.set(f"{len(self.get_text(self.source)):,} символов")
            self.source.tag_remove("finding", "1.0", "end")
            self.findings.clear()
            self.analysis_snapshot = None
            if self.result is not None:
                self.status.set(
                    "Исходник изменён. Показанный результат относится к предыдущему тексту."
                )

    def _mode_changed(self):
        mode = self.mode.get()
        if mode == "offline":
            note = "Без ИИ · Небольшие правки. Для переписывания выберите ИИ."
        else:
            config = self.configs[mode]
            if mode == "ollama":
                note = f"ИИ на компьютере · {config.model or 'Модель пока не выбрана'}"
            else:
                host = urllib.parse.urlsplit(config.url).hostname or "сервис"
                note = f"ИИ через API · {config.model or 'Настройте подключение'} · {host[:65]}"
        self.mode_note.configure(text=note)
        detail = "Сейчас: без ИИ. Установка не требуется." if mode == "offline" else note
        if mode != "offline":
            config = self.configs[mode]
            detail += f"\n\nАдрес: {config.url}\nМодель: {config.model or 'не выбрана'}"
        self.connection_note.set(detail)
        self.run_button.configure(text="Улучшить текст")
        self.settings_button.configure(state="disabled" if self.busy else "normal")
        self.text_settings_button.configure(state="disabled" if self.busy else "normal")
        self.chat_button.configure(state="disabled" if self.busy else "normal")
        if hasattr(self, "connection_button") and self.connection_button.winfo_exists():
            self.connection_button.configure(
                state="disabled" if mode == "offline" or self.busy else "normal"
            )

    def toggle_details(self, show=None):
        hide_tooltips(self.root)
        self.details_visible = True if show is None else bool(show)
        if self.details_visible:
            self.review_window.deiconify()
            self.review_window.lift()
        else:
            self.review_window.withdraw()

    def dialog(self, title, geometry):
        hide_tooltips(self.root)
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry(geometry)
        window.configure(background=self.colors["bg"])
        window.transient(self.root)
        window.grab_set()
        return window

    def processing_settings(self):
        if self.busy:
            return
        window = self.dialog("Способ обработки", "620x475")
        frame = ttk.Frame(window, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Как обрабатывать текст", style="Heading.TLabel").pack(anchor="w")
        for key, title, hint in (
            (
                "offline",
                "Без ИИ",
                "Небольшие правки по правилам и проверка символов. Работает сразу.",
            ),
            (
                "ollama",
                "ИИ на компьютере",
                "Переписывание локальной моделью. Установка через кнопку ниже.",
            ),
            (
                "api",
                "ИИ-сервис по ключу",
                "Для тех, у кого есть API-ключ. Текст отправляется выбранному сервису.",
            ),
        ):
            ttk.Radiobutton(
                frame, text=title, value=key, variable=self.mode, command=self.select_mode
            ).pack(anchor="w", pady=(12, 0))
            ttk.Label(frame, text=hint, wraplength=570, style="Muted.TLabel").pack(
                anchor="w", padx=22
            )
        ttk.Separator(frame).pack(fill="x", pady=12)
        ttk.Label(
            frame, textvariable=self.connection_note, wraplength=570, style="Muted.TLabel"
        ).pack(fill="x")
        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(15, 0))

        def open_next(action):
            window.destroy()
            action()

        ttk.Button(
            buttons, text="Установить локальный ИИ", command=lambda: open_next(self.setup_local)
        ).pack(side="left")
        self.connection_button = ttk.Button(
            buttons, text="Настроить подключение", command=lambda: open_next(self.settings)
        )
        self.connection_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="Готово", command=window.destroy).pack(side="right")
        self._mode_changed()
        attach_help(window, lambda: self.colors)

    def text_settings(self):
        if self.busy:
            return
        window = self.dialog("Настройки текста", "610x565")
        frame = ttk.Frame(window, padding=22)
        frame.pack(fill="both", expand=True)
        tabs = ttk.Notebook(frame)
        tabs.pack(fill="both", expand=True)
        text = ttk.Frame(tabs, padding=18)
        symbols = ttk.Frame(tabs, padding=18)
        tabs.add(text, text="Текст и стиль")
        tabs.add(symbols, text="Очистка символов")
        ttk.Label(text, text="Тип текста", style="Heading.TLabel").pack(anchor="w", pady=(0, 7))
        ttk.Combobox(
            text, textvariable=self.genre, values=tuple(GENRES.values()), state="readonly"
        ).pack(fill="x")
        ttk.Label(text, text="Что изменить", style="Heading.TLabel").pack(anchor="w", pady=(20, 7))
        ttk.Combobox(
            text, textvariable=self.depth, values=tuple(EDIT_CHOICES.values()), state="readonly"
        ).pack(fill="x")
        ttk.Label(
            text,
            text="Небольшие правки сохраняют формулировки. Переписывание меняет предложения, сохраняя содержание. Этот выбор действует при работе с ИИ.",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(fill="x", pady=8)

        def style_dialog():
            window.destroy()
            self.voice_settings()

        ttk.Button(text, text="Стиль и важные слова", command=style_dialog).pack(
            fill="x", pady=(12, 5)
        )
        ttk.Label(
            text,
            text="Добавьте свой текст как образец стиля и слова, которые нельзя менять. Необязательно.",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(fill="x", pady=5)
        ttk.Checkbutton(text, text="Дополнительная ИИ-вычитка", variable=self.second_pass).pack(
            anchor="w", pady=(15, 0)
        )
        ttk.Label(
            text,
            text="Второй запрос: дольше и, при работе через API, может стоить дороже.",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(fill="x", pady=5)
        ttk.Label(
            symbols,
            text="Скрытые знаки убираются автоматически. Замена спецпробелов и нормализация букв уже включены; любую опцию можно отключить.",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(fill="x", pady=(0, 20))
        for label, variable, hint in (
            (
                "Спецпробелы → обычные",
                self.spaces,
                "Полезно после копирования; типографические пробелы иногда нужны.",
            ),
            (
                "Похожие латинские буквы",
                self.confusables,
                "Замена похожих букв в смешанных русских словах. Для других языков оставьте выключенной.",
            ),
            (
                "Нормализация NFC",
                self.nfc,
                "Стандартное представление составных букв. Это не удаление кириллицы или emoji.",
            ),
        ):
            ttk.Checkbutton(symbols, text=label, variable=variable).pack(anchor="w", pady=(10, 0))
            ttk.Label(symbols, text=hint, wraplength=480, style="Muted.TLabel").pack(
                fill="x", padx=22
            )
        ttk.Label(
            symbols,
            text="Цитаты, ссылки и код защищены. Настройки применяются к очистке, правкам без ИИ и результату ИИ-редактуры в приложении.",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(fill="x", pady=20)
        ttk.Button(frame, text="Готово", command=window.destroy).pack(anchor="e", pady=(12, 0))
        attach_help(window, lambda: self.colors)

    def chat_workflow(self):
        if self.busy:
            return
        window = self.dialog("Обработать текст в ИИ-чате", "650x525")
        frame = ttk.Frame(window, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="Через привычный ИИ-чат — без API-ключа", style="Heading.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Приложение подготовит задание вместе с вашим текстом. Вы отправите его в чат самостоятельно.",
            wraplength=590,
            style="Muted.TLabel",
        ).pack(fill="x", pady=(8, 18))
        note = tk.StringVar(master=self.root, value="Начните с копирования задания.")
        ttk.Label(frame, text="1. Скопируйте задание", style="Heading.TLabel").pack(anchor="w")

        def copy_task():
            if self.prompt():
                note.set("Задание скопировано. Теперь вставьте его в ИИ-чат и отправьте.")

        ttk.Button(frame, text="Копировать задание с текстом", command=copy_task).pack(
            anchor="w", pady=8
        )
        ttk.Label(
            frame, text="2. Отправьте его в ChatGPT или другой ИИ-чат", style="Heading.TLabel"
        ).pack(anchor="w", pady=(12, 0))
        ttk.Label(
            frame,
            text="Вставьте скопированное, отправьте сообщение и скопируйте полученный ответ. При переходе на сайт текст не отправляется автоматически.",
            wraplength=590,
            style="Muted.TLabel",
        ).pack(fill="x", pady=6)

        def open_chat():
            try:
                opened = webbrowser.open("https://chatgpt.com/", new=2)
            except OSError:
                opened = False
            note.set(
                "Вставьте задание в чат и отправьте, затем скопируйте ответ."
                if opened
                else "Откройте ChatGPT или другой ИИ-чат в своём браузере и вставьте задание."
            )

        ttk.Button(frame, text="Открыть ChatGPT ↗", command=open_chat).pack(anchor="w")
        ttk.Label(frame, text="3. Верните ответ в приложение", style="Heading.TLabel").pack(
            anchor="w", pady=(18, 6)
        )

        def paste_answer():
            try:
                original = self.get_text(self.source)
                if not original.strip():
                    raise ValueError("Сначала вставьте исходный текст слева.")
                if self.last_chat_source is not None and original != self.last_chat_source:
                    raise ValueError(
                        "Исходник изменился после копирования задания. Скопируйте новое задание и получите ответ для него."
                    )
                answer = read_clipboard(self.root)
                validate_text(answer)
                if not answer.strip():
                    raise ValueError("Скопируйте ответ ИИ-чата перед вставкой.")
                if answer == self.last_chat_prompt:
                    raise ValueError("В буфере ещё задание. Сначала скопируйте ответ из ИИ-чата.")
                answer = clean_unicode(
                    answer, CleanOptions(self.spaces.get(), self.confusables.get(), self.nfc.get())
                )
                self.replace_text(self.output, answer)
                self.review_result()
                self.result_note.set("Ответ из ИИ-чата. Проверьте смысл, числа и цитаты.")
                window.destroy()
            except (tk.TclError, ValueError) as exc:
                messagebox.showerror(
                    "Проверьте буфер обмена",
                    str(exc) if isinstance(exc, ValueError) else "В буфере обмена нет текста.",
                    parent=window,
                )

        ttk.Button(frame, text="Вставить ответ в результат", command=paste_answer).pack(anchor="w")
        ttk.Label(frame, textvariable=note, wraplength=590, style="Muted.TLabel").pack(
            fill="x", pady=12
        )
        attach_help(window, lambda: self.colors)

    def select_mode(self):
        if self.closed:
            return
        self._mode_changed()
        model = self.runtime.selected_model()
        if self.mode.get() != "ollama" or not model or self.busy:
            return
        if not self.managed_connection and self.configs["ollama"].model:
            return  # Preserve an explicitly configured external Ollama connection.
        if self.runtime.process is not None and self.runtime.process.poll() is None:
            self.configs["ollama"] = ProviderConfig(url=self.runtime.url, model=model, timeout=180)
            self.managed_connection = True
            self._mode_changed()
            return
        events, runtime = self.events, self.runtime
        self.cancel = threading.Event()
        cancel = self.cancel
        self.set_busy(True)
        self.status.set("Запускаем установленный локальный ИИ…")

        def worker():
            try:
                url = runtime.start(cancel, lambda message: None)
                events.put(("managed", 0, (model, url, "")))
            except Exception:
                events.put(
                    (
                        "managed",
                        0,
                        (
                            model,
                            "",
                            "Не удалось запустить локальный ИИ. Нажмите «Выбрать ИИ» → «Установить локальный ИИ» для повторной настройки.",
                        ),
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def paste(self):
        try:
            text = read_clipboard(self.root)
            validate_text(text)
            self.set_source(text)
        except (tk.TclError, ValueError) as exc:
            self.error(
                str(exc)
                if isinstance(exc, ValueError)
                else "В буфере обмена нет доступного текста."
            )

    def load(self):
        path = filedialog.askopenfilename(
            parent=self.root, filetypes=[("Текст UTF-8", "*.txt"), ("Все файлы", "*")]
        )
        if path:
            try:
                self.set_source(read_text(path))
            except (ValueError, OSError) as exc:
                self.error(str(exc))

    def set_busy(self, value):
        self.busy = value
        for button in (self.run_button, self.clean_button, self.analyze_button):
            button.configure(state="disabled" if value else "normal")
        self.cancel_button.configure(state="normal" if value else "disabled")
        if value:
            self.progress.start(12)
        else:
            self.progress.stop()
        self._mode_changed()

    def run(self, action):
        if self.busy:
            return
        text = self.get_text(self.source)
        genre = self.genre_key()
        depth = self.depth_key()
        mode = self.mode.get()
        options = CleanOptions(self.spaces.get(), self.confusables.get(), self.nfc.get())
        config, voice, terms = self.configs.get(mode), self.voice, self.terms
        second_pass = self.second_pass.get()
        try:
            validate_text(text)
            if not text.strip():
                raise ValueError("Сначала вставьте текст или откройте пример.")
            if action == "edit" and mode != "offline":
                config.validate()
        except ValueError as exc:
            self.error(str(exc))
            return
        revision = self.revision
        self.cancel = threading.Event()
        cancel = self.cancel
        events = self.events
        self.set_busy(True)
        self.status.set("Обработка…")

        def worker():
            try:
                if action == "analyze":
                    value = analyze(text, genre)
                elif action == "clean" or mode == "offline":
                    value = offline(text, "clean" if action == "clean" else "light", genre, options)
                else:
                    active_config = config
                    if mode == "ollama" and self.managed_connection:
                        url = self.runtime.start(
                            cancel, lambda message: events.put(("progress", revision, message))
                        )
                        active_config = ProviderConfig(url=url, model=config.model, timeout=180)
                    value = rewrite(
                        text,
                        Client(active_config),
                        genre=genre,
                        voice=voice,
                        terms=terms,
                        second_pass=second_pass,
                        depth=depth,
                        options=options,
                        cancel=cancel,
                        progress=lambda message: events.put(("progress", revision, message)),
                    )
                if cancel.is_set():
                    raise Cancelled("Обработка отменена. Исходник сохранён.")
                events.put(("done", revision, (action, value)))
            except (ValueError, OSError) as exc:
                events.put(("error", revision, str(exc)))
            except Exception:
                # Do not expose provider response bodies or secrets in a traceback.
                events.put(
                    ("error", revision, "Не удалось завершить обработку. Исходник сохранён.")
                )

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        # Keep one timer even when a test or embedded host explicitly polls.
        self.root.after_cancel(self.poll_id)
        try:
            while True:
                kind, revision, value = self.events.get_nowait()
                if kind == "managed":
                    model, url, error = value
                    if self.cancel.is_set():
                        self.set_busy(False)
                        self.status.set("Запуск локального ИИ отменён.")
                        continue
                    if not error:
                        self.configs["ollama"] = ProviderConfig(url=url, model=model, timeout=180)
                        self.managed_connection = True
                    self.set_busy(False)
                    self.status.set(error or "Локальный ИИ готов к работе.")
                    continue
                if kind == "models":
                    token, models, error = value
                    request = self.model_requests.pop(token, None)
                    if request is not None:
                        window, chooser, note, button = request
                    if request is not None and window.winfo_exists():
                        button.configure(state="normal")
                        if error:
                            note.set(error)
                        else:
                            chooser.configure(values=models)
                            note.set(
                                f"Доступно моделей: {len(models)}. Выберите из списка или впишите имя."
                            )
                            if len(models) == 1:
                                chooser.set(models[0])
                    continue
                if kind == "progress":
                    if revision == self.revision and not self.cancel.is_set():
                        self.status.set(value)
                    continue
                self.set_busy(False)
                if self.cancel.is_set():
                    self.status.set(
                        "Обработка отменена. Исходник и предыдущий результат сохранены."
                    )
                    continue
                if revision != self.revision:
                    self.status.set(
                        "Исходник изменился во время обработки. Устаревший результат не применён."
                    )
                    continue
                if kind == "error":
                    self.status.set(value)
                    if not self.cancel.is_set():
                        self.error(value)
                else:
                    action, result = value
                    if action == "analyze":
                        source = self.get_text(self.source)
                        self.analysis_snapshot = Result(source, source, "analyze", result, result)
                        self.show_findings(result)
                        self.toggle_details(True)
                        self.tabs.select(0)
                        self.status.set(
                            f"Проверено: символы — {len(result.unicode)}, стиль — {len(result.style)}. Исходник сохранён."
                        )
                    else:
                        self.show_result(result)
        except queue.Empty:
            pass
        self.poll_id = self.root.after(80, self._poll)

    def cancel_run(self):
        self.cancel.set()
        config = self.configs.get(self.mode.get())
        timeout = int(config.timeout) if config is not None else 60
        self.status.set(
            f"Отмена запрошена. Сетевой запрос может ждать до {timeout} секунд; результат не будет применён."
        )

    def show_findings(self, analysis):
        self.findings.clear()
        for tree, items, name in (
            (self.unicode_tree, analysis.unicode, "u"),
            (self.style_tree, analysis.style, "s"),
        ):
            tree.delete(*tree.get_children())
            for idx, item in enumerate(items[:300]):
                identifier = f"{name}{idx}"
                self.findings[identifier] = item
                tree.insert(
                    "",
                    "end",
                    iid=identifier,
                    values=(f"{item.line}:{item.column}", item.code, item.message),
                )
            if len(items) > 300:
                tree.insert(
                    "",
                    "end",
                    values=(
                        "",
                        "…",
                        f"Первые 300 из {len(items)} находок. Полный список — в отчёте JSON.",
                    ),
                )

    def highlight(self, tree):
        selection = tree.selection()
        item = self.findings.get(selection[0]) if selection else None
        if item is None:
            return

        # Text's +N chars advances Unicode characters, unlike Tcl string length.
        def index(offset):
            return f"1.0 + {offset} chars"

        self.source.tag_remove("finding", "1.0", "end")
        self.source.tag_add("finding", index(item.start), index(item.end))
        self.source.see(index(item.start))

    @staticmethod
    def replace_text(widget, text, readonly=False):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        if readonly:
            widget.configure(state="disabled")

    def show_result(self, result):
        self.result = result
        self.analysis_snapshot = None
        self.replace_text(self.output, result.text)
        self.output.edit_reset()
        self.show_findings(result.before)
        self.update_review(result)
        if result.original == result.text:
            self.result_note.set(
                "Без изменений. Для перефразирования выберите ИИ или обработку в ИИ-чате."
                if result.mode in ("light", "clean")
                else "Текст не изменился. Можно править вручную."
            )
        else:
            self.result_note.set("Готово. Сравните правки; текст можно редактировать.")
        if any(
            not warning.startswith("Сравните смысл и авторский тон") for warning in result.warnings
        ):
            self.result_note.set(
                "Есть замечания к изменениям смысла. Проверьте их перед копированием."
            )
            self.toggle_details(True)
            self.tabs.select(3)
        self.status.set(
            f"Готово. Символы: {len(result.before.unicode)} → {len(result.after.unicode)}; "
            f"стиль: {len(result.before.style)} → {len(result.after.style)}. Исходник сохранён."
        )

    def update_review(self, result):
        lines = list(
            difflib.unified_diff(
                result.original.splitlines(),
                result.text.splitlines(),
                fromfile="Исходник",
                tofile="Результат",
                lineterm="",
            )
        )
        self.replace_text(self.diff, "\n".join(lines) if lines else "Текст не изменился.")
        for idx, line in enumerate(lines, 1):
            if line.startswith("+") and not line.startswith("+++"):
                self.diff.tag_add("added", f"{idx}.0", f"{idx}.end")
            elif line.startswith("-") and not line.startswith("---"):
                self.diff.tag_add("removed", f"{idx}.0", f"{idx}.end")
        self.diff.configure(state="disabled")
        note = (
            "\n".join(result.warnings)
            or "Числа, ссылки и проверяемые оговорки совпадают. Это не полная проверка смысла: прочитайте результат."
        )
        self.replace_text(
            self.summary,
            note + "\n\nНаходки стиля и Unicode не доказывают, что текст написан ИИ.",
            readonly=True,
        )

    def current_result(self):
        if self.result is None:
            raise ValueError(
                "Сначала обработайте текст. Ответ из внешнего чата можно вставить справа и нажать «Проверить правки»."
            )
        return compare(
            self.result.original, self.get_text(self.output), self.genre_key(), self.result.mode
        )

    def review_result(self):
        try:
            original = self.get_text(self.source)
            self.result = compare(original, self.get_text(self.output), self.genre_key())
            self.update_review(self.result)
            self.toggle_details(True)
            self.tabs.select(3)
            self.status.set(
                "Правки проверены по исходнику. Замечания — во вкладке «Проверка смысла»."
            )
        except ValueError as exc:
            self.error(str(exc))

    def clipboard(self, text):
        try:
            write_clipboard(self.root, text)
        except tk.TclError as exc:
            self.error(str(exc))
            return False
        self.status.set("Скопировано в буфер обмена.")
        return True

    def copy_result(self):
        text = self.get_text(self.output)
        if text.strip():
            self.clipboard(text)
        else:
            self.error("Справа пока нет результата.")

    def prompt(self):
        try:
            text = self.get_text(self.source)
            validate_text(text, 12_000)
            if not text.strip():
                raise ValueError("Сначала вставьте текст.")
            task = copy_prompt(
                text, self.genre_key(), self.voice, depth=self.depth_key(), terms=self.terms
            )
            self.last_chat_prompt = task
            self.last_chat_source = text
            if not self.clipboard(task):
                return False
            self.status.set(
                "Задание скопировано. Вставьте его в ИИ-чат, затем верните ответ в результат."
            )
            return True
        except ValueError as exc:
            self.error(str(exc))
            return False

    def save(self):
        text = self.get_text(self.output)
        if not text:
            self.error("Справа пока нет результата.")
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".txt", filetypes=[("Текст UTF-8", "*.txt")]
        )
        if path:
            try:
                write_text(path, text)
                self.status.set("Результат сохранён в UTF-8 TXT.")
            except (ValueError, OSError) as exc:
                self.error(str(exc))

    def report(self):
        try:
            result = self.analysis_snapshot
            if result is None and self.result is not None:
                result = self.current_result()
            if result is None:
                raise ValueError("Сначала проверьте или обработайте текст.")
        except ValueError as exc:
            self.error(str(exc))
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Отчёт содержит исходник и результат",
            defaultextension=".json",
            filetypes=[("Отчёт с текстами", "*.json")],
        )
        if path:
            try:
                write_report(path, result)
                self.status.set("Отчёт сохранён. Он содержит оба текста и находки, без API-ключа.")
            except (ValueError, OSError) as exc:
                self.error(str(exc))

    def settings(self):
        hide_tooltips(self.root)
        kind = self.mode.get()
        if kind == "offline" or self.busy:
            return
        config = self.configs[kind]
        window = tk.Toplevel(self.root)
        window.configure(background=self.colors["bg"])
        window.title("Подключение к ИИ")
        window.geometry("620x460")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        url, model, key = (
            tk.StringVar(value=config.url),
            tk.StringVar(value=config.model),
            tk.StringVar(value=config.key),
        )
        for label, var, hidden in (
            ("Адрес сервера (base URL)", url, False),
            ("Точное имя модели", model, False),
            ("API-ключ · только до закрытия приложения", key, True),
        ):
            if hidden and kind == "ollama":
                continue
            ttk.Label(frame, text=label).pack(anchor="w", pady=(7, 3))
            if var is model:
                chooser = ttk.Combobox(frame, textvariable=var)
                chooser.pack(fill="x")
            else:
                ttk.Entry(frame, textvariable=var, show="•" if hidden else "").pack(fill="x")
        model_note = tk.StringVar(
            value="Имя можно вписать вручную или запросить список без отправки текста."
        )

        def get_models():
            candidate = ProviderConfig(kind, url.get(), model.get(), key.get())
            try:
                candidate.base_url()
            except ValueError as exc:
                model_note.set(str(exc))
                return
            models_button.configure(state="disabled")
            model_note.set("Запрашиваем список моделей… Текст не отправляется.")
            token = object()
            self.model_requests[token] = (window, chooser, model_note, models_button)
            events = self.events

            def forget_request(event):
                if event.widget is window:
                    self.model_requests.pop(token, None)

            window.bind("<Destroy>", forget_request, add="+")

            def worker():
                try:
                    models, error = Client(candidate).models(), ""
                except ValueError as exc:
                    models, error = [], str(exc)
                except Exception:
                    models, error = [], "Не удалось загрузить список; имя можно вписать вручную."
                # The worker owns no Tk widget or variable, even if the dialog closes.
                events.put(("models", 0, (token, models, error)))

            threading.Thread(target=worker, daemon=True).start()

        models_button = ttk.Button(frame, text="Показать доступные модели", command=get_models)
        models_button.pack(anchor="w", pady=(8, 2))
        ttk.Label(frame, textvariable=model_note, wraplength=575, style="Muted.TLabel").pack(
            anchor="w"
        )
        note = (
            "Ollama: сначала установите и загрузите модель с ollama.com. "
            "Адрес по умолчанию: http://localhost:11434. Имя модели — из ollama list."
            if kind == "ollama"
            else "OpenRouter: https://openrouter.ai/api/v1. Подойдёт также другой OpenAI-compatible сервис. "
            "Не добавляйте /chat/completions. Сервис получает текст и образец стиля."
        )
        ttk.Label(frame, text=note, wraplength=575, style="Muted.TLabel").pack(fill="x", pady=12)
        ttk.Label(
            frame,
            text="Настройки действуют в текущем запуске. Ключ не сохраняется на диск.",
            wraplength=575,
            style="Muted.TLabel",
        ).pack(anchor="w")

        def save():
            candidate = ProviderConfig(
                kind, url.get(), model.get().strip(), key.get().strip(), config.timeout
            )
            try:
                candidate.validate()
                self.configs[kind] = candidate
                if kind == "ollama":
                    self.managed_connection = False
                self._mode_changed()
                window.destroy()
                self.status.set("Подключение настроено для текущего запуска.")
            except ValueError as exc:
                messagebox.showerror("Проверьте настройки", str(exc), parent=window)

        ttk.Button(frame, text="Применить", style="Accent.TButton", command=save).pack(
            anchor="e", pady=12
        )
        attach_help(window, lambda: self.colors)

    def setup_local(self):
        hide_tooltips(self.root)
        if self.busy:
            self.status.set("Дождитесь завершения текущей обработки перед настройкой ИИ.")
            return
        if self.local_wizard is not None and not self.local_wizard.closed:
            self.local_wizard.window.lift()
            return

        def ready(model, url):
            self.configs["ollama"] = ProviderConfig(url=url, model=model, timeout=180)
            self.managed_connection = True
            self.mode.set("ollama")
            self._mode_changed()
            self.status.set(f"Локальная модель выбрана: {model}. Можно запускать ИИ-редактуру.")

        self.local_wizard = LocalSetup(self.root, lambda: self.colors, ready, self.runtime)

    def voice_settings(self):
        hide_tooltips(self.root)
        window = tk.Toplevel(self.root)
        window.configure(background=self.colors["bg"])
        window.title("Стиль и важные слова")
        window.geometry("650x530")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Образец вашего текста · необязательно, до 3000 символов").pack(
            anchor="w", pady=6
        )
        voice = self.text_box(frame, height=8)
        voice.insert("1.0", self.voice)
        ttk.Label(frame, text="Сохранять буквально · одно имя / термин на строку").pack(
            anchor="w", pady=6
        )
        terms = self.text_box(frame, height=5)
        terms.insert("1.0", "\n".join(self.terms))
        ttk.Label(
            frame,
            text="Применяется к ИИ-режимам. Образец стиля отправляется выбранной модели.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=9)

        def save():
            sample = self.get_text(voice)
            words = tuple(
                line.strip() for line in self.get_text(terms).splitlines() if line.strip()
            )
            try:
                validate_text(sample, 3000)
                if len(words) > 100 or any(len(word) > 200 for word in words):
                    raise ValueError("Не больше 100 терминов по 200 символов.")
                self.voice, self.terms = sample, words
                window.destroy()
            except ValueError as exc:
                messagebox.showerror("Проверьте текст", str(exc), parent=window)

        ttk.Button(frame, text="Применить", command=save).pack(anchor="e")
        attach_help(window, lambda: self.colors)

    def help(self):
        messagebox.showinfo(
            "Как пользоваться",
            "1. Вставьте текст или откройте пример.\n"
            "2. Нажмите «Улучшить текст» и скопируйте результат.\n"
            "3. Без ИИ выполняются небольшие правки. Для переписывания нажмите «Выбрать ИИ».\n"
            "4. «Настройки текста» позволяют выбрать тип правок и задать свой стиль.\n"
            "5. «Обработать в ИИ-чате» объясняет, как получить ответ в привычном чате.\n\n"
            "Unicode — нормальная часть текста. Тире, кавычки, emoji и ударения сохраняются. "
            "Программа ищет конкретные подозрительные знаки, а не «доказательства ИИ».\n\n"
            "Процент уникальности по интернету и обход детекторов не проверяются. "
            "История автоматически не сохраняется.",
            parent=self.root,
        )

    def error(self, message):
        messagebox.showerror("Shelter Humanizer", message, parent=self.root)

    def close(self):
        self.closed = True
        self.cancel.set()
        if self.local_wizard is not None:
            self.local_wizard.close()
        self.runtime.close()
        self.root.after_cancel(self.poll_id)
        self.model_requests.clear()
        self.root.update_idletasks()
        self.root.destroy()


def launch():
    root = tk.Tk()
    App(root)
    root.mainloop()
