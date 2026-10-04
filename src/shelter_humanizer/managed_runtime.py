"""Private portable Ollama, pinned download and an owned process tree."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from .local_models import PRESETS, DownloadCancelled
from .providers import Client, ProviderConfig, ProviderError

RUNTIME_VERSION = "0.35.1"
RUNTIME_URL = "https://github.com/ollama/ollama/releases/download/v0.35.1/ollama-windows-amd64.zip"
RUNTIME_SIZE = 1471094402
RUNTIME_SHA256 = "dc50b9ca7f9023c86525012632cd1615b093d0407987444a7f62ecab617e8e93"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def check_cancel(cancel):
    if cancel.is_set():
        raise DownloadCancelled(
            "Установка отменена. Загруженные файлы сохранятся для повторного запуска."
        )


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in (
            "github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
            "github-releases.githubusercontent.com",
        ):
            raise ProviderError("Неожиданный адрес загрузки. Установка остановлена.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_runtime(path, cancel, progress):
    check_cancel(cancel)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    offset = path.stat().st_size if path.is_file() else 0
    if offset > RUNTIME_SIZE:
        path.unlink()
        offset = 0
    if offset < RUNTIME_SIZE:
        headers = {"Accept-Encoding": "identity", "User-Agent": "Shelter-Humanizer"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(RUNTIME_URL, headers=headers)
        opener = urllib.request.build_opener(SafeRedirect())
        started, last_update = time.monotonic(), 0
        with opener.open(request, timeout=10) as response:
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ProviderError("Неподдерживаемое сжатие дистрибутива.")
            if response.status == 206:
                content_range = response.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {offset}-") or not content_range.endswith(
                    f"/{RUNTIME_SIZE}"
                ):
                    raise ProviderError("Сервер не подтвердил продолжение загрузки.")
            elif response.status == 200:
                offset = 0
            else:
                raise ProviderError("Не удалось загрузить официальный дистрибутив.")
            with path.open("ab" if offset else "wb") as target:
                while True:
                    check_cancel(cancel)
                    if time.monotonic() - started > 7200:
                        raise ProviderError("Загрузка заняла больше двух часов. Попробуйте снова.")
                    chunk = response.read1(1024 * 1024)
                    if not chunk:
                        break
                    offset += len(chunk)
                    if offset > RUNTIME_SIZE:
                        raise ProviderError("Размер дистрибутива не совпадает с официальным.")
                    target.write(chunk)
                    if time.monotonic() - last_update >= 0.2:
                        progress(
                            f"Компоненты ИИ: {offset * 100 // RUNTIME_SIZE}% · {offset / 1024**2:.0f} / {RUNTIME_SIZE / 1024**2:.0f} МБ"
                        )
                        last_update = time.monotonic()
    check_cancel(cancel)
    progress("Проверяем контрольную сумму компонентов…")
    if path.stat().st_size != RUNTIME_SIZE or sha256_file(path) != RUNTIME_SHA256:
        path.unlink(missing_ok=True)
        raise ProviderError("Контрольная сумма не совпала. Файлы не запущены; повторите загрузку.")


def extract_runtime(archive, destination, cancel, progress):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        total = sum(entry.file_size for entry in entries)
        if len(entries) > 10000 or total > 8 * 1024**3:
            raise ProviderError("Дистрибутив превышает допустимый размер.")
        seen = set()
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            target = (destination / name).resolve()
            if (
                not target.is_relative_to(destination)
                or ":" in name
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
                or str(target).casefold() in seen
            ):
                raise ProviderError("Недопустимый путь в дистрибутиве.")
            seen.add(str(target).casefold())
        if shutil.disk_usage(destination).free < total + 1024**3:
            raise ProviderError("Недостаточно места для распаковки. Освободите несколько гигабайт.")
        completed, last_update = 0, 0
        for entry in entries:
            check_cancel(cancel)
            target = destination / entry.filename.replace("\\", "/")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    check_cancel(cancel)
                    output.write(chunk)
                    completed += len(chunk)
                    if time.monotonic() - last_update >= 0.2:
                        progress(f"Устанавливаем компоненты: {completed * 100 // max(1, total)}%")
                        last_update = time.monotonic()
    if not (destination / "ollama.exe").is_file():
        raise ProviderError("В дистрибутиве отсутствует исполняемый файл Ollama.")


class WindowsJob:
    """Kill only this runtime and its children, including on a parent crash."""

    def __init__(self, process):
        import ctypes
        from ctypes import wintypes

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (
            not self.handle
            or not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle))
        ):
            self.close()
            process.kill()
            process.wait(timeout=5)
            raise ProviderError(
                "Windows не разрешила управляемый запуск ИИ. Компоненты установлены, но запуск остановлен."
            )

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class CombinedCancel:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)


class InstallLock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        import msvcrt

        self.file = self.path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise ProviderError(
                "Компоненты уже устанавливаются в другой копии приложения. Дождитесь завершения."
            ) from None
        return self

    def __exit__(self, *args):
        import msvcrt

        self.file.seek(0)
        msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()


class ManagedRuntime:
    def __init__(self, directory=None):
        self.directory = (
            Path(directory)
            if directory
            else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
            / "ShelterHumanizer"
            / "ai"
        )
        self.directory = self.directory.resolve()
        self.runtime_dir = self.directory / f"ollama-{RUNTIME_VERSION}"
        self.executable = self.runtime_dir / "ollama.exe"
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.process = None
        self.job = None
        self.url = ""

    def installed(self):
        try:
            receipt = json.loads((self.runtime_dir / "installed.json").read_text(encoding="utf-8"))
            return (
                isinstance(receipt, dict)
                and self.executable.is_file()
                and receipt.get("archive_sha256") == RUNTIME_SHA256
                and receipt.get("exe_sha256") == sha256_file(self.executable)
            )
        except (OSError, ValueError):
            return False

    def selected_model(self):
        try:
            selection = json.loads((self.directory / "selection.json").read_text(encoding="utf-8"))
            model = selection.get("model") if isinstance(selection, dict) else ""
            return model if model in {p.name for p in PRESETS} else ""
        except (OSError, ValueError):
            return ""

    def save_model(self, model):
        if model not in {p.name for p in PRESETS}:
            raise ProviderError("Неизвестная модель.")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / "selection.json"
        temporary = target.with_name(f"selection-{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps({"model": model}) + "\n", encoding="utf-8")
        temporary.replace(target)

    def install(self, cancel, progress):
        if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
            raise ProviderError(
                "Автоматическая установка поддерживается на Windows x64. Для другой системы используйте своё подключение Ollama."
            )
        token = CombinedCancel(cancel, self.closed)
        check_cancel(token)
        if self.installed():
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        with InstallLock(self.directory / "install.lock"):
            self._install_files(token, progress)

    def _install_files(self, token, progress):
        if self.installed():
            return
        if shutil.disk_usage(self.directory).free < 8 * 1024**3:
            raise ProviderError(
                "Для установки нужно хотя бы 8 ГБ свободного места; большая модель потребует дополнительного места."
            )
        # An OS file lock protects a resumable cache even across app restarts.
        cache = self.directory / f"ollama-{RUNTIME_VERSION}.zip.part"
        download_runtime(cache, token, progress)
        with tempfile.TemporaryDirectory(prefix="install-", dir=self.directory) as stage:
            stage = Path(stage)
            extract_runtime(cache, stage, token, progress)
            receipt = {
                "archive_sha256": RUNTIME_SHA256,
                "exe_sha256": sha256_file(stage / "ollama.exe"),
            }
            (stage / "installed.json").write_text(json.dumps(receipt), encoding="utf-8")
            check_cancel(token)
            if self.runtime_dir.exists():
                # A damaged previous install is preserved for recovery, never deleted blindly.
                self.runtime_dir.rename(
                    self.directory / f"old-{RUNTIME_VERSION}-{uuid.uuid4().hex}"
                )
            stage.rename(self.runtime_dir)
        cache.unlink(missing_ok=True)

    def start(self, cancel, progress):
        token = CombinedCancel(cancel, self.closed)
        with self.lock:
            check_cancel(token)
            if self.process is not None and self.process.poll() is None:
                return self.url
            if self.process is not None:
                self.stop()
            if not self.installed():
                raise ProviderError("Сначала установите компоненты локального ИИ.")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            profile = self.directory / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ)
            env.update(
                USERPROFILE=str(profile),
                HOME=str(profile),
                OLLAMA_HOST=f"127.0.0.1:{port}",
                OLLAMA_MODELS=str(self.directory / "models"),
                OLLAMA_NO_CLOUD="1",
                OLLAMA_NOHISTORY="1",
                OLLAMA_DEBUG_LOG_REQUESTS="0",
                OLLAMA_NUM_PARALLEL="1",
                OLLAMA_MAX_LOADED_MODELS="1",
                OLLAMA_CONTEXT_LENGTH="8192",
                OLLAMA_DEBUG="0",
            )
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            process = subprocess.Popen(
                [str(self.executable), "serve"],
                cwd=self.runtime_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                start_new_session=sys.platform != "win32",
            )
            try:
                self.job = WindowsJob(process) if sys.platform == "win32" else None
            except Exception:
                process.kill()
                process.wait(timeout=5)
                raise
            self.process = process
            self.url = f"http://127.0.0.1:{port}"
        progress("Запускаем локальный ИИ…")
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                check_cancel(token)
                if process.poll() is not None:
                    raise ProviderError(
                        "Локальный ИИ завершился при запуске. Проверьте совместимость Windows и доступную память."
                    )
                try:
                    Client(ProviderConfig(url=self.url, timeout=1)).models()
                    check_cancel(token)
                    return self.url
                except ValueError:
                    self.closed.wait(0.2)
            raise ProviderError("Локальный ИИ не запустился за 30 секунд. Попробуйте снова.")
        except Exception:
            self.stop()
            raise

    def stop(self):
        with self.lock:
            process, self.process = self.process, None
            if self.job is not None:
                self.job.close()
                self.job = None
            elif process is not None and process.poll() is None:
                if sys.platform != "win32":
                    import signal

                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            self.url = ""

    def close(self):
        self.closed.set()
        self.stop()
