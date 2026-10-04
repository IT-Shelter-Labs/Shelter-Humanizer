import json
import unittest

from shelter_humanizer.proofread import InvalidCorrections, apply, sentences
from shelter_humanizer.service import rewrite


def response(edits):
    return json.dumps({"edits": edits}, ensure_ascii=False)


class ProofreadTests(unittest.TestCase):
    def test_sentence_ids_keep_quotes_code_and_numeric_dates_whole(self):
        text = 'Она пишет: «Первое. Второе».\n`x = "a. b"` стоит 2.5 ₽. Сайт https://a.org/docs.'
        parts = sentences(text)
        self.assertEqual(
            [part.text for part in parts],
            [
                "Она пишет: «Первое. Второе».",
                '`x = "a. b"` стоит 2.5 ₽.',
                "Сайт https://a.org/docs.",
            ],
        )
        self.assertEqual(apply(text, response([]), parts), text)

    def test_duplicate_sentences_are_corrected_by_id_and_whitespace_is_preserved(self):
        text = "  Было тепло.\n\nБыло тепло.  "
        result = apply(
            text, response([{"id": "S002", "replacement": "Стало прохладно."}]), sentences(text)
        )
        self.assertEqual(result, "  Было тепло.\n\nСтало прохладно.  ")

    def test_invalid_json_ids_duplicates_or_replacements_reject_entire_patch(self):
        text = "Первое предложение. Второе предложение."
        invalid = [
            "[]",
            "not json",
            response([{"id": "S999", "replacement": "Текст."}]),
            response([{"id": "S001", "replacement": "Текст."}] * 2),
            response([{"id": "S001", "replacement": ""}]),
            response([{"id": "S001", "replacement": "а" * 601}]),
            response([{"id": "S001", "replacement": "Новый\nабзац."}]),
            response([{"id": "S001", "replacement": "Текст.", "command": "run"}]),
        ]
        for value in invalid:
            with self.subTest(value=value[:60]):
                self.assertRaises(InvalidCorrections, apply, text, value, sentences(text))

    def test_proofread_cannot_replace_most_of_a_long_text(self):
        text = "Уже хорошее предложение. " * 25
        edits = [{"id": part.id, "replacement": "Другая фраза."} for part in sentences(text)[:12]]
        self.assertRaises(InvalidCorrections, apply, text, response(edits), sentences(text))

    def test_invalid_proofread_keeps_first_draft_with_explicit_warning(self):
        class Client:
            calls = 0

            def complete(self, messages):
                self.calls += 1
                return "Сервис проверяет файлы." if self.calls == 1 else "Новый рерайт целиком."

        client = Client()
        original = "Сервис осуществляет проверку файлов."
        result = rewrite(original, client, depth="rephrase")
        self.assertEqual(result.original, original)
        self.assertEqual(result.text, "Сервис проверяет файлы.")
        self.assertTrue(any("не прошли проверку вычитки" in w for w in result.warnings))
        self.assertEqual(client.calls, 2)

    def test_proofread_number_changes_are_rejected(self):
        class Client:
            calls = 0

            def complete(self, messages):
                self.calls += 1
                return (
                    "Цена 200 ₽."
                    if self.calls == 1
                    else response([{"id": "S001", "replacement": "Цена 100 ₽."}])
                )

        self.assertRaisesRegex(ValueError, "числа", rewrite, "Цена 200 ₽.", Client())

    def test_proofread_does_not_restore_stock_intro_or_drop_a_condition(self):
        for replacement in (
            "Важно отметить, что сервис может работать без интернета.",
            "Сервис работает с интернетом.",
        ):
            with self.subTest(replacement=replacement):

                class Client:
                    calls = 0

                    def complete(self, messages):
                        self.calls += 1
                        return (
                            "Сервис может работать без интернета."
                            if self.calls == 1
                            else response([{"id": "S001", "replacement": replacement}])
                        )

                result = rewrite("Сервис может работать без интернета.", Client())
                self.assertEqual(result.text, "Сервис может работать без интернета.")
                self.assertTrue(any("не прошли проверку вычитки" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
