"""Conservative completeness signals, not a semantic or authorship score."""

import re


def completeness(original, draft, depth="edit"):
    warnings, flags = [], []

    def words(value):
        return re.findall(r"[^\W_]+", value, re.UNICODE)

    old, new = len(words(original)), len(words(draft))
    threshold = 0.6 if depth == "edit" else 0.4
    if old >= 35 and new < old * threshold:
        flags.append("content_shortened")
        warnings.append(
            "Текст сильно сократился. Возможно, пропали сведения или абзацы. "
            "Сравните весь результат с исходником перед использованием."
        )

    def bullets(value):
        return len(re.findall(r"(?m)^\s*(?:[-*+] |\d+[.)] )", value))

    if bullets(original) >= 3 and bullets(draft) < bullets(original):
        flags.append("list_changed")
        warnings.append("В результате стало меньше пунктов списка. Проверьте полноту содержания.")

    def letters(value):
        return len(re.findall(r"[А-Яа-яЁё]", value)), len(re.findall(r"[A-Za-z]", value))

    before, after = letters(original), letters(draft)
    if min(sum(before), sum(after)) >= 60:
        if (before[0] / sum(before) > 0.8 and after[1] / sum(after) > 0.8) or (
            before[1] / sum(before) > 0.8 and after[0] / sum(after) > 0.8
        ):
            flags.append("language_changed")
            warnings.append("Возможно, изменился язык текста. Проверьте результат по исходнику.")
    return warnings, flags
