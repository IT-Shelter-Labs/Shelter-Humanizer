import gc
import io
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tkinter import ttk
from unittest.mock import MagicMock, patch

from shelter_humanizer.cli import main
from shelter_humanizer.clipboard import read as read_clipboard
from shelter_humanizer.gui import EDIT_CHOICES, SAMPLE, TELEGRAM_URL, App
from shelter_humanizer.local_models import PRESETS
from shelter_humanizer.managed_runtime import ManagedRuntime
from shelter_humanizer.providers import ProviderError
from shelter_humanizer.selftest import SMOKE_TEXT
from shelter_humanizer.service import compare, offline
from shelter_humanizer.ui_helpers import shortcut_action


class CliTests(unittest.TestCase):
    def test_cli_file_edit_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "in.txt"
            output = Path(directory) / "out.txt"
            report = Path(directory) / "report.json"
            source.write_text("Сервис осуществляет проверку.", encoding="utf-8")
            self.assertEqual(
                main(["light", str(source), "-o", str(output), "--report", str(report)]), 0
            )
            self.assertEqual(output.read_text(encoding="utf-8"), "Сервис проводит проверку.")
            self.assertTrue(report.is_file())

    def test_cli_stdin_unicode_analysis(self):
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO("про\u200bверка")), redirect_stdout(stdout):
            self.assertEqual(main(["analyze"]), 0)
        self.assertIn("U+200B", stdout.getvalue())

    def test_cli_failure_exit_code(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["light", "missing-file-for-test.txt"]), 2)


class GuiTests(unittest.TestCase):
    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from GuiTests.descendants(child)

    def button(self, parent, label):
        return next(
            w
            for w in self.descendants(parent)
            if isinstance(w, ttk.Button) and w.cget("text") == label
        )

    def menu_action(self, widget, label):
        menu = widget.shelter_menu
        self.root.tk.call(menu.cget("postcommand"))
        index = next(
            i
            for i in range(menu.index("end") + 1)
            if menu.type(i) == "command" and menu.entrycget(i, "label") == label
        )
        return menu, index

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            if os.getenv("SHELTER_REQUIRE_GUI_TESTS") == "1":
                raise
            self.skipTest("Tk needs a display; use Xvfb on Linux")
        self.root.withdraw()
        self.app = App(self.root, auto_connect=False)
        self.errors = []
        self.app.error = self.errors.append
        self.root.update()

    def tearDown(self):
        if hasattr(self, "app") and not self.app.closed:
            self.app.close()
        if hasattr(self, "app"):
            del self.app
        # Tk finalizers must run on the GUI thread, not a later HTTP test worker.
        gc.collect()

    def wait_for_worker(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        self.assertFalse(self.app.busy)

    def test_default_offline_worker_never_uses_network(self):
        self.app.set_source(SMOKE_TEXT)
        self.root.update()
        with patch("shelter_humanizer.gui.Client", side_effect=AssertionError("no network")):
            self.app.run("edit")
            self.wait_for_worker()
        self.assertEqual(self.app.get_text(self.app.source), SMOKE_TEXT)
        self.assertNotIn("про\u200bверка", self.app.get_text(self.app.output))
        self.assertGreater(len(self.app.unicode_tree.get_children()), 0)
        self.assertEqual(self.errors, [])

    def test_example_is_plain_prose_and_has_no_hidden_diagnostics(self):
        self.button(self.root, "Пример").invoke()
        self.root.update()
        self.assertEqual(self.app.get_text(self.app.source), SAMPLE)
        self.assertEqual(offline(SAMPLE).text, SAMPLE)
        self.assertNotIn("example.org", SAMPLE)
        self.assertNotIn("Emoji", SAMPLE)
        self.assertNotIn("\u200b", SAMPLE)

    def test_theme_roundtrip_preserves_editing_and_result(self):
        self.app.set_source(SAMPLE)
        result = offline(SAMPLE)
        self.app.show_result(result)
        self.app.output.insert("end", " Авторская правка.")
        self.root.update()
        before = self.app.get_text(self.app.output)
        revision = self.app.revision
        selected = self.app.tabs.select()
        self.app.source.tag_add("sel", "1.0", "1.5")
        for theme in ("dark", "light"):
            self.app.apply_theme(theme)
            self.root.update()
            self.assertEqual(self.app.get_text(self.app.source), SAMPLE)
            self.assertEqual(self.app.get_text(self.app.output), before)
            self.assertIs(self.app.result, result)
            self.assertEqual(self.app.revision, revision)
            self.assertEqual(self.app.tabs.select(), selected)
            self.assertEqual(self.app.source.get("sel.first", "sel.last"), SAMPLE[:5])
            self.assertEqual(self.app.output.cget("background"), self.app.colors["card"])
            self.assertEqual(self.app.output.cget("foreground"), self.app.colors["ink"])

    def test_theme_applies_to_open_dialog_and_combobox_popdown(self):
        self.app.voice_settings()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        self.app.apply_theme("dark")
        self.assertEqual(window.cget("background"), self.app.colors["bg"])
        window.destroy()
        self.app.mode.set("api")
        self.app.settings()
        self.root.update()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        chooser = next(w for w in self.descendants(window) if isinstance(w, ttk.Combobox))
        # A dialog created after a theme switch must use that palette, too.
        self.assertEqual(window.cget("background"), self.app.colors["bg"])
        self.app.apply_theme("light")
        popdown = chooser.tk.call("ttk::combobox::PopdownWindow", str(chooser))
        self.assertEqual(
            chooser.tk.call(str(popdown) + ".f.l", "cget", "-foreground"), self.app.colors["ink"]
        )
        window.destroy()

    def test_logo_and_telegram_link(self):
        self.assertGreater(self.app.logo.width(), 0)
        self.assertLessEqual(self.app.logo.width(), 64)
        with patch("shelter_humanizer.gui.webbrowser.open", return_value=True) as browser:
            self.app.apply_theme("dark")
            browser.assert_not_called()
            self.app.open_telegram()
            browser.assert_called_once_with(TELEGRAM_URL, new=2)
        self.assertEqual(TELEGRAM_URL, "https://t.me/+txLM4MYbMh9lZDli")

    def test_empty_text_shows_actionable_error(self):
        self.app.run("edit")
        self.assertFalse(self.app.busy)
        self.assertIn("вставьте", self.errors[0])

    def test_settings_model_list_updates_main_thread_without_text(self):
        self.app.mode.set("ollama")
        self.app.settings()
        self.root.update()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        widgets = list(children(window))
        button = next(
            w
            for w in widgets
            if isinstance(w, ttk.Button) and w.cget("text") == "Показать доступные модели"
        )
        chooser = next(w for w in widgets if isinstance(w, ttk.Combobox))
        with patch("shelter_humanizer.gui.Client") as client:
            client.return_value.models.return_value = ["installed-model"]
            button.invoke()
            deadline = time.monotonic() + 3
            while chooser.get() != "installed-model" and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.02)
            self.assertEqual(chooser.get(), "installed-model")
            client.return_value.complete.assert_not_called()
        self.assertEqual(self.app.get_text(self.app.source), "")
        window.destroy()

    def test_analysis_snapshot_can_be_exported_without_edit(self):
        self.app.set_source("про\u200bверка")
        self.root.update()
        self.app.run("analyze")
        self.wait_for_worker()
        self.assertEqual(self.app.analysis_snapshot.original, "про\u200bверка")
        self.assertEqual(self.app.analysis_snapshot.mode, "analyze")
        self.assertEqual(len(self.app.analysis_snapshot.before.unicode), 1)
        self.app.set_source("Другой текст")
        self.assertIsNone(self.app.analysis_snapshot)

    def test_cancelled_queued_answer_is_not_applied(self):
        self.app.set_source("Исходник")
        self.root.update()
        self.app.set_busy(True)
        self.app.events.put(("done", self.app.revision, ("edit", offline("Исходник"))))
        self.app.cancel_run()
        self.app._poll()
        self.assertEqual(self.app.get_text(self.app.output), "")
        self.assertFalse(self.app.busy)

    def test_explicit_review_uses_current_source_after_new_prompt(self):
        self.app.set_source("Старый текст 10")
        self.app.show_result(offline("Старый текст 10"))
        self.app.set_source("Новый текст 20")
        self.app.replace_text(self.app.output, "Новый текст 20")
        self.app.review_result()
        self.assertEqual(self.app.result.original, "Новый текст 20")
        self.assertEqual(self.app.result.warnings, [])

    def test_small_window_keeps_actions_mapped(self):
        self.root.deiconify()
        self.root.geometry("1060x668")
        self.root.update()
        self.assertTrue(self.app.run_button.winfo_ismapped())
        self.assertLess(self.app.run_button.winfo_rooty(), self.root.winfo_rooty() + 668)

    def test_minimum_window_keeps_export_and_theme_controls_visible(self):
        self.root.deiconify()
        self.root.geometry("960x620")
        self.root.update()

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        for label in ("Сохранить TXT", "Копировать", "Telegram IT Shelter ↗", "Тёмная"):
            button = next(
                w
                for w in children(self.root)
                if isinstance(w, (ttk.Button, ttk.Radiobutton)) and w.cget("text") == label
            )
            self.assertTrue(button.winfo_ismapped(), label)
            self.assertLessEqual(
                button.winfo_rooty() + button.winfo_height(), self.root.winfo_rooty() + 620, label
            )
            self.assertLessEqual(
                button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + 960, label
            )

    def test_stale_result_cannot_replace_output(self):
        self.app.set_source("Старый текст")
        self.root.update()
        old = self.app.revision
        self.app.set_busy(True)
        self.app.set_source("Новый текст")
        self.root.update()
        self.app.events.put(("done", old, ("edit", offline("Старый текст"))))
        self.app._poll()
        self.assertEqual(self.app.get_text(self.app.output), "")
        self.assertIn("Устаревший", self.app.status.get())

    def test_manual_external_chat_result_and_export_snapshot(self):
        self.app.set_source("Примерно 10 файлов")
        self.app.output.insert("1.0", "20 файлов")
        self.app.review_result()
        self.assertTrue(self.app.result.warnings)
        self.app.set_source("Другой исходник")
        self.assertEqual(self.app.current_result().original, "Примерно 10 файлов")

    def test_highlight_after_emoji_correct_character(self):
        text = "👩‍💻 про\u200bверка"
        self.app.set_source(text)
        self.app.show_findings(offline(text).before)
        self.app.unicode_tree.selection_set("u0")
        self.app.highlight(self.app.unicode_tree)
        start, end = self.app.source.tag_ranges("finding")
        self.assertEqual(self.app.source.get(start, end), "\u200b")

    def test_edit_shortcuts_with_physical_keys_and_russian_symbols(self):
        for keycode, keysym, action in (
            (86, "Cyrillic_em", "paste"),
            (67, "Cyrillic_es", "copy"),
            (65, "Cyrillic_ef", "all"),
            (88, "Cyrillic_che", "cut"),
            (90, "Cyrillic_ya", "undo"),
            (89, "Cyrillic_en", "redo"),
            (66, "Cyrillic_i", "paste"),
        ):
            self.assertEqual(shortcut_action(keycode, keysym, 4, True), action)
            self.assertEqual(shortcut_action(0, keysym, 4), action)
        self.assertEqual(shortcut_action(86, "v", 4 | 8, True), "paste")
        self.assertIsNone(shortcut_action(86, "v", 4 | 0x20000, True))
        self.assertIsNone(shortcut_action(86, "м", 0, True))
        self.assertEqual(shortcut_action(90, "я", 5, True), "redo")

    def test_native_keyboard_paste_select_copy_in_text_and_entry(self):
        self.root.deiconify()
        self.app.source.focus_force()
        self.root.update()
        self.app.clipboard("Русский текст 👩‍💻")
        windows = self.root.tk.call("tk", "windowingsystem") == "win32"

        def press(widget, letter, code):
            widget.event_generate(
                "<KeyPress>", state=4, **({"keycode": code} if windows else {"keysym": letter})
            )
            self.root.update()

        press(self.app.source, "v", 86)
        self.assertEqual(self.app.get_text(self.app.source), "Русский текст 👩‍💻")
        press(self.app.source, "a", 65)
        press(self.app.source, "c", 67)
        self.assertEqual(read_clipboard(self.root), "Русский текст 👩‍💻")
        self.app.mode.set("api")
        self.app.settings()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        entry = next(w for w in self.descendants(window) if isinstance(w, ttk.Entry))
        entry.delete(0, "end")
        entry.focus_force()
        self.root.update()
        press(entry, "v", 86)
        self.assertEqual(entry.get(), "Русский текст 👩‍💻")
        window.destroy()

    def test_copy_button_is_beside_result_and_copies_manual_edit(self):
        self.app.output.insert("1.0", "Ручная правка")
        self.app.copy_button.invoke()
        self.assertEqual(read_clipboard(self.root), "Ручная правка")
        self.assertEqual(self.app.copy_button.master.master, self.app.output.master.master)

    def test_long_russian_clipboard_round_trip_and_paste(self):
        text = "Русский текст: ё, кавычки «пример», 👩‍💻.\n" * 1800
        self.assertTrue(self.app.clipboard(text))
        self.assertEqual(read_clipboard(self.root), text)
        self.app.paste()
        self.assertEqual(self.app.get_text(self.app.source), text)

    def test_long_prompt_clipboard_keeps_every_character(self):
        self.app.set_source("Исходный текст 10 👩‍💻")
        self.assertTrue(self.app.prompt())
        self.assertEqual(read_clipboard(self.root), self.app.last_chat_prompt)

    def test_unchanged_offline_result_explains_next_action(self):
        self.app.show_result(offline("Обычное короткое предложение."))
        self.assertIn("Без изменений", self.app.result_note.get())
        self.assertIn("перефразирования", self.app.result_note.get())

    def test_help_popup_has_description_and_does_not_change_text(self):
        self.root.deiconify()
        self.root.update()
        self.app.set_source("Сохранить этот текст")
        tip = self.app.copy_button.shelter_tip
        tip.show()
        self.assertIsNotNone(tip.window)
        self.assertIn("выделять", tip.text.lower())
        tip.hide()
        self.assertEqual(self.app.get_text(self.app.source), "Сохранить этот текст")

    def test_first_launch_offers_qwen_without_installing_or_starting_runtime(self):
        self.app.close()
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            self.root = tk.Tk()
            self.root.withdraw()
            with patch("shelter_humanizer.gui.ManagedRuntime", return_value=runtime):
                self.app = App(self.root)
            self.root.update()
            self.assertEqual(self.app.mode.get(), "offline")
            self.app.setup_local()
            self.assertEqual(self.app.local_wizard.choice.get(), "qwen3.5:4b")
            self.assertEqual(runtime.selected_model(), "")
            self.assertIsNone(runtime.process)
            self.assertFalse((Path(directory) / "selection.json").exists())
            self.app.local_wizard.close()
            self.app.close()

    def test_local_connection_prefills_qwen_and_keeps_explicit_choices(self):
        from shelter_humanizer.providers import ProviderConfig

        for kind, saved, expected in (
            ("ollama", "", "qwen3.5:4b"),
            ("ollama", "deepseek-r1:8b-0528-qwen3-q4_K_M", "deepseek-r1:8b-0528-qwen3-q4_K_M"),
            ("ollama", "my-custom-model:latest", "my-custom-model:latest"),
            ("api", "", ""),
        ):
            with self.subTest(kind=kind, saved=saved):
                self.app.configs[kind] = ProviderConfig(
                    kind=kind,
                    url="http://localhost:11434" if kind == "ollama" else "https://example.org/v1",
                    model=saved,
                )
                self.app.mode.set(kind)
                self.app.settings()
                window = next(
                    w
                    for w in self.root.winfo_children()
                    if isinstance(w, tk.Toplevel) and w is not self.app.review_window
                )
                chooser = next(w for w in self.descendants(window) if isinstance(w, ttk.Combobox))
                try:
                    self.assertEqual(chooser.get(), expected)
                    if kind == "ollama":
                        self.button(window, "Применить").invoke()
                        self.assertEqual(self.app.configs[kind].model, expected)
                finally:
                    if window.winfo_exists():
                        window.destroy()

    def test_local_setup_restores_saved_deepseek_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            runtime.save_model("deepseek-r1:8b-0528-qwen3-q4_K_M")
            self.app.runtime = runtime
            self.app.setup_local()
            self.assertEqual(self.app.local_wizard.choice.get(), "deepseek-r1:8b-0528-qwen3-q4_K_M")
            self.assertEqual(runtime.selected_model(), "deepseek-r1:8b-0528-qwen3-q4_K_M")
            self.assertIsNone(runtime.process)
            self.app.local_wizard.close()
            runtime.close()

    def test_local_setup_does_not_download_on_open_and_applies_installed_model(self):
        runtime = MagicMock()
        runtime.closed = threading.Event()
        runtime.selected_model.return_value = ""
        runtime.start.return_value = "http://127.0.0.1:12345"
        self.app.runtime = runtime
        with (
            patch("shelter_humanizer.local_setup.pull_model") as pull,
            patch("shelter_humanizer.local_setup.Client") as client,
        ):
            self.app.setup_local()
            wizard = self.app.local_wizard
            pull.assert_not_called()
            client.assert_not_called()
            client.return_value.models.return_value = [PRESETS[0].name]
            runtime.install.assert_not_called()
            wizard.start(PRESETS[0].name)
            deadline = time.monotonic() + 4
            while wizard.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.02)
            self.assertFalse(wizard.busy)
            self.assertEqual(self.app.configs["ollama"].model, PRESETS[0].name)
            self.assertEqual(self.app.mode.get(), "ollama")
            self.assertTrue(wizard.closed)
            self.assertEqual(self.app.configs["ollama"].url, runtime.start.return_value)
            runtime.save_model.assert_called_once_with(PRESETS[0].name)
            client.return_value.complete.assert_not_called()

    def test_local_setup_download_needs_explicit_acceptance(self):
        self.app.setup_local()
        wizard = self.app.local_wizard
        with (
            patch("shelter_humanizer.local_setup.messagebox.askyesno", return_value=False),
            patch("shelter_humanizer.local_setup.pull_model") as pull,
        ):
            wizard.download()
            pull.assert_not_called()
            self.assertFalse(wizard.busy)

    def test_first_screen_hides_advanced_settings_and_can_show_review(self):
        self.root.deiconify()
        self.root.geometry("960x620")
        self.root.update()
        self.assertFalse(self.app.tabs.winfo_ismapped())
        self.app.details_button.invoke()
        self.root.update()
        self.assertTrue(self.app.tabs.winfo_ismapped())
        self.button(self.app.review_window, "Закрыть").invoke()
        self.root.update()
        self.assertFalse(self.app.tabs.winfo_ismapped())
        self.assertTrue(self.app.second_pass.get())

    def test_ready_to_use_defaults_keep_script_replacement_optional(self):
        self.app.set_source("Хороший исходный текст.")
        with patch.object(self.app, "clipboard", return_value=True):
            self.assertTrue(self.app.prompt())
        data = json.loads(self.app.last_chat_prompt.rsplit("\n", 1)[1])
        self.assertEqual(data["edit_goal"], "Аккуратная редактура")
        self.assertEqual(data["original"], "Хороший исходный текст.")
        self.assertTrue(self.app.second_pass.get())
        self.assertTrue(self.app.spaces.get())
        self.assertTrue(self.app.nfc.get())
        self.assertFalse(self.app.confusables.get())

    def test_ai_worker_applies_enabled_cleanup_without_touching_source(self):
        source = "Слово\u00a0е\u0308лка. «Цитата\u00a0точно» https://example.org/"
        self.app.set_source(source)
        self.app.mode.set("api")
        self.app.configs["api"] = self.app.configs["api"].__class__(
            "api", "https://example.org/v1", "test"
        )
        self.root.update()
        client = MagicMock()

        def echo(messages):
            data = json.loads(messages[-1]["content"].split("\n", 1)[1])
            if "draft_sentences" in data:
                return '{"edits":[]}'
            return data.get("draft", data["original"])

        client.complete.side_effect = echo
        with patch("shelter_humanizer.gui.Client", return_value=client):
            self.app.run("edit")
            self.wait_for_worker()
        self.assertEqual(self.errors, [])
        self.assertEqual(client.complete.call_count, 2)
        self.assertEqual(self.app.get_text(self.app.source), source)
        self.assertEqual(
            self.app.get_text(self.app.output),
            "Слово ёлка. «Цитата\u00a0точно» https://example.org/",
        )

    def test_optional_review_timeout_shows_first_draft_in_gui_without_error_dialog(self):
        source = "Сервис осуществляет проверку 10 файлов."
        self.app.set_source(source)
        self.app.mode.set("ollama")
        self.app.configs["ollama"] = self.app.configs["ollama"].__class__(
            "ollama", "http://localhost:11434", "qwen3.5:4b", timeout=180
        )
        self.root.update()
        client = MagicMock()
        client.complete.side_effect = ["Сервис проверяет 10 файлов.", ProviderError("timeout")]
        with patch("shelter_humanizer.gui.Client", return_value=client) as factory:
            self.app.run("edit")
            self.wait_for_worker()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.app.get_text(self.app.source), source)
        self.assertEqual(self.app.get_text(self.app.output), "Сервис проверяет 10 файлов.")
        self.assertTrue(any("вычитка не завершилась" in w for w in self.app.result.warnings))
        self.assertIs(factory.call_args.kwargs["cancel"], self.app.cancel)
        self.assertTrue(callable(factory.call_args.kwargs["progress"]))

    def test_review_window_does_not_shrink_editors_on_small_screen(self):
        self.root.deiconify()
        self.root.geometry("960x620")
        self.root.update()
        self.app.toggle_details(True)
        self.app.tabs.select(3)
        self.root.update()
        for widget in (self.app.source, self.app.output, self.app.copy_button, self.app.run_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(
                widget.winfo_rooty() + widget.winfo_height(), self.root.winfo_rooty() + 620
            )
        self.assertGreater(self.app.source.winfo_height(), 80)
        self.assertGreater(self.app.summary.winfo_height(), 80)

    def test_advanced_options_remain_available_and_keep_values(self):
        self.root.deiconify()
        self.app.text_settings_button.invoke()
        self.root.update()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        choices = [w for w in self.descendants(window) if isinstance(w, ttk.Combobox)]
        choices[0].set("Деловой")
        choices[1].set(EDIT_CHOICES["rephrase"])
        self.app.spaces.set(True)
        self.app.confusables.set(True)
        self.app.nfc.set(True)
        self.app.second_pass.set(True)
        self.button(window, "Готово").invoke()
        self.assertEqual(self.app.depth_key(), "rephrase")
        self.assertEqual(self.app.genre_key(), "business")
        self.app.text_settings()
        reopened = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        self.assertTrue(self.app.spaces.get())
        self.assertTrue(self.app.confusables.get())
        self.assertTrue(self.app.nfc.get())
        self.assertTrue(self.app.second_pass.get())
        self.button(reopened, "Стиль и важные слова").invoke()
        style_window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        fields = [w for w in self.descendants(style_window) if isinstance(w, tk.Text)]
        fields[0].insert("1.0", "Мой спокойный стиль.")
        fields[1].insert("1.0", "Shelter\nНазвание продукта")
        self.button(style_window, "Применить").invoke()
        self.assertEqual(self.app.voice, "Мой спокойный стиль.")
        self.assertEqual(self.app.terms, ("Shelter", "Название продукта"))

    def test_external_chat_guides_copy_and_paste_without_sending(self):
        source = "Сервис осуществляет проверку 10 файлов."
        answer = "Сервис проводит проверку 10 файлов."
        self.app.set_source(source)
        self.app.clipboard("До открытия")
        with patch("shelter_humanizer.gui.webbrowser.open") as browser:
            self.app.chat_workflow()
            window = next(
                w
                for w in self.root.winfo_children()
                if isinstance(w, tk.Toplevel) and w is not self.app.review_window
            )
            self.assertEqual(read_clipboard(self.root), "До открытия")
            browser.assert_not_called()
            self.button(window, "Копировать задание с текстом").invoke()
            self.assertIn(source, read_clipboard(self.root))
            self.assertEqual(self.app.get_text(self.app.source), source)
            self.button(window, "Открыть ChatGPT ↗").invoke()
            browser.assert_called_once_with("https://chatgpt.com/", new=2)
            self.app.clipboard(answer)
            self.button(window, "Вставить ответ в результат").invoke()
            self.assertEqual(self.app.get_text(self.app.output), answer)
            self.assertEqual(self.app.result.original, source)
            self.assertTrue(self.app.details_visible)

    def test_external_chat_rejects_task_instead_of_answer_and_stale_source(self):
        self.app.set_source("Исходный текст 10")
        self.app.replace_text(self.app.output, "Прежний результат")
        self.app.chat_workflow()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        self.button(window, "Копировать задание с текстом").invoke()
        with patch("shelter_humanizer.gui.messagebox.showerror") as error:
            self.button(window, "Вставить ответ в результат").invoke()
            self.assertIn("ещё задание", error.call_args.args[1])
            self.app.set_source("Другой исходник 20")
            self.app.clipboard("Ответ для старого исходника")
            self.button(window, "Вставить ответ в результат").invoke()
            self.assertIn("Исходник изменился", error.call_args.args[1])
        self.assertEqual(self.app.get_text(self.app.output), "Прежний результат")
        window.destroy()

    def test_external_chat_answer_is_cleaned_after_import(self):
        self.app.set_source("Проверка текста. 👩‍💻")
        self.app.chat_workflow()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        self.app.clipboard("Про\u200bверка\u00a0те\u00adкста. 👩‍💻")
        self.button(window, "Вставить ответ в результат").invoke()
        self.assertEqual(self.app.get_text(self.app.output), "Проверка текста. 👩‍💻")
        self.assertEqual(self.app.get_text(self.app.source), "Проверка текста. 👩‍💻")

    def test_meaning_warnings_are_visible_without_manual_disclosure(self):
        self.root.deiconify()
        self.app.show_result(compare("Примерно 10 файлов", "20 файлов"))
        self.root.update()
        self.assertTrue(self.app.tabs.winfo_ismapped())
        self.assertIn("замечания", self.app.result_note.get())

    def test_saved_model_reconnects_on_start_without_installing(self):
        self.app.close()
        runtime = MagicMock()
        runtime.closed = threading.Event()
        runtime.selected_model.return_value = "deepseek-r1:8b-0528-qwen3-q4_K_M"
        runtime.process = None
        runtime.start.return_value = "http://127.0.0.1:12345"
        self.root = tk.Tk()
        self.root.withdraw()
        with patch("shelter_humanizer.gui.ManagedRuntime", return_value=runtime):
            self.app = App(self.root)
        self.root.update()
        self.wait_for_worker()
        self.assertEqual(self.app.mode.get(), "ollama")
        self.assertEqual(self.app.configs["ollama"].model, "deepseek-r1:8b-0528-qwen3-q4_K_M")
        runtime.install.assert_not_called()
        runtime.start.assert_called_once()

    def test_tip_disappears_when_opening_another_window(self):
        self.root.deiconify()
        self.root.update()
        tip = self.app.copy_button.shelter_tip
        tip.show()
        self.assertIsNotNone(tip.window)
        self.app.mode.set("api")
        self.app.settings()
        self.assertIsNone(tip.window)
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w is not self.app.review_window
        )
        window.destroy()

    def test_tip_disappears_when_owner_is_minimized(self):
        self.root.deiconify()
        self.root.update()
        tip = self.app.copy_button.shelter_tip
        tip.show()
        self.root.withdraw()
        self.root.update()
        self.assertIsNone(tip.window)

    def test_tip_disappears_when_another_window_receives_focus(self):
        self.root.deiconify()
        self.root.focus_force()
        self.root.update()
        tip = self.app.copy_button.shelter_tip
        tip.show()
        other = tk.Toplevel(self.root)
        try:
            other.focus_force()
            self.root.update()
            tip.check_owner()
            self.assertIsNone(tip.window)
        finally:
            other.destroy()

    def test_saved_model_starts_again_without_installation(self):
        runtime = MagicMock()
        runtime.closed = threading.Event()
        runtime.selected_model.return_value = PRESETS[0].name
        runtime.process = None
        runtime.start.return_value = "http://127.0.0.1:12345"
        self.app.runtime = runtime
        self.app.mode.set("ollama")
        self.app.select_mode()
        deadline = time.monotonic() + 4
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.managed_connection)
        self.assertEqual(self.app.configs["ollama"].url, runtime.start.return_value)
        runtime.install.assert_not_called()
        runtime.start.assert_called_once()

    def test_saved_model_does_not_override_explicit_external_connection(self):
        from shelter_humanizer.providers import ProviderConfig

        runtime = MagicMock()
        runtime.selected_model.return_value = PRESETS[0].name
        self.app.runtime = runtime
        self.app.configs["ollama"] = ProviderConfig(
            url="http://localhost:11434", model="custom-model"
        )
        self.app.mode.set("ollama")
        self.app.select_mode()
        runtime.start.assert_not_called()
        self.assertEqual(self.app.configs["ollama"].model, "custom-model")

    def test_cancelled_installation_stops_owned_runtime_without_applying_connection(self):
        runtime = MagicMock()
        runtime.closed = threading.Event()
        runtime.selected_model.return_value = ""
        runtime.start.return_value = "http://127.0.0.1:12345"
        self.app.runtime = runtime
        with patch("shelter_humanizer.local_setup.Client") as client:
            from shelter_humanizer.local_models import DownloadCancelled

            client.return_value.models.side_effect = DownloadCancelled("cancelled")
            self.app.setup_local()
            wizard = self.app.local_wizard
            wizard.start(PRESETS[0].name)
            deadline = time.monotonic() + 4
            while wizard.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.02)
            self.assertFalse(wizard.busy)
            self.assertEqual(self.app.mode.get(), "offline")
            runtime.stop.assert_called_once()
            runtime.save_model.assert_not_called()

    def test_manual_output_is_not_overwritten_by_a_late_reply(self):
        started, finish = threading.Event(), threading.Event()
        client = MagicMock()

        def complete(messages):
            started.set()
            finish.wait(3)
            return "Сервис проверяет файлы."

        client.complete.side_effect = complete
        self.app.set_source("Сервис осуществляет проверку файлов.")
        self.app.mode.set("ollama")
        self.app.configs["ollama"] = self.app.configs["ollama"].__class__(model="test")
        self.app.second_pass.set(False)
        self.root.update()
        with (
            patch("shelter_humanizer.gui.Client", return_value=client),
            patch.object(self.app, "offer_pending_result") as offer,
        ):
            self.app.run("edit")
            self.assertTrue(started.wait(2))
            self.app.replace_text(self.app.output, "Мои ручные изменения.")
            self.root.update()
            finish.set()
            self.wait_for_worker()
        self.assertEqual(self.app.get_text(self.app.output), "Мои ручные изменения.")
        self.assertEqual(self.app.pending_result.text, "Сервис проверяет файлы.")
        offer.assert_called_once()

    def test_export_keeps_processing_warnings_and_recomputes_manual_comparison(self):
        client = MagicMock()
        client.complete.side_effect = ["Цена 200 ₽.", ProviderError("timeout")]
        from shelter_humanizer.service import rewrite

        self.app.set_source("Цена 200 ₽.")
        self.app.show_result(rewrite("Цена 200 ₽.", client))
        report = self.app.current_result()
        self.assertTrue(any("вычитка не завершилась" in warning for warning in report.warnings))
        self.app.replace_text(self.app.output, "Цена 300 ₽.")
        report = self.app.current_result()
        self.assertIn("manual_edit", report.flags)
        self.assertTrue(any("числа" in warning for warning in report.warnings))
        self.assertTrue(any("вычитка не завершилась" in warning for warning in report.warnings))

    def test_setup_actions_remain_visible_with_large_fonts(self):
        from shelter_humanizer.local_setup import LocalSetup

        original = self.root.tk.call("tk", "scaling")
        try:
            self.root.tk.call("tk", "scaling", 2.0)
            with tempfile.TemporaryDirectory() as directory:
                wizard = LocalSetup(
                    self.root,
                    lambda: self.app.colors,
                    lambda *args: None,
                    ManagedRuntime(directory),
                )
                wizard.window.attributes("-alpha", 0)
                wizard.window.transient("")
                wizard.window.geometry("720x540+10000+10000")
                wizard.window.deiconify()
                self.root.update()
                self.assertTrue(wizard.install_button.winfo_ismapped())
                self.assertGreater(wizard.install_button.winfo_height(), 10)
                self.assertTrue(wizard.window.resizable()[1])
                wizard.close()
        finally:
            self.root.tk.call("tk", "scaling", original)

    def test_installer_confirm_stays_inside_small_window_and_wheel_reaches_details(self):
        from shelter_humanizer.local_setup import LocalSetup
        from shelter_humanizer.panels import ScrollArea

        original = self.root.tk.call("tk", "scaling")
        try:
            for scaling, geometry in (
                (1.0, "640x480"),
                (1.5, "540x400"),
                (2.0, "480x360"),
                (2.67, "480x360"),
            ):
                with self.subTest(scaling=scaling, geometry=geometry):
                    self.root.tk.call("tk", "scaling", scaling)
                    with tempfile.TemporaryDirectory() as directory:
                        wizard = LocalSetup(
                            self.root,
                            lambda: self.app.colors,
                            lambda *args: None,
                            ManagedRuntime(directory),
                        )
                        try:
                            wizard.window.attributes("-alpha", 0)
                            wizard.window.transient("")
                            wizard.window.geometry(geometry + "+10000+10000")
                            wizard.window.deiconify()
                            self.root.update()
                            button = wizard.install_button
                            self.assertTrue(button.winfo_ismapped())
                            self.assertEqual(button.winfo_width(), button.winfo_reqwidth())
                            self.assertGreaterEqual(
                                button.winfo_rootx(), wizard.window.winfo_rootx()
                            )
                            self.assertLessEqual(
                                button.winfo_rootx() + button.winfo_width(),
                                wizard.window.winfo_rootx() + wizard.window.winfo_width(),
                            )
                            self.assertLessEqual(
                                button.winfo_rooty() + button.winfo_height(),
                                wizard.window.winfo_rooty() + wizard.window.winfo_height(),
                            )
                            area = next(
                                w
                                for w in self.descendants(wizard.window)
                                if isinstance(w, ScrollArea)
                            )
                            start = area.canvas.yview()[0]
                            area.body.event_generate("<MouseWheel>", delta=-120)
                            self.root.update()
                            self.assertGreater(area.canvas.yview()[0], start)
                            with (
                                patch.object(wizard, "start") as start_install,
                                patch(
                                    "shelter_humanizer.local_setup.messagebox.askyesno",
                                    return_value=False,
                                ) as confirmation,
                            ):
                                button.invoke()
                                confirmation.assert_called_once()
                                start_install.assert_not_called()
                        finally:
                            wizard.close()
        finally:
            self.root.tk.call("tk", "scaling", original)

    def test_window_close_can_be_cancelled_without_losing_text(self):
        self.app.set_source("Не потерять текст.")
        with patch("shelter_humanizer.gui.messagebox.askyesnocancel", return_value=None):
            self.app.request_close()
        self.assertFalse(self.app.closed)
        self.assertEqual(self.app.get_text(self.app.source), "Не потерять текст.")

    def test_source_replacement_requires_confirmation(self):
        self.app.set_source("Мой исходник.")
        with patch("shelter_humanizer.gui.messagebox.askyesno", return_value=False):
            self.assertFalse(self.app.replace_source("Новый текст."))
        self.assertEqual(self.app.get_text(self.app.source), "Мой исходник.")

    def test_custom_instruction_can_apply_and_reset_without_main_screen_button(self):
        from shelter_humanizer.preferences import Preferences
        from shelter_humanizer.prompts import SYSTEM

        with tempfile.TemporaryDirectory() as directory:
            self.app.preferences = Preferences(Path(directory) / "settings.json")
            self.app.instruction_settings()
            window = next(
                w
                for w in self.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "редакторская инструкция" in w.title()
            )
            box = next(w for w in self.descendants(window) if isinstance(w, tk.Text))
            self.assertEqual(self.app.get_text(box), SYSTEM)
            self.app.replace_text(box, "Используй короткие предложения.")
            self.button(window, "Применить").invoke()
            self.assertEqual(self.app.instruction, "Используй короткие предложения.")
            self.assertFalse(self.app.preferences.path.exists())
            self.app.instruction_settings()
            window = next(
                w
                for w in self.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "редакторская инструкция" in w.title()
            )
            self.button(window, "Вернуть предустановку").invoke()
            self.button(window, "Применить").invoke()
            self.assertIsNone(self.app.instruction)

    def test_custom_instruction_persists_only_after_opt_in_and_reset_deletes_it(self):
        from shelter_humanizer.preferences import Preferences

        with tempfile.TemporaryDirectory() as directory:
            self.app.preferences = Preferences(Path(directory) / "settings.json")
            self.app.instruction_settings()
            window = next(
                w
                for w in self.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "редакторская инструкция" in w.title()
            )
            box = next(w for w in self.descendants(window) if isinstance(w, tk.Text))
            self.app.replace_text(box, "Не меняй стиль автора.")
            checkbox = next(w for w in self.descendants(window) if isinstance(w, ttk.Checkbutton))
            checkbox.invoke()
            self.button(window, "Применить").invoke()
            self.assertEqual(self.app.preferences.load()["instruction"], "Не меняй стиль автора.")
            self.app.instruction_settings()
            window = next(
                w
                for w in self.root.winfo_children()
                if isinstance(w, tk.Toplevel) and "редакторская инструкция" in w.title()
            )
            self.button(window, "Вернуть предустановку").invoke()
            self.button(window, "Применить").invoke()
            self.assertFalse(self.app.preferences.path.exists())

    def test_custom_model_requires_confirmation_and_passes_only_validated_tag(self):
        from shelter_humanizer.local_setup import LocalSetup

        with tempfile.TemporaryDirectory() as directory:
            wizard = LocalSetup(
                self.root, lambda: self.app.colors, lambda *args: None, ManagedRuntime(directory)
            )
            wizard.notebook.select(1)
            wizard.custom_name.set("author/editor:q4")
            with patch.object(wizard, "start") as start:
                with patch("shelter_humanizer.local_setup.messagebox.askyesno", return_value=False):
                    wizard.download()
                    start.assert_not_called()
                with patch("shelter_humanizer.local_setup.messagebox.askyesno", return_value=True):
                    wizard.download()
                    start.assert_called_once_with(
                        "author/editor:q4", custom=True, gguf=None, installed_only=False
                    )
                start.reset_mock()
                wizard.custom_name.set("https://untrusted.example/model")
                wizard.download()
                start.assert_not_called()
            wizard.close()

    def test_fixed_manual_result_recomputes_completeness_warning(self):
        source = "В библиотеке готовят встречу с авторами. " * 20
        self.app.show_result(compare(source, "Встреча состоится.", mode="ai"))
        self.assertIn("content_shortened", self.app.current_result().flags)
        self.app.replace_text(self.app.output, source)
        current = self.app.current_result()
        self.assertNotIn("content_shortened", current.flags)
        self.assertFalse(any("сократился" in warning for warning in current.warnings))

    def test_long_document_resumes_in_gui_without_applying_partial_answer(self):
        source = ("Обычный текст абзаца для редактуры. " * 90 + "\n\n") * 3
        calls = []

        class Model:
            config = self.app.configs["ollama"].__class__(model="test")

            def complete(self, prompt):
                calls.append(prompt)
                if len(calls) == 2:
                    raise ProviderError("synthetic failure")
                return json.loads(prompt[-1]["content"].split("\n", 1)[1])["original"]

        self.app.set_source(source)
        self.root.update()
        self.app.replace_text(self.app.output, "Предыдущий результат.")
        self.app.mode.set("ollama")
        self.app.configs["ollama"] = Model.config
        self.app.long_mode.set(True)
        self.app.second_pass.set(False)
        with (
            patch("shelter_humanizer.gui.Client", return_value=Model()),
            patch("shelter_humanizer.gui.messagebox.askyesno", return_value=True),
            patch.object(self.app, "error"),
        ):
            self.app.run("edit")
            self.wait_for_worker()
            self.assertEqual(self.app.get_text(self.app.output), "Предыдущий результат.")
            self.assertEqual(len(self.app.document_session.results), 1)
            expected_calls = len(self.app.document_session.parts) + 1
            self.app.run("edit")
            self.wait_for_worker()
        self.assertEqual(len(calls), expected_calls)
        self.assertEqual(self.app.get_text(self.app.output), source)
        self.assertIn("chunked", self.app.result.flags)

    def test_context_menu_copies_cuts_pastes_unicode_and_undoes(self):
        self.app.set_source("Русский текст 👩‍💻")
        text = self.app.source
        text.tag_add("sel", "1.0", "end-1c")
        menu, index = self.menu_action(text, "Копировать")
        menu.invoke(index)
        self.assertEqual(read_clipboard(self.root), "Русский текст 👩‍💻")
        menu, index = self.menu_action(text, "Вырезать")
        menu.invoke(index)
        self.assertEqual(self.app.get_text(text), "")
        menu, index = self.menu_action(text, "Вставить")
        menu.invoke(index)
        self.assertEqual(self.app.get_text(text), "Русский текст 👩‍💻")
        menu, index = self.menu_action(text, "Отменить")
        menu.invoke(index)
        self.assertEqual(self.app.get_text(text), "")
        menu, index = self.menu_action(text, "Повторить")
        menu.invoke(index)
        self.assertEqual(self.app.get_text(text), "Русский текст 👩‍💻")

    def test_context_menu_replaces_selection_and_readonly_protects_text(self):
        from shelter_humanizer.clipboard import write

        self.app.replace_text(self.app.output, "Старый текст")
        box = self.app.output
        box.tag_add("sel", "1.0", "end-1c")
        write(self.root, "Новый текст")
        menu, index = self.menu_action(box, "Вставить")
        menu.invoke(index)
        self.assertEqual(self.app.get_text(box), "Новый текст")
        box.configure(state="disabled")
        box.tag_add("sel", "1.0", "end-1c")
        for action in ("Вставить", "Вырезать", "Удалить выделенное", "Отменить"):
            menu, index = self.menu_action(box, action)
            self.assertEqual(menu.entrycget(index, "state"), "disabled")
            menu.invoke(index)
        menu, index = self.menu_action(box, "Копировать")
        menu.invoke(index)
        self.assertEqual(read_clipboard(self.root), "Новый текст")
        self.assertEqual(self.app.get_text(box), "Новый текст")

    def test_context_menu_for_entry_and_readonly_model_choice(self):
        from shelter_humanizer.clipboard import write
        from shelter_humanizer.ui_helpers import bind_editing

        self.app.mode.set("api")
        self.app.settings()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w.title() == "Подключение к ИИ"
        )
        entry = next(w for w in self.descendants(window) if type(w) is ttk.Entry)
        entry.delete(0, "end")
        write(self.root, "Пример 👩‍💻")
        menu, index = self.menu_action(entry, "Вставить")
        menu.invoke(index)
        self.assertEqual(entry.get(), "Пример 👩‍💻")
        entry.selection_range(0, "end")
        menu, index = self.menu_action(entry, "Вырезать")
        menu.invoke(index)
        self.assertEqual(entry.get(), "")
        self.assertEqual(read_clipboard(self.root), "Пример 👩‍💻")
        chooser = ttk.Combobox(window, values=["Qwen"], state="readonly")
        chooser.set("Qwen")
        chooser.selection_range(0, "end")
        bind_editing(chooser)
        menu, index = self.menu_action(chooser, "Вставить")
        self.assertEqual(menu.entrycget(index, "state"), "disabled")
        window.destroy()

    def test_right_click_and_keyboard_open_one_menu_without_losing_selection(self):
        from shelter_humanizer.ui_helpers import bind_editing

        self.root.attributes("-alpha", 0)
        self.root.geometry("960x620+10000+10000")
        self.root.deiconify()
        self.root.update()
        self.app.set_source("Выделенный текст")
        text = self.app.source
        text.tag_add("sel", "1.0", "end-1c")
        menu = text.shelter_menu
        bind_editing(text)
        self.assertIs(text.shelter_menu, menu)
        text.focus_force()
        self.root.update()
        with patch.object(menu, "tk_popup") as popup:
            text.event_generate("<Button-3>", x=5, y=5)
            self.root.update()
            self.assertEqual(popup.call_count, 1)
            self.assertEqual(text.get("sel.first", "sel.last"), "Выделенный текст")
            text.event_generate("<Shift-F10>")
            self.root.update()
            self.assertEqual(popup.call_count, 2)

    def test_cpu_setting_keeps_owned_connection_and_request_deadline(self):
        from shelter_humanizer.providers import ProviderConfig

        self.app.mode.set("ollama")
        self.app.configs["ollama"] = ProviderConfig(
            url="http://127.0.0.1:11435", model="qwen3.5:4b", generation_timeout=1200
        )
        self.app.managed_connection = True
        self.app.settings()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w.title() == "Подключение к ИИ"
        )
        cpu = next(
            w
            for w in self.descendants(window)
            if isinstance(w, ttk.Checkbutton) and str(w.cget("text")).startswith("Только процессор")
        )
        cpu.invoke()
        self.button(window, "Применить").invoke()
        self.assertTrue(self.app.configs["ollama"].cpu_only)
        self.assertTrue(self.app.managed_connection)
        self.assertEqual(self.app.configs["ollama"].generation_timeout, 1200)

    def test_last_generation_failure_survives_successful_connection_check(self):
        failure = {
            "provider": "ollama",
            "error_code": "ollama_gpu",
            "engine_error": "gpu",
            "model": "qwen3.5:4b",
            "cpu_only": False,
            "stage": "editing",
        }
        self.app.events.put(("diagnostics", self.app.revision, failure))
        self.app._poll()
        self.app.diagnostics_settings()
        window = next(
            w
            for w in self.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w.title().startswith("Диагностика")
        )
        box = next(w for w in self.descendants(window) if isinstance(w, tk.Text))
        token = object()
        button = self.button(window, "Проверить подключение")
        self.app.health_requests[token] = (window, box, button, threading.Event())
        success = {"provider": "ollama", "error_code": "none", "stage": "connection"}
        self.app.events.put(("health", 0, (token, success)))
        self.app._poll()
        visible = json.loads(self.app.get_text(box))
        self.assertEqual(visible["error_code"], "none")
        self.assertEqual(visible["last_failure"]["error_code"], "ollama_gpu")
        self.button(window, "Копировать диагностику").invoke()
        self.assertEqual(json.loads(read_clipboard(self.root)), visible)
        window.destroy()


if __name__ == "__main__":
    unittest.main()
