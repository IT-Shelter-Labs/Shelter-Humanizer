"""Small bounded HTTP clients. No retries, redirects, fallback or key persistence."""

from __future__ import annotations

import http.client
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace

from . import __version__
from .engine_errors import HINTS, MAX_ERROR_BODY, classify, classify_body

MAX_RESPONSE = 2 * 1024 * 1024
MAX_STREAM_LINE = 64 * 1024
LOCAL_GENERATION_TIMEOUT = 900


class ProviderError(ValueError):
    def __init__(self, message, *, code="provider_error"):
        super().__init__(message)
        self.code = code


class ProviderCancelled(ProviderError):
    pass


class _Exchange:
    """Own only this request's socket; shutdown wakes a blocked transport read."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stopped = False
        self.socket = None
        self.characters = 0
        self.started = time.monotonic()
        self.first_fragment = None
        self.phase = "загрузка модели и подготовка текста"

    def register(self, connection):
        with self.lock:
            self.socket = connection
            if self.stopped:
                self._shutdown()

    def _shutdown(self):
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stop(self):
        with self.lock:
            self.stopped = True
            self._shutdown()

    def handlers(self):
        exchange = self

        class Connection(http.client.HTTPConnection):
            def connect(self):
                super().connect()
                exchange.register(self.sock)

        class SecureConnection(http.client.HTTPSConnection):
            def connect(self):
                super().connect()
                exchange.register(self.sock)

        class HTTP(urllib.request.HTTPHandler):
            def http_open(self, request):
                return self.do_open(Connection, request)

        class HTTPS(urllib.request.HTTPSHandler):
            def https_open(self, request):
                return self.do_open(SecureConnection, request, context=self._context)

        return [HTTP(), HTTPS()]


@dataclass(frozen=True)
class ProviderConfig:
    kind: str = "ollama"
    url: str = "http://localhost:11434"
    model: str = ""
    key: str = field(default="", repr=False)
    timeout: float = 60
    generation_timeout: float = LOCAL_GENERATION_TIMEOUT
    adaptive_context: bool = True
    inspect_model: bool = False
    streaming: bool = True
    cpu_only: bool = False

    def base_url(self) -> str:
        if self.kind not in ("ollama", "api"):
            raise ProviderError("Неизвестный тип подключения.")
        try:
            parsed = urllib.parse.urlsplit(self.url.strip())
            port = parsed.port
        except ValueError:
            raise ProviderError("Некорректный адрес сервера.") from None
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port == 0
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
        ):
            raise ProviderError(
                "Нужен HTTPS-адрес; HTTP разрешён только на localhost / 127.0.0.1 / ::1."
            )
        if not 1 <= self.timeout <= 180:
            raise ProviderError("Таймаут должен быть от 1 до 180 секунд.")
        if not 1 <= self.generation_timeout <= 1800:
            raise ProviderError("Лимит локальной генерации должен быть от 1 до 1800 секунд.")
        if any(ord(c) < 32 for c in self.url + self.key):
            raise ProviderError("Адрес или ключ содержат управляющие символы.")
        base = self.url.strip().rstrip("/")
        suffixes = (
            ("/api/chat", "/api/tags", "/api/show")
            if self.kind == "ollama"
            else ("/chat/completions", "/models")
        )
        for suffix in suffixes:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return base

    def validate(self) -> None:
        self.base_url()
        if not self.model.strip() or len(self.model) > 160 or any(ord(c) < 32 for c in self.model):
            raise ProviderError("Укажите точное имя модели из вашего сервиса.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config: ProviderConfig, *, cancel=None, progress=None):
        self.config = config
        self.cancel = cancel
        self.progress = progress
        self.last_stats = {}
        self.model_details = {}
        self.prepared = False
        # Local models do not accidentally go through the system HTTP proxy.
        local = urllib.parse.urlsplit(config.base_url()).hostname in (
            "localhost",
            "127.0.0.1",
            "::1",
        )
        self.handlers = [NoRedirect()]
        self.local_engine = local and config.kind == "ollama"
        if local:
            self.handlers.append(urllib.request.ProxyHandler({}))

    def _request(self, path: str, payload: dict | None = None) -> dict:
        # Socket timeouts alone reset for each read. Bound the whole exchange as well.
        completed = queue.Queue(maxsize=1)
        exchange = _Exchange()
        streaming = payload is not None and payload.get("stream")
        generation = payload is not None and "messages" in payload
        limit = self.config.generation_timeout if generation else self.config.timeout
        started, last_progress = time.monotonic(), 0

        def transport():
            try:
                completed.put((True, self._blocking_request(path, payload, exchange)))
            except ProviderError as exc:
                completed.put((False, exc))
            except Exception:
                completed.put(
                    (False, ProviderError("Ошибка сетевого обмена. Проверьте подключение."))
                )

        if self.cancel is not None and self.cancel.is_set():
            raise ProviderCancelled("Обработка отменена. Исходник сохранён.")
        threading.Thread(target=transport, daemon=True).start()
        try:
            while True:
                if self.cancel is not None and self.cancel.is_set():
                    raise ProviderCancelled("Обработка отменена. Исходник сохранён.")
                elapsed = time.monotonic() - started
                if elapsed >= limit:
                    raise ProviderError(
                        self._timeout_message(self.config.kind == "ollama"),
                        code="generation_timeout",
                    )
                try:
                    success, value = completed.get(timeout=min(0.1, limit - elapsed))
                except queue.Empty:
                    if streaming and self.progress and elapsed - last_progress >= 1:
                        count = (
                            f" · получено {exchange.characters} символов"
                            if exchange.characters
                            else ""
                        )
                        label = "Локальный ИИ" if self.config.kind == "ollama" else "ИИ-сервис"
                        self.progress(f"{label}: {exchange.phase} · {elapsed:.0f} с{count}.")
                        last_progress = elapsed
                    continue
                if not success:
                    raise value
                if generation:
                    self.last_stats["request_time_s"] = round(time.monotonic() - started, 2)
                    if exchange.first_fragment is not None:
                        self.last_stats["first_fragment_s"] = round(exchange.first_fragment, 2)
                return value
        finally:
            exchange.stop()

    @staticmethod
    def _timeout_message(local):
        if local:
            return (
                "Локальный ИИ не успел завершить ответ или перестал передавать данные. "
                "Исходник сохранён. Попробуйте меньшую модель или отправьте текст частями. "
                "Проверьте загрузку CPU/GPU командой ollama ps."
            )
        return "Сервис не ответил вовремя. Исходник сохранён."

    def _blocking_request(self, path, payload, exchange) -> dict:
        base = self.config.base_url()
        headers = {"Accept": "application/json", "User-Agent": f"Shelter-Humanizer/{__version__}"}
        if self.config.kind == "api" and self.config.key:
            headers["Authorization"] = "Bearer " + self.config.key
        try:
            data = (
                None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            if data is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(base + path, data=data, headers=headers)
            opener = urllib.request.build_opener(*self.handlers, *exchange.handlers())
            with opener.open(req, timeout=self.config.timeout) as response:
                self.last_stats["http_status"] = response.status
                if exchange.stopped:
                    raise ProviderCancelled("Обработка отменена. Исходник сохранён.")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ProviderError("Сервис вернул неподдерживаемое сжатие ответа.")
                if payload and payload.get("stream"):
                    if self.config.kind == "ollama":
                        return self._read_stream(response, exchange)
                    if response.headers.get_content_type() == "text/event-stream":
                        return self._read_sse(response, exchange)
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ProviderError("Ответ сервиса слишком большой.")
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ProviderError("Сервис вернул неподходящий формат ответа.")
            return decoded
        except urllib.error.HTTPError as exc:
            status = exc.code
            self.last_stats["http_status"] = status
            category = None
            try:
                if status == 500 and self.local_engine:
                    # Bound the read, classify in memory, and discard the raw body.
                    category = classify_body(exc.read(MAX_ERROR_BODY + 1))
            except (OSError, ValueError):
                category = "unknown"
            finally:
                exc.close()
            if category is not None:
                self.last_stats["engine_error"] = category
                raise ProviderError(
                    f"HTTP {status}. {HINTS[category]}", code="ollama_" + category
                ) from None
            label = {
                401: "Проверьте API-ключ.",
                403: "Сервис запретил доступ.",
                404: "Проверьте адрес и имя модели.",
                429: "Лимит сервиса; попробуйте позже.",
                400: "Параметры или формат не поддерживаются выбранной моделью/сервисом.",
                500: "Внутренняя ошибка ИИ-сервиса. Причина не установлена; проверьте его доступность и диагностику.",
                503: "ИИ-сервис временно недоступен или занят. Повторите позже.",
            }.get(status, "Проверьте подключение и настройки сервиса.")
            raise ProviderError(f"HTTP {status}. {label}", code=f"http_{status}") from None
        except (TimeoutError, socket.timeout):
            local = self.config.kind == "ollama" and payload and payload.get("stream")
            raise ProviderError(self._timeout_message(local), code="idle_timeout") from None
        except urllib.error.URLError:
            raise ProviderError(
                "Не удалось подключиться. Для Ollama проверьте, что она запущена."
            ) from None
        except (UnicodeError, json.JSONDecodeError):
            raise ProviderError("Некорректный текст или JSON-ответ сервиса.") from None

    def _read_stream(self, response, exchange):
        buffer, received, content = b"", 0, []
        while True:
            chunk = response.read1(8192)
            received += len(chunk)
            if received > MAX_RESPONSE:
                raise ProviderError("Ответ сервиса слишком большой.")
            buffer += chunk
            lines = buffer.split(b"\n")
            buffer = lines.pop()
            if not chunk and buffer:
                lines.append(buffer)
                buffer = b""
            if len(buffer) > MAX_STREAM_LINE or any(len(line) > MAX_STREAM_LINE for line in lines):
                raise ProviderError("Строка потока ответа слишком большая.")
            for line in lines:
                if not line.strip():
                    continue
                record = json.loads(line.decode("utf-8"))
                if isinstance(record, dict) and record.get("error") and self.local_engine:
                    category = classify(record["error"])
                    self.last_stats["engine_error"] = category
                    raise ProviderError(HINTS[category], code="ollama_" + category)
                if not isinstance(record, dict) or record.get("error"):
                    raise ProviderError("Ollama не смогла завершить ответ. Проверьте её работу.")
                message = record.get("message", {})
                if not isinstance(message, dict):
                    raise ProviderError("Сервис вернул неподходящий формат ответа.")
                piece = message.get("content", "")
                if not isinstance(piece, str):
                    raise ProviderError("Сервис вернул неподходящий формат ответа.")
                if piece:
                    content.append(piece)
                    exchange.characters += len(piece)
                    exchange.phase = "формирование ответа"
                elif message.get("thinking"):
                    exchange.phase = "модель рассуждает"
                if (piece or message.get("thinking")) and exchange.first_fragment is None:
                    exchange.first_fragment = time.monotonic() - exchange.started
                if record.get("done") is True:
                    for key in (
                        "total_duration",
                        "load_duration",
                        "prompt_eval_count",
                        "eval_count",
                    ):
                        value = record.get(key)
                        if type(value) is int and value >= 0:
                            self.last_stats[key] = value
                    return {
                        "done": True,
                        "done_reason": record.get("done_reason"),
                        "message": {"content": "".join(content)},
                    }
            if not chunk:
                raise ProviderError(
                    "Поток ответа оборвался до завершения. Результат не принят; исходник сохранён."
                )

    def models(self) -> list[str]:
        data = self._request("/api/tags" if self.config.kind == "ollama" else "/models")
        items = data.get("models" if self.config.kind == "ollama" else "data", [])
        if not isinstance(items, list):
            raise ProviderError("Сервис вернул неподходящий список моделей.")
        field_name = "name" if self.config.kind == "ollama" else "id"
        return sorted(
            {
                str(item[field_name])
                for item in items
                if isinstance(item, dict) and isinstance(item.get(field_name), str)
            }
        )

    def prepare_model(self):
        if self.config.kind != "ollama" or self.prepared:
            return
        self.config.validate()
        if self.progress:
            self.progress("Проверяем возможности выбранной модели…")
        metadata = Client(
            replace(self.config, timeout=min(5, self.config.timeout), inspect_model=False),
            cancel=self.cancel,
        )
        data = metadata._request("/api/show", {"model": self.config.model})
        capabilities = data.get("capabilities", [])
        if (
            not isinstance(capabilities, list)
            or not isinstance(data.get("details", {}), dict)
            or not isinstance(data.get("model_info", {}), dict)
        ):
            raise ProviderError("Сервис вернул неверные сведения о модели.", code="bad_model_info")
        if capabilities and "completion" not in capabilities:
            raise ProviderError(
                "Выбрана модель без генерации текста. Нужна текстовая chat/instruct-модель.",
                code="model_capability",
            )
        self.model_details = data
        self.prepared = True

    def running(self):
        if self.config.kind != "ollama":
            return []
        data = self._request("/api/ps")
        items = data.get("models", [])
        return items if isinstance(items, list) else []

    def _read_sse(self, response, exchange):
        from .streaming import read_sse

        return read_sse(response, exchange, MAX_RESPONSE, MAX_STREAM_LINE)

    def complete(self, messages: list[dict[str, str]], *, response_schema=None) -> str:
        self.config.validate()
        for key in ("http_status", "engine_error", "request_time_s", "first_fragment_s"):
            self.last_stats.pop(key, None)
        if self.config.inspect_model:
            self.prepare_model()
        payload = {"model": self.config.model.strip(), "messages": messages, "stream": False}
        if self.config.kind == "ollama":
            payload["stream"] = True
            payload["options"] = {"temperature": 0.35, "num_predict": 8192}
            family = self.config.model.strip().lower().rsplit("/", 1)[-1].split(":", 1)[0]
            details = self.model_details.get("details", {})
            info = self.model_details.get("model_info", {})
            hints = " ".join(
                str(info.get(key, ""))
                for key in ("general.architecture", "general.name", "general.basename")
            ).lower()
            if family not in ("qwen3", "qwen3.5", "deepseek-r1"):
                native = str(details.get("family", "")).lower()
                if "deepseek" in hints and "r1" in hints:
                    family = "deepseek-r1"
                elif native in ("qwen35", "qwen3.5") or "qwen3.5" in hints:
                    family = "qwen3.5"
                elif native == "qwen3":
                    family = "qwen3"
            if family in ("qwen3", "qwen3.5"):
                # Explicit API switch: /no_think is not supported by Qwen 3.5.
                payload["think"] = False
                payload["options"].update(
                    temperature=0.7,
                    top_p=0.8,
                    top_k=20,
                    min_p=0.0,
                    repeat_penalty=1.0,
                    num_ctx=16384,
                )
                if family == "qwen3.5":
                    payload["options"]["presence_penalty"] = 1.5
            elif family == "deepseek-r1":
                # R1-0528 supports system instructions. Ollama separates reasoning
                # into message.thinking; only message.content is returned below.
                payload["think"] = True
                payload["options"].update(
                    temperature=0.6,
                    top_p=0.95,
                    top_k=0,
                    min_p=0.0,
                    repeat_penalty=1.0,
                    num_ctx=32768,
                    num_predict=16384,
                )
            if self.config.adaptive_context:
                from .budget import request_budget

                limits = [
                    value
                    for key, value in info.items()
                    if key.endswith(".context_length") and type(value) is int and value > 0
                ]
                context, output, estimate = request_budget(
                    messages,
                    reasoning=family == "deepseek-r1",
                    context_limit=min(limits) if limits else 32768,
                    schema=response_schema,
                )
                payload["options"].update(num_ctx=context, num_predict=output)
                self.last_stats.update(
                    context_tokens=context, reply_limit=output, input_token_estimate=estimate
                )
            if response_schema is not None:
                payload["format"] = response_schema
            if self.config.cpu_only:
                payload["options"]["num_gpu"] = 0
            self.last_stats["cpu_only"] = bool(self.config.cpu_only)
            data = self._request("/api/chat", payload)
            if data.get("done") is not True or data.get("done_reason") in ("length", "max_tokens"):
                raise ProviderError(
                    "Модель не завершила ответ; результат не принят. Сократите текст."
                )
            reply = data.get("message", {})
        else:
            payload["stream"] = self.config.streaming
            payload.update(temperature=0.35, max_tokens=8192)
            data = self._request("/chat/completions", payload)
            choices = data.get("choices", [])
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ProviderError("Сервис не вернул вариант текста.")
            if choices[0].get("finish_reason") != "stop":
                raise ProviderError("Ответ прерван или отклонён сервисом; результат не принят.")
            reply = choices[0].get("message", {})
        text = reply.get("content") if isinstance(reply, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("Модель вернула пустой или неподдерживаемый ответ.")
        return text.strip()
