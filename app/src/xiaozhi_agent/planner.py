from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import RouteDecision, TaskPlan


class TaskPlanner:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _fallback_title(text: str) -> str:
        compact = " ".join((text or "").split())
        return compact[:28] + ("…" if len(compact) > 28 else "") or "处理任务"

    def plan(
        self,
        *,
        request: str,
        decision: RouteDecision,
        attachments: list[Path],
        blockers: list[str] | None = None,
    ) -> TaskPlan:
        blockers = list(blockers or [])
        deterministic = {
            "route": decision.route,
            "route_summary": decision.summary,
            "local_action": decision.local_action,
            "local_args": decision.local_args,
            "delivery_contact": decision.delivery_contact,
            "delivery_channel": decision.delivery_channel,
            "delivery_mode": decision.delivery_mode,
            "scheduled_at": decision.scheduled_at,
            "scheduled_action": decision.scheduled_action,
            "scheduled_payload": decision.scheduled_payload,
            "attachments": [p.name for p in attachments],
            "blockers": blockers,
            "current_local_time": datetime.now().astimezone().isoformat(timespec="minutes"),
        }
        system = """你是小智电脑助手的 Planner，不执行任何工具，只负责把用户请求整理成执行前计划。
Task Center 已经给出一个确定性的 route/交付/定时决定。你不得擅自把 local 改成 harness，也不得改变收件人、定时时间或交付通道。
请输出 JSON，字段必须包含：
- title: 12~30字任务标题
- understanding: 1~3句你对需求的理解；如果用户是在征求建议，可先给非常简短的方向性建议，但不要假装已经做了研究
- steps: 2~7条将要执行的具体动作
- recommendations: 0~4条执行前建议/可选项
- risks: 0~4条真实风险或需要注意的边界
- expected_outputs: 0~5条预计交付物
- estimated_minutes: 整数或 null
- approval_prompt: 一句确认提示
不要声称已经读取、研究或生成任何尚未执行的内容。不要输出 Markdown。"""
        user = "USER REQUEST:\n" + (request or "处理用户发来的文件") + "\n\nDETERMINISTIC CONTEXT:\n" + json.dumps(deterministic, ensure_ascii=False, indent=2)
        data = self.client.json_chat(system, user, max_tokens=8000)

        def list_of_strings(value: Any, limit: int) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(x).strip() for x in value if str(x).strip()][:limit]

        estimated = data.get("estimated_minutes")
        try:
            estimated_int = int(estimated) if estimated is not None else None
            if estimated_int is not None and estimated_int < 0:
                estimated_int = None
        except Exception:
            estimated_int = None

        return TaskPlan(
            title=str(data.get("title") or self._fallback_title(request))[:80],
            understanding=str(data.get("understanding") or decision.summary or request).strip(),
            route=decision.route,
            steps=list_of_strings(data.get("steps"), 7) or [decision.summary or "按请求执行任务"],
            recommendations=list_of_strings(data.get("recommendations"), 4),
            risks=list_of_strings(data.get("risks"), 4),
            expected_outputs=list_of_strings(data.get("expected_outputs"), 5),
            estimated_minutes=estimated_int,
            blockers=blockers,
            approval_prompt=str(data.get("approval_prompt") or "确认后我再开始执行。").strip(),
        )
