"""Import one GGUF through the loopback Ollama API. No shell or model code execution."""

import hashlib
import json
import re
import shutil
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .local_models import DownloadCancelled, model_tag, stream_records
from .providers import NoRedirect, ProviderConfig, ProviderError


def check_cancel(cancel):
    if cancel.is_set():
        raise DownloadCancelled("Импорт отменён. Исходный файл не изменён.")


def inspect_gguf(path):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".gguf":
        raise ProviderError("Выберите существующий файл GGUF.")
    if re.search(r"-\d{5}-of-\d{5}\.gguf$", path.name, re.I):
        raise ProviderError(
            "Выберите модель одним GGUF-файлом. Для частей используйте свою Ollama и готовое подключение."
        )
    size = path.stat().st_size
    with path.open("rb") as source:
        header = source.read(24)
    if (
        len(header) != 24
        or header[:4] != b"GGUF"
        or struct.unpack_from("<I", header, 4)[0] not in (2, 3)
        or max(struct.unpack_from("<QQ", header, 8)) > 1000000
        or size > 128 * 1024**3
    ):
        raise ProviderError(
            "Файл не похож на поддерживаемую GGUF-модель или имеет недопустимый размер."
        )
    return path, size


def import_gguf(path, model, cancel, progress, url, *, directory=None, timeout=30):
    model = model_tag(model)
    path, size = inspect_gguf(path)
    base = ProviderConfig(url=url, timeout=timeout).base_url()
    parsed = urllib.parse.urlsplit(base)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.path not in ("", "/"):
        raise ProviderError("Импорт файлов разрешён только в локальную Ollama.")
    check_cancel(cancel)
    if directory and shutil.disk_usage(directory).free < size + 1024**3:
        raise ProviderError("Для копии GGUF и рабочего запаса не хватает места на диске моделей.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        done, last_progress = 0, -1
        while block := source.read(1024 * 1024):
            check_cancel(cancel)
            digest.update(block)
            done += len(block)
            percent = done * 100 // size
            if percent != last_progress:
                progress(f"Проверка GGUF: {percent}%")
                last_progress = percent
    digest = "sha256:" + digest.hexdigest()
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    blob = base + "/api/blobs/" + digest
    try:
        try:
            with opener.open(
                urllib.request.Request(blob, method="HEAD"), timeout=timeout
            ) as response:
                exists = response.status == 200
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code != 404:
                raise ProviderError(f"Ollama вернула HTTP {code} при проверке файла.") from None
            exists = False
        if not exists:
            with path.open("rb") as source:

                class Upload:
                    last = 0
                    done = 0

                    def read(self, amount):
                        check_cancel(cancel)
                        block = source.read(amount)
                        self.done += len(block)
                        if time.monotonic() - self.last > 0.2:
                            progress(f"Копирование GGUF в локальный ИИ: {self.done * 100 // size}%")
                            self.last = time.monotonic()
                        return block

                request = urllib.request.Request(
                    blob,
                    data=Upload(),
                    method="POST",
                    headers={
                        "Content-Length": str(size),
                        "Content-Type": "application/octet-stream",
                    },
                )
                with opener.open(request, timeout=timeout) as response:
                    if response.status != 201:
                        raise ProviderError("Ollama не подтвердила копирование GGUF.")
        check_cancel(cancel)
        request = urllib.request.Request(
            base + "/api/create",
            data=json.dumps(
                {"model": model, "files": {"model.gguf": digest}, "stream": True}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=timeout) as response:
            for record in stream_records(response, cancel, time.monotonic()):
                if not isinstance(record, dict) or record.get("error"):
                    raise ProviderError(
                        "Ollama не смогла импортировать GGUF. Проверьте поддержку модели и доступную память."
                    )
                if record.get("status") == "success":
                    check_cancel(cancel)
                    progress("GGUF импортирован в локальную Ollama.")
                    return model
                progress("Ollama подготавливает GGUF-модель…")
        raise ProviderError("Ollama не подтвердила завершение импорта.")
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ProviderError(
            f"Ollama вернула HTTP {code} при импорте. Исходный GGUF не изменён."
        ) from None
    except (urllib.error.URLError, TimeoutError):
        raise ProviderError(
            "Локальная Ollama недоступна или не отвечает при импорте. Исходный GGUF сохранён."
        ) from None
    except (UnicodeError, json.JSONDecodeError):
        raise ProviderError("Ollama вернула неверный статус импорта.") from None
