"""Explicit model download through a loopback Ollama; no bundled weights or installers."""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .providers import NoRedirect, ProviderConfig, ProviderError


@dataclass(frozen=True)
class ModelPreset:
    name: str
    label: str
    size: str
    description: str


DEFAULT_MODEL = "qwen3.5:4b"

PRESETS = (
    ModelPreset(
        DEFAULT_MODEL,
        "Qwen 3.5 · 4B — основной вариант",
        "около 3,4 ГБ",
        "Начальный выбор для ИИ-редактуры. Желательно от 16 ГБ оперативной памяти. Качество и скорость зависят от текста и устройства; результат нужно вычитать.",
    ),
    ModelPreset(
        "deepseek-r1:8b-0528-qwen3-q4_K_M",
        "DeepSeek R1 · 8B (0528) — альтернатива",
        "около 5,2 ГБ",
        "На базе Qwen3 8B, выпуск мая 2025 года. Желательно от 24 ГБ оперативной памяти или подходящая видеокарта. Сначала рассуждает, поэтому на CPU может работать долго и не уложиться в таймаут 180 секунд. Начните с короткого текста и вычитайте результат.",
    ),
    ModelPreset(
        "qwen3:1.7b",
        "Qwen 3 · 1.7B — компактная",
        "около 1,4 ГБ",
        "Для коротких простых текстов; желательно от 8 ГБ оперативной памяти. В наших пробах сохраняла шаблонные обороты и теряла защищённый фрагмент. Для важных текстов начните с 4B; отклонённый ответ не заменяет исходник.",
    ),
    ModelPreset(
        "qwen3.5:9b",
        "Qwen 3.5 · 9B — для мощного компьютера",
        "около 6,6 ГБ",
        "Более крупная модель новой серии. Желательно от 24 ГБ оперативной памяти или подходящая видеокарта. На CPU может работать долго; размер не гарантирует хорошую редактуру.",
    ),
)
MAX_LINE = 64 * 1024


class DownloadCancelled(ValueError):
    pass


def stream_records(response, cancel, started):
    buffer, received = b"", 0
    while True:
        if cancel.is_set():
            raise DownloadCancelled("Загрузка отменена.")
        if time.monotonic() - started > 7200:
            raise ProviderError("Загрузка заняла больше двух часов. Попробуйте снова.")
        # read1 returns after one underlying read, so a slowly delivered line cannot
        # postpone cancellation until its final newline arrives.
        chunk = response.read1(8192)
        received += len(chunk)
        if received > 16 * 1024 * 1024:
            raise ProviderError("Поток статуса загрузки слишком большой.")
        buffer += chunk
        lines = buffer.split(b"\n")
        buffer = lines.pop()
        if not chunk and buffer:
            lines.append(buffer)
            buffer = b""
        if len(buffer) > MAX_LINE or any(len(line) > MAX_LINE for line in lines):
            raise ProviderError("Поток статуса загрузки слишком большой.")
        for line in lines:
            if cancel.is_set():
                raise DownloadCancelled("Загрузка отменена.")
            if line.strip():
                yield json.loads(line.decode("utf-8"))
        if not chunk:
            return


def pull_model(model, cancel, progress, url="http://localhost:11434", timeout=30):
    if model not in {p.name for p in PRESETS}:
        raise ProviderError("Выберите модель из списка установщика.")
    base = ProviderConfig(url=url, timeout=timeout).base_url()
    parsed = urllib.parse.urlsplit(base)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.path not in ("", "/"):
        raise ProviderError("Установка моделей доступна только через локальную Ollama.")
    if cancel.is_set():
        raise DownloadCancelled("Загрузка отменена.")
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        base + "/api/pull",
        data=json.dumps({"model": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    started, last_update = time.monotonic(), 0
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ProviderError("Неподдерживаемый формат загрузки.")
            for data in stream_records(response, cancel, started):
                if not isinstance(data, dict) or data.get("error"):
                    raise ProviderError(
                        "Ollama не смогла загрузить модель. Проверьте интернет и свободное место."
                    )
                if cancel.is_set():
                    raise DownloadCancelled("Загрузка отменена.")
                status = data.get("status", "")
                if status == "success":
                    progress("Модель загружена и проверена Ollama.")
                    return
                if time.monotonic() - last_update >= 0.2:
                    total, done = data.get("total"), data.get("completed")
                    if isinstance(total, int) and isinstance(done, int) and total > 0:
                        progress(
                            f"Загрузка текущего файла: {min(100, max(0, done * 100 // total))}% · {done / 1024**2:.0f} / {total / 1024**2:.0f} МБ"
                        )
                    elif isinstance(status, str) and status.startswith("pulling"):
                        progress("Подготовка и загрузка файлов модели…")
                    else:
                        progress("Проверка и сохранение файлов модели…")
                    last_update = time.monotonic()
            raise ProviderError("Ollama не подтвердила завершение загрузки. Попробуйте снова.")
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ProviderError(f"Ollama вернула HTTP {code}. Проверьте доступность модели.") from None
    except (TimeoutError, socket.timeout):
        raise ProviderError(
            "Загрузка модели не отвечает вовремя. Проверьте интернет и повторите установку; скачанные части сохраняются."
        ) from None
    except urllib.error.URLError:
        raise ProviderError(
            "Не удалось подключиться к локальному ИИ. Повторите установку или проверьте своё подключение."
        ) from None
    except (UnicodeError, json.JSONDecodeError):
        raise ProviderError("Некорректный ответ Ollama; загрузка не подтверждена.") from None
