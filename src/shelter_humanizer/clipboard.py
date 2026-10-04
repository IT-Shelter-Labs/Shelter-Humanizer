"""Unicode clipboard transport; Tk's STRING conversion can corrupt long Russian text."""

import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes


def windows_api():
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.CloseClipboard.argtypes = []
    user.CloseClipboard.restype = wintypes.BOOL
    user.EmptyClipboard.argtypes = []
    user.EmptyClipboard.restype = wintypes.BOOL
    user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user.SetClipboardData.restype = wintypes.HANDLE
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalUnlock.restype = wintypes.BOOL
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.restype = wintypes.HGLOBAL
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    return user, kernel


def open_windows(widget, user):
    # Clipboard managers can briefly hold the system lock. Bound retries to 50 ms.
    for attempt in range(6):
        if user.OpenClipboard(widget.winfo_id()):
            return
        if attempt < 5:
            time.sleep(0.01)
    raise tk.TclError("Буфер обмена занят. Попробуйте ещё раз.")


def write(widget, text):
    if sys.platform != "win32":
        widget.clipboard_clear()
        widget.clipboard_append(text)
        return
    # CF_UNICODETEXT owns a movable UTF-16 allocation after successful transfer.
    # Clipboard text cannot represent NUL; do not silently truncate a source.
    if "\x00" in text:
        raise tk.TclError("Сначала очистите управляющий NUL-символ в тексте.")
    raw = (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\x00").encode("utf-16-le")
    user, kernel = windows_api()
    memory = kernel.GlobalAlloc(0x0002, len(raw))
    if not memory:
        raise tk.TclError("Не удалось подготовить текст для копирования.")
    try:
        pointer = kernel.GlobalLock(memory)
        if not pointer:
            raise tk.TclError("Не удалось подготовить текст для копирования.")
        try:
            ctypes.memmove(pointer, raw, len(raw))
        finally:
            kernel.GlobalUnlock(memory)
        open_windows(widget, user)
        try:
            if not user.EmptyClipboard() or not user.SetClipboardData(13, memory):
                raise tk.TclError("Не удалось скопировать текст.")
            memory = None  # The system now owns the allocation.
        finally:
            user.CloseClipboard()
    finally:
        if memory:
            kernel.GlobalFree(memory)


def read(widget):
    if sys.platform != "win32":
        return widget.clipboard_get()
    user, kernel = windows_api()
    open_windows(widget, user)
    try:
        memory = user.GetClipboardData(13)
        if not memory:
            raise tk.TclError("В буфере обмена нет текста.")
        size = kernel.GlobalSize(memory)
        if not size or size > 2 * 1024 * 1024 or size % 2:
            raise tk.TclError("Текст в буфере слишком большой или имеет неверный формат.")
        pointer = kernel.GlobalLock(memory)
        if not pointer:
            raise tk.TclError("Не удалось прочитать буфер обмена.")
        try:
            raw = ctypes.string_at(pointer, size)
        finally:
            kernel.GlobalUnlock(memory)
        terminator = next(
            (index for index in range(0, len(raw) - 1, 2) if raw[index : index + 2] == b"\x00\x00"),
            None,
        )
        if terminator is None:
            raise tk.TclError("Некорректный текст в буфере обмена.")
        try:
            value = raw[:terminator].decode("utf-16-le")
        except UnicodeError:
            raise tk.TclError("Некорректный текст в буфере обмена.") from None
        return value.replace("\r\n", "\n")
    finally:
        user.CloseClipboard()
