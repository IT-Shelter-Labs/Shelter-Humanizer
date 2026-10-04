"""UI-independent workflows. Original text is never modified in place."""

from __future__ import annotations

import re
from threading import Event

from .models import MAX_AI_TEXT, Analysis, Result, validate_text
from .prompts import DEPTHS, messages
from .proofread import InvalidCorrections, apply, sentences
from .proofread import messages as proofread_messages
from .protection import fact_changes, mask, qualification_changes, validate_protected
from .style import GENRES, almost_unchanged, editorial_targets, light_edit, scan_style
from .unicode_check import CleanOptions, clean_unicode, scan_unicode


class Cancelled(ValueError):
    pass


def analyze(text: str, genre: str = "plain") -> Analysis:
    validate_text(text)
    return Analysis(
        len(text),
        len(re.findall(r"[^\W_]+(?:[-’'][^\W_]+)*", text)),
        scan_unicode(text),
        scan_style(text, genre),
    )


def compare(original: str, edited: str, genre: str = "plain", mode: str = "manual") -> Result:
    validate_text(original)
    validate_text(edited)
    return Result(
        original,
        edited,
        mode,
        analyze(original, genre),
        analyze(edited, genre),
        fact_changes(original, edited),
    )


def offline(
    text: str, mode: str = "light", genre: str = "plain", options: CleanOptions = CleanOptions()
) -> Result:
    validate_text(text)
    if mode not in ("clean", "light"):
        raise ValueError("Неизвестный режим редактуры.")
    cleaned = clean_unicode(text, options)
    edited = light_edit(cleaned, genre) if mode == "light" else cleaned
    return compare(text, edited, genre, mode)


def rewrite(
    text: str,
    client,
    *,
    genre: str = "plain",
    voice: str = "",
    terms: tuple[str, ...] = (),
    second_pass: bool = True,
    depth: str = "edit",
    options: CleanOptions = CleanOptions(),
    cancel: Event | None = None,
    progress=None,
) -> Result:
    validate_text(text, MAX_AI_TEXT)
    if not text.strip():
        raise ValueError("Сначала вставьте текст.")
    if genre not in GENRES:
        raise ValueError("Неизвестный жанр.")
    if depth not in DEPTHS:
        raise ValueError("Неизвестная глубина редактуры.")
    validate_text(voice, 3000)
    if len(terms) > 100 or any(not isinstance(term, str) or len(term) > 200 for term in terms):
        raise ValueError("Не больше 100 защищённых терминов по 200 символов.")

    def cancelled():
        if cancel is not None and cancel.is_set():
            raise Cancelled("Обработка отменена. Исходник сохранён.")

    def checked(draft):
        validate_text(draft)
        validate_protected(text, draft, terms)
        return draft

    cancelled()
    if progress:
        progress("ИИ редактирует текст…")
    draft = client.complete(messages(text, genre, voice, depth=depth, terms=terms))
    cancelled()
    restored = checked(draft)
    review_warning = None
    if second_pass:
        if progress:
            progress("ИИ вычитывает результат…")
        parts = sentences(draft)
        corrections = client.complete(proofread_messages(text, genre, parts))
        cancelled()
        try:
            candidate = checked(apply(draft, corrections, parts))
            old_alerts = set(fact_changes(text, draft))
            new_alerts = set(fact_changes(text, candidate))

            def qualifier_drift(value):
                return sum(
                    abs(item["source_count"] - item["draft_count"])
                    for item in qualification_changes(text, value)
                )

            if (
                new_alerts - old_alerts
                or qualifier_drift(candidate) > qualifier_drift(draft)
                or len(editorial_targets(candidate)) > len(editorial_targets(draft))
            ):
                raise InvalidCorrections("Вычитка добавила замечания или вернула подводки.")
        except InvalidCorrections:
            review_warning = (
                "Предложенные ИИ исправления не прошли проверку вычитки. "
                "Показана первая редактура; проверьте её самостоятельно."
            )
        else:
            restored = candidate
    # Clean outside all protected atoms, including numeric typography and explicit terms.
    # Mask locally again; these new tokens are never sent to the model.
    if progress:
        progress("Очищаем скрытые символы и проверяем результат…")
    cleanup = mask(restored, terms)
    restored = cleanup.restore(clean_unicode(cleanup.text, options))
    checked(restored)
    result = compare(text, restored, genre, "ai")
    if review_warning:
        result.warnings.append(review_warning)
    if depth == "rephrase" and editorial_targets(text) and almost_unchanged(text, restored):
        result.warnings.append(
            "ИИ почти сохранил исходные формулировки, включая места для редактуры. "
            "Глубокая переработка могла не выполниться; сравните текст или попробуйте другую модель."
        )
    result.warnings.append(
        "Сравните смысл и авторский тон с исходником: автоматическая проверка не заменяет вычитку."
    )
    return result
