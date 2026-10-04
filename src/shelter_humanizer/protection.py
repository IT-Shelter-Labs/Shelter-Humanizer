"""Protect explicit atoms; this does not establish semantic equivalence."""

from __future__ import annotations

import re
import secrets
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass

CODE = re.compile(r"(?<!`)(`{1,3})(?!`)([^\n]{1,4096}?)(?<!`)\1(?!`)")
LINK = re.compile(r"!?\[[^\]\n]{0,1024}\]\((?:[^()\n]|\([^()\n]{0,1024}\)){0,8192}\)")
URL = re.compile(
    r"(?i:https?://)[^\s<>\"«»]+|(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,253}\.[A-Za-z]{2,63}\b"
)
NUMBER = re.compile(
    r"(?<!\w)[+−-]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)"
    r"(?:[.,:/−-]\d+)*"
    r"(?:[ \t\u00a0\u202f]*(?:млрд\.?|млн\.?|тыс\.?|миллион\w*|миллиард\w*|тысяч\w*)(?!\w))?"
    r"(?:[ \t\u00a0\u202f]*(?:₽|%|руб(?:лей|ля|ль|лях)?\.?|процент\w*|секунд\w*|минут\w*|"
    r"час(?:ов|а)?|дн(?:ей|я)|дней|год(?:а|ов)?|лет|км|кг|мг|мл|ГБ|МБ|ТБ|°[CС])(?!\w))?",
    re.IGNORECASE,
)
QUALIFIERS = re.compile(
    r"\b(?:обычно|примерно|возможно|иногда|вероятно|может|могут|часто|редко|"
    r"всегда|никогда|во многом|прежде всего|в основном)\b",
    re.I,
)


def qualification_changes(before: str, after: str) -> list[dict]:
    old = Counter(m.group().casefold() for m in QUALIFIERS.finditer(before))
    new = Counter(m.group().casefold() for m in QUALIFIERS.finditer(after))
    return [
        {"phrase": phrase, "source_count": old[phrase], "draft_count": new[phrase]}
        for phrase in sorted(old.keys() | new.keys())
        if old[phrase] != new[phrase]
    ]


def url_spans(text: str):
    """Bare links follow prose punctuation; explicit Markdown links protect it all."""
    for match in URL.finditer(text):
        end = match.end()
        if "://" in match.group():
            while end > match.start():
                value = text[match.start() : end]
                if value[-1] in ".,;!?" or (
                    value[-1] == ")" and value.count(")") > value.count("(")
                ):
                    end -= 1
                else:
                    break
        yield match.start(), end


def merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if result and start < result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def quote_spans(text: str) -> list[tuple[int, int]]:
    """Linear scan; preserve unfinished quotations conservatively."""
    pairs = {"«": "»", "“": "”", '"': '"'}
    spans, start, closing, escaped = [], None, "", False
    for index, char in enumerate(text):
        if start is None:
            if char in pairs:
                start, closing = index, pairs[char]
        else:
            if char == closing and not (closing == '"' and escaped):
                spans.append((start, index + 1))
                start, closing = None, ""
            escaped = char == "\\" and not escaped
    if start is not None:
        spans.append((start, len(text)))
    return spans


def protected_spans(text: str, *, numbers: bool = False, terms: tuple[str, ...] = ()):
    spans = []
    fence_start = None
    fence_char, fence_size = "", 0
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if marker:
            token, tail = marker.groups()
            if fence_start is None:
                fence_start, fence_char, fence_size = offset, token[0], len(token)
            elif token[0] == fence_char and len(token) >= fence_size and not tail.strip():
                spans.append((fence_start, offset + len(line)))
                fence_start = None
        if fence_start is None and line.lstrip().startswith(">"):
            spans.append((offset, offset + len(line)))
        offset += len(line)
    if fence_start is not None:
        spans.append((fence_start, len(text)))
    spans.extend(quote_spans(text))
    for pattern in (CODE, LINK):
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    spans.extend(url_spans(text))
    if numbers:
        spans.extend((m.start(), m.end()) for m in NUMBER.finditer(text))
    for term in terms:
        if term.strip():
            pattern = re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.I)
            spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    return merge(spans)


def overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    # All callers use sorted, merged spans. Avoid O(text length * span count).
    index = bisect_right(spans, (start, float("inf")))
    return (index > 0 and spans[index - 1][1] > start) or (
        index < len(spans) and spans[index][0] < end
    )


@dataclass(frozen=True)
class Masked:
    text: str
    replacements: tuple[tuple[str, str], ...]
    prefix: str

    def restore(self, draft: str) -> str:
        expected = {token for token, _ in self.replacements}
        actual = re.findall(re.escape(self.prefix) + r"\d+__", draft)
        if set(actual) != expected or any(draft.count(token) != 1 for token in expected):
            raise ValueError(
                "Модель изменила или потеряла защищённые данные. "
                "Результат отклонён; исходник сохранён. Попробуйте другой режим или модель."
            )
        for token, original in self.replacements:
            draft = draft.replace(token, original)
        if self.prefix in draft:
            raise ValueError("Модель оставила неизвестную защищённую метку. Результат отклонён.")
        return draft


def mask(text: str, terms: tuple[str, ...] = ()) -> Masked:
    prefix = "__SH_" + secrets.token_hex(6) + "_"
    while prefix in text:
        prefix = "__SH_" + secrets.token_hex(6) + "_"
    replacements = []
    parts = []
    previous = 0
    for idx, (start, end) in enumerate(protected_spans(text, numbers=True, terms=terms)):
        token = prefix + str(idx) + "__"
        parts.extend((text[previous:start], token))
        replacements.append((token, text[start:end]))
        previous = end
    parts.append(text[previous:])
    return Masked("".join(parts), tuple(replacements), prefix)


def validate_protected(original: str, draft: str, terms: tuple[str, ...] = ()) -> None:
    """Validate readable responses without making the model write opaque tokens."""
    if Counter(m.group() for m in NUMBER.finditer(original)) != Counter(
        m.group() for m in NUMBER.finditer(draft)
    ):
        raise ValueError("Модель добавила или изменила числа/единицы. Результат отклонён.")

    def atoms(text):
        return Counter(
            text[start:end] for start, end in protected_spans(text, numbers=True, terms=terms)
        )

    if atoms(original) != atoms(draft):
        raise ValueError(
            "Модель изменила, добавила или потеряла защищённые данные. "
            "Результат отклонён; исходник сохранён. Попробуйте другой режим или модель."
        )


def fact_changes(before: str, after: str) -> list[str]:
    warnings = []
    for label, old, new in (
        (
            "числа/единицы",
            Counter(m.group() for m in NUMBER.finditer(before)),
            Counter(m.group() for m in NUMBER.finditer(after)),
        ),
        (
            "ссылки",
            Counter(before[start:end] for start, end in url_spans(before)),
            Counter(after[start:end] for start, end in url_spans(after)),
        ),
    ):
        if old != new:
            warnings.append(f"Изменились {label}: проверьте результат по исходнику.")
    if qualification_changes(before, after):
        warnings.append("Изменились оговорки о точности или возможности: нужна вычитка.")
    negative = re.compile(r"\b(?:не|нет|ни|без|невозможно|нельзя)\b", re.I)
    if Counter(m.group().lower() for m in negative.finditer(before)) != Counter(
        m.group().lower() for m in negative.finditer(after)
    ):
        warnings.append("Изменились отрицания или условия: проверьте, сохранился ли смысл.")
    return warnings
