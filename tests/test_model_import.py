import hashlib
import json
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shelter_humanizer.local_models import DownloadCancelled
from shelter_humanizer.model_import import import_gguf, inspect_gguf
from shelter_humanizer.providers import ProviderError


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "моя модель.gguf"
        self.raw = b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + b"synthetic payload"
        self.path.write_bytes(self.raw)
        self.requests = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                parent.requests.append(("HEAD", self.path, b""))
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                parent.requests.append(("POST", self.path, body))
                self.send_response(201 if self.path.startswith("/api/blobs/") else 200)
                self.end_headers()
                if self.path == "/api/create":
                    self.wfile.write(b'{"status":"success"}\n')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def test_import_hashes_streams_local_file_and_uses_generic_filename(self):
        progress = []
        original = self.path.read_bytes()
        name = import_gguf(self.path, "my-model", threading.Event(), progress.append, self.url)
        self.assertEqual(name, "my-model:latest")
        self.assertEqual(self.path.read_bytes(), original)
        digest = "sha256:" + hashlib.sha256(self.raw).hexdigest()
        self.assertEqual(self.requests[1][1], "/api/blobs/" + digest)
        self.assertEqual(self.requests[1][2], self.raw)
        create = json.loads(self.requests[2][2])
        self.assertEqual(create["files"], {"model.gguf": digest})
        self.assertNotIn(str(self.path), json.dumps(create))
        self.assertTrue(any("импортирован" in message for message in progress))

    def test_remote_import_and_invalid_model_are_rejected_before_upload(self):
        self.assertRaises(
            ProviderError,
            import_gguf,
            self.path,
            "my-model",
            threading.Event(),
            lambda message: None,
            "https://example.org",
        )
        self.assertRaises(
            ProviderError,
            import_gguf,
            self.path,
            "../../bad",
            threading.Event(),
            lambda message: None,
            self.url,
        )
        self.assertEqual(self.requests, [])

    def test_cancel_before_upload_does_not_send_or_modify_file(self):
        cancel = threading.Event()
        cancel.set()
        self.assertRaises(
            DownloadCancelled,
            import_gguf,
            self.path,
            "my-model",
            cancel,
            lambda message: None,
            self.url,
        )
        self.assertEqual(self.requests, [])
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_bad_magic_and_split_file_require_clear_error(self):
        self.path.write_bytes(b"not a GGUF model")
        self.assertRaises(ProviderError, inspect_gguf, self.path)
        split = self.path.with_name("model-00001-of-00002.gguf")
        split.write_bytes(self.raw)
        self.assertRaisesRegex(ProviderError, "одним GGUF", inspect_gguf, split)


if __name__ == "__main__":
    unittest.main()
