"""Bounded Russian-prose inspection, not an AI detector or full UTS #39 implementation."""

from __future__ import annotations

import re
import unicodedata as ud
from dataclasses import dataclass

from .models import Finding, finding, line_starts, validate_text
from .protection import overlaps, protected_spans

REMOVABLE = {
    "\u200b": "Пробел нулевой ширины",
    "\u2060": "Невидимый соединитель слов",
    "\ufeff": "BOM / невидимый знак внутри текста",
    "\u00ad": "Мягкий перенос",
    "\u202a": "Управление направлением текста",
    "\u202b": "Управление направлением текста",
    "\u202c": "Управление направлением текста",
    "\u202d": "Переопределение направления текста",
    "\u202e": "Переопределение направления текста",
}
SPACES = (
    "\u00a0\u202f\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u205f\u3000"
)
REVIEW = {
    "\u200e": "Метка направления слева направо",
    "\u200f": "Метка направления справа налево",
    "\u061c": "Арабская метка направления",
    "\u2066": "Изолятор направления текста",
    "\u2067": "Изолятор направления текста",
    "\u2068": "Изолятор направления текста",
    "\u2069": "Конец изолятора направления",
    "\u180e": "Монгольский разделитель",
    "\u2800": "Пустой символ Брайля",
    "\u3164": "Корейский заполнитель",
    "\u115f": "Корейский заполнитель",
    "\u1160": "Корейский заполнитель",
    "\ufffd": "Символ замены: исходный знак мог быть потерян при декодировании",
    "\u034f": "Невидимый соединитель комбинируемых знаков",
}
CONFUSABLES = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "C": "С",
        "E": "Е",
        "H": "Н",
        "K": "К",
        "M": "М",
        "O": "О",
        "P": "Р",
        "T": "Т",
        "X": "Х",
        "a": "а",
        "c": "с",
        "e": "е",
        "o": "о",
        "p": "р",
        "x": "х",
        "y": "у",
    }
)


def cyrillic(char: str) -> bool:
    return "CYRILLIC" in ud.name(char, "") and ud.category(char).startswith("L")


def emoji_neighbor(text: str, index: int, direction: int) -> bool:
    index += direction
    while 0 <= index < len(text):
        char = text[index]
        code = ord(char)
        if char in "\ufe0e\ufe0f" or 0x1F3FB <= code <= 0x1F3FF:
            index += direction
            continue
        return 0x1F000 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF
    return False


def scan_unicode(text: str) -> list[Finding]:
    validate_text(text)
    result = []
    positions = line_starts(text)
    emoji_tags = [
        (m.start(), m.end()) for m in re.finditer("🏴[\U000e0020-\U000e007e]{1,64}\U000e007f", text)
    ]
    spans = protected_spans(text)
    for pos, char in enumerate(text):
        code = ord(char)
        description, replacement, auto = "", None, False
        severity = "review"
        if char in REMOVABLE:
            description, replacement, auto = REMOVABLE[char], "", True
            severity = "warning"
        elif (ud.category(char) in ("Cc", "Cs") and char not in "\r\n\t") or (
            0xFDD0 <= code <= 0xFDEF or code & 0xFFFF in (0xFFFE, 0xFFFF)
        ):
            description, replacement, auto = "Управляющий или недопустимый символ", "", True
            severity = "warning"
        elif char in SPACES:
            description, replacement = "Специальный пробел: часто полезен в типографике", " "
            severity = "info"
        elif char in REVIEW:
            description = REVIEW[char]
        elif char in "\u200c\u200d":
            if char == "\u200d" and emoji_neighbor(text, pos, -1) and emoji_neighbor(text, pos, 1):
                continue
            if pos and pos + 1 < len(text) and cyrillic(text[pos - 1]) and cyrillic(text[pos + 1]):
                description, replacement, auto = (
                    "Невидимый соединитель внутри русского слова",
                    "",
                    True,
                )
                severity = "warning"
            else:
                description = "Соединитель письменности: может быть необходим"
                severity = "info"
        elif ud.category(char) == "Cf":
            # Emoji tag sequences and script controls are not removed blindly.
            if 0xE0020 <= code <= 0xE007F:
                if overlaps(pos, pos + 1, emoji_tags):
                    continue
            description = "Форматирующий символ: проверьте назначение"
        elif char in "\ufe0e\ufe0f" and not emoji_neighbor(text, pos, -1):
            if (
                pos
                and text[pos - 1] in "0123456789#*"
                and pos + 1 < len(text)
                and text[pos + 1] == "\u20e3"
            ):
                continue
            description = "Селектор представления вне распознанной emoji-последовательности"
            severity = "info"
        if description:
            protected = overlaps(pos, pos + 1, spans)
            if protected:
                description += "; защищённый участок, без автоматической правки"
            name = ud.name(char, "UNNAMED")
            result.append(
                finding(
                    text,
                    pos,
                    pos + 1,
                    kind="unicode",
                    positions=positions,
                    code=f"U+{code:04X}",
                    severity=severity,
                    message=f"{description} ({name})",
                    replacement=replacement,
                    auto_fix=auto and not protected,
                )
            )
    for match in re.finditer(r"[^\W\d_]+", text):
        word = match.group()
        latin = [char for char in word if "LATIN" in ud.name(char, "")]
        if latin and any(cyrillic(char) for char in word):
            changed = word.translate(CONFUSABLES)
            suggestion = changed if changed != word and all(cyrillic(c) for c in changed) else None
            result.append(
                finding(
                    text,
                    match.start(),
                    match.end(),
                    kind="unicode",
                    positions=positions,
                    code="MIXED_SCRIPT",
                    severity="review",
                    message="Латиница и кириллица в одном слове; возможна опечатка",
                    replacement=suggestion,
                )
            )
    return sorted(result, key=lambda item: (item.start, item.end))


@dataclass(frozen=True)
class CleanOptions:
    spaces: bool = False
    confusables: bool = False
    nfc: bool = False


def clean_unicode(text: str, options: CleanOptions = CleanOptions()) -> str:
    findings = scan_unicode(text)
    spans = protected_spans(text)
    edits = []
    for item in findings:
        optional = (options.spaces and text[item.start : item.end] in SPACES) or (
            options.confusables and item.code == "MIXED_SCRIPT"
        )
        if (
            (item.auto_fix or optional)
            and item.replacement is not None
            and not overlaps(item.start, item.end, spans)
        ):
            edits.append((item.start, item.end, item.replacement))
    parts, previous = [], 0
    for start, end, replacement in sorted(edits):
        if start >= previous:
            parts.extend((text[previous:start], replacement))
            previous = end
    parts.append(text[previous:])
    text = "".join(parts)
    if options.nfc:
        spans = protected_spans(text)
        parts, previous = [], 0
        for start, end in spans:
            parts.extend((ud.normalize("NFC", text[previous:start]), text[start:end]))
            previous = end
        parts.append(ud.normalize("NFC", text[previous:]))
        text = "".join(parts)
    return text
