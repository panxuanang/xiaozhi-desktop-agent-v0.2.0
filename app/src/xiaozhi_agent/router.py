from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import RouteDecision
from .scheduling import parse_scheduled_request


LOCAL_HINTS = (
    "打开", "关闭", "截图", "音量", "静音", "找文件", "查找文件", "复制文件", "移动文件",
    "打开网页", "浏览器", "窗口", "最小化", "最大化", "重命名", "新建文件夹", "列出目录", "排序", "去重", "替换", "填空", "标红", "条件格式", "拆excel",
    "拆 sheet", "拆sheet", "vlookup", "xlookup", "合并excel", "合并表格",
)
HARNESS_HINTS = (
    "做一份ppt", "做个ppt", "做 ppt", "word和ppt", "word 和 ppt", "研究", "联网研究",
    "多来源", "复杂excel", "复杂 excel", "分析异常", "生成ppt", "生成 ppt", "做汇报",
    "多个不同结构", "清洗", "自动判断字段", "写代码", "运行代码",
)
DIRECT_HINTS = (
    "写通知", "写一份通知", "写个通知", "写文案", "润色", "改写", "总结", "写材料", "写回复", "回复微信", "起草",
)


def _contact_intent(text: str) -> tuple[str | None, str | None, str | None, str | None]:
    compact = re.sub(r"\s+", "", text)
    review_contact = None
    delivery_contact = None
    delivery_mode = None
    completion_action = None
    if any(x in compact for x in ("做好先发我", "先发我审核", "先给我看", "做好发我看看")):
        review_contact = "我"
        delivery_mode = "review"
        completion_action = "send"
    m = re.search(r"(?:做好|完成后|做好以后)?(?:直接)?发(?:给)?([^，。,.！!？?\s]{1,12})", compact)
    if m:
        who = m.group(1)
        if who not in ("我", "我审核", "我看看"):
            delivery_contact = who
            delivery_mode = "on_complete"
            completion_action = "send"
    return review_contact, delivery_contact, delivery_mode, completion_action


class IntentRouter:
    def __init__(self, client):
        self.client = client

    def route(self, text: str, attachments: list[Path]) -> RouteDecision:
        low = text.lower()
        scheduled = parse_scheduled_request(text)
        if scheduled and scheduled.kind == "send_text":
            return RouteDecision(
                route="local",
                summary="\u672c\u5730\u5b9a\u65f6\u53d1\u9001",
                local_action="schedule_send_text",
                local_args={
                    "text": scheduled.text or "",
                    "recipient": scheduled.recipient or "\u6211",
                    "delivery_channel": scheduled.delivery_channel or "weixin",
                },
                delivery_contact=scheduled.recipient or "\u6211",
                delivery_channel=scheduled.delivery_channel or "weixin",
                delivery_mode="scheduled",
                completion_action="send",
                scheduled_at=scheduled.scheduled_at,
                scheduled_action=(
                    "weixin_send_text"
                    if scheduled.delivery_channel == "weixin"
                    else "wechat_send_text"
                ),
                scheduled_payload={"text": scheduled.text or ""},
                reason="schedule_heuristic",
            )
        if scheduled and scheduled.kind == "local_command":
            scheduled_low = scheduled.command_text.lower()
            if any(h.lower() in scheduled_low for h in LOCAL_HINTS):
                return RouteDecision(
                    route="local",
                    summary="\u672c\u5730\u5b9a\u65f6\u64cd\u4f5c",
                    local_action="schedule_local_command",
                    local_args={"command_text": scheduled.command_text},
                    delivery_contact="\u6211",
                    delivery_channel="weixin",
                    delivery_mode="scheduled",
                    completion_action="reply",
                    scheduled_at=scheduled.scheduled_at,
                    scheduled_action="local_driver",
                    reason="schedule_local_heuristic",
                )

        review_contact, delivery_contact, delivery_mode, completion_action = _contact_intent(text)

        if attachments or any(h.lower() in low for h in HARNESS_HINTS):
            return RouteDecision(
                route="harness",
                summary="复杂文件/研究/多步骤任务",
                review_contact=review_contact,
                delivery_contact=delivery_contact,
                delivery_mode=delivery_mode,
                completion_action=completion_action,
                reason="heuristic",
            )
        if any(h.lower() in low for h in LOCAL_HINTS):
            return RouteDecision(
                route="local",
                summary="确定性本地操作",
                review_contact=review_contact,
                delivery_contact=delivery_contact,
                delivery_mode=delivery_mode,
                completion_action=completion_action,
                reason="heuristic",
            )
        if any(h.lower() in low for h in DIRECT_HINTS):
            return RouteDecision(
                route="direct",
                summary="一次模型即可完成",
                review_contact=review_contact,
                delivery_contact=delivery_contact,
                delivery_mode=delivery_mode,
                completion_action=completion_action,
                reason="heuristic",
            )

        if self.client is None:
            raise RuntimeError("这条任务需要 Planner 模型判断，但对应 API Key 尚未配置")

        system = """你是小智电脑助手的 Intent Router。只输出 JSON，不解释。
route 只能是 local/direct/harness。
local: 确定性的 Windows/文件/浏览器/简单 Excel 操作。
direct: 一次模型调用完成的写作、总结、改写、材料。
harness: 复杂多步骤、自主规划、联网研究、多个文件、复杂 Excel、Office 生成、需要写代码执行修复。
同时抽取：review_contact、delivery_contact、delivery_mode(review/on_complete/scheduled/none)、completion_action(send/reply/none)、scheduled_at(ISO8601 或 null)。
不要让微信 Channel 自己成为 Agent。"""
        payload = self.client.json_chat(system, text)
        route = str(payload.get("route") or "harness").lower()
        if route not in {"local", "direct", "harness"}:
            route = "harness"
        return RouteDecision(
            route=route,
            summary=str(payload.get("summary") or ""),
            local_action=payload.get("local_action"),
            local_args=payload.get("local_args") if isinstance(payload.get("local_args"), dict) else {},
            review_contact=review_contact or payload.get("review_contact"),
            delivery_contact=delivery_contact or payload.get("delivery_contact"),
            delivery_mode=(delivery_mode or payload.get("delivery_mode") or None),
            delivery_channel=payload.get("delivery_channel") or None,
            completion_action=(completion_action or payload.get("completion_action") or None),
            scheduled_at=payload.get("scheduled_at"),
            reason="model",
        )

    def plan_local(self, text: str, workspace: Path) -> tuple[str, dict[str, Any]]:
        low = text.lower().strip()
        compact = re.sub(r"\s+", "", low)
        # Zero-token fast paths for the most common deterministic Windows actions.
        known_apps = {
            "微信": "微信", "wechat": "wechat", "记事本": "记事本", "notepad": "notepad",
            "计算器": "计算器", "calculator": "calculator", "文件资源管理器": "文件资源管理器",
            "资源管理器": "资源管理器", "explorer": "explorer", "edge": "edge", "浏览器": "浏览器",
            "chrome": "chrome", "excel": "excel", "word": "word", "powerpoint": "powerpoint", "ppt": "ppt",
        }
        for name, canonical in known_apps.items():
            if re.fullmatch(rf"(?:请)?(?:帮我)?打开(?:一下)?{re.escape(name)}(?:吧)?", compact, re.I):
                return "open_app", {"name": canonical}
            if re.fullmatch(rf"(?:请)?(?:帮我)?(?:关闭|关掉)(?:一下)?{re.escape(name)}(?:吧)?", compact, re.I):
                return "close_app", {"name": canonical}
        if "截图" in low:
            return "screenshot", {}
        if "静音" in low:
            return "volume_mute", {}
        if "音量" in low and any(x in low for x in ("大", "高", "加")):
            return "volume_up", {"steps": 3}
        if "音量" in low and any(x in low for x in ("小", "低", "减")):
            return "volume_down", {"steps": 3}
        url = re.search(r"https?://\S+", text)
        if url:
            return "open_url", {"url": url.group(0).rstrip("，。,.！!？?")}

        if self.client is None:
            return "handoff_harness", {}

        system = f"""你是 Windows 本地确定性操作规划器。只输出 JSON: {{"action":"...","args":{{...}}}}。
工作区: {workspace}
只允许以下 action，禁止生成 shell 命令：
open_app(name), close_app(name), open_path(path), open_url(url), window_control(name,operation), screenshot(), volume_up(steps), volume_down(steps), volume_mute(),
find_files(query, roots?), list_dir(path,limit?), make_dir(path), rename_path(src,new_name), copy_file(src,dst), move_file(src,dst),
excel_sort(path,sheet,column,descending), excel_filter(path,sheet,column,operator,value), excel_dedupe(path,sheet,columns), excel_replace(path,sheet,find,replace),
excel_fill_blank(path,sheet,column,value), excel_sum(path,sheet,column,output_cell),
excel_compute_column(path,sheet,target_column,formula_template), excel_conditional_red(path,sheet,column,operator,value), excel_conditional_set(path,sheet,condition_column,operator,value,target_column,target_value),
excel_merge_files(paths,output), excel_lookup(left_path,right_path,left_key,right_key,right_value,output), excel_batch_replace(paths,find,replace),
excel_split_by_column(path,sheet,column,output_dir), excel_split_sheets(path,output_dir),
wechat_send_text(contact,text), wechat_send_file(contact,path).
路径尽量使用绝对路径；若用户说下载/桌面/文档目录可直接表达为 Downloads/Desktop/Documents，执行器会解析。
如果无法确定参数，action=handoff_harness。"""
        data = self.client.json_chat(system, text)
        action = str(data.get("action") or "handoff_harness")
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        # Never let the model invent a desktop-WeChat recipient. The contact
        # must be literally present in the user's current instruction.
        if action in {"wechat_send_text", "wechat_send_file"}:
            contact = str(args.get("contact") or "").strip()
            if not contact or contact not in text:
                return "handoff_harness", {}
        return action, args
