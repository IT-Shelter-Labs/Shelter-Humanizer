import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shelter_humanizer.local_models import PRESETS, DownloadCancelled, pull_model
from shelter_humanizer.providers import MAX_RESPONSE, Client, ProviderConfig, ProviderError


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.response = {}
        cls.status = 200
        cls.headers = {}
        cls.drip = False
        cls.chunks = None
        cls.wait_for_disconnect = False
        cls.peer_closed = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                size = int(self.headers.get("Content-Length", 0))
                cls.requests.append((self.path, dict(self.headers), self.rfile.read(size)))
                self.send_response(cls.status)
                for key, value in cls.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                raw = (
                    cls.response
                    if isinstance(cls.response, bytes)
                    else json.dumps(cls.response, ensure_ascii=False).encode()
                )
                if cls.chunks is not None:
                    for delay, chunk in cls.chunks:
                        time.sleep(delay)
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except OSError:
                            break
                    if cls.wait_for_disconnect:
                        self.connection.settimeout(2)
                        try:
                            if self.rfile.read(1) == b"":
                                cls.peer_closed.set()
                        except OSError:
                            pass
                elif cls.drip:
                    for byte in raw:
                        try:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                        except OSError:
                            break
                        time.sleep(0.1)
                else:
                    self.wfile.write(raw)

            do_POST = handle_request
            do_GET = handle_request

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        type(self).requests = []
        type(self).response = {}
        type(self).status = 200
        type(self).headers = {}
        type(self).drip = False
        type(self).chunks = None
        type(self).wait_for_disconnect = False
        type(self).peer_closed = threading.Event()

    def client(self, kind="api"):
        return Client(ProviderConfig(kind, self.url, "test-model", "test-key", 2))

    def test_api_utf8_payload_auth_and_finish(self):
        type(self).response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "Текст"}}]
        }
        self.assertEqual(self.client().complete([{"role": "user", "content": "Ёлка"}]), "Текст")
        path, headers, body = self.requests[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(json.loads(body)["messages"][0]["content"], "Ёлка")
        self.assertTrue(json.loads(body)["stream"])

    def test_ollama_no_auth_and_correct_protocol(self):
        type(self).response = {"done": True, "message": {"content": "Готово"}}
        self.assertEqual(self.client("ollama").complete([]), "Готово")
        path, headers, body = self.requests[0]
        self.assertEqual(path, "/api/chat")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(json.loads(body)["options"]["temperature"], 0.35)
        self.assertIs(json.loads(body)["stream"], True)

    @staticmethod
    def record(content="", thinking="", **extra):
        return (
            json.dumps(
                {"message": {"content": content, "thinking": thinking}, "done": False, **extra},
                ensure_ascii=False,
            )
            + "\n"
        ).encode()

    def test_local_stream_can_exceed_idle_timeout_while_tokens_arrive(self):
        source = ("Редактор сохраняет смысл текста. " * 150)[:4407]
        progress = []
        type(self).chunks = [
            (0.2, self.record(source[start : start + 600])) for start in range(0, len(source), 600)
        ] + [(0, self.record(done=True, done_reason="stop"))]
        started = time.monotonic()
        client = Client(
            ProviderConfig("ollama", self.url, "qwen3.5:4b", timeout=1, generation_timeout=4),
            progress=progress.append,
        )
        self.assertEqual(client.complete([{"role": "user", "content": source}]), source)
        self.assertGreater(time.monotonic() - started, 1)
        self.assertTrue(progress)
        self.assertTrue(any("символов" in value for value in progress))
        self.assertTrue(all(source[:20] not in value for value in progress))

    def test_stream_handles_split_utf8_and_keeps_reasoning_out_of_result(self):
        raw = (
            self.record(thinking="private reasoning")
            + self.record("Ёлка 👩‍💻")
            + self.record(" готова", done=True, done_reason="stop")
        )
        type(self).chunks = [(0, raw[i : i + 1]) for i in range(len(raw))]
        self.assertEqual(self.client("ollama").complete([]), "Ёлка 👩‍💻 готова")

    def test_stream_requires_terminal_record_and_rejects_private_errors(self):
        for raw in (
            self.record("partial"),
            self.record("partial", done=True, done_reason="length"),
            b'{"error":"private server data"}\n',
            b'{"message":{"content":[]},"done":true}\n',
            b'{"message":{"content":"ok"},"done":false}\nnot-json\n',
            b" " * 65537,
        ):
            type(self).response = raw
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(ProviderError) as caught:
                    self.client("ollama").complete([])
                self.assertNotIn("private server data", str(caught.exception))

    def test_stream_has_total_deadline_even_if_model_keeps_sending(self):
        type(self).chunks = [(0.15, self.record("part"))] * 12
        client = Client(ProviderConfig("ollama", self.url, "test", timeout=1, generation_timeout=1))
        started = time.monotonic()
        self.assertRaisesRegex(ProviderError, "Локальный ИИ", client.complete, [])
        self.assertLess(time.monotonic() - started, 2)

    def test_stream_idle_timeout_is_still_bounded(self):
        type(self).chunks = [(0, self.record("start")), (1.4, self.record(done=True))]
        client = Client(ProviderConfig("ollama", self.url, "test", timeout=1, generation_timeout=4))
        started = time.monotonic()
        self.assertRaisesRegex(ProviderError, "Локальный ИИ", client.complete, [])
        self.assertLess(time.monotonic() - started, 2)

    def test_cancel_interrupts_active_stream_without_accepting_partial_text(self):
        from shelter_humanizer.providers import ProviderCancelled

        type(self).chunks = [(0, self.record("partial"))]
        type(self).wait_for_disconnect = True
        cancel = threading.Event()
        timer = threading.Timer(0.15, cancel.set)
        client = Client(self.client("ollama").config, cancel=cancel)
        timer.start()
        started = time.monotonic()
        try:
            self.assertRaises(ProviderCancelled, client.complete, [])
            self.assertLess(time.monotonic() - started, 0.7)
            self.assertTrue(self.peer_closed.wait(1), "Cancelled request socket remained open")
        finally:
            timer.cancel()

    def test_pre_cancel_does_not_send_text(self):
        from shelter_humanizer.providers import ProviderCancelled

        cancel = threading.Event()
        cancel.set()
        self.assertRaises(
            ProviderCancelled, Client(self.client().config, cancel=cancel).complete, []
        )
        self.assertEqual(self.requests, [])

    def test_model_list(self):
        type(self).response = {
            "models": [{"name": "model-b"}, {"name": "model-a"}, {"name": "model-a"}]
        }
        self.assertEqual(self.client("ollama").models(), ["model-a", "model-b"])
        self.assertEqual(self.requests[0][0], "/api/tags")

    def test_qwen3_families_use_non_thinking_and_suitable_context(self):
        type(self).response = {"done": True, "message": {"content": "Текст"}}
        for preset in PRESETS:
            if not preset.name.startswith(("qwen3:", "qwen3.5:")):
                continue
            with self.subTest(model=preset.name):
                client = Client(ProviderConfig("ollama", self.url, preset.name, timeout=2))
                self.assertEqual(client.complete([]), "Текст")
                payload = json.loads(self.requests[-1][2])
                self.assertIs(payload["think"], False)
                self.assertGreaterEqual(payload["options"]["num_ctx"], 4096)
                self.assertLessEqual(payload["options"]["num_ctx"], 16384)
                self.assertEqual(payload["options"]["temperature"], 0.7)
                self.assertEqual(payload["options"]["top_p"], 0.8)
                self.assertEqual(payload["options"]["top_k"], 20)
                if preset.name.startswith("qwen3.5:"):
                    self.assertEqual(payload["options"]["presence_penalty"], 1.5)

    def test_deepseek_returns_only_final_answer_not_reasoning(self):
        type(self).response = {
            "done": True,
            "message": {"content": "Готовый текст", "thinking": "Внутренние рассуждения"},
        }
        client = Client(
            ProviderConfig("ollama", self.url, "deepseek-r1:8b-0528-qwen3-q4_K_M", timeout=2)
        )
        self.assertEqual(client.complete([]), "Готовый текст")
        payload = json.loads(self.requests[-1][2])
        self.assertIs(payload["think"], True)
        self.assertEqual(payload["options"]["temperature"], 0.6)
        self.assertEqual(payload["options"]["num_ctx"], 32768)
        self.assertEqual(payload["options"]["num_predict"], 16384)
        type(self).response = {"done": True, "message": {"thinking": "Только рассуждения"}}
        self.assertRaises(ProviderError, client.complete, [])
        type(self).response = {
            "done": True,
            "done_reason": "length",
            "message": {"content": "Незавершённый текст"},
        }
        self.assertRaises(ProviderError, client.complete, [])

    def test_generic_api_does_not_receive_ollama_thinking_parameters(self):
        type(self).response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "Текст"}}]
        }
        Client(ProviderConfig("api", self.url, "qwen3.5:4b", timeout=2)).complete([])
        payload = json.loads(self.requests[-1][2])
        self.assertNotIn("think", payload)
        self.assertNotIn("options", payload)

    def test_model_pull_stream_has_progress_and_no_text_or_auth(self):
        type(
            self
        ).response = (
            b'{"status":"pulling layer","total":100,"completed":50}\n{"status":"success"}\n'
        )
        progress = []
        pull_model(PRESETS[0].name, threading.Event(), progress.append, self.url, 2)
        path, headers, body = self.requests[0]
        self.assertEqual(path, "/api/pull")
        self.assertEqual(json.loads(body), {"model": PRESETS[0].name, "stream": True})
        self.assertNotIn("Authorization", headers)
        self.assertIn("50%", progress[0])
        self.assertIn("проверена", progress[-1])

    def test_model_pull_requires_confirmed_completion(self):
        for response in (
            b'{"status":"pulling manifest"}\n',
            b"[]\n",
            b"broken\n",
            b"\xff\n",
            b" " * 65537,
        ):
            type(self).response = response
            self.assertRaises(
                ProviderError,
                pull_model,
                PRESETS[0].name,
                threading.Event(),
                lambda text: None,
                self.url,
                2,
            )

    def test_model_pull_cancel_before_and_during_stream(self):
        cancel = threading.Event()
        cancel.set()
        self.assertRaises(
            DownloadCancelled, pull_model, PRESETS[0].name, cancel, lambda text: None, self.url, 2
        )
        self.assertEqual(self.requests, [])
        cancel.clear()
        type(
            self
        ).response = (
            b'{"status":"pulling layer","total":100,"completed":50}\n{"status":"success"}\n'
        )
        self.assertRaises(
            DownloadCancelled,
            pull_model,
            PRESETS[0].name,
            cancel,
            lambda text: cancel.set(),
            self.url,
            2,
        )

    def test_model_pull_can_cancel_an_incomplete_slow_line(self):
        type(self).response = b'{"status":"pulling manifest"}\n'
        type(self).drip = True
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        started = time.monotonic()
        timer.start()
        try:
            self.assertRaises(
                DownloadCancelled,
                pull_model,
                PRESETS[0].name,
                cancel,
                lambda text: None,
                self.url,
                2,
            )
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            timer.cancel()

    def test_model_pull_rejects_remote_and_unknown_model(self):
        for model, url in (
            (PRESETS[0].name, "https://example.org"),
            ("arbitrary:latest", self.url),
        ):
            self.assertRaises(
                ProviderError, pull_model, model, threading.Event(), lambda text: None, url, 2
            )
        self.assertEqual(self.requests, [])

    def test_model_pull_redirect_and_provider_errors_are_safe(self):
        type(self).status = 302
        type(self).headers = {"Location": self.url + "/redirect"}
        self.assertRaises(
            ProviderError,
            pull_model,
            PRESETS[0].name,
            threading.Event(),
            lambda text: None,
            self.url,
            2,
        )
        self.assertEqual(len(self.requests), 1)
        type(self).status = 200
        type(self).headers = {}
        type(self).response = b'{"error":"private server data"}\n'
        with self.assertRaises(ProviderError) as caught:
            pull_model(PRESETS[0].name, threading.Event(), lambda text: None, self.url, 2)
        self.assertNotIn("private server data", str(caught.exception))

    def test_whole_exchange_deadline_even_with_trickling_body(self):
        type(self).response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "Длинный ответ"}}]
        }
        type(self).drip = True
        started = time.monotonic()
        client = Client(
            ProviderConfig("api", self.url, "test-model", "test-key", 1, generation_timeout=1)
        )
        self.assertRaisesRegex(ProviderError, "вовремя", client.complete, [])
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(len(self.requests), 1)

    def test_truncation_or_non_string_never_accepted(self):
        for reason, content in (
            ("length", "partial"),
            ("content_filter", "blocked"),
            ("stop", []),
            ("stop", ""),
        ):
            type(self).response = {
                "choices": [{"finish_reason": reason, "message": {"content": content}}]
            }
            self.assertRaises(ProviderError, self.client().complete, [])
        type(self).response = {"done": False, "message": {"content": "partial"}}
        self.assertRaises(ProviderError, self.client("ollama").complete, [])

    def test_redirect_not_followed(self):
        type(self).status = 302
        type(self).headers = {"Location": self.url + "/steal"}
        self.assertRaises(ProviderError, self.client().complete, [])
        self.assertEqual(len(self.requests), 1)

    def test_http_body_and_key_are_not_in_error(self):
        type(self).status = 401
        type(self).response = b"test-key private-text"
        with self.assertRaises(ProviderError) as caught:
            self.client().complete([])
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("test-key", str(caught.exception))
        self.assertNotIn("private-text", str(caught.exception))

    def test_local_http_500_recognizes_categories_without_exposing_raw_body(self):
        type(self).status = 500
        for message, category in (
            ("model requires more system memory", "memory"),
            ("CUDA error: out of memory", "memory"),
            ("CUDA error: driver failure", "gpu"),
            ("unknown model architecture: qwen35", "architecture"),
            ("invalid GGUF file", "model_file"),
            ("runner process has terminated", "runner"),
            ("something failed", "unknown"),
        ):
            with self.subTest(category=category, message=message):
                type(self).response = {"error": message + " private-text test-key C:/Users/private"}
                client = self.client("ollama")
                with self.assertRaises(ProviderError) as caught:
                    client.complete([])
                self.assertEqual(caught.exception.code, "ollama_" + category)
                self.assertEqual(client.last_stats["engine_error"], category)
                for secret in ("private-text", "test-key", "C:/Users/private"):
                    self.assertNotIn(secret, str(caught.exception))
                self.assertEqual(len(self.requests), 1)
                type(self).requests = []

    def test_500_does_not_guess_memory_or_classify_remote_api_body(self):
        type(self).status = 500
        for response in (
            b"not json private-text",
            {"error": []},
            b'{"error":"out of memory ' + b"x" * 9000 + b'"}',
        ):
            type(self).response = response
            with self.assertRaises(ProviderError) as caught:
                self.client("ollama").complete([])
            self.assertEqual(caught.exception.code, "ollama_unknown")
            self.assertIn("не доказывает", str(caught.exception))
            self.assertNotIn("private-text", str(caught.exception))
        type(self).response = {"error": "CUDA error private-text"}
        with self.assertRaises(ProviderError) as caught:
            self.client("api").complete([])
        self.assertEqual(caught.exception.code, "http_500")
        self.assertNotIn("CUDA", str(caught.exception))

    def test_streaming_engine_error_rejects_partial_text_and_keeps_safe_category(self):
        type(self).response = self.record("partial") + b'{"error":"CUDA error private data"}\n'
        client = self.client("ollama")
        with self.assertRaises(ProviderError) as caught:
            client.complete([])
        self.assertEqual(caught.exception.code, "ollama_gpu")
        self.assertNotIn("private data", str(caught.exception))
        self.assertEqual(client.last_stats["engine_error"], "gpu")

    def test_cpu_only_is_explicit_and_never_changes_api_payload_or_model(self):
        type(self).response = {"done": True, "message": {"content": "Текст"}}
        for cpu in (False, True):
            client = Client(ProviderConfig("ollama", self.url, "qwen3.5:4b", cpu_only=cpu))
            client.complete([])
            data = json.loads(self.requests[-1][2])
            self.assertEqual(data["model"], "qwen3.5:4b")
            if cpu:
                self.assertEqual(data["options"]["num_gpu"], 0)
            else:
                self.assertNotIn("num_gpu", data["options"])
        type(self).response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "Текст"}}]
        }
        Client(ProviderConfig("api", self.url, "test-model", cpu_only=True)).complete([])
        self.assertNotIn("options", json.loads(self.requests[-1][2]))

    def test_success_after_manual_retry_does_not_report_stale_engine_error(self):
        client = self.client("ollama")
        type(self).status = 500
        type(self).response = {"error": "CUDA error"}
        self.assertRaises(ProviderError, client.complete, [])
        self.assertEqual(client.last_stats["engine_error"], "gpu")
        type(self).status = 200
        type(self).response = {"done": True, "message": {"content": "Текст"}}
        self.assertEqual(client.complete([]), "Текст")
        self.assertNotIn("engine_error", client.last_stats)
        self.assertEqual(client.last_stats["http_status"], 200)

    def test_malformed_and_oversized(self):
        for response in (b"not json", b"\xff", [], b" " * (MAX_RESPONSE + 1)):
            type(self).response = response
            self.assertRaises(ProviderError, self.client().complete, [])

    def test_address_validation(self):
        for url in (
            "http://example.org",
            "file:///tmp/key",
            "https://user:pass@host",
            "https://host?q=1",
            "https://host/#key",
            "http://127.0.0.1.evil",
            "http://localhost:bad",
            "http://localhost:0",
        ):
            self.assertRaises(ProviderError, ProviderConfig(url=url).base_url)
        self.assertEqual(ProviderConfig(url="http://[::1]:11434/").base_url(), "http://[::1]:11434")

    def test_invalid_model_prevents_request(self):
        client = Client(ProviderConfig("api", self.url, "", "test-key"))
        self.assertRaises(ProviderError, client.complete, [])
        self.assertEqual(self.requests, [])

    def test_openai_compatible_sse_is_reassembled_without_reasoning(self):
        type(self).headers = {"Content-Type": "text/event-stream"}
        records = [
            {"choices": [{"index": 0, "delta": {"reasoning_content": "private thinking"}}]},
            {"choices": [{"index": 0, "delta": {"content": "Ёлка "}}]},
            {"choices": [{"index": 0, "delta": {"content": "👩‍💻"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        raw = (
            b": heartbeat\n\n"
            + b"".join(
                ("data: " + json.dumps(record, ensure_ascii=False) + "\r\n\r\n").encode()
                for record in records
            )
            + b"data: [DONE]\n\n"
        )
        type(self).chunks = [(0, raw[i : i + 3]) for i in range(0, len(raw), 3)]
        self.assertEqual(self.client().complete([]), "Ёлка 👩‍💻")

    def test_api_stream_can_be_explicitly_disabled_for_legacy_server(self):
        type(self).response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "Текст"}}]
        }
        client = Client(ProviderConfig("api", self.url, "legacy", streaming=False))
        self.assertEqual(client.complete([]), "Текст")
        self.assertIs(json.loads(self.requests[-1][2])["stream"], False)

    def test_sse_incomplete_or_truncated_text_is_never_accepted(self):
        type(self).headers = {"Content-Type": "text/event-stream"}
        for response in (
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n',
            b"data: [DONE]\n\n",
        ):
            type(self).response = response
            self.assertRaises(ProviderError, self.client().complete, [])

    def test_custom_alias_uses_model_metadata_and_rejects_embedding_only(self):
        type(self).response = {
            "capabilities": ["completion"],
            "details": {"family": "qwen35"},
            "model_info": {"qwen35.context_length": 32768},
        }
        client = Client(
            ProviderConfig("ollama", self.url, "my-editor:latest", timeout=2, inspect_model=True)
        )
        client.prepare_model()
        self.assertEqual(self.requests[-1][0], "/api/show")
        type(self).response = {"done": True, "message": {"content": "Текст"}}
        self.assertEqual(client.complete([]), "Текст")
        payload = json.loads(self.requests[-1][2])
        self.assertIs(payload["think"], False)
        type(self).response = {"capabilities": ["embedding"]}
        other = Client(
            ProviderConfig("ollama", self.url, "embedding", timeout=2, inspect_model=True)
        )
        self.assertRaisesRegex(ProviderError, "генерации текста", other.complete, [])

    def test_structured_review_schema_is_only_sent_when_requested(self):
        type(self).response = {"done": True, "message": {"content": '{"edits":[]}'}}
        schema = {"type": "object", "properties": {"edits": {"type": "array"}}}
        self.client("ollama").complete([], response_schema=schema)
        self.assertEqual(json.loads(self.requests[-1][2])["format"], schema)

    def test_full_endpoint_urls_are_normalized_without_redirects(self):
        self.assertEqual(
            ProviderConfig("api", "https://example.org/v1/chat/completions").base_url(),
            "https://example.org/v1",
        )
        self.assertEqual(ProviderConfig(url=self.url + "/api/chat").base_url(), self.url)

    def test_local_generation_deadline_must_be_finite_and_bounded(self):
        for limit in (0, 1801, float("inf"), float("nan")):
            self.assertRaises(ProviderError, ProviderConfig(generation_timeout=limit).base_url)


if __name__ == "__main__":
    unittest.main()
