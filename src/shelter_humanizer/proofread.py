"""Apply bounded corrections to known sentences; never regenerate the whole draft."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .models import validate_text
from .prompts import DATA_RULES
from .protection import overlaps, protected_spans, qualification_changes
from .style import GENRES


class InvalidCorrections(ValueError):
    pass


@dataclass(frozen=True)
class Sentence:
    id: str
    start: int
    end: int
    text: str


def sentences(text: str) -> tuple[Sentence, ...]:
    spans = protected_spans(text, numbers=True)
    result, previous = [], 0

    def append(end):
        nonlocal previous
        start = previous
        while start < end and text[start].isspace():
            start += 1
        trimmed = end
        while trimmed > start and text[trimmed - 1].isspace():
            trimmed -= 1
        if start < trimmed:
            result.append(Sentence(f"S{len(result) + 1:03}", start, trimmed, text[start:trimmed]))
        previous = end

    for match in re.finditer(r"[.!?](?=\s|$)|\n", text):
        if not overlaps(match.start(), match.end(), spans):
            append(match.end())
    append(len(text))
    return tuple(result)


def messages(original: str, genre: str, parts: tuple[Sentence, ...]):
    instruction = """Ты — корректор. Проверяемый текст дан в draft_sentences с идентификаторами.
source_for_fact_check — справочный исходник; его не редактируй.
Найди конкретные ошибки в draft_sentences: потерянные сведения и оговорки (например,
«часто», «во многом»), усиленные утверждения, неправильный падеж или согласование,
неестественное словосочетание. Сохрани жанр, язык, имена, числа, ссылки, цитаты и код.
fact_alerts указывает изменённые оговорки: проверь эти места по справочному исходнику.
Хорошие правки сохрани. Не возвращай старые подводки и не переписывай текст целиком.
Для ошибки выбери id из draft_sentences и replacement — исправленный текст только
этого предложения. Не добавляй абзацев; изменяй лишь то, что нужно для исправления.
Если ошибок нет, edits пустой. Максимум 12 исправлений, replacement до 600 символов.
Верни только JSON: {"edits":[{"id":"S001","replacement":"исправленное предложение"}]}.
Никаких комментариев, объяснений или Markdown."""
    data = {
        "genre": GENRES[genre],
        "source_for_fact_check": original,
        "draft_sentences": [{"id": part.id, "text": part.text} for part in parts],
        "fact_alerts": qualification_changes(original, " ".join(part.text for part in parts)),
    }
    return [
        {"role": "system", "content": instruction + "\n" + DATA_RULES},
        {
            "role": "user",
            "content": "Проверь draft_sentences.\n" + json.dumps(data, ensure_ascii=False),
        },
    ]


def apply(draft: str, response: str, parts: tuple[Sentence, ...]) -> str:
    validate_text(response, 20_000)
    try:
        data = json.loads(response)
    except (ValueError, TypeError):
        raise InvalidCorrections("Неверный формат вычитки.") from None
    if not isinstance(data, dict) or set(data) != {"edits"}:
        raise InvalidCorrections("Неверный формат вычитки.")
    edits = data["edits"]
    if not isinstance(edits, list) or len(edits) > 12:
        raise InvalidCorrections("Слишком много исправлений.")
    available = {part.id: part for part in parts}
    used, changes = set(), []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"id", "replacement"}:
            raise InvalidCorrections("Неверный формат исправления.")
        key, replacement = edit["id"], edit["replacement"]
        if not isinstance(key, str) or key not in available or key in used:
            raise InvalidCorrections("Неверный номер предложения.")
        used.add(key)
        if not isinstance(replacement, str) or not replacement.strip() or len(replacement) > 600:
            raise InvalidCorrections("Неподходящий размер исправления.")
        part = available[key]
        replacement = replacement.strip()
        if "\n" in replacement and "\n" not in part.text:
            raise InvalidCorrections("Вычитка изменила структуру абзацев.")
        if replacement != part.text:
            changes.append((part, replacement))
    # A correction pass cannot replace most of a long text with a new rewrite.
    if sum(part.end - part.start for part, _ in changes) > max(120, int(len(draft) * 0.4)):
        raise InvalidCorrections("Вычитка попыталась переписать слишком много текста.")
    result, previous = [], 0
    for part, replacement in sorted(changes, key=lambda item: item[0].start):
        result.extend((draft[previous : part.start], replacement))
        previous = part.end
    result.append(draft[previous:])
    return "".join(result)
