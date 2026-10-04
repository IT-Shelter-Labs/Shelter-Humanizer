"""Layout-independent editing and small contextual help for our own widgets."""

import tkinter as tk
from tkinter import ttk

from .clipboard import read as read_clipboard
from .clipboard import write as write_clipboard


def edit_action(widget, action):
    """Edit directly: do not send a virtual key event back through Tk's bindings."""
    is_text = isinstance(widget, tk.Text)
    try:
        state = str(widget.cget("state"))
        writable = state not in ("disabled", "readonly")
        if action == "all":
            if is_text:
                widget.tag_add("sel", "1.0", "end-1c")
            else:
                widget.selection_range(0, "end")
        elif action in ("copy", "cut"):
            selected = (
                widget.get("sel.first", "sel.last")
                if is_text
                else widget.get()[widget.index("sel.first") : widget.index("sel.last")]
            )
            write_clipboard(widget, selected)
            if action == "cut" and writable:
                if is_text:
                    widget.edit_separator()
                    widget.delete("sel.first", "sel.last")
                    widget.edit_separator()
                else:
                    widget.delete("sel.first", "sel.last")
        elif action == "paste" and writable:
            value = read_clipboard(widget)
            if is_text:
                widget.edit_separator()
                if widget.tag_ranges("sel"):
                    widget.delete("sel.first", "sel.last")
                widget.insert("insert", value)
                widget.see("insert")
                widget.edit_separator()
            else:
                if widget.selection_present():
                    widget.delete("sel.first", "sel.last")
                widget.insert("insert", value)
        elif is_text and writable and action in ("undo", "redo"):
            widget.edit_undo() if action == "undo" else widget.edit_redo()
    except tk.TclError:
        # Empty clipboard/selection and an empty undo stack are normal UI states.
        pass


def shortcut_action(keycode, keysym, state, windows=False):
    # On Windows bit 8 is NumLock, not Alt (X11 uses it for Alt).
    if not state & 4 or state & (0x20000 if windows else 8):
        return None
    letters = {65: "a", 66: "b", 67: "c", 86: "v", 88: "x", 89: "y", 90: "z"}
    aliases = {
        "ф": "a",
        "с": "c",
        "м": "v",
        "ч": "x",
        "я": "z",
        "н": "y",
        "и": "b",
        "cyrillic_ef": "a",
        "cyrillic_es": "c",
        "cyrillic_em": "v",
        "cyrillic_che": "x",
        "cyrillic_ya": "z",
        "cyrillic_en": "y",
        "cyrillic_i": "b",
    }
    key = letters.get(keycode) if windows else None
    key = key or aliases.get(keysym.lower(), keysym.lower())
    actions = {
        "a": "all",
        "b": "paste",
        "v": "paste",
        "c": "copy",
        "x": "cut",
        "z": "undo",
        "y": "redo",
    }
    return "redo" if key == "z" and state & 1 else actions.get(key)


def install_shortcuts(root):
    windows = root.tk.call("tk", "windowingsystem") == "win32"

    def handle(event):
        state = event.state
        if windows:
            import ctypes

            # Tk may drop Control from the translated non-Latin WM_CHAR event.
            # GetKeyState is local to this UI thread, not a global keyboard hook.
            if ctypes.windll.user32.GetKeyState(0x11) & 0x8000:
                state |= 4
            if ctypes.windll.user32.GetKeyState(0x12) & 0x8000:
                return None
        action = shortcut_action(event.keycode, event.keysym, state, windows)
        if action is None:
            return None
        widget = event.widget
        edit_action(widget, action)
        return "break"

    root.bind_class("ShelterEditing", "<KeyPress>", handle)


def bind_editing(widget):
    if "ShelterEditing" not in widget.bindtags():
        widget.bindtags(("ShelterEditing",) + widget.bindtags())


class Tooltip:
    def __init__(self, widget, text, colors):
        self.widget, self.text, self.colors = widget, text, colors
        self.timer = None
        self.window = None
        for event in ("<Enter>",):
            widget.bind(event, self.schedule, add="+")
        for event in ("<Leave>", "<FocusOut>", "<ButtonPress>", "<Destroy>"):
            widget.bind(event, self.hide, add="+")
        self.owner = widget.winfo_toplevel()
        self.owner.bind("<FocusOut>", self.hide, add="+")
        self.owner.bind("<Unmap>", self.hide, add="+")
        self.owner.bind("<Configure>", self.hide, add="+")
        self.watch = None

    def schedule(self, event=None):
        self.hide()
        self.timer = self.widget.after(550, self.show)

    def show(self):
        self.timer = None
        if not self.widget.winfo_exists() or not self.widget.winfo_ismapped():
            return
        hide_tooltips(self.widget.winfo_toplevel(), except_tip=self)
        p = self.colors()
        window = self.window = tk.Toplevel(self.widget)
        window.withdraw()
        window.overrideredirect(True)
        window.transient(self.owner)
        window.configure(background=p["border"])
        label = tk.Label(
            window,
            text=self.text,
            wraplength=330,
            justify="left",
            background=p["card"],
            foreground=p["ink"],
            font=("Segoe UI", 10),
            padx=12,
            pady=10,
        )
        label.pack(padx=1, pady=1)
        window.update_idletasks()
        x = min(
            self.widget.winfo_rootx(), window.winfo_screenwidth() - window.winfo_reqwidth() - 12
        )
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        if y + window.winfo_reqheight() > window.winfo_screenheight() - 12:
            y = self.widget.winfo_rooty() - window.winfo_reqheight() - 8
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
        window.deiconify()
        self.watch = self.widget.after(120, self.check_owner)

    def check_owner(self):
        self.watch = None
        focused = self.widget.focus_displayof()
        if focused is None or focused.winfo_toplevel() is not self.owner:
            self.hide()
        elif self.window is not None:
            self.watch = self.widget.after(120, self.check_owner)

    def hide(self, event=None):
        if event is not None and event.widget is not self.owner and event.widget is not self.widget:
            return
        if self.watch is not None:
            self.widget.after_cancel(self.watch)
            self.watch = None
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def hide_tooltips(parent, except_tip=None):
    for widget in list(parent.winfo_children()):
        tip = getattr(widget, "shelter_tip", None)
        if tip is not None and tip is not except_tip:
            tip.hide()
        if widget.winfo_exists():
            hide_tooltips(widget, except_tip)


HELP = {
    "Установить и включить ИИ": "После подтверждения скачивает компоненты Ollama и модель, проверяет файлы и запускает ИИ для приложения. При закрытии приложения ИИ выключается. Для первого запуска нужны интернет и место на диске.",
    "Использовать модель": "Выбирает установленную модель в основном приложении и включает режим «ИИ на компьютере». Никакой текст пока не отправляется.",
    "Копировать": "Копирует весь текст результата, включая ваши ручные изменения. Выделять его не нужно.",
    "Без ИИ": "Работает без ИИ: ищет подозрительные символы и делает только небольшие правки по правилам. Хороший текст может остаться без изменений.",
    "ИИ на компьютере": "Перефразирование локальной моделью через Ollama. Кнопка «Установить локальный ИИ» поможет загрузить модель без терминала.",
    "ИИ-сервис по ключу": "Отправляет текст выбранному ИИ-сервису. Нужны API-ключ и имя модели; возможна оплата по тарифам сервиса.",
    "Тип текста": "Выберите жанр: обычный, деловой, научный или пост. Научный режим сохраняет допустимые формальные обороты.",
    "Что изменить": "Небольшие правки сохраняют формулировки. Переписывание меняет предложения с сохранением содержания. Офлайн-правила от этого не меняются.",
    "Стиль и важные слова": "Добавьте образец вашего стиля и имена/термины, которые нужно сохранить буквально. Образец передаётся выбранной ИИ-модели.",
    "Дополнительная ИИ-вычитка": "Второй запрос к той же модели для дополнительной редактуры. Занимает больше времени и может увеличить стоимость API.",
    "Спецпробелы → обычные": "Заменяет специальные пробелы обычными вне цитат, кода и ссылок. Полезно при копировании; типографические пробелы иногда нужны.",
    "Похожие латинские буквы": "Заменяет похожие латинские буквы в смешанных русских словах. Выключено по умолчанию: в именах и технических терминах смешение бывает намеренным.",
    "Нормализация NFC": "Приводит составные Unicode-буквы к стандартному представлению, когда оно существует. Сохраняет русский текст и ударения; это не удаление всего Unicode.",
    "Улучшить текст": "В ИИ-режиме перерабатывает текст с сохранением смысла и вычитывает результат. Без ИИ делает небольшие правки по правилам. Глубину и второй проход можно изменить в настройках.",
    "Проверить текст": "Показывает скрытые символы, смешение алфавитов и обороты для вычитки. Не изменяет текст и не определяет, кто его написал.",
    "Очистить символы": "Убирает подозрительные управляющие и невидимые знаки вне защищённых фрагментов. Кириллица, кавычки, тире и корректные emoji сохраняются.",
    "Обработать в ИИ-чате…": "Копирует готовое задание с вашим текстом. Отправьте его в привычный ИИ-чат, вставьте ответ справа и проверьте правки.",
    "Копировать задание с текстом": "Копирует редакторское задание и исходник. Вставьте это сообщение в свой ИИ-чат и отправьте самостоятельно.",
    "Выбрать ИИ": "Выберите обработку без ИИ, локальную модель или сервис по API-ключу. Для локального ИИ есть автоматическая установка.",
    "Настройки текста": "Необязательные параметры: тип правок, ваш стиль, важные слова и дополнительные замены символов.",
    "Проверки и изменения…": "Открывает находки символов, замечания к стилю, сравнение текстов и проверку смысловых изменений.",
    "Вставить ответ в результат": "Сначала скопируйте ответ из ИИ-чата. Приложение вставит его справа и сравнит с исходником.",
    "Открыть ChatGPT ↗": "Открывает сайт в браузере. Ваш текст не отправляется автоматически; задание нужно вставить и отправить самостоятельно.",
    "Копировать текст": "Копирует весь текст из правого поля, включая ваши ручные изменения. Выделять его не нужно.",
    "Настроить подключение": "Укажите адрес сервера, модель и, для облачного API, ключ. Ключ и настройки не сохраняются на диск.",
    "Установить локальный ИИ": "Выберите модель и подтвердите — приложение само скачает и включит ИИ. Без команд и отдельного установщика. При закрытии ИИ выключится, а файлы останутся для следующего запуска.",
    "Светлая": "Светлое оформление. При переключении исходник и результат сохраняются.",
    "Тёмная": "Тёмное оформление. При переключении исходник и результат сохраняются.",
    "Сохранить TXT": "Сохраняет правое поле в UTF-8 по выбранному вами пути. Автоматической истории нет.",
    "Отчёт JSON": "Сохраняет оба текста и результаты проверок. Отчёт содержит исходный текст — учитывайте это при передаче файла другим людям.",
    "Проверить правки": "Сравнивает текущий исходник с правым полем, включая ручную правку или ответ из внешнего чата. Проверьте замечания и прочитайте результат.",
    "Адрес сервера (base URL)": "Адрес подключения без /chat/completions. Для Ollama обычно http://localhost:11434. Внешний сервис получает ваш текст.",
    "Точное имя модели": "Выберите установленную модель или впишите точное имя. Список доступных моделей можно запросить без отправки текста.",
    "API-ключ · только до закрытия приложения": "Секретный ключ выбранного сервиса. Он хранится только в памяти и не попадает в текст, отчёты или файл настроек.",
    "Образец вашего текста · необязательно, до 3000 символов": "Небольшой собственный текст задаёт тон и ритм ИИ-редактуры. Не отправляйте чужие личные данные.",
    "Сохранять буквально · одно имя / термин на строку": "В ИИ-режиме эти фрагменты защищаются от изменения. По одному имени или термину на строку, до 100 записей.",
}


def attach_help(parent, colors):
    last_help = None
    for widget in parent.winfo_children():
        if isinstance(widget, (tk.Text, ttk.Entry, ttk.Combobox)):
            bind_editing(widget)
            if last_help and not hasattr(widget, "shelter_tip"):
                widget.shelter_tip = Tooltip(widget, last_help, colors)
        if isinstance(widget, (ttk.Label, ttk.Button, ttk.Radiobutton, ttk.Checkbutton)):
            text = str(widget.cget("text"))
            if isinstance(widget, ttk.Label):
                last_help = HELP.get(text)
            if text in HELP and not hasattr(widget, "shelter_tip"):
                widget.shelter_tip = Tooltip(widget, HELP[text], colors)
        attach_help(widget, colors)
