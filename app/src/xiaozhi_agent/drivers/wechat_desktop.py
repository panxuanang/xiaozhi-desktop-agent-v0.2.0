from __future__ import annotations

import os
import time
from pathlib import Path

import pyperclip


class WeChatDesktopDriver:
    """Best-effort deterministic automation for the normal desktop WeChat client.

    It intentionally does not read chats. It only focuses an already logged-in
    WeChat window, searches a contact, and sends exact text/files selected by
    Task Center. If UI automation cannot see the client, it fails loudly.
    """

    WINDOW_RE = r".*(微信|WeChat).*"

    def _window(self):
        if os.name != "nt":
            raise RuntimeError("桌面微信 Driver 仅支持 Windows")
        from pywinauto import Desktop  # type: ignore

        def visible_windows():
            return Desktop(backend="uia").windows(title_re=self.WINDOW_RE, visible_only=True)

        windows = visible_windows()
        if not windows:
            # A scheduled delivery may fire while WeChat is closed/minimized to
            # the tray.  Deterministically ask Windows to launch/restore it, then
            # wait for a real UIA-visible window before touching the keyboard.
            from .windows import open_app
            try:
                open_app("微信")
            except Exception as exc:
                raise RuntimeError(f"WECHAT_START_FAILED: {exc}") from exc
            deadline = time.time() + 12.0
            while time.time() < deadline:
                time.sleep(0.5)
                windows = visible_windows()
                if windows:
                    break
        if not windows:
            raise RuntimeError("WECHAT_UIA_NOT_VISIBLE: 已尝试启动微信，但仍未找到已登录的微信桌面窗口")
        win = windows[0]
        try:
            win.restore()
        except Exception:
            pass
        win.set_focus()
        time.sleep(0.4)
        return win

    @staticmethod
    def _paste_text(text: str) -> None:
        from pywinauto.keyboard import send_keys  # type: ignore
        pyperclip.copy(text)
        send_keys("^v")

    def _open_contact(self, contact: str) -> None:
        from pywinauto.keyboard import send_keys  # type: ignore
        self._window()
        send_keys("^f")
        time.sleep(0.35)
        self._paste_text(contact)
        time.sleep(0.8)
        send_keys("{ENTER}")
        time.sleep(0.8)

    def send_text(self, contact: str, text: str) -> None:
        from pywinauto.keyboard import send_keys  # type: ignore
        self._open_contact(contact)
        self._paste_text(text)
        time.sleep(0.15)
        send_keys("{ENTER}")

    @staticmethod
    def _set_file_drop_clipboard(paths: list[Path]) -> None:
        # Windows Forms handles the native CF_HDROP clipboard format reliably.
        # Run PowerShell in STA mode because Clipboard requires an STA thread.
        import subprocess
        normalized = [str(p.resolve()) for p in paths]
        lines = [
            "Add-Type -AssemblyName System.Windows.Forms",
            "$c = New-Object System.Collections.Specialized.StringCollection",
        ]
        for value in normalized:
            escaped = value.replace("'", "''")
            lines.append(f"[void]$c.Add('{escaped}')")
        lines.append("[System.Windows.Forms.Clipboard]::SetFileDropList($c)")
        script = "; ".join(lines)
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"设置文件剪贴板失败: {proc.stderr.strip()[:500]}")

    def send_files(self, contact: str, files: list[Path]) -> None:
        from pywinauto.keyboard import send_keys  # type: ignore
        exact = [Path(p).resolve() for p in files]
        missing = [str(p) for p in exact if not p.exists()]
        if missing:
            raise RuntimeError("待发送文件不存在: " + ", ".join(missing))
        self._open_contact(contact)
        self._set_file_drop_clipboard(exact)
        send_keys("^v")
        time.sleep(1.0)
        send_keys("{ENTER}")
