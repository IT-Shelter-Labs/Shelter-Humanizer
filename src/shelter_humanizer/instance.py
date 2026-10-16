"""Windows session-local instance guard; no service, process scan or persistent lock file."""

import ctypes
import hashlib
import os
from ctypes import wintypes


class SingleInstance:
    def __init__(self, identity=None):
        self.handle = None
        self.owned = True
        if os.name != "nt":
            return
        identity = identity or os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        name = "Local\\ShelterHumanizer-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, False, name)
        if not handle:
            raise OSError("Не удалось проверить уже запущенное приложение.")
        self.owned = ctypes.get_last_error() != 183
        if self.owned:
            self.handle = handle
        else:
            self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
