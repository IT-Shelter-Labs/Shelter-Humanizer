import ctypes
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from shelter_humanizer.local_models import PRESETS, DownloadCancelled
from shelter_humanizer.managed_runtime import (
    CombinedCancel,
    InstallLock,
    ManagedRuntime,
    SafeRedirect,
    WindowsJob,
    download_runtime,
    extract_runtime,
)
from shelter_humanizer.providers import ProviderError


class DownloadTests(unittest.TestCase):
    def download(self, initial=b"", response=b"portable-runtime", status=200, headers=None):
        content = b"portable-runtime"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "runtime.part"
            target.write_bytes(initial)
            source = io.BytesIO(response)
            source.status, source.headers = status, headers or {}
            opener = Mock()
            opener.open.return_value = source
            with (
                patch("shelter_humanizer.managed_runtime.RUNTIME_SIZE", len(content)),
                patch(
                    "shelter_humanizer.managed_runtime.RUNTIME_SHA256",
                    hashlib.sha256(content).hexdigest(),
                ),
                patch(
                    "shelter_humanizer.managed_runtime.urllib.request.build_opener",
                    return_value=opener,
                ),
            ):
                download_runtime(target, threading.Event(), lambda message: None)
            self.assertEqual(target.read_bytes(), content)
            return opener.open.call_args.args[0]

    def test_verified_download(self):
        request = self.download()
        self.assertTrue(request.full_url.startswith("https://github.com/ollama/ollama/"))

    def test_resume_and_range_validation(self):
        request = self.download(b"portable-", b"runtime", 206, {"Content-Range": "bytes 9-15/16"})
        self.assertEqual(request.get_header("Range"), "bytes=9-")

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        self.download(b"partial")

    def test_bad_range_fails_closed(self):
        with self.assertRaisesRegex(ProviderError, "продолжение"):
            self.download(b"portable-", b"runtime", 206, {"Content-Range": "bytes 0-6/16"})

    def test_bad_checksum_fails_closed(self):
        with self.assertRaisesRegex(ProviderError, "Контрольная сумма"):
            self.download(response=b"untrusted-bytes!")

    def test_cancel_before_network(self):
        cancel = threading.Event()
        cancel.set()
        with patch("shelter_humanizer.managed_runtime.urllib.request.build_opener") as opener:
            with self.assertRaises(DownloadCancelled):
                download_runtime("unused.part", cancel, lambda message: None)
            opener.assert_not_called()

    def test_redirect_rejects_external_and_plain_http(self):
        handler = SafeRedirect()
        request = urllib.request.Request("https://github.com/ollama/ollama/releases/download/x.zip")
        for url in ("https://example.org/ollama.zip", "http://github.com/ollama.zip"):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                handler.redirect_request(request, None, 302, "", {}, url)


class ArchiveTests(unittest.TestCase):
    def bundle(self, name="ollama.exe", info=None):
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as archive:
            archive.writestr(info or name, b"verified synthetic executable")
        result.seek(0)
        return result

    def test_extracts_root_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            extract_runtime(self.bundle(), directory, threading.Event(), lambda message: None)
            self.assertTrue((Path(directory) / "ollama.exe").is_file())

    def test_rejects_traversal_absolute_and_alternate_streams(self):
        for name in ("../escape", "..\\escape", "/escape", "C:/escape", "ollama.exe:stream"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ProviderError):
                    extract_runtime(
                        self.bundle(name), directory, threading.Event(), lambda message: None
                    )
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_rejects_symlink(self):
        info = zipfile.ZipInfo("ollama.exe")
        info.external_attr = 0o120777 << 16
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ProviderError):
            extract_runtime(
                self.bundle(info=info), directory, threading.Event(), lambda message: None
            )

    def test_rejects_case_collisions_before_extracting(self):
        bundle = self.bundle()
        with zipfile.ZipFile(bundle, "a") as archive:
            archive.writestr("OLLAMA.exe", b"duplicate")
        bundle.seek(0)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ProviderError):
            extract_runtime(bundle, directory, threading.Event(), lambda message: None)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_requires_executable(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ProviderError, "отсутствует"),
        ):
            extract_runtime(
                self.bundle("readme.txt"), directory, threading.Event(), lambda message: None
            )


class RuntimeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows owned process launch")
    def test_wizard_combined_cancel_allows_cold_server_to_become_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            with (
                patch.object(runtime, "installed", return_value=True),
                patch("shelter_humanizer.managed_runtime.subprocess.Popen") as spawn,
                patch("shelter_humanizer.managed_runtime.WindowsJob") as job,
                patch("shelter_humanizer.managed_runtime.Client") as client,
            ):
                spawn.return_value.poll.return_value = None
                client.return_value.models.side_effect = [ProviderError("still starting"), []]
                token = CombinedCancel(threading.Event(), runtime.closed)
                url = runtime.start(token, lambda message: None)
                self.assertTrue(url.startswith("http://127.0.0.1:"))
                self.assertEqual(client.return_value.models.call_count, 2)
                runtime.close()
                job.return_value.close.assert_called_once()

    def test_only_whitelisted_model_name_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            self.assertEqual(runtime.selected_model(), "")
            runtime.save_model(PRESETS[0].name)
            self.assertEqual(runtime.selected_model(), PRESETS[0].name)
            self.assertEqual(
                json.loads((Path(directory) / "selection.json").read_text()),
                {"model": PRESETS[0].name},
            )
            with self.assertRaises(ProviderError):
                runtime.save_model("untrusted/model")
            (Path(directory) / "selection.json").write_text("[]")
            self.assertEqual(runtime.selected_model(), "")

    def test_closed_runtime_cannot_install_or_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            runtime.close()
            with patch("shelter_humanizer.managed_runtime.subprocess.Popen") as spawn:
                with self.assertRaises(DownloadCancelled):
                    runtime.start(threading.Event(), lambda message: None)
                spawn.assert_not_called()

    def test_removed_qwen25_presets_are_not_selected_or_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)
            for model in ("qwen2.5:1.5b", "qwen2.5:7b"):
                (Path(directory) / "selection.json").write_text(json.dumps({"model": model}))
                self.assertEqual(runtime.selected_model(), "")
                self.assertRaises(ProviderError, runtime.save_model, model)

    @unittest.skipUnless(sys.platform == "win32", "Windows portable installation")
    def test_transactional_install_verifies_executable_and_preserves_damaged_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ManagedRuntime(directory)

            def download(path, cancel, progress):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("ollama.exe", b"synthetic-test-executable")

            with (
                patch(
                    "shelter_humanizer.managed_runtime.download_runtime", side_effect=download
                ) as fetch,
                patch(
                    "shelter_humanizer.managed_runtime.shutil.disk_usage",
                    return_value=SimpleNamespace(free=20 * 1024**3),
                ),
            ):
                runtime.install(threading.Event(), lambda message: None)
                self.assertTrue(runtime.installed())
                runtime.install(threading.Event(), lambda message: None)
                self.assertEqual(fetch.call_count, 1)
                runtime.executable.write_bytes(b"damaged")
                self.assertFalse(runtime.installed())
                runtime.install(threading.Event(), lambda message: None)
                self.assertTrue(runtime.installed())
                self.assertEqual(len(list(Path(directory).glob("old-*"))), 1)
                (runtime.runtime_dir / "installed.json").write_text("[]")
                self.assertFalse(runtime.installed())

    @unittest.skipUnless(sys.platform == "win32", "Windows file lock")
    def test_install_lock_excludes_second_app_and_releases_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "install.lock"
            with InstallLock(path):
                with self.assertRaisesRegex(ProviderError, "другой копии"):
                    with InstallLock(path):
                        self.fail("Concurrent installation lock was accepted")
            with InstallLock(path):
                pass


@unittest.skipUnless(sys.platform == "win32", "Windows process ownership")
class ProcessTests(unittest.TestCase):
    def spawn(self, script):
        return subprocess.Popen(
            [sys.executable, "-c", script], creationflags=subprocess.CREATE_NO_WINDOW
        )

    def wait_file(self, path):
        deadline = time.monotonic() + 6
        while not path.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(path.is_file(), "Owned test process did not become ready")

    def test_job_stops_owned_child_tree_and_preserves_unrelated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            go, ready = Path(directory) / "go", Path(directory) / "ready"
            script = f"import subprocess, sys, time; from pathlib import Path; go=Path({str(go)!r}); ready=Path({str(ready)!r});\nwhile not go.exists(): time.sleep(.02)\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'],creationflags=0x08000000); ready.write_text(str(p.pid)); time.sleep(300)"
            process = self.spawn(script)
            unrelated = self.spawn("import time; time.sleep(300)")
            job, child_handle = None, None
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            try:
                job = WindowsJob(process)
                go.touch()
                self.wait_file(ready)
                child_handle = kernel.OpenProcess(0x100000, False, int(ready.read_text()))
                self.assertTrue(child_handle)
                job.close()
                process.wait(timeout=5)
                self.assertEqual(kernel.WaitForSingleObject(child_handle, 5000), 0)
                self.assertIsNone(unrelated.poll())
            finally:
                if job is not None:
                    job.close()
                if child_handle:
                    kernel.CloseHandle(child_handle)
                for own in (process, unrelated):
                    if own.poll() is None:
                        own.kill()
                    own.wait(timeout=5)

    def test_job_stops_child_when_owner_crashes(self):
        with tempfile.TemporaryDirectory() as directory:
            ready, crash = Path(directory) / "ready", Path(directory) / "crash"
            script = (
                "import os, subprocess, sys, time; from pathlib import Path; "
                "from shelter_humanizer.managed_runtime import WindowsJob; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'],creationflags=0x08000000); "
                f"job=WindowsJob(p); Path({str(ready)!r}).write_text(str(p.pid));\n"
                f"while not Path({str(crash)!r}).exists(): time.sleep(.02)\n"
                "os._exit(23)"
            )
            owner = self.spawn(script)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = None
            try:
                self.wait_file(ready)
                handle = kernel.OpenProcess(0x100000, False, int(ready.read_text()))
                self.assertTrue(handle)
                crash.touch()
                self.assertEqual(owner.wait(timeout=5), 23)
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)
                if handle:
                    kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
