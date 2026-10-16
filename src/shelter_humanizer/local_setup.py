"""One-click private Windows AI installation, with explicit consent and progress."""

import queue
import re
import shutil
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .local_models import DEFAULT_MODEL, PRESETS, DownloadCancelled, model_tag, pull_model
from .managed_runtime import CombinedCancel, check_cancel
from .model_import import import_gguf, inspect_gguf
from .panels import ScrollArea, fit_dialog
from .providers import Client, ProviderConfig, ProviderError
from .ui_helpers import Tooltip, attach_help, hide_tooltips


class LocalSetup:
    def __init__(self, root, colors, on_ready, runtime):
        self.colors, self.on_ready, self.runtime = colors, on_ready, runtime
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy, self.closed = False, False
        self.window = window = tk.Toplevel(root)
        window.configure(background=colors()["bg"])
        window.title("ИИ на компьютере · простая установка")
        fit_dialog(window, 720, 680)
        window.transient(root)
        window.grab_set()
        window.protocol("WM_DELETE_WINDOW", self.close)
        footer = ttk.Frame(window, padding=14)
        footer.pack(side="bottom", fill="x")
        bottom = ttk.Frame(footer)
        bottom.pack(side="bottom", fill="x", pady=(8, 0))
        area = ScrollArea(window)
        area.pack(fill="both", expand=True)
        frame = area.body
        ttk.Label(
            frame, text="Выберите модель — остальное сделает приложение", style="Heading.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Shelter Humanizer скачает компоненты Ollama, проверит их, установит выбранную модель и включит ИИ. Команды и отдельный установщик не нужны.",
            wraplength=610,
            style="Muted.TLabel",
        ).pack(fill="x", pady=12)
        selected = runtime.selected_model()
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill="both", expand=True)
        recommended = ttk.Frame(self.notebook, padding=8)
        custom = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(recommended, text="Рекомендованные")
        self.notebook.add(custom, text="Своя модель")
        self.choice = tk.StringVar(
            master=window,
            value=selected if selected in {p.name for p in PRESETS} else DEFAULT_MODEL,
        )
        self.model_note = tk.StringVar(master=window)
        self.radios = []
        for preset in PRESETS:
            radio = ttk.Radiobutton(
                recommended,
                text=f"{preset.label} · {preset.size}",
                value=preset.name,
                variable=self.choice,
                command=self.selection_changed,
            )
            radio.pack(anchor="w", pady=5)
            self.radios.append(radio)
            radio.shelter_tip = Tooltip(radio, preset.description, colors)
        ttk.Label(
            recommended, textvariable=self.model_note, wraplength=570, style="Muted.TLabel"
        ).pack(fill="x", pady=10)
        self.custom_source = tk.StringVar(master=window, value="catalog")
        self.custom_name = tk.StringVar(master=window)
        self.gguf_path = tk.StringVar(master=window)
        self.custom_controls = []
        for value, label in (
            ("catalog", "Скачать из каталога Ollama"),
            ("installed", "Выбрать уже установленную"),
            ("gguf", "Импортировать один GGUF-файл"),
        ):
            radio = ttk.Radiobutton(custom, text=label, variable=self.custom_source, value=value)
            radio.pack(anchor="w", pady=3)
            self.custom_controls.append(radio)
        ttk.Label(custom, text="Точное имя: author/model:tag или my-model:latest").pack(
            anchor="w", pady=(10, 3)
        )
        self.custom_chooser = ttk.Combobox(custom, textvariable=self.custom_name)
        self.custom_chooser.pack(fill="x")
        self.custom_controls.append(self.custom_chooser)
        tools = ttk.Frame(custom)
        tools.pack(fill="x", pady=8)
        ttk.Button(
            tools,
            text="Каталог Ollama ↗",
            command=lambda: webbrowser.open("https://ollama.com/search"),
        ).pack(side="left")
        listing = ttk.Button(tools, text="Модели встроенной Ollama", command=self.list_models)
        listing.pack(side="left", padx=8)
        self.custom_controls.append(listing)
        browse = ttk.Button(custom, text="Выбрать GGUF-файл…", command=self.choose_gguf)
        browse.pack(anchor="w", pady=5)
        self.custom_controls.append(browse)
        ttk.Label(custom, textvariable=self.gguf_path, wraplength=570, style="Muted.TLabel").pack(
            fill="x"
        )
        ttk.Label(
            custom,
            text="Нужна текстовая модель, совместимая с Ollama. Размер и качество произвольной модели не проверены IT Shelter. Импортируйте GGUF только из источника, которому доверяете.",
            wraplength=570,
        ).pack(fill="x", pady=8)
        if selected and selected not in {p.name for p in PRESETS}:
            self.custom_name.set(selected)
            self.custom_source.set("installed")
            self.notebook.select(custom)
        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(
            frame,
            text="Первый запуск: компоненты ≈1,4 ГБ + выбранная модель. Нужно от 8 ГБ свободного места; большая модель потребует больше. После загрузки интернет не нужен для редактуры.",
            wraplength=610,
        ).pack(fill="x", pady=8)
        ttk.Label(
            frame,
            text="Файлы остаются в папке данных Shelter Humanizer. ИИ запускается только для приложения и выключается при его закрытии. Уже установленная Ollama не затрагивается.",
            wraplength=610,
            style="Muted.TLabel",
        ).pack(fill="x", pady=8)
        self.note = tk.StringVar(
            master=window, value="Ничего не устанавливается без вашего подтверждения."
        )
        ttk.Label(footer, textvariable=self.note, wraplength=610).pack(fill="x", pady=4)
        self.bar = ttk.Progressbar(footer, mode="indeterminate")
        self.bar.pack(fill="x", pady=5)
        self.install_button = ttk.Button(
            bottom, text="Установить и включить ИИ", style="Accent.TButton", command=self.download
        )
        self.install_button.pack(side="right")
        self.cancel_button = ttk.Button(
            bottom, text="Отменить", command=self.cancel_download, state="disabled"
        )
        self.cancel_button.pack(side="left")
        self.selection_changed()
        attach_help(frame, colors)
        self.poll_id = window.after(80, self.poll)

    def selection_changed(self):
        preset = next(p for p in PRESETS if p.name == self.choice.get())
        self.model_note.set(preset.description)

    def set_busy(self, busy):
        self.busy = busy
        self.install_button.configure(state="disabled" if busy else "normal")
        for radio in self.radios:
            radio.configure(state="disabled" if busy else "normal")
        for control in self.custom_controls:
            control.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.bar.start(15) if busy else self.bar.stop()

    def download(self):
        if self.busy:
            return
        if self.notebook.index("current") == 1:
            try:
                model = model_tag(self.custom_name.get().strip())
                source = self.custom_source.get()
                path = self.gguf_path.get() if source == "gguf" else None
                if path:
                    inspect_gguf(path)
                elif source == "gguf":
                    raise ProviderError("Сначала выберите GGUF-файл.")
            except (ValueError, OSError) as exc:
                self.note.set(str(exc))
                return
            action = {
                "catalog": "скачает модель из каталога Ollama",
                "installed": "включит уже установленную модель",
                "gguf": "скопирует GGUF в локальное хранилище и импортирует модель",
            }[source]
            if messagebox.askyesno(
                "Подключить свою модель?",
                f"Модель: {model}.\n\nПриложение {action}. При необходимости установятся компоненты Ollama. Файлы займут место на диске. Текст редактора не отправляется.\n\nПродолжить?",
                parent=self.window,
            ):
                self.start(model, custom=True, gguf=path, installed_only=source == "installed")
            return
        preset = next(p for p in PRESETS if p.name == self.choice.get())
        hide_tooltips(self.window)
        if not messagebox.askyesno(
            "Установить и включить локальный ИИ?",
            f"Модель: {preset.label} ({preset.size}).\n\nПриложение загрузит переносимую Ollama (≈1,4 ГБ, если её ещё нет) и модель. Файлы сохранятся в:\n{self.runtime.directory}\n\nИИ будет работать только для Shelter Humanizer и выключится при закрытии. Системные службы и автозапуск не добавляются. Ваш текст при установке не передаётся.\n\nНачать?",
            parent=self.window,
        ):
            return
        self.start(preset.name)

    def start(self, model, *, custom=False, gguf=None, installed_only=False):
        if self.busy:
            return
        self.cancel = threading.Event()
        token = CombinedCancel(self.cancel, self.runtime.closed)
        events, runtime = self.events, self.runtime
        self.set_busy(True)
        self.note.set("Готовим локальный ИИ…")

        def worker():
            try:

                def progress(text):
                    events.put(("progress", text))

                runtime.install(token, progress)
                url = runtime.start(token, progress)
                models = Client(ProviderConfig(url=url, timeout=3)).models()
                if gguf:
                    if model in models:
                        raise ProviderError(
                            "Это имя уже занято. Укажите другое имя для импортируемой модели."
                        )
                    import_gguf(gguf, model, token, progress, url, directory=runtime.directory)
                elif model not in models:
                    if installed_only:
                        raise ProviderError(
                            "Модель не установлена во встроенной Ollama. Обновите список или выберите загрузку из каталога."
                        )
                    needed = {
                        "qwen3.5:4b": 5,
                        "qwen3:1.7b": 3,
                        "qwen3.5:9b": 8,
                        "deepseek-r1:8b-0528-qwen3-q4_K_M": 7,
                    }.get(model, 2)
                    if shutil.disk_usage(runtime.directory).free < needed * 1024**3:
                        raise ProviderError(
                            "Не хватает свободного места на диске моделей. Освободите место и повторите."
                        )
                    if custom:
                        pull_model(model, token, progress, url, allow_custom=True)
                    else:
                        pull_model(model, token, progress, url)
                check_cancel(token)
                if custom:
                    runtime.save_model(model, allow_custom=True)
                else:
                    runtime.save_model(model)
                events.put(("done", (model, url)))
            except DownloadCancelled:
                runtime.stop()
                events.put(
                    (
                        "error",
                        "Установка отменена. ИИ остановлен; скачанные файлы сохранены для повторного запуска.",
                    )
                )
            except Exception as exc:
                runtime.stop()
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Не удалось установить ИИ. Проверьте интернет, свободное место и доступность GitHub. Повторная попытка продолжит загрузку компонентов."
                )
                events.put(("error", message))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_download(self):
        self.cancel.set()
        self.note.set("Отмена запрошена. Текущее сетевое чтение может занять до 30 секунд.")

    def poll(self):
        if self.closed:
            return
        self.window.after_cancel(self.poll_id)
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    if not self.cancel.is_set():
                        self.note.set(value)
                    continue
                if kind == "models":
                    self.set_busy(False)
                    self.custom_chooser.configure(values=value)
                    self.note.set(f"Установлено моделей: {len(value)}. Выберите имя из списка.")
                    continue
                self.set_busy(False)
                if self.cancel.is_set():
                    self.note.set(
                        "Операция отменена. Для повторного запуска нажмите кнопку установки."
                    )
                elif kind == "error":
                    self.note.set(value)
                else:
                    self.on_ready(*value)
                    self.close()
                    return
        except queue.Empty:
            pass
        self.poll_id = self.window.after(80, self.poll)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.busy:
            self.cancel.set()
        hide_tooltips(self.window)
        self.window.after_cancel(self.poll_id)
        self.window.update_idletasks()
        self.window.destroy()

    def choose_gguf(self):
        path = filedialog.askopenfilename(parent=self.window, filetypes=[("GGUF", "*.gguf")])
        if not path:
            return
        try:
            inspect_gguf(path)
        except (ValueError, OSError) as exc:
            self.note.set(str(exc))
            return
        self.gguf_path.set(path)
        self.custom_source.set("gguf")
        if not self.custom_name.get().strip():
            name = re.sub(r"[^a-z0-9.-]+", "-", Path(path).stem.lower()).strip("-.")[:40]
            self.custom_name.set("shelter-import:" + (name or "local"))

    def list_models(self):
        if self.busy:
            return
        if not self.runtime.installed():
            self.note.set(
                "Компоненты встроенного ИИ ещё не установлены. Выберите модель и подтвердите установку."
            )
            return
        self.cancel = threading.Event()
        token = CombinedCancel(self.cancel, self.runtime.closed)
        self.set_busy(True)

        def worker():
            try:
                url = self.runtime.start(token, lambda text: self.events.put(("progress", text)))
                models = Client(ProviderConfig(url=url, timeout=5)).models()
                self.events.put(("models", models))
            except (ValueError, OSError):
                self.events.put(("error", "Не удалось получить список локальных моделей."))

        threading.Thread(target=worker, daemon=True).start()
