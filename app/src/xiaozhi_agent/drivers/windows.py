from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
import webbrowser
import shutil
from pathlib import Path

import psutil
from PIL import ImageGrab

from ..paths import OUTBOX_DIR


APP_ALIASES = {
    "微信": "WeChat.exe",
    "wechat": "WeChat.exe",
    "记事本": "notepad.exe",
    "notepad": "notepad.exe",
    "计算器": "calc.exe",
    "calculator": "calc.exe",
    "文件资源管理器": "explorer.exe",
    "资源管理器": "explorer.exe",
    "explorer": "explorer.exe",
    "edge": "msedge.exe",
    "浏览器": "msedge.exe",
    "chrome": "chrome.exe",
    "excel": "excel.exe",
    "表格": "excel.exe",
    "word": "winword.exe",
    "文档": "winword.exe",
    "powerpoint": "powerpnt.exe",
    "ppt": "powerpnt.exe",
}


def _exe_for(name: str) -> str:
    n = name.strip().lower()
    for key, exe in APP_ALIASES.items():
        if key.lower() == n:
            return exe
    return name.strip()


APP_EXECUTABLE_CANDIDATES = {
    "微信": ["Weixin.exe", "WeChat.exe"],
    "wechat": ["Weixin.exe", "WeChat.exe"],
}


def _exe_candidates(name: str) -> list[str]:
    n = name.strip().lower()
    for key, candidates in APP_EXECUTABLE_CANDIDATES.items():
        if key.lower() == n:
            return list(candidates)
    return [_exe_for(name)]


def open_app(name: str) -> str:
    candidates = _exe_candidates(name)
    last_error: Exception | None = None
    for target in candidates:
        try:
            resolved = shutil.which(target) or target
            subprocess.Popen([resolved], shell=False)
            return f"已尝试打开 {name}"
        except (FileNotFoundError, OSError) as exc:
            last_error = exc
            if os.name != "nt":
                continue
            try:
                # ShellExecute/App Paths can resolve installed GUI apps such as
                # Weixin/WeChat and Office without routing user text through cmd.exe.
                os.startfile(target)  # type: ignore[attr-defined]
                return f"已尝试打开 {name}"
            except (FileNotFoundError, OSError) as shell_exc:
                last_error = shell_exc
                continue
    if last_error:
        raise last_error
    raise FileNotFoundError(name)


def close_app(name: str) -> str:
    targets = {Path(x).name.lower() for x in _exe_candidates(name)}
    stems = {x.removesuffix(".exe") for x in targets}
    killed = 0
    for p in psutil.process_iter(["name"]):
        try:
            pname = (p.info.get("name") or "").lower()
            pname_stem = pname.removesuffix(".exe")
            if pname in targets or pname_stem in stems:
                p.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return f"已请求关闭 {name}，匹配进程 {killed} 个"


def open_url(url: str) -> str:
    webbrowser.open(url, new=2)
    return f"已打开 {url}"



def open_path(path: str | Path) -> str:
    target = Path(path).resolve()
    if not target.exists():
        raise FileNotFoundError(target)
    if os.name != "nt":
        raise RuntimeError("打开本地文件仅支持 Windows")
    os.startfile(str(target))  # type: ignore[attr-defined]
    return f"已打开 {target}"


def window_control(name: str, operation: str) -> str:
    if os.name != "nt":
        raise RuntimeError("窗口控制仅支持 Windows")
    from pywinauto import Desktop  # type: ignore
    target = name.strip().lower()
    candidates = []
    for win in Desktop(backend="uia").windows(visible_only=True):
        try:
            title = (win.window_text() or "").lower()
            if target in title or _exe_for(name).removesuffix(".exe").lower() in title:
                candidates.append(win)
        except Exception:
            continue
    if not candidates:
        raise RuntimeError(f"没有找到可见窗口: {name}")
    win = candidates[0]
    op = operation.strip().lower()
    if op in {"activate", "focus", "激活", "切换"}:
        try:
            win.restore()
        except Exception:
            pass
        win.set_focus()
    elif op in {"minimize", "最小化"}:
        win.minimize()
    elif op in {"maximize", "最大化"}:
        win.maximize()
    elif op in {"restore", "还原"}:
        win.restore()
    else:
        raise ValueError(f"不支持的窗口操作: {operation}")
    return f"窗口操作完成: {name} -> {operation}"

def screenshot() -> Path:
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime("screenshot_%Y%m%d_%H%M%S.png")
    path = OUTBOX_DIR / name
    img = ImageGrab.grab(all_screens=True)
    img.save(path)
    return path


VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002


def _press(vk: int, steps: int = 1) -> None:
    if os.name != "nt":
        raise RuntimeError("音量控制仅支持 Windows")
    user32 = ctypes.windll.user32
    for _ in range(max(1, min(int(steps), 20))):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.04)


def volume_up(steps: int = 3) -> str:
    _press(VK_VOLUME_UP, steps)
    return f"音量已提高 {steps} 档"


def volume_down(steps: int = 3) -> str:
    _press(VK_VOLUME_DOWN, steps)
    return f"音量已降低 {steps} 档"


def volume_mute() -> str:
    _press(VK_VOLUME_MUTE, 1)
    return "已切换静音状态"
