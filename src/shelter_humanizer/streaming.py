"""Bounded OpenAI-compatible SSE parser. Only finished text is accepted."""

import json
import time

from .providers import ProviderError


def read_sse(response, exchange, max_response, max_line):
    buffer, received, fields, content = b"", 0, [], []
    finished = None
    started = False

    def event(lines):
        nonlocal finished, started
        if not lines:
            return
        data = "\n".join(lines)
        if data == "[DONE]":
            if finished != "stop":
                raise ProviderError(
                    "Поток ИИ завершился без полного ответа.", code="incomplete_response"
                )
            return "done"
        record = json.loads(data)
        if not isinstance(record, dict) or record.get("error"):
            raise ProviderError("ИИ-сервис вернул ошибку потока.", code="bad_response")
        choices = record.get("choices", [])
        if not isinstance(choices, list):
            raise ProviderError("Некорректный поток ответа.", code="bad_response")
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise ProviderError("Некорректный поток ответа.", code="bad_response")
            piece = delta.get("content", "")
            if piece is None:
                piece = ""
            if not isinstance(piece, str):
                raise ProviderError("Сервис вернул неподдерживаемый текст.", code="bad_response")
            if piece:
                if finished is not None:
                    raise ProviderError(
                        "Сервис продолжил ответ после завершения.", code="bad_response"
                    )
                content.append(piece)
                exchange.characters += len(piece)
                exchange.phase = "формирование ответа"
                started = True
                if exchange.first_fragment is None:
                    exchange.first_fragment = time.monotonic() - exchange.started
            reason = choice.get("finish_reason")
            if reason is not None:
                finished = reason
                if reason != "stop":
                    raise ProviderError(
                        "Ответ прерван или отклонён сервисом; результат не принят.",
                        code="incomplete_response",
                    )

    while True:
        chunk = response.read1(8192)
        received += len(chunk)
        if received > max_response:
            raise ProviderError("Ответ сервиса слишком большой.", code="response_size")
        buffer += chunk
        lines = buffer.split(b"\n")
        buffer = lines.pop()
        if not chunk and buffer:
            lines.append(buffer)
            buffer = b""
        if len(buffer) > max_line or any(len(line) > max_line for line in lines):
            raise ProviderError("Строка потока ответа слишком большая.", code="response_size")
        for raw in lines:
            line = raw.decode("utf-8").rstrip("\r")
            if not line:
                if event(fields) == "done":
                    return {
                        "choices": [
                            {"finish_reason": "stop", "message": {"content": "".join(content)}}
                        ]
                    }
                fields = []
            elif line.startswith("data:"):
                fields.append(line[5:].lstrip(" "))
        if not chunk:
            event(fields)
            if finished == "stop" and started:
                return {
                    "choices": [{"finish_reason": "stop", "message": {"content": "".join(content)}}]
                }
            raise ProviderError(
                "Поток ответа оборвался. Исходник сохранён.", code="incomplete_response"
            )
