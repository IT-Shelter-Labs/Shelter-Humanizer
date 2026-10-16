"""Allowlisted support metadata. Never collect text, prompts, keys, URLs or paths."""

import ctypes
import os
import platform
import re

from . import __version__
from .build_info import build_info
from .engine_errors import ENGINE_CODES

NUMBERS = {
    "request_time_s",
    "first_fragment_s",
    "context_tokens",
    "reply_limit",
    "input_token_estimate",
    "load_duration",
    "prompt_eval_count",
    "eval_count",
    "model_memory_mib",
    "model_vram_mib",
    "loaded_context_tokens",
    "http_status",
}


def memory_info():
    if os.name != "nt":
        return {}

    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                "physical_total",
                "physical_available",
                "page_total",
                "page_available",
                "virtual_total",
                "virtual_available",
                "extended_available",
            )
        ]

    status = Memory()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    return {
        "ram_total_mib": status.physical_total // 1024**2,
        "ram_available_mib": status.physical_available // 1024**2,
    }


def snapshot(config=None, stats=None, *, error_code="none", phase="idle", custom_instruction=False):
    model = config.model if config else ""
    # Some local APIs use a full filesystem path as model ID. Do not export it.
    if model and (
        len(model) > 160
        or not re.fullmatch(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*/)?[A-Za-z0-9][A-Za-z0-9_.:-]*", model)
    ):
        model = "[model ID hidden]"
    result = {
        "schema_version": 1,
        "app_version": __version__,
        "system": platform.system(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "provider": config.kind if config else "offline",
        "model": model,
        "error_code": error_code,
        "custom_instruction": bool(custom_instruction),
        "cpu_only": bool(config.cpu_only) if config and config.kind == "ollama" else False,
    }
    # Stage names are our own fixed labels, never provider-supplied strings.
    result["stage"] = (
        phase
        if phase
        in ("idle", "connection", "editing", "proofreading", "checking", "complete", "failed")
        else "unknown"
    )
    result.update(memory_info())
    result["build"] = build_info()
    if isinstance(stats, dict):
        if isinstance(stats.get("engine_error"), str) and stats["engine_error"] in ENGINE_CODES:
            result["engine_error"] = stats["engine_error"]
        version = stats.get("ollama_version")
        if isinstance(version, str) and re.fullmatch(
            r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]{1,24})?", version
        ):
            result["ollama_version"] = version
        for key in NUMBERS:
            value = stats.get(key)
            if type(value) in (int, float) and 0 <= value < 10**18:
                result[key] = value
    return result
