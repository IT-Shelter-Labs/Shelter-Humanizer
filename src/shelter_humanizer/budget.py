"""Conservative context estimate with room for a reply; never silently trim input."""

import math

from .providers import ProviderError


def request_budget(messages, *, reasoning=False, context_limit=32768, schema=None):
    # A documented estimate, not a tokenizer. Include all prompts and JSON overhead.
    byte_count = sum(len(item["content"].encode("utf-8")) for item in messages)
    if schema is not None:
        import json

        byte_count += len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    estimated_input = math.ceil(byte_count / 2) + 1024
    user_size = len(messages[-1]["content"]) if messages else 0
    reply = min(8192, max(2048, math.ceil(user_size / 2) + 1024))
    if reasoning:
        reply = 16384
    needed = estimated_input + reply
    for size in (4096, 8192, 16384, 32768):
        if needed <= size <= context_limit:
            return size, reply, estimated_input
    raise ProviderError(
        "Задание слишком объёмное для выбранного контекста. Включите обработку частями "
        "в расширенных настройках или сократите образец стиля/свою инструкцию.",
        code="context_budget",
    )
