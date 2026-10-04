from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass, field

MAX_TEXT = 200_000
MAX_AI_TEXT = 12_000


@dataclass(frozen=True)
class Finding:
    kind: str
    code: str
    severity: str
    start: int
    end: int
    line: int
    column: int
    message: str
    excerpt: str
    replacement: str | None = None
    auto_fix: bool = False


def line_starts(text: str) -> list[int]:
    return [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]


def finding(text: str, start: int, end: int, *, positions=None, **details) -> Finding:
    if positions is None:
        line = text.count("\n", 0, start) + 1
        column = start - text.rfind("\n", 0, start)
    else:
        line = bisect_right(positions, start)
        column = start - positions[line - 1] + 1
    # Render controls visibly; never inject a bidi control into a diagnostic label.
    excerpt = text[max(0, start - 18) : min(len(text), end + 18)]
    visible = "".join(
        f"\\u{ord(c):04X}" if not c.isprintable() or c in "\u2800\u3164" else c for c in excerpt
    )
    return Finding(start=start, end=end, line=line, column=column, excerpt=visible, **details)


def validate_text(text: str, limit: int = MAX_TEXT) -> None:
    if not isinstance(text, str):
        raise ValueError("Ожидается текст.")
    if len(text) > limit:
        raise ValueError(f"Слишком большой текст: предел {limit:,} символов.")


@dataclass
class Analysis:
    characters: int
    words: int
    unicode: list[Finding]
    style: list[Finding]


@dataclass
class Result:
    original: str
    text: str
    mode: str
    before: Analysis
    after: Analysis
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"schema_version": 1, **asdict(self)}
