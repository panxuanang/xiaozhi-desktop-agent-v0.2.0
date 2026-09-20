from __future__ import annotations

import os
import shutil
from pathlib import Path


COMMON_ALIASES = {
    "desktop": Path.home() / "Desktop",
    "桌面": Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "download": Path.home() / "Downloads",
    "下载": Path.home() / "Downloads",
    "下载目录": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "文档": Path.home() / "Documents",
}


def resolve_path(value: str | Path, workspace: Path) -> Path:
    if isinstance(value, Path):
        return value.expanduser().resolve()
    raw = os.path.expandvars(str(value).strip().strip('"'))
    low = raw.lower()
    if low in COMMON_ALIASES:
        return COMMON_ALIASES[low].resolve()
    for alias, root in COMMON_ALIASES.items():
        prefix = alias + "/"
        prefix2 = alias + "\\"
        if low.startswith(prefix) or low.startswith(prefix2):
            rest = raw[len(alias):].lstrip("/\\")
            return (root / rest).resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = workspace / p
    return p.resolve()


def find_files(query: str, workspace: Path, roots: list[str] | None = None, limit: int = 100) -> list[Path]:
    root_paths: list[Path] = []
    if roots:
        root_paths.extend(resolve_path(x, workspace) for x in roots)
    else:
        root_paths = [workspace, Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Documents"]
    needle = query.lower().replace("*", "")
    found: list[Path] = []
    seen: set[str] = set()
    for root in root_paths:
        if not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if needle and needle not in p.name.lower():
                    continue
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append(p)
                if len(found) >= limit:
                    return found
        except (PermissionError, OSError):
            continue
    return found


def copy_file(src: str, dst: str, workspace: Path) -> Path:
    src_p = resolve_path(src, workspace)
    dst_p = resolve_path(dst, workspace)
    if dst_p.exists() and dst_p.is_dir():
        dst_p = dst_p / src_p.name
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(src_p, dst_p))


def move_file(src: str, dst: str, workspace: Path) -> Path:
    src_p = resolve_path(src, workspace)
    dst_p = resolve_path(dst, workspace)
    if dst_p.exists() and dst_p.is_dir():
        dst_p = dst_p / src_p.name
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(src_p), str(dst_p)))


def list_dir(path: str, workspace: Path, limit: int = 200) -> list[Path]:
    root = resolve_path(path, workspace)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(root)
    return list(root.iterdir())[:max(1, min(int(limit), 1000))]


def make_dir(path: str, workspace: Path) -> Path:
    target = resolve_path(path, workspace)
    target.mkdir(parents=True, exist_ok=True)
    return target


def rename_path(src: str, new_name: str, workspace: Path) -> Path:
    source = resolve_path(src, workspace)
    if not source.exists():
        raise FileNotFoundError(source)
    # new_name is intentionally only a basename: renaming cannot escape the
    # source directory. Moving to another directory uses move_file explicitly.
    safe = Path(str(new_name)).name
    if not safe or safe in {".", ".."}:
        raise ValueError("新名称无效")
    target = source.with_name(safe)
    source.rename(target)
    return target
