from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import WorkResult
from . import excel as excel_driver
from .files import copy_file, find_files, list_dir, make_dir, move_file, rename_path, resolve_path
from .wechat_desktop import WeChatDesktopDriver
from .windows import close_app, open_app, open_path, open_url, screenshot, volume_down, volume_mute, volume_up, window_control


class LocalDriver:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.wechat = WeChatDesktopDriver()

    def execute(self, action: str, args: dict[str, Any]) -> WorkResult:
        try:
            if action == "open_app":
                return WorkResult(True, open_app(str(args.get("name") or "")))
            if action == "close_app":
                return WorkResult(True, close_app(str(args.get("name") or "")))
            if action == "open_url":
                return WorkResult(True, open_url(str(args.get("url") or "")))
            if action == "open_path":
                p = resolve_path(str(args.get("path") or ""), self.workspace)
                return WorkResult(True, open_path(p))
            if action == "window_control":
                return WorkResult(True, window_control(str(args.get("name") or ""), str(args.get("operation") or "activate")))
            if action == "screenshot":
                p = screenshot()
                return WorkResult(True, "截图完成", [p])
            if action == "volume_up":
                return WorkResult(True, volume_up(int(args.get("steps") or 3)))
            if action == "volume_down":
                return WorkResult(True, volume_down(int(args.get("steps") or 3)))
            if action == "volume_mute":
                return WorkResult(True, volume_mute())
            if action == "find_files":
                files = find_files(str(args.get("query") or ""), self.workspace, args.get("roots"))
                text = "找到文件：\n" + "\n".join(str(p) for p in files[:50]) if files else "没有找到匹配文件"
                return WorkResult(True, text, files=[])
            if action == "copy_file":
                p = copy_file(str(args["src"]), str(args["dst"]), self.workspace)
                return WorkResult(True, f"已复制到 {p}", [p])
            if action == "move_file":
                p = move_file(str(args["src"]), str(args["dst"]), self.workspace)
                return WorkResult(True, f"已移动到 {p}", [p])
            if action == "list_dir":
                rows = list_dir(str(args.get("path") or self.workspace), self.workspace, int(args.get("limit") or 200))
                return WorkResult(True, "目录内容：\n" + "\n".join(str(p) for p in rows))
            if action == "make_dir":
                p = make_dir(str(args["path"]), self.workspace)
                return WorkResult(True, f"已创建目录 {p}")
            if action == "rename_path":
                p = rename_path(str(args["src"]), str(args["new_name"]), self.workspace)
                return WorkResult(True, f"已重命名为 {p}")
            if action == "wechat_send_text":
                self.wechat.send_text(str(args["contact"]), str(args["text"]))
                return WorkResult(True, f"已通过桌面微信发送给 {args['contact']}")
            if action == "wechat_send_file":
                p = resolve_path(str(args["path"]), self.workspace)
                self.wechat.send_files(str(args["contact"]), [p])
                return WorkResult(True, f"已通过桌面微信发送文件给 {args['contact']}")

            # Simple deterministic Excel actions.
            excel_map = {
                "excel_sort": excel_driver.excel_sort,
                "excel_dedupe": excel_driver.excel_dedupe,
                "excel_replace": excel_driver.excel_replace,
                "excel_fill_blank": excel_driver.excel_fill_blank,
                "excel_sum": excel_driver.excel_sum,
                "excel_compute_column": excel_driver.excel_compute_column,
                "excel_conditional_red": excel_driver.excel_conditional_red,
                "excel_merge_files": excel_driver.excel_merge_files,
                "excel_lookup": excel_driver.excel_lookup,
                "excel_split_by_column": excel_driver.excel_split_by_column,
                "excel_split_sheets": excel_driver.excel_split_sheets,
                "excel_filter": excel_driver.excel_filter,
                "excel_conditional_set": excel_driver.excel_conditional_set,
                "excel_batch_replace": excel_driver.excel_batch_replace,
            }
            if action in excel_map:
                kwargs = dict(args)
                kwargs["workspace"] = self.workspace
                result = excel_map[action](**kwargs)
                files = result if isinstance(result, list) else [result]
                return WorkResult(True, f"Excel 本地处理完成，共生成 {len(files)} 个文件", files)
            if action == "handoff_harness":
                return WorkResult(False, error="HANDOFF_HARNESS")
            return WorkResult(False, error=f"不支持的本地 action: {action}")
        except Exception as exc:
            return WorkResult(False, error=str(exc))
