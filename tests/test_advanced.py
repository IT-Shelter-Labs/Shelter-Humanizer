import hashlib
import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from shelter_humanizer.budget import request_budget
from shelter_humanizer.diagnostics import snapshot
from shelter_humanizer.documents import DocumentPaused, rewrite_document, split_document
from shelter_humanizer.files import TextEncodingError, read_text
from shelter_humanizer.instance import SingleInstance
from shelter_humanizer.local_models import model_tag
from shelter_humanizer.managed_runtime import ManagedRuntime
from shelter_humanizer.preferences import Preferences
from shelter_humanizer.prompts import SYSTEM, copy_prompt, messages
from shelter_humanizer.providers import ProviderConfig, ProviderError
from shelter_humanizer.quality import completeness
from shelter_humanizer.service import rewrite


class Echo:
    config = ProviderConfig(model="qwen3.5:4b")

    def __init__(self, fail_at=None, transform=lambda text: text):
        self.calls = 0
        self.fail_at = fail_at
        self.transform = transform

    def complete(self, prompt):
        self.calls += 1
        if self.calls == self.fail_at:
            raise ProviderError("synthetic failure")
        data = json.loads(prompt[-1]["content"].split("\n", 1)[1])
        return self.transform(data["original"])


class InstructionTests(unittest.TestCase):
    def test_default_instruction_matches_evaluated_bytes(self):
        expected = "2d7a20a3b3917178a5bed75b697d73041291dab04d4018dbfd0040548b078a96"
        self.assertEqual(hashlib.sha256(SYSTEM.encode()).hexdigest(), expected)
        self.assertEqual(messages("Текст", "plain")[0]["content"], SYSTEM)

    def test_custom_instruction_is_used_with_mandatory_contract(self):
        prompt = messages("Цена 200 ₽. «Цитата»", "plain", instruction="Пиши проще.")
        self.assertTrue(prompt[0]["content"].startswith("Пиши проще."))
        self.assertIn("Обязательный формат Shelter Humanizer", prompt[0]["content"])
        self.assertIn("Цена 200 ₽", copy_prompt("Цена 200 ₽", instruction="Пиши проще."))
        self.assertRaisesRegex(
            ValueError,
            "числа",
            rewrite,
            "Цена 200 ₽.",
            Echo(transform=lambda text: "Цена 300 ₽."),
            instruction="Измени любые числа.",
            second_pass=False,
        )

    def test_invalid_custom_instruction_never_calls_model(self):
        for value in ("", " ", "x" * 12001, "bad\x00instruction"):
            client = Echo()
            self.assertRaises(
                ValueError, rewrite, "Текст", client, instruction=value, second_pass=False
            )
            self.assertEqual(client.calls, 0)

    def test_instruction_persistence_is_explicit_and_resettable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            settings = Preferences(path)
            self.assertEqual(settings.load(), {})
            self.assertFalse(path.exists())
            settings.save("Пиши проще.")
            self.assertEqual(Preferences(path).load(), {"instruction": "Пиши проще."})
            self.assertEqual(set(json.loads(path.read_text(encoding="utf-8"))), {"instruction"})
            settings.save(None)
            self.assertFalse(path.exists())
            for raw in ("[]", "null", '{"instruction":12}', '{"instruction":""}', "bad json"):
                path.write_text(raw, encoding="utf-8")
                self.assertEqual(settings.load(), {})


class CustomModelTests(unittest.TestCase):
    def test_malformed_selection_is_ignored_without_breaking_first_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            path = Path(directory) / "selection.json"
            for data in ([], {"model": []}, {"model": {}}, {"model": None}):
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(runtime.selected_model(), "")

    def test_names_are_models_not_urls_commands_or_cloud_routes(self):
        self.assertEqual(model_tag("author/my-model:Q4_K_M"), "author/my-model:Q4_K_M")
        self.assertEqual(model_tag("my-model"), "my-model:latest")
        for value in (
            "",
            "../../bad",
            "https://example.org/model",
            "x; calc",
            "x\nFROM bad",
            "x:cloud",
            "x:480b-cloud",
            "a/b/c",
            "x" * 161,
        ):
            self.assertRaises(ProviderError, model_tag, value)

    def test_custom_selection_requires_explicit_flag_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            self.assertRaises(ProviderError, runtime.save_model, "author/model:latest")
            runtime.save_model("author/model:latest", allow_custom=True)
            self.assertEqual(ManagedRuntime(directory).selected_model(), "author/model:latest")
            (Path(directory) / "selection.json").write_text('{"model":"../../bad","custom":true}')
            self.assertEqual(runtime.selected_model(), "")


class QualityTests(unittest.TestCase):
    def test_severe_shortening_is_visible_without_protected_atoms(self):
        source = "В библиотеке готовят встречу с местными авторами. " * 12
        result = rewrite(source, Echo(transform=lambda text: text[:49]), second_pass=False)
        self.assertIn("content_shortened", result.flags)
        self.assertTrue(any("сократился" in warning for warning in result.warnings))

    def test_reasonable_edit_is_not_flagged_and_list_loss_is(self):
        self.assertEqual(completeness("Сервис проводит проверку.", "Сервис проверяет."), ([], []))
        source = "- Первый пункт\n- Второй пункт\n- Третий пункт"
        self.assertIn("list_changed", completeness(source, "Первый пункт.")[1])

    def test_clear_script_change_is_flagged_without_claiming_authorship(self):
        source = "The library prepares a meeting with local authors. " * 5
        target = "Библиотека готовит встречу с местными авторами. " * 5
        warnings, flags = completeness(source, target)
        self.assertIn("language_changed", flags)
        self.assertFalse(any("написан ИИ" in value for value in warnings))


class DocumentTests(unittest.TestCase):
    def test_split_roundtrip_preserves_whitespace_quotes_code_and_links(self):
        source = (
            "  Начало абзаца. " * 35
            + "\n\n«Точная цитата. Ещё фраза».\n\n"
            + "Продолжение. " * 40
            + '\n\n```python\nprint("hello")\n```\n\nhttps://example.org/docs\n'
        )
        parts = split_document(source, limit=500)
        self.assertEqual("".join(p.prefix + p.text + p.suffix for p in parts), source)
        for fragment in (
            "«Точная цитата. Ещё фраза»",
            '```python\nprint("hello")\n```',
            "https://example.org/docs",
        ):
            self.assertEqual(sum(fragment in part.text for part in parts), 1)

    def test_unbreakable_protected_fragment_is_not_silently_cut(self):
        self.assertRaises(ValueError, split_document, "«" + "слово " * 150 + "»", limit=500)

    def test_failed_part_can_resume_without_regenerating_completed_parts(self):
        source = ("Первый абзац содержит обычный текст. " * 15 + "\n\n") * 4
        first = Echo(fail_at=2)
        with self.assertRaises(DocumentPaused) as caught:
            rewrite_document(source, first, limit=600, second_pass=False)
        checkpoint = caught.exception.session
        self.assertEqual(len(checkpoint.results), 1)
        second = Echo()
        result, completed = rewrite_document(
            source, second, limit=600, second_pass=False, session=checkpoint
        )
        self.assertEqual(result.original, source)
        self.assertEqual(result.text, source)
        self.assertEqual(second.calls, len(completed.parts) - 1)
        self.assertIn("chunked", result.flags)

    def test_settings_change_invalidates_old_progress(self):
        source = ("Текст обычного абзаца. " * 30 + "\n\n") * 3
        with self.assertRaises(DocumentPaused) as caught:
            rewrite_document(source, Echo(fail_at=2), limit=600, second_pass=False)
        client = Echo()
        result, session = rewrite_document(
            source,
            client,
            limit=600,
            second_pass=False,
            instruction="Пиши проще.",
            session=caught.exception.session,
        )
        self.assertEqual(client.calls, len(session.parts))
        self.assertEqual(result.text, source)

    def test_rejected_protected_data_keeps_prior_parts_for_resume(self):
        source = ("Цена 200 ₽. Обычный текст абзаца. " * 20 + "\n\n") * 3
        client = Echo()
        original_complete = client.complete

        def bad_second(messages):
            text = original_complete(messages)
            return text.replace("200", "300") if client.calls == 2 else text

        client.complete = bad_second
        with self.assertRaises(DocumentPaused) as caught:
            rewrite_document(source, client, limit=700, second_pass=False)
        self.assertEqual(len(caught.exception.session.results), 1)
        result, _ = rewrite_document(
            source, Echo(), limit=700, second_pass=False, session=caught.exception.session
        )
        self.assertEqual(result.text, source)

    def test_pre_cancel_keeps_all_work_and_does_not_call_model(self):
        cancel = threading.Event()
        cancel.set()
        client = Echo()
        with self.assertRaises(DocumentPaused) as caught:
            rewrite_document("Текст", client, cancel=cancel)
        self.assertEqual(client.calls, 0)
        self.assertEqual(caught.exception.session.results, [])


class SupportTests(unittest.TestCase):
    def test_model_path_from_local_api_is_not_exported_in_diagnostics(self):
        data = snapshot(
            ProviderConfig("api", "http://localhost:1234/v1", r"C:\Users\private\model.gguf")
        )
        self.assertNotIn("private", json.dumps(data))
        self.assertEqual(data["model"], "[model ID hidden]")

    def test_diagnostics_are_allowlisted_and_do_not_contain_credentials_or_text(self):
        config = ProviderConfig(
            "api", "https://private.example/secret-path", "test-model", "secret-key"
        )
        data = snapshot(
            config,
            {"prompt": "private text", "key": "secret-key", "request_time_s": 4.2},
            phase="private text",
            error_code="http_503",
        )
        raw = json.dumps(data)
        for secret in ("private.example", "secret-path", "secret-key", "private text"):
            self.assertNotIn(secret, raw)
        self.assertEqual(data["request_time_s"], 4.2)
        self.assertEqual(data["stage"], "unknown")

    def test_context_estimate_keeps_reply_reserve_and_rejects_oversized_task(self):
        small = [{"role": "user", "content": "Привет"}]
        context, reply, estimate = request_budget(small)
        self.assertGreaterEqual(context, reply + estimate)
        self.assertLess(context, 16384)
        self.assertRaisesRegex(
            ProviderError, "контекста", request_budget, [{"role": "user", "content": "я" * 100000}]
        )

    def test_bom_encodings_are_deterministic_and_ansi_requires_explicit_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "текст.txt"
            for encoding in ("utf-8-sig", "utf-16", "utf-32"):
                path.write_bytes("Ёлка и текст".encode(encoding))
                self.assertEqual(read_text(path), "Ёлка и текст")
            path.write_bytes("Ёлка и текст".encode("cp1251"))
            self.assertRaises(TextEncodingError, read_text, path)
            self.assertEqual(read_text(path, "cp1251"), "Ёлка и текст")

    @unittest.skipUnless(os.name == "nt", "Windows instance guard")
    def test_instance_guard_releases_without_stale_lock_file(self):
        identity = uuid.uuid4().hex
        with SingleInstance(identity) as first:
            self.assertTrue(first.owned)
            with SingleInstance(identity) as duplicate:
                self.assertFalse(duplicate.owned)
        with SingleInstance(identity) as restarted:
            self.assertTrue(restarted.owned)


if __name__ == "__main__":
    unittest.main()
