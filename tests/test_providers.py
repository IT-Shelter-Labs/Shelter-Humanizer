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
                if cls.drip:
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
        self.assertFalse(json.loads(body)["stream"])

    def test_ollama_no_auth_and_correct_protocol(self):
        type(self).response = {"done": True, "message": {"content": "Готово"}}
        self.assertEqual(self.client("ollama").complete([]), "Готово")
        path, headers, body = self.requests[0]
        self.assertEqual(path, "/api/chat")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(json.loads(body)["options"]["temperature"], 0.35)

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
                self.assertEqual(payload["options"]["num_ctx"], 16384)
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
        client = Client(ProviderConfig("api", self.url, "test-model", "test-key", 1))
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


if __name__ == "__main__":
    unittest.main()
