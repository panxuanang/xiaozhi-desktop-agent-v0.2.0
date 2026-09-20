from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from .paths import SECRETS_FILE


class SecretStore:
    """Small Windows DPAPI-backed secret store.

    On Windows, secrets are encrypted with DPAPI and scoped to the current
    Windows user. The base64 fallback exists only so non-Windows tests can run.
    """

    def __init__(self, path: Path = SECRETS_FILE):
        self.path = path

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _as_bytes(value: Any, *, operation: str) -> bytes:
        """Normalize pywin32 DPAPI return values without guessing byte indexes.

        Current pywin32 CryptProtectData returns bytes directly, while
        CryptUnprotectData returns (description, bytes).  Handling both shapes
        defensively avoids accidentally indexing a bytes object and turning one
        encrypted byte into an int.
        """
        if isinstance(value, tuple):
            if len(value) < 2:
                raise TypeError(f"{operation} returned an unexpected tuple")
            value = value[1]
        if isinstance(value, memoryview):
            value = value.tobytes()
        if isinstance(value, bytearray):
            value = bytes(value)
        if not isinstance(value, bytes):
            raise TypeError(f"{operation} returned {type(value).__name__}, expected bytes")
        return value

    @classmethod
    def _protect(cls, value: str) -> str:
        raw = value.encode("utf-8")
        if os.name == "nt":
            import win32crypt  # type: ignore

            result = win32crypt.CryptProtectData(
                raw, "XiaoZhiAssistant", None, None, None, 0
            )
            protected = cls._as_bytes(result, operation="CryptProtectData")
            return "dpapi:" + base64.b64encode(protected).decode("ascii")
        return "plain-b64:" + base64.b64encode(raw).decode("ascii")

    @classmethod
    def _unprotect(cls, value: str) -> str:
        if value.startswith("dpapi:"):
            import win32crypt  # type: ignore

            blob = base64.b64decode(value[6:])
            result = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
            clear = cls._as_bytes(result, operation="CryptUnprotectData")
            return clear.decode("utf-8")
        if value.startswith("plain-b64:"):
            return base64.b64decode(value[10:]).decode("utf-8")
        return ""

    def set(self, key: str, value: str) -> None:
        data = self._load()
        if value:
            # Protect first. If DPAPI fails, the existing file is left untouched.
            protected = self._protect(value)
            data[key] = protected
        else:
            data.pop(key, None)
        self._save(data)

    def get(self, key: str, default: str = "") -> str:
        raw = self._load().get(key)
        if not raw:
            return default
        try:
            return self._unprotect(raw)
        except Exception:
            return default

    def has(self, key: str) -> bool:
        return bool(self.get(key))
