"""Conservative original rules. Suggestions describe style, not authorship."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .models import Finding, finding, line_starts
from .protection import overlaps, protected_spans

GENRES = {"plain": "Обычный текст", "business": "Деловой", "academic": "Научный", "post": "Пост"}


@dataclass(frozen=True)
class Rule:
    code: str
    pattern: str
    explanation: str
    replacement: str | None = None
    formal_ok: bool = False


RULES = (
    Rule(
        "PARAGRAPH_OPENING",
        r"(?:^|(?<=[.!?\n])\s*)(?:одним\s+из|одной\s+из|важное\s+место|"
        r"большой\s+вклад|значительный\s+вклад|важную\s+роль)\b",
        "Попробуйте начать с предмета и его действия. Сохраните сведения о значимости, "
        "месте и распространённости; не меняйте одну общую подводку на другую.",
    ),
    Rule(
        "CONCLUSION_OPENING",
        r"(?:^|(?<=[.!?\n])\s*)(?:таким\s+образом|итак|в\s+целом),\s*",
        "Если вывод понятен без связки, выразите его тезис прямо. Сам вывод и его "
        "связь с предыдущими сведениями сохраните.",
    ),
    Rule(
        "INTRO",
        r"(?:^|(?<=[.!?\n])\s*)(?:необходимо|следует|стоит|важно)\s+(?:отметить|подчеркнуть),\s+что\s+",
        "Подводка перед утверждением: попробуйте начать с самой мысли.",
        "",
        True,
    ),
    Rule(
        "INTRO2",
        r"(?:^|(?<=[.!?\n])\s*)как\s+уже\s+было\s+сказано\s+выше,\s*",
        "Ссылка на уже сказанное часто не нужна.",
        "",
        True,
    ),
    Rule(
        "CHECK",
        r"\bосуществляет\s+проверку\b",
        "Более привычный оборот; падеж следующего слова сохранён.",
        "проводит проверку",
        True,
    ),
    Rule(
        "CHECK_INF",
        r"\bосуществлять\s+проверку\b",
        "Можно сказать привычнее.",
        "проводить проверку",
        True,
    ),
    Rule(
        "PROCESS",
        r"\bосуществляет\s+обработку\b",
        "Попробуйте глагол «обрабатывает», согласовав падеж следующего слова.",
        None,
        True,
    ),
    Rule(
        "PROCESS_INF",
        r"\bосуществлять\s+обработку\b",
        "Попробуйте глагол «обрабатывать», согласовав падеж следующего слова.",
        None,
        True,
    ),
    Rule(
        "INSTALL",
        r"\bосуществить\s+установку\b",
        "Попробуйте «установить», согласовав падеж объекта.",
        None,
        True,
    ),
    Rule("HELP", r"\bоказывает\s+помощь\b", "Можно назвать действие прямо.", "помогает", True),
    Rule(
        "USE",
        r"\bпроизводит\s+анализ\b",
        "Попробуйте «анализирует», согласовав падеж объекта.",
        None,
        True,
    ),
    Rule(
        "BECAUSE",
        r"\bпо\s+той\s+причине,\s+что\b",
        "Можно заменить на «потому что», проверив запятую перед союзом.",
    ),
    Rule(
        "NOW", r"\bна\s+данный\s+момент\s+времени\b", "Избыточное указание времени.", "сейчас", True
    ),
    Rule(
        "IN_ORDER",
        r"\bдля\s+того,\s+чтобы\b",
        "Часто достаточно «чтобы»; при замене проверьте запятую перед союзом.",
        None,
        True,
    ),
    Rule(
        "CAN",
        r"\bимеет\s+возможность\b(?=\s+[а-яё]+(?:ть|ти|чь)\b)",
        "Можно сказать короче, сохранив возможность.",
        "может",
        True,
    ),
    Rule(
        "WORLD",
        r"\bв\s+(?:современном|сегодняшнем)\s+(?:цифровом\s+)?мире\b",
        "Проверьте, сообщает ли вступление что-то конкретное.",
    ),
    Rule(
        "ROLE",
        r"\bиграет\s+(?:ключевую|важную|решающую)\s+роль\b",
        "Объясните конкретную роль; не удаляйте содержательное утверждение.",
    ),
    Rule(
        "AUTHORITY",
        r"\b(?:эксперты|исследователи)\s+(?:считают|отмечают|утверждают)\b",
        "Если это утверждение о факте, нужен конкретный источник.",
    ),
    Rule("CONTRAST", r"\bэто\s+не\s+просто\b", "Проверьте, нужен ли контраст вместо прямой мысли."),
    Rule(
        "WRAPPER",
        r"\b(?:надеюсь,?\s+(?:это\s+)?(?:помогло|было\s+полезно)|давайте\s+разбер[её]мся)\b",
        "Возможно, здесь осталась обвязка ответа чатбота.",
    ),
)


def scan_style(text: str, genre: str = "plain") -> list[Finding]:
    if genre not in GENRES:
        raise ValueError("Неизвестный жанр.")
    spans = protected_spans(text)
    result = []
    positions = line_starts(text)
    for rule in RULES:
        if genre in ("business", "academic") and rule.formal_ok:
            continue
        for match in re.finditer(rule.pattern, text, re.I):
            if overlaps(match.start(), match.end(), spans):
                continue
            replacement = rule.replacement
            raw = match.group()
            end = match.end()
            if replacement and raw.lstrip()[:1].isupper():
                replacement = replacement[0].upper() + replacement[1:]
            # Keep sentence-boundary whitespace while removing only the preamble.
            if replacement == "":
                replacement = raw[: len(raw) - len(raw.lstrip())]
                if (
                    raw.lstrip()[:1].isupper()
                    and end < len(text)
                    and text[end] in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
                    and not overlaps(end, end + 1, spans)
                ):
                    replacement += text[end].upper()
                    end += 1
            result.append(
                finding(
                    text,
                    match.start(),
                    end,
                    kind="style",
                    positions=positions,
                    code=rule.code,
                    severity="suggestion",
                    message=rule.explanation,
                    replacement=replacement,
                    auto_fix=replacement is not None,
                )
            )
    for match in re.finditer(r"[^.!?\n]{220,}[.!?]?(?=\s|$)", text):
        if not overlaps(match.start(), match.end(), spans):
            result.append(
                finding(
                    text,
                    match.start(),
                    match.end(),
                    kind="style",
                    positions=positions,
                    code="LONG_SENTENCE",
                    severity="suggestion",
                    message="Длинное предложение: проверьте, можно ли разделить мысли.",
                )
            )
    return sorted(result, key=lambda item: item.start)


def light_edit(text: str, genre: str = "plain") -> str:
    edits = [item for item in scan_style(text, genre) if item.auto_fix]
    parts, previous = [], 0
    for item in sorted(edits, key=lambda item: item.start):
        if item.start >= previous:
            parts.extend((text[previous : item.start], item.replacement or ""))
            previous = item.end
    parts.append(text[previous:])
    return "".join(parts)


def editorial_targets(text: str) -> list[dict[str, str]]:
    """Bounded suggestions for an AI editor; never automatic deletions."""
    targets = []
    for item in scan_style(text, "plain"):
        if item.code not in (
            "INTRO",
            "INTRO2",
            "PARAGRAPH_OPENING",
            "CONCLUSION_OPENING",
            "CHECK",
            "PROCESS",
            "NOW",
        ):
            continue
        start = item.start
        while start < item.end and text[start].isspace():
            start += 1
        # Give the model the actual sentence, not just an isolated stop phrase.
        tail = text[start : start + 400]
        end = re.search(r"[.!?](?=\s|$)|\n", tail)
        fragment = tail[: end.end()] if end else tail
        action = item.message
        if item.code == "PARAGRAPH_OPENING":
            beginning = fragment.casefold()
            if beginning.startswith(("одним из", "одной из")):
                action = (
                    "Начни с самого предмета, затем назови его характеристику. "
                    "Сведения о распространённости и месте сохрани."
                )
            elif beginning.startswith(("большой вклад", "значительный вклад")):
                action = (
                    "Расскажи прямо, что делал этот человек или организация: покажи вклад "
                    "через действия из исходника, сохранив его значимость. "
                    "Не заменяй подводку другой абстрактной оценкой."
                )
            else:
                action = (
                    "Свяжи сведения о предмете и следующие за ними подробности прямой мыслью. "
                    "Не начинай с общей подводки об абстрактной важности; её смысл сохрани."
                )
        targets.append({"fragment": fragment, "action": action})
        if len(targets) == 24:
            break
    return targets


def almost_unchanged(before: str, after: str) -> bool:
    """Lexical reuse, not an authorship or quality score. Linear in text length."""

    def phrases(text):
        words = re.findall(r"[^\W_]+", text.casefold())
        return Counter(tuple(words[index : index + 4]) for index in range(len(words) - 3))

    old, new = phrases(before), phrases(after)
    size = sum(old.values()) + sum(new.values())
    return sum(old.values()) >= 57 and size > 0 and 2 * sum((old & new).values()) / size >= 0.9
