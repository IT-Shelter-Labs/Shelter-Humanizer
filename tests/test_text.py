import unittest
from unittest.mock import patch

from shelter_humanizer.models import validate_text
from shelter_humanizer.protection import (
    Masked,
    fact_changes,
    mask,
    overlaps,
    protected_spans,
    validate_protected,
)
from shelter_humanizer.service import analyze, offline
from shelter_humanizer.style import light_edit, scan_style
from shelter_humanizer.unicode_check import CleanOptions, clean_unicode, scan_unicode


class UnicodeTests(unittest.TestCase):
    def test_remove_controls_with_locations(self):
        text = "Текст\nпро\u200bверка\x00\u202e!"
        items = scan_unicode(text)
        self.assertEqual(
            [(i.code, i.line, i.column) for i in items],
            [("U+200B", 2, 4), ("U+0000", 2, 10), ("U+202E", 2, 11)],
        )
        self.assertEqual(clean_unicode(text), "Текст\nпроверка!")
        self.assertIn("\\u202E", items[-1].excerpt)

    def test_legitimate_russian_unicode_is_kept(self):
        text = "Ёлка — «привет»… сло́во\tда\r\n👩🏽‍💻 👨‍👩‍👧‍👦 ❤️ 1️⃣"
        self.assertEqual(scan_unicode(text), [])
        self.assertEqual(clean_unicode(text), text)

    def test_special_spaces_optional(self):
        text = "Цена: 2\u00a0000\u202f₽"
        self.assertEqual(clean_unicode(text), text)
        self.assertEqual(clean_unicode(text, CleanOptions(spaces=True)), "Цена: 2 000 ₽")
        self.assertTrue(all(not i.auto_fix for i in scan_unicode(text)))

    def test_confusables_review_only(self):
        text = "пpоверка IT-школа OpenAI Python"
        mixed = [item for item in scan_unicode(text) if item.code == "MIXED_SCRIPT"]
        self.assertEqual(len(mixed), 1)
        self.assertEqual(mixed[0].replacement, "проверка")
        self.assertEqual(clean_unicode(text), text)
        self.assertEqual(
            clean_unicode(text, CleanOptions(confusables=True)), "проверка IT-школа OpenAI Python"
        )

    def test_mixed_brand_not_guessed(self):
        self.assertIsNone(scan_unicode("ITшкола")[0].replacement)

    def test_protected_content_reported_not_edited(self):
        text = "«про\u200bверка» `про\u200bверка` https://example.org/про\u200bверка\n```\nпро\u200bверка\n```"
        self.assertEqual(len(scan_unicode(text)), 4)
        self.assertTrue(all(not i.auto_fix for i in scan_unicode(text)))
        self.assertEqual(clean_unicode(text), text)

    def test_russian_joiner_vs_script_joiner(self):
        text = "про\u200dверка فارسی\u200cنامه"
        self.assertEqual(clean_unicode(text), "проверка فارسی\u200cنامه")
        self.assertEqual(len(scan_unicode(text)), 2)

    def test_nfc_does_not_normalize_quotes(self):
        text = "и\u0306 «и\u0306»"
        self.assertEqual(clean_unicode(text, CleanOptions(nfc=True)), "й «и\u0306»")

    def test_tag_emoji_and_isolated_tag(self):
        flag = "🏴\U000e0067\U000e0062\U000e007f"
        self.assertEqual(scan_unicode(flag), [])
        self.assertEqual(len(scan_unicode("a\U000e0067")), 1)
        self.assertEqual(clean_unicode(flag), flag)

    def test_noncharacters_and_surrogates(self):
        self.assertEqual(clean_unicode("a\ud800\ufdd0\uffffb"), "ab")

    def test_cleanup_idempotent(self):
        text = "про\u200bверка пpоверка\ufeff 2\u00a0000"
        options = CleanOptions(True, True, True)
        result = clean_unicode(text, options)
        self.assertEqual(clean_unicode(result, options), result)

    def test_limits(self):
        self.assertRaises(ValueError, validate_text, "а" * 200_001)
        self.assertRaises(ValueError, validate_text, None)


class ProtectionTests(unittest.TestCase):
    def test_roundtrip_nested_link_fence_quote_numbers_terms(self):
        text = "Цена — 2 000 ₽ и 2 минуты. «Цитата» [подробнее](https://host/a_(b))\n```py\nx=10\n```\nShelter API и `x=1`"
        value = mask(text, ("Shelter API",))
        self.assertEqual(value.restore(value.text), text)
        for atom in ("2 000 ₽", "2 минуты", "Shelter API", "Цитата", "x=10", "https://host"):
            self.assertNotIn(atom, value.text)

    def test_missing_duplicate_and_unknown_tokens_rejected(self):
        value = mask("Скидка 10 %")
        token = value.replacements[0][0]
        for draft in (
            value.text.replace(token, ""),
            value.text + token,
            value.text + value.prefix + "999__",
        ):
            with self.subTest(draft=draft):
                self.assertRaises(ValueError, value.restore, draft)
        self.assertRaises(ValueError, value.restore, value.text + value.prefix + "unknown__")

    def test_prefix_collision_regenerated(self):
        with patch("shelter_humanizer.protection.secrets.token_hex", side_effect=["abc", "def"]):
            value = mask("__SH_abc_0__ и 1")
        self.assertEqual(value.prefix, "__SH_def_")
        self.assertEqual(value.restore(value.text), "__SH_abc_0__ и 1")

    def test_open_fence_and_blockquotes_protected(self):
        text = "> исходник\nПосле\n~~~python\nx=1\n"
        spans = protected_spans(text)
        self.assertTrue(overlaps(1, 2, spans))
        self.assertFalse(overlaps(text.index("После"), text.index("После") + 5, spans))
        self.assertTrue(overlaps(text.index("x=1"), len(text), spans))

    def test_counter_tracks_duplicate_numbers(self):
        self.assertTrue(fact_changes("10 файлов и ещё 10 файлов", "10 файлов"))
        self.assertTrue(fact_changes("Возможно, работает.", "Работает."))
        self.assertTrue(fact_changes("https://a.org", "https://b.org"))

    def test_empty_atoms_restore(self):
        self.assertEqual(Masked("Текст", (), "__SH_x_").restore("Новый текст"), "Новый текст")

    def test_bare_url_sentence_punctuation_is_not_part_of_the_address(self):
        source = "Описание: https://example.org/guide"
        validate_protected(source, source + ".")
        self.assertEqual(fact_changes(source, source + "."), [])
        value = mask(source + ".")
        self.assertEqual(value.replacements[0][1], "https://example.org/guide")
        self.assertTrue(value.text.endswith("."))
        self.assertRaises(ValueError, validate_protected, source, source + "/other.")

    def test_balanced_url_parentheses_and_explicit_markdown_punctuation_are_kept(self):
        text = "(https://example.org/a_(b)). [Точка](https://example.org/a.)"
        value = mask(text)
        self.assertEqual(value.restore(value.text), text)
        self.assertEqual(value.replacements[0][1], "https://example.org/a_(b)")
        self.assertEqual(value.replacements[1][1], "[Точка](https://example.org/a.)")
        self.assertRaises(ValueError, validate_protected, text, text.replace("a.)", "a)"))

    def test_overlap_boundaries(self):
        spans = [(1, 3), (5, 7)]
        self.assertFalse(overlaps(3, 5, spans))
        self.assertTrue(overlaps(2, 6, spans))
        self.assertTrue(overlaps(5, 6, spans))

    def test_long_unterminated_markdown_is_bounded(self):
        text = "[" * 10_000 + "\n[text](" + "a" * 10_000
        self.assertEqual(protected_spans(text), [])

    def test_inline_multiple_backticks_and_uppercase_url(self):
        text = "``x=`y` `` HTTPS://example.org/a"
        value = mask(text)
        self.assertEqual(value.restore(value.text), text)
        self.assertNotIn("x=", value.text)
        self.assertNotIn("example.org", value.text)

    def test_unfinished_quote_is_conservatively_protected(self):
        text = "Начало «" + "«" * 10_000
        self.assertEqual(protected_spans(text), [(7, len(text))])


class StyleTests(unittest.TestCase):
    def test_edit_sentence_case_and_keep_newline(self):
        text = "Важно отметить, что приложение осуществляет проверку.\nСледует подчеркнуть, что это удобно."
        self.assertEqual(light_edit(text), "Приложение проводит проверку.\nЭто удобно.")

    def test_quotes_urls_and_code_are_unchanged(self):
        text = (
            "«Осуществляет проверку» `осуществляет проверку` [Осуществляет проверку](https://a.org)"
        )
        self.assertEqual(scan_style(text), [])
        self.assertEqual(light_edit(text), text)

    def test_professional_genre_exceptions(self):
        text = "Сторона осуществляет проверку документов."
        self.assertEqual(light_edit(text, "business"), text)
        self.assertEqual(light_edit(text, "academic"), text)
        self.assertEqual(light_edit(text), "Сторона проводит проверку документов.")

    def test_review_only_claim_is_not_deleted(self):
        text = "Исследователи считают, что вода играет важную роль."
        self.assertGreater(len(scan_style(text)), 0)
        self.assertEqual(light_edit(text), text)

    def test_academic_preambles_are_reviewed_without_deleting_their_claims(self):
        text = "Одним из популярных направлений был стоицизм. Важное место занимал эпикуреизм."
        findings = scan_style(text, "academic")
        self.assertEqual([item.code for item in findings], ["PARAGRAPH_OPENING"] * 2)
        self.assertTrue(all(not item.auto_fix for item in findings))
        self.assertEqual(light_edit(text, "academic"), text)

    def test_light_preserves_atoms_and_negation(self):
        text = "Сервис не осуществляет проверку 10 файлов. Цена — 2 000 ₽. «Цитата»"
        result = offline(text)
        self.assertEqual(
            result.text, "Сервис не проводит проверку 10 файлов. Цена — 2 000 ₽. «Цитата»"
        )
        self.assertEqual(result.original, text)
        self.assertEqual(result.warnings, [])

    def test_analysis_is_not_origin_score(self):
        result = analyze("В современном мире всё меняется.")
        self.assertGreater(len(result.style), 0)
        self.assertFalse(hasattr(result, "ai_probability"))

    def test_object_cases_are_not_broken_by_generic_verb_substitution(self):
        text = "Сервис осуществляет обработку данных и производит анализ документов. Нужно осуществить установку приложения."
        self.assertEqual(light_edit(text), text)
        self.assertEqual(
            light_edit("Имеет возможность обработки данных."), "Имеет возможность обработки данных."
        )
        self.assertEqual(
            light_edit("Имеет возможность обработать данные."), "Может обработать данные."
        )

    def test_conjunction_replacement_does_not_break_punctuation(self):
        text = "Я работаю для того, чтобы помочь. Не вышло по той причине, что дверь закрыта."
        self.assertEqual(light_edit(text), text)
        self.assertEqual(len(scan_style(text)), 2)


if __name__ == "__main__":
    unittest.main()
