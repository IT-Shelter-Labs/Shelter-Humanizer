"""Explicit, resumable in-memory editing of every part of a document."""

import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass, field

from .models import MAX_TEXT, Result, validate_text
from .protection import protected_spans, validate_protected
from .providers import Client
from .service import Cancelled, compare, rewrite


@dataclass(frozen=True)
class Part:
    text: str
    prefix: str = ""
    suffix: str = ""


@dataclass
class DocumentSession:
    fingerprint: str
    original: str
    parts: tuple[Part, ...]
    results: list[Result] = field(default_factory=list)


class DocumentPaused(ValueError):
    def __init__(self, session, reason):
        self.session = session
        super().__init__(
            f"Обработано частей: {len(session.results)} из {len(session.parts)}. "
            f"Прогресс сохранён до закрытия приложения. {reason} "
            "Для продолжения нажмите «Улучшить текст» с теми же настройками."
        )


def split_document(text, *, limit=6000, terms=()):
    validate_text(text)
    if not text.strip():
        raise ValueError("Сначала вставьте текст.")
    if not 500 <= limit <= 12000:
        raise ValueError("Недопустимый размер части.")
    spans = protected_spans(text, numbers=True, terms=terms)
    starts = [left for left, right in spans]

    def safe(offset):
        index = bisect_right(starts, offset) - 1
        return index < 0 or not (spans[index][0] < offset < spans[index][1])

    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            region = text[start:end]
            end = 0
            for pattern in (r"\n[ \t]*\n", r"(?<=[.!?…»”])\s+", r"\s+"):
                choices = [
                    start + match.end()
                    for match in re.finditer(pattern, region)
                    if safe(start + match.end())
                ]
                if choices:
                    end = choices[-1]
                    break
            if end <= start:
                raise ValueError(
                    "Один защищённый фрагмент или слово слишком длинные для части. "
                    "Сократите его или обработайте отдельно; цитаты и код не разрезаются."
                )
        raw = text[start:end]
        prefix = raw[: len(raw) - len(raw.lstrip())]
        suffix = raw[len(raw.rstrip()) :]
        body = raw.strip()
        if body:
            parts.append(Part(body, prefix, suffix))
        elif parts:
            previous = parts[-1]
            parts[-1] = Part(previous.text, previous.prefix, previous.suffix + raw)
        start = end
    return tuple(parts)


def fingerprint(text, settings, client):
    config = getattr(client, "config", None)
    identity = {key: getattr(config, key, "") for key in ("kind", "url", "model")}
    payload = json.dumps(
        {"text": text, "settings": settings, "provider": identity},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def rewrite_document(
    text, client, *, session=None, limit=6000, cancel=None, progress=None, **settings
):
    validate_text(text, MAX_TEXT)
    signature = fingerprint(text, {**settings, "limit": limit}, client)
    if session is None or session.fingerprint != signature:
        session = DocumentSession(
            signature, text, split_document(text, limit=limit, terms=settings.get("terms", ()))
        )
    original_progress = client.progress if isinstance(client, Client) else None
    try:
        for index in range(len(session.results), len(session.parts)):
            if cancel is not None and cancel.is_set():
                raise Cancelled("Обработка отменена.")

            def part_progress(message):
                if progress:
                    progress(f"Часть {index + 1}/{len(session.parts)} · {message}")

            if isinstance(client, Client):
                client.progress = part_progress
            result = rewrite(
                session.parts[index].text, client, cancel=cancel, progress=part_progress, **settings
            )
            session.results.append(result)
    except (ValueError, OSError) as exc:
        raise DocumentPaused(session, str(exc)) from None
    finally:
        if isinstance(client, Client):
            client.progress = original_progress
    edited = "".join(
        part.prefix + result.text + part.suffix
        for part, result in zip(session.parts, session.results)
    )
    validate_protected(text, edited, settings.get("terms", ()))
    combined = compare(
        text,
        edited,
        settings.get("genre", "plain"),
        "ai-chunks",
        depth=settings.get("depth", "edit"),
    )
    for index, result in enumerate(session.results, 1):
        combined.processing_warnings.extend(
            f"Часть {index}: {message}" for message in result.processing_warnings
        )
        combined.quality_warnings.extend(
            f"Часть {index}: {message}" for message in result.quality_warnings
        )
        combined.flags.extend(result.flags)
    combined.flags = list(dict.fromkeys(combined.flags + ["chunked"]))
    combined.warnings = list(
        dict.fromkeys(combined.warnings + combined.quality_warnings + combined.processing_warnings)
    )
    return combined, session
