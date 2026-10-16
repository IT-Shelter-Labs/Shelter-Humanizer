"""Recognize a few local-engine failures without exporting raw server messages."""

import json

MAX_ERROR_BODY = 8192
ENGINE_CODES = frozenset({"memory", "gpu", "model_file", "architecture", "runner", "unknown"})

HINTS = {
    "memory": (
        "Ollama сообщила о нехватке оперативной или видеопамяти. "
        "Закройте тяжёлые программы, выберите Qwen 3.5 4B или компактную модель "
        "и попробуйте короткий текст. Размер модели не равен всему расходу памяти."
    ),
    "gpu": (
        "Ollama сообщила об ошибке видеокарты или её драйвера. "
        "В «Выбрать ИИ → Настроить подключение» можно включить "
        "«Только процессор» и повторить запрос вручную. Это может работать медленнее."
    ),
    "model_file": (
        "Ollama сообщила об ошибке чтения файла модели. "
        "Попробуйте другую установленную модель. Если ошибка относится к своему GGUF, "
        "проверьте файл и поддержку его формата движком."
    ),
    "architecture": (
        "Ollama сообщила о неподдерживаемой архитектуре модели. "
        "Выберите рекомендованную модель или подключите свою совместимую версию Ollama."
    ),
    "runner": (
        "Процесс модели неожиданно завершился. Причина не установлена. "
        "Сохраните текст и перезапустите приложение. При повторении попробуйте "
        "короткий текст, меньшую модель или режим «Только процессор» в подключении."
    ),
    "unknown": (
        "Движок Ollama вернул внутреннюю ошибку. Причина не установлена: "
        "Такой сбой сам по себе не доказывает нехватку памяти. "
        "Попробуйте короткий текст. При повторении сохраните текст, перезапустите "
        "приложение и скопируйте диагностику в расширенных настройках."
    ),
}


def classify(message):
    if not isinstance(message, str) or len(message) > MAX_ERROR_BODY:
        return "unknown"
    text = message.lower()
    for category, patterns in (
        (
            "memory",
            (
                "out of memory",
                "not enough system memory",
                "requires more system memory",
                "unable to allocate",
                "failed to allocate",
                "cannot allocate memory",
            ),
        ),
        (
            "gpu",
            (
                "cuda error",
                "cuda driver",
                "cublas",
                "hip error",
                "vulkan error",
                "failed to initialize cuda",
                "no cuda-capable device",
            ),
        ),
        (
            "architecture",
            (
                "unsupported model architecture",
                "unknown model architecture",
                "unsupported architecture",
            ),
        ),
        (
            "model_file",
            (
                "invalid file magic",
                "invalid gguf",
                "unexpected end of file",
                "checksum mismatch",
                "failed to read model",
            ),
        ),
        (
            "runner",
            (
                "runner process has terminated",
                "runner process no longer running",
                "runner process stopped",
                "runner process exited",
            ),
        ),
    ):
        if any(pattern in text for pattern in patterns):
            return category
    return "unknown"


def classify_body(raw):
    if len(raw) > MAX_ERROR_BODY:
        return "unknown"
    try:
        record = json.loads(raw.decode("utf-8"))
        return classify(record.get("error")) if isinstance(record, dict) else "unknown"
    except (ValueError, UnicodeError):
        return "unknown"
