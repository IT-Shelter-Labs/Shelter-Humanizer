"""One-click private Windows AI installation, with explicit consent and progress."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .local_models import DEFAULT_MODEL, PRESETS, DownloadCancelled, pull_model
from .managed_runtime import CombinedCancel, check_cancel
from .providers import Client, ProviderConfig
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
        window.geometry("660x650")
        window.resizable(False, False)
        window.transient(root)
        window.grab_set()
        window.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(window, padding=22)
        frame.pack(fill="both", expand=True)
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
        self.choice = tk.StringVar(
            master=window,
            value=selected if selected in {p.name for p in PRESETS} else DEFAULT_MODEL,
        )
        self.model_note = tk.StringVar(master=window)
        self.radios = []
        for preset in PRESETS:
            radio = ttk.Radiobutton(
                frame,
                text=f"{preset.label} · {preset.size}",
                value=preset.name,
                variable=self.choice,
                command=self.selection_changed,
            )
            radio.pack(anchor="w", pady=5)
            self.radios.append(radio)
            radio.shelter_tip = Tooltip(radio, preset.description, colors)
        ttk.Label(frame, textvariable=self.model_note, wraplength=610, style="Muted.TLabel").pack(
            fill="x", pady=10
        )
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
        ttk.Label(frame, textvariable=self.note, wraplength=610).pack(fill="x", pady=10)
        self.bar = ttk.Progressbar(frame, mode="indeterminate")
        self.bar.pack(fill="x", pady=5)
        bottom = ttk.Frame(frame)
        bottom.pack(side="bottom", fill="x", pady=(12, 0))
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
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.bar.start(15) if busy else self.bar.stop()

    def download(self):
        if self.busy:
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

    def start(self, model):
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
                if model not in models:
                    pull_model(model, token, progress, url)
                check_cancel(token)
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
