"""Small bounded HTTP clients. No retries, redirects, fallback or key persistence."""

from __future__ import annotations

import json
import queue
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import __version__

MAX_RESPONSE = 2 * 1024 * 1024


class ProviderError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    kind: str = "ollama"
    url: str = "http://localhost:11434"
    model: str = ""
    key: str = field(default="", repr=False)
    timeout: float = 60

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
        if any(ord(c) < 32 for c in self.url + self.key):
            raise ProviderError("Адрес или ключ содержат управляющие символы.")
        return self.url.strip().rstrip("/")

    def validate(self) -> None:
        self.base_url()
        if not self.model.strip() or len(self.model) > 160 or any(ord(c) < 32 for c in self.model):
            raise ProviderError("Укажите точное имя модели из вашего сервиса.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config: ProviderConfig):
        self.config = config
        # Local models do not accidentally go through the system HTTP proxy.
        local = urllib.parse.urlsplit(config.base_url()).hostname in (
            "localhost",
            "127.0.0.1",
            "::1",
        )
        handlers = [NoRedirect()]
        if local:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def _request(self, path: str, payload: dict | None = None) -> dict:
        # Socket timeouts alone reset for each read. Bound the whole exchange as well.
        completed = queue.Queue(maxsize=1)

        def transport():
            try:
                completed.put((True, self._blocking_request(path, payload)))
            except ProviderError as exc:
                completed.put((False, exc))
            except Exception:
                completed.put(
                    (False, ProviderError("Ошибка сетевого обмена. Проверьте подключение."))
                )

        threading.Thread(target=transport, daemon=True).start()
        try:
            success, value = completed.get(timeout=self.config.timeout)
        except queue.Empty:
            raise ProviderError("Сервис не ответил вовремя. Исходник сохранён.") from None
        if not success:
            raise value
        return value

    def _blocking_request(self, path: str, payload: dict | None = None) -> dict:
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
            with self.opener.open(req, timeout=self.config.timeout) as response:
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ProviderError("Сервис вернул неподдерживаемое сжатие ответа.")
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ProviderError("Ответ сервиса слишком большой.")
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ProviderError("Сервис вернул неподходящий формат ответа.")
            return decoded
        except urllib.error.HTTPError as exc:
            exc.close()
            label = {
                401: "Проверьте API-ключ.",
                403: "Сервис запретил доступ.",
                404: "Проверьте адрес и имя модели.",
                429: "Лимит сервиса; попробуйте позже.",
            }.get(exc.code, "Проверьте подключение и настройки сервиса.")
            raise ProviderError(f"HTTP {exc.code}. {label}") from None
        except (TimeoutError, socket.timeout):
            raise ProviderError("Сервис не ответил вовремя. Исходник сохранён.") from None
        except urllib.error.URLError:
            raise ProviderError(
                "Не удалось подключиться. Для Ollama проверьте, что она запущена."
            ) from None
        except (UnicodeError, json.JSONDecodeError):
            raise ProviderError("Некорректный текст или JSON-ответ сервиса.") from None

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

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.config.validate()
        payload = {"model": self.config.model.strip(), "messages": messages, "stream": False}
        if self.config.kind == "ollama":
            payload["options"] = {"temperature": 0.35, "num_predict": 8192}
            family = self.config.model.strip().lower().rsplit("/", 1)[-1].split(":", 1)[0]
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
            data = self._request("/api/chat", payload)
            if data.get("done") is not True or data.get("done_reason") in ("length", "max_tokens"):
                raise ProviderError(
                    "Модель не завершила ответ; результат не принят. Сократите текст."
                )
            reply = data.get("message", {})
        else:
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
