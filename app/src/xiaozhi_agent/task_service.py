from __future__ import annotations

import logging
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig, ConfigStore
from .drivers.local import LocalDriver
from .drivers.wechat_desktop import WeChatDesktopDriver
from .harness_worker import HarnessWorker
from .llm_client import create_client
from .models import InboundMessage, RouteDecision, WorkResult
from .paths import DATA_DIR
from .planner import TaskPlanner
from .router import IntentRouter
from .secrets_store import SecretStore
from .task_center import TaskCenter
from .weixin import WeixinChannel

log = logging.getLogger(__name__)


def new_task_id() -> str:
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"TASK-{now}-{secrets.token_hex(2).upper()}"


class TaskService:
    """Task Center orchestration.

    v0.2 adds a durable Planner gate: incoming requests are understood and
    converted into a concrete RouteDecision + TaskPlan first.  Execution never
    starts until that exact plan is approved.  This prevents the assistant from
    silently doing work the user did not intend and gives both Weixin and the
    desktop console the same workflow.
    """

    def __init__(
        self,
        center: TaskCenter,
        config_store: ConfigStore,
        secrets_store: SecretStore,
        channel: WeixinChannel,
    ):
        self.center = center
        self.config_store = config_store
        self.secrets = secrets_store
        self.channel = channel
        cfg = config_store.load()
        self.pool = ThreadPoolExecutor(max_workers=max(1, int(cfg.max_workers or 2)), thread_name_prefix="xiaozhi-task")
        self.wechat_driver = WeChatDesktopDriver()

    # ------------------------------------------------------------------
    # Provider helpers
    # ------------------------------------------------------------------
    def _key_for(self, provider: str) -> str:
        name = "openai_api_key" if (provider or "").lower() == "openai" else "deepseek_api_key"
        return self.secrets.get(name) if self.secrets.has(name) else ""

    def _client_for(self, provider: str, model: str, cfg: AppConfig):
        key = self._key_for(provider)
        if not key:
            label = "OpenAI" if provider.lower() == "openai" else "DeepSeek"
            raise RuntimeError(f"{label} API Key 尚未配置")
        return create_client(
            provider,
            api_key=key,
            model=model,
            deepseek_base_url=cfg.deepseek_base_url,
            openai_base_url=cfg.openai_base_url,
        )

    # ------------------------------------------------------------------
    # Channel-neutral notification
    # ------------------------------------------------------------------
    def _send_weixin_only(self, msg: InboundMessage | dict[str, Any], text: str) -> None:
        channel = msg.channel if isinstance(msg, InboundMessage) else str(msg.get("channel") or "")
        if channel != "weixin":
            return
        try:
            if isinstance(msg, InboundMessage):
                self.channel.send_text(msg.user_id, text, msg.context_token)
            else:
                self.channel.send_text(str(msg.get("channel_user_id") or ""), text, str(msg.get("context_token") or ""))
        except Exception:
            log.exception("Failed to send Weixin text")

    def _notify(self, task_id: str, msg: InboundMessage | dict[str, Any], text: str) -> None:
        self.center.update_task(task_id, result_text=text)
        self._send_weixin_only(msg, text)

    def _message_from_task(self, task: dict[str, Any], text: str = "") -> InboundMessage:
        return InboundMessage(
            channel=str(task.get("channel") or "desktop"),
            user_id=str(task.get("channel_user_id") or "desktop-user"),
            message_id=f"ui-{secrets.token_hex(4)}",
            message_type="text",
            text=text,
            attachments=[Path(p) for p in task.get("input_files") or []],
            context_token=str(task.get("context_token") or ""),
            received_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        )

    # ------------------------------------------------------------------
    # Inbound request -> plan
    # ------------------------------------------------------------------
    def handle_message(self, msg: InboundMessage) -> None:
        self.submit_message(msg, notify=True)

    def submit_message(self, msg: InboundMessage, *, notify: bool = True) -> dict[str, Any]:
        text = (msg.text or "").strip()
        if not text and not msg.attachments:
            raise RuntimeError("任务内容为空")
        if self._handle_control_message(msg, text):
            latest = self.center.latest_for_user(msg.user_id)
            return latest or {"control": True}

        cfg = self.config_store.load()
        configured_workspace = cfg.workspace_path
        task_workspace = configured_workspace or DATA_DIR
        task_id = new_task_id()
        self.center.create_task(
            task_id=task_id,
            workspace=str(task_workspace),
            channel=msg.channel,
            channel_user_id=msg.user_id,
            source_message_id=msg.message_id,
            context_token=msg.context_token,
            original_message=text,
            input_files=[str(p) for p in msg.attachments],
        )
        try:
            task = self._plan_task(task_id, msg, cfg)
            if notify:
                self._send_weixin_only(msg, self._format_plan_message(task))
            return self.center.get_task(task_id) or task
        except Exception as exc:
            self.center.update_task(task_id, status="failed", error=str(exc), current_stage="planning_failed", latest_action="planner_failed")
            if notify:
                self._notify(task_id, msg, f"{task_id} 已创建，但计划生成失败：{exc}")
            return self.center.get_task(task_id) or {}

    def _plan_task(self, task_id: str, msg: InboundMessage, cfg: AppConfig) -> dict[str, Any]:
        planner_client = self._client_for(cfg.planner_provider, cfg.planner_model, cfg)
        router = IntentRouter(planner_client)
        text = (msg.text or "").strip() or "处理用户发来的文件"
        decision = router.route(text, msg.attachments)
        workspace = cfg.workspace_path or DATA_DIR

        # Freeze deterministic local execution parameters during planning.  The
        # approved plan will later execute these exact parameters without asking
        # another model to reinterpret the command.
        if decision.local_action == "schedule_local_command":
            command = str(decision.local_args.get("command_text") or "").strip()
            action, args = router.plan_local(command, workspace)
            if action == "handoff_harness":
                decision.route = "harness"
                decision.summary = "定时内容不是确定性本地动作，需要 Harness"
                decision.local_action = None
                decision.local_args = {}
                decision.scheduled_action = None
                decision.scheduled_payload = {}
            else:
                decision.scheduled_action = "local_driver"
                decision.scheduled_payload = {"action": action, "args": args, "command": command}
        elif decision.route == "local" and not decision.scheduled_action:
            action, args = router.plan_local(text, workspace)
            if action == "handoff_harness":
                decision.route = "harness"
                decision.summary = "本地动作无法确定性解析，交给 Harness"
                decision.local_action = None
                decision.local_args = {}
            else:
                decision.local_action = action
                decision.local_args = args

        if decision.delivery_contact and not decision.delivery_channel:
            decision.delivery_channel = "weixin" if decision.delivery_contact == "我" else "desktop_wechat"

        # A desktop reminder to "me" stays on the desktop console instead of
        # pretending the desktop pseudo-user has an iLink Weixin identity.
        if msg.channel == "desktop" and decision.delivery_contact == "我" and decision.scheduled_action == "weixin_send_text":
            decision.delivery_channel = "desktop_ui"
            decision.scheduled_action = "desktop_notify"

        blockers: list[str] = []
        if decision.route == "harness":
            if cfg.workspace_path is None:
                blockers.append("Harness 工作空间尚未配置")
            if not self.secrets.has("deepseek_api_key"):
                blockers.append("Harness 当前需要 DeepSeek API Key")
        if decision.route == "direct" and not self._key_for(cfg.direct_provider):
            blockers.append(f"Direct 模型提供方 {cfg.direct_provider} 尚未配置 API Key")

        plan = TaskPlanner(planner_client).plan(
            request=text,
            decision=decision,
            attachments=msg.attachments,
            blockers=blockers,
        )
        self.center.update_task(
            task_id,
            status="awaiting_approval",
            route=decision.route,
            route_summary=decision.summary,
            plan=plan.to_dict(),
            decision=decision.to_dict(),
            planner_provider=cfg.planner_provider,
            planner_model=cfg.planner_model,
            approval_required=True,
            current_stage="awaiting_approval",
            latest_action="plan_ready",
            error=None,
        )
        return self.center.get_task(task_id) or {}

    @staticmethod
    def _format_plan_message(task: dict[str, Any]) -> str:
        plan = task.get("plan") or {}
        route_names = {"local": "本地确定性执行", "direct": "一次大模型", "harness": "Harness 复杂任务"}
        lines = [
            f"🧭 {task['task_id']} 执行前计划",
            f"任务：{plan.get('title') or task.get('route_summary') or '-'}",
            f"理解：{plan.get('understanding') or '-'}",
            f"执行方式：{route_names.get(task.get('route'), task.get('route') or '-')}",
        ]
        steps = plan.get("steps") or []
        if steps:
            lines.append("\n将要做：")
            lines.extend(f"{i}. {x}" for i, x in enumerate(steps, 1))
        recs = plan.get("recommendations") or []
        if recs:
            lines.append("\n执行前建议：")
            lines.extend(f"• {x}" for x in recs)
        risks = plan.get("risks") or []
        if risks:
            lines.append("\n注意：")
            lines.extend(f"• {x}" for x in risks)
        blockers = plan.get("blockers") or []
        if blockers:
            lines.append("\n当前阻塞：")
            lines.extend(f"• {x}" for x in blockers)
        outputs = plan.get("expected_outputs") or []
        if outputs:
            lines.append("\n预计交付：" + "、".join(outputs))
        if plan.get("estimated_minutes"):
            lines.append(f"预计耗时：约 {plan['estimated_minutes']} 分钟")
        lines.append("\n回复「开始执行」继续；回复「修改：……」调整计划；回复「取消」停止。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Approval / plan controls
    # ------------------------------------------------------------------
    def approve_plan(self, task_id: str, msg: InboundMessage | None = None) -> dict[str, Any]:
        task = self.center.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if task.get("status") != "awaiting_approval":
            raise RuntimeError(f"任务当前不是待执行确认状态：{task.get('status')}")
        plan = task.get("plan") or {}
        blockers = plan.get("blockers") or []
        if blockers:
            raise RuntimeError("计划存在阻塞项，请先处理：" + "；".join(str(x) for x in blockers))
        decision = RouteDecision.from_dict(task.get("decision"))
        if not decision.route:
            raise RuntimeError("计划缺少执行路由")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.center.update_task(
            task_id,
            route=decision.route,
            route_summary=decision.summary,
            review_contact=decision.review_contact,
            delivery_contact=decision.delivery_contact,
            delivery_mode=decision.delivery_mode,
            delivery_channel=decision.delivery_channel,
            completion_action=decision.completion_action,
            scheduled_at=decision.scheduled_at,
            scheduled_action=decision.scheduled_action,
            scheduled_payload=decision.scheduled_payload,
            approved_at=now,
            current_stage="approved",
            latest_action="plan_approved",
            status="queued",
            error=None,
        )
        actual_msg = msg or self._message_from_task(task, text="开始执行")
        if decision.scheduled_at and decision.scheduled_action:
            self.center.update_task(task_id, status="queued", current_stage="scheduled_waiting", latest_action=f"scheduled:{decision.scheduled_action}")
            self._notify(task_id, actual_msg, self._scheduled_ack(task_id, decision))
            return self.center.get_task(task_id) or {}

        self._notify(task_id, actual_msg, f"{task_id} 已确认，开始执行。")
        self.pool.submit(self._execute_new_task, task_id, actual_msg, decision)
        return self.center.get_task(task_id) or {}

    def cancel_plan(self, task_id: str, msg: InboundMessage | None = None) -> dict[str, Any]:
        task = self.center.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if task.get("status") not in {"awaiting_approval", "queued"}:
            raise RuntimeError(f"当前状态不能取消计划：{task.get('status')}")
        self.center.update_task(task_id, status="cancelled", current_stage="cancelled", latest_action="plan_cancelled")
        actual_msg = msg or self._message_from_task(task, text="取消")
        self._notify(task_id, actual_msg, f"{task_id} 已取消，不会执行。")
        return self.center.get_task(task_id) or {}

    def replan(self, task_id: str, instruction: str, msg: InboundMessage | None = None) -> dict[str, Any]:
        task = self.center.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        if task.get("status") != "awaiting_approval":
            raise RuntimeError("只有待确认计划可以修改")
        instruction = instruction.strip()
        if not instruction:
            raise RuntimeError("修改要求为空")
        combined = str(task.get("original_message") or "") + "\n\n补充/修改要求：" + instruction
        self.center.update_task(task_id, original_message=combined, current_stage="replanning", latest_action="replan_requested")
        actual_msg = msg or self._message_from_task(task, text=combined)
        actual_msg.text = combined
        cfg = self.config_store.load()
        updated = self._plan_task(task_id, actual_msg, cfg)
        self._send_weixin_only(actual_msg, self._format_plan_message(updated))
        return self.center.get_task(task_id) or {}

    @staticmethod
    def _scheduled_ack(task_id: str, decision: RouteDecision) -> str:
        try:
            due = datetime.fromisoformat(str(decision.scheduled_at)).astimezone()
            when = due.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            when = str(decision.scheduled_at or "")
        text = str(decision.scheduled_payload.get("text") or "")
        if decision.scheduled_action == "weixin_send_text":
            return f"已确认 {task_id}：{when} 会通过当前微信 Claw 发给你：{text}"
        if decision.scheduled_action == "desktop_notify":
            return f"已确认 {task_id}：{when} 会在电脑端提醒你：{text}"
        if decision.scheduled_action == "wechat_send_text":
            return f"已确认 {task_id}：{when} 会打开/聚焦桌面微信，发给「{decision.delivery_contact}」：{text}"
        return f"已确认 {task_id}：{when} 执行本地定时操作。"

    def _handle_control_message(self, msg: InboundMessage, text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if re.match(r"^(状态|进度|status)(TASK[-\w]+)?$", compact, re.I):
            m = re.search(r"TASK[-\w]+", compact, re.I)
            task = self.center.get_task(m.group(0).upper()) if m else self.center.latest_for_user(msg.user_id)
            if not task:
                return True
            self._notify(
                task["task_id"], msg,
                f"{task['task_id']}\n状态: {task['status']}\n阶段: {task.get('current_stage') or '-'}\n版本: V{task.get('version') or 0}\n最后动作: {task.get('latest_action') or '-'}"
                + (f"\n错误: {task['error']}" if task.get("error") else ""),
            )
            return True

        pending = self.center.latest_for_user(msg.user_id, ("awaiting_approval",))
        if pending:
            if self._is_cancel(text):
                self.cancel_plan(pending["task_id"], msg)
                return True
            if self._is_plan_approval(text):
                try:
                    self.approve_plan(pending["task_id"], msg)
                except Exception as exc:
                    self._notify(pending["task_id"], msg, f"{pending['task_id']} 还不能开始：{exc}")
                return True
            instruction = self._extract_replan_instruction(text)
            if instruction:
                try:
                    self.replan(pending["task_id"], instruction, msg)
                except Exception as exc:
                    self._notify(pending["task_id"], msg, f"计划修改失败：{exc}")
                return True

        latest = self.center.latest_for_user(msg.user_id, ("awaiting_review", "completed"))
        if latest and self._is_output_approval(text):
            contact = self._extract_delivery_contact(text) or latest.get("delivery_contact")
            self.center.update_task(latest["task_id"], context_token=msg.context_token)
            self.pool.submit(self._approve_and_maybe_deliver, latest["task_id"], msg, contact)
            return True

        if latest and self._is_revision(text) and int(latest.get("version") or 0) > 0:
            self.center.update_task(
                latest["task_id"], status="running", context_token=msg.context_token,
                current_stage="revision_queued", latest_action="revision_requested", error=None,
            )
            self._notify(latest["task_id"], msg, f"收到，继续修改 {latest['task_id']}，旧版本不会覆盖。")
            self.pool.submit(self._run_revision, latest["task_id"], msg)
            return True
        return False

    @staticmethod
    def _is_plan_approval(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return bool(re.match(r"^(?:开始执行|确认执行|同意|确认|可以|按这个做|执行|开始)", compact))

    @staticmethod
    def _is_cancel(text: str) -> bool:
        return re.sub(r"\s+", "", text) in {"取消", "取消任务", "不做了", "停止"}

    @staticmethod
    def _extract_replan_instruction(text: str) -> str | None:
        m = re.match(r"^\s*(?:修改计划|修改|调整|改成|补充)\s*[：:]?\s*(.+)$", text, re.S)
        return m.group(1).strip() if m else None

    @staticmethod
    def _is_output_approval(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return (
            bool(re.search(r"^(可以|通过|就这版|这版可以|批准)", compact))
            or "这版发" in compact
            or ("发给" in compact and "这版" in compact)
            or bool(re.search(r"最终(?:版|版本).*发(?:给)?", compact))
            or bool(re.search(r"(?:这个|当前)版本.*发(?:给)?", compact))
        )

    @staticmethod
    def _is_revision(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return bool(re.search(r"(第[一二三四五六七八九十0-9]+页|修改|改一下|改成|简单一点|再精简|再调整|换成|增加|删掉)", compact))

    @staticmethod
    def _extract_delivery_contact(text: str) -> str | None:
        compact = re.sub(r"\s+", "", text)
        m = re.search(r"发(?:给)?([^，。,.！!？?]{1,12})", compact)
        if not m:
            return None
        contact = re.sub(r"(吧|。|！|!|，|,)$", "", m.group(1))
        return None if contact in {"我", "我审核", "我看看"} else contact

    # ------------------------------------------------------------------
    # Execute approved plan
    # ------------------------------------------------------------------
    def _execute_new_task(self, task_id: str, msg: InboundMessage, decision: RouteDecision) -> None:
        cfg = self.config_store.load()
        try:
            self.center.update_task(task_id, status="running", current_stage=f"{decision.route}_running", latest_action=f"execute:{decision.route}")
            if decision.route == "direct":
                result = self._run_direct(msg.text, cfg)
                self._complete_text_task(task_id, msg, decision, result)
                return
            if decision.route == "local":
                if not decision.local_action:
                    raise RuntimeError("批准的本地计划缺少 local_action")
                workspace = cfg.workspace_path or DATA_DIR
                result = LocalDriver(workspace).execute(decision.local_action, decision.local_args)
                self._complete_local_task(task_id, msg, decision, result)
                return
            if decision.route != "harness":
                raise RuntimeError(f"未知执行路由: {decision.route}")
            api_key = self._key_for("deepseek")
            if not api_key:
                raise RuntimeError("Harness 当前需要 DeepSeek API Key")
            if cfg.workspace_path is None:
                raise RuntimeError("Harness 任务需要先选择工作空间")
            self._run_harness_new(task_id, msg, decision, cfg, api_key)
        except Exception as exc:
            log.exception("Task execution failed %s", task_id)
            self.center.update_task(task_id, status="failed", error=str(exc), current_stage="failed", latest_action="execution_failed")
            self._notify(task_id, msg, f"{task_id} 执行失败：{exc}")

    def _run_direct(self, text: str, cfg: AppConfig) -> WorkResult:
        client = self._client_for(cfg.direct_provider, cfg.direct_model, cfg)
        answer = client.chat(
            [
                {"role": "system", "content": "你是小智打工人搭子。用户已经批准计划。直接完成文字任务，输出可直接使用的中文结果；不要再次询问是否执行。"},
                {"role": "user", "content": text},
            ],
            max_tokens=12_000,
        )
        return WorkResult(True, text=answer)

    def _complete_text_task(self, task_id: str, msg: InboundMessage, decision: RouteDecision, result: WorkResult) -> None:
        if not result.ok:
            raise RuntimeError(result.error or "Direct task failed")
        if decision.delivery_mode == "on_complete" and decision.delivery_contact:
            self.wechat_driver.send_text(decision.delivery_contact, result.text)
            self.center.record_delivery(task_id=task_id, version=0, contact=decision.delivery_contact, channel="desktop_wechat", status="sent", detail="text")
            self.center.update_task(task_id, status="completed", delivered_at=datetime.now(timezone.utc).isoformat(), latest_action="direct_text_delivered")
            self._notify(task_id, msg, f"{task_id} 已完成并发送给「{decision.delivery_contact}」。")
        else:
            self.center.update_task(task_id, status="completed", current_stage="completed", latest_action="direct_completed", result_text=result.text)
            self._notify(task_id, msg, f"{task_id} 已完成：\n\n{result.text}")

    def _complete_local_task(self, task_id: str, msg: InboundMessage, decision: RouteDecision, result: WorkResult) -> None:
        if not result.ok:
            raise RuntimeError(result.error or "Local task failed")
        if result.files:
            version, _ = self.center.add_version(task_id, result.files)
            if decision.delivery_mode == "on_complete" and decision.delivery_contact:
                self.center.approve_version(task_id, version)
                approved = self.center.verified_approved_version(task_id)
                exact = [Path(row["path"]) for row in approved["files"]]
                self.wechat_driver.send_files(decision.delivery_contact, exact)
                self.center.record_delivery(task_id=task_id, version=version, contact=decision.delivery_contact, channel="desktop_wechat", status="sent", detail=approved["hash"])
                self.center.update_task(task_id, status="completed", delivered_at=datetime.now(timezone.utc).isoformat(), latest_action="local_files_delivered")
                self._notify(task_id, msg, f"{task_id} 已完成，并把 V{version} 原文件发送给「{decision.delivery_contact}」。")
            else:
                version_row = self.center.get_version(task_id, version) or {"files": []}
                review_files = [Path(row["path"]) for row in version_row["files"]]
                summary = f"{task_id} 已完成，生成 V{version}。{result.text or ''}".strip()
                if msg.channel == "weixin":
                    self.channel.send_text(msg.user_id, summary, msg.context_token)
                    for p in review_files:
                        self.channel.send_file(msg.user_id, p, msg.context_token)
                self.center.update_task(task_id, status="awaiting_review", current_stage="awaiting_review", latest_action="local_files_ready_for_review", result_text=summary)
        else:
            self.center.update_task(task_id, status="completed", current_stage="completed", latest_action="local_done", result_text=result.text)
            self._notify(task_id, msg, f"{task_id} 已完成：{result.text}")

    def _run_harness_new(self, task_id: str, msg: InboundMessage, decision: RouteDecision, cfg: AppConfig, api_key: str) -> None:
        task = self.center.get_task(task_id) or {}
        worker = HarnessWorker(cfg, api_key)
        self.center.update_task(task_id, current_stage="harness_running", latest_action="harness_started")
        result = worker.run_task(
            task_id=task_id,
            text=msg.text or "处理用户发来的文件并完成任务",
            input_files=msg.attachments,
            next_version=1,
            session_id=str(task.get("harness_session_id") or f"xiaozhi-{task_id}"),
            on_event=lambda e: self.center.update_task(task_id, latest_action=f"harness:{e}"),
        )
        actual_session_id = str(result.metadata.get("session_id") or "").strip()
        if actual_session_id:
            self.center.update_task(task_id, harness_session_id=actual_session_id)
        self._finish_harness(task_id, msg, decision, result)

    def _run_revision(self, task_id: str, msg: InboundMessage) -> None:
        cfg = self.config_store.load()
        api_key = self._key_for("deepseek")
        task = self.center.get_task(task_id)
        if not task:
            return
        if not api_key:
            self._notify(task_id, msg, "修改需要 Harness，但 DeepSeek API Key 未配置。")
            return
        worker = HarnessWorker(cfg, api_key)
        next_version = int(task.get("version") or 0) + 1
        previous = [Path(p) for p in task.get("output_files") or []]
        inputs = [Path(p) for p in task.get("input_files") or []]
        revision_session_id = f"xiaozhi-{task_id}-v{next_version}-{secrets.token_hex(3)}"
        self.center.update_task(task_id, harness_session_id=revision_session_id)
        result = worker.run_task(
            task_id=task_id,
            text=f"这是对现有成果的修改要求：{msg.text}",
            input_files=inputs,
            next_version=next_version,
            session_id=revision_session_id,
            revision_of=previous,
            on_event=lambda e: self.center.update_task(task_id, latest_action=f"harness_revision:{e}"),
        )
        actual_session_id = str(result.metadata.get("session_id") or "").strip()
        if actual_session_id:
            self.center.update_task(task_id, harness_session_id=actual_session_id)
        decision = RouteDecision(route="harness", summary="revision", delivery_mode="review", completion_action="send", review_contact="我")
        self._finish_harness(task_id, msg, decision, result)

    def _finish_harness(self, task_id: str, msg: InboundMessage, decision: RouteDecision, result: WorkResult) -> None:
        if not result.ok:
            self.center.update_task(task_id, status="failed", error=result.error, current_stage="harness_failed", result_text=result.text)
            self._notify(task_id, msg, f"{task_id} Harness 执行失败：{result.error}")
            return
        if not result.files:
            self.center.update_task(task_id, status="completed", current_stage="completed", latest_action="harness_text_completed", result_text=result.text)
            self._notify(task_id, msg, f"{task_id} 已完成：\n\n{result.text or '任务完成'}")
            return

        version, digest = self.center.add_version(task_id, result.files)
        mode = result.metadata.get("harness_mode")
        self.center.update_task(task_id, current_stage="outputs_verified", latest_action=f"v{version}_verified_{mode}", result_text=result.text)

        if decision.delivery_mode == "on_complete" and decision.delivery_contact:
            self.center.approve_version(task_id, version)
            approved = self.center.verified_approved_version(task_id)
            exact = [Path(row["path"]) for row in approved["files"]]
            self.wechat_driver.send_text(decision.delivery_contact, f"小智任务 {task_id} 已完成，发送最终文件 V{version}。")
            self.wechat_driver.send_files(decision.delivery_contact, exact)
            self.center.record_delivery(task_id=task_id, version=version, contact=decision.delivery_contact, channel="desktop_wechat", status="sent", detail=approved["hash"])
            self.center.update_task(task_id, status="completed", delivered_at=datetime.now(timezone.utc).isoformat(), latest_action=f"v{version}_delivered")
            self._notify(task_id, msg, f"{task_id} 已完成并发送 V{version} 给「{decision.delivery_contact}」。SHA256 批次指纹：{digest[:16]}…")
            return

        version_row = self.center.get_version(task_id, version) or {"files": []}
        review_files = [Path(row["path"]) for row in version_row["files"]]
        summary = f"{task_id} 已完成 V{version}，等待你审核。\n{(result.text or '')[:1800]}"
        if msg.channel == "weixin":
            self.channel.send_text(msg.user_id, summary, msg.context_token)
            for p in review_files:
                self.channel.send_file(msg.user_id, p, msg.context_token)
        self.center.update_task(task_id, status="awaiting_review", current_stage="awaiting_review", latest_action=f"v{version}_ready_for_review", result_text=summary)

    def approve_output(self, task_id: str, contact: str | None = None, msg: InboundMessage | None = None) -> dict[str, Any]:
        task = self.center.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        actual_msg = msg or self._message_from_task(task, text="批准")
        self._approve_and_maybe_deliver(task_id, actual_msg, contact)
        return self.center.get_task(task_id) or {}

    def _approve_and_maybe_deliver(self, task_id: str, msg: InboundMessage, contact: str | None) -> None:
        try:
            self.center.approve_version(task_id)
            approved = self.center.verified_approved_version(task_id)
            version = approved["version"]
            if not contact:
                self.center.update_task(task_id, status="completed", latest_action=f"v{version}_approved")
                self._notify(task_id, msg, f"{task_id} 的 V{version} 已批准。已锁定 SHA256 批次指纹 {approved['hash'][:16]}…")
                return
            self.center.update_task(task_id, delivery_contact=contact, completion_action="send", delivery_mode="approved_send", delivery_channel="desktop_wechat")
            exact = [Path(row["path"]) for row in approved["files"]]
            self.wechat_driver.send_text(contact, f"小智任务 {task_id} 最终版本 V{version}。")
            self.wechat_driver.send_files(contact, exact)
            self.center.record_delivery(task_id=task_id, version=version, contact=contact, channel="desktop_wechat", status="sent", detail=approved["hash"])
            self.center.update_task(task_id, status="completed", delivered_at=datetime.now(timezone.utc).isoformat(), latest_action=f"approved_v{version}_delivered")
            self._notify(task_id, msg, f"已把你批准的 V{version} 原文件发送给「{contact}」，没有重新生成。")
        except Exception as exc:
            log.exception("Approval/delivery failed")
            self.center.update_task(task_id, status="failed", error=str(exc), latest_action="delivery_failed")
            self._notify(task_id, msg, f"{task_id} 审批/发送失败：{exc}")

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------
    def run_due_deliveries(self) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="seconds")
        stale_before = (now_dt - timedelta(minutes=10)).isoformat(timespec="seconds")
        for task in self.center.scheduled_ready(now, stale_before):
            if not self.center.claim_scheduled(task["task_id"], now, stale_before):
                continue
            try:
                action = str(task.get("scheduled_action") or "")
                payload = task.get("scheduled_payload") if isinstance(task.get("scheduled_payload"), dict) else {}

                if action == "weixin_send_text":
                    user_id = str(task.get("channel_user_id") or "")
                    text = str(payload.get("text") or "")
                    if not user_id or not text:
                        raise RuntimeError("定时微信消息缺少 user_id/text")
                    latest_ctx = self.channel.last_context_by_user.get(user_id, "")
                    context = latest_ctx or str(task.get("context_token") or "")
                    self.channel.send_text(user_id, text, context)
                    self.center.record_delivery(task_id=task["task_id"], version=0, contact="我", channel="weixin", status="sent", detail="scheduled_text")
                    self.center.update_task(task["task_id"], status="completed", delivered_at=now, current_stage="completed", latest_action="scheduled_weixin_text_delivered", error=None, result_text=text)
                    continue

                if action == "desktop_notify":
                    text = str(payload.get("text") or "")
                    self.center.update_task(task["task_id"], status="completed", delivered_at=now, current_stage="completed", latest_action="scheduled_desktop_notification", error=None, result_text=text)
                    continue

                if action == "wechat_send_text":
                    contact = str(task.get("delivery_contact") or payload.get("contact") or "").strip()
                    text = str(payload.get("text") or "")
                    if not contact or not text:
                        raise RuntimeError("定时桌面微信消息缺少 contact/text")
                    self.wechat_driver.send_text(contact, text)
                    self.center.record_delivery(task_id=task["task_id"], version=0, contact=contact, channel="desktop_wechat", status="sent", detail="scheduled_text")
                    self.center.update_task(task["task_id"], status="completed", delivered_at=now, current_stage="completed", latest_action="scheduled_desktop_wechat_text_delivered", error=None, result_text=f"已发送给 {contact}: {text}")
                    self._notify(task["task_id"], task, f"{task['task_id']} 已按计划通过桌面微信发送给「{contact}」。")
                    continue

                if action == "local_driver":
                    local_action = str(payload.get("action") or "")
                    local_args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
                    workspace = Path(str(task.get("workspace") or DATA_DIR))
                    result = LocalDriver(workspace).execute(local_action, local_args)
                    if not result.ok:
                        raise RuntimeError(result.error or "scheduled local action failed")
                    self.center.update_task(task["task_id"], status="completed", delivered_at=now, current_stage="completed", latest_action=f"scheduled_local:{local_action}", error=None, result_text=result.text)
                    self._notify(task["task_id"], task, f"{task['task_id']} 定时本地操作已完成：{result.text}")
                    continue

                version = int(task.get("approved_version") or 0)
                if version <= 0:
                    raise RuntimeError("定时任务没有可执行 action 或批准版本")
                approved = self.center.verified_approved_version(task["task_id"])
                contact = str(task.get("delivery_contact") or "")
                if not contact:
                    raise RuntimeError("定时文件交付缺少联系人")
                files = [Path(row["path"]) for row in approved["files"]]
                self.wechat_driver.send_files(contact, files)
                self.center.record_delivery(task_id=task["task_id"], version=version, contact=contact, channel="desktop_wechat", status="sent", detail="scheduled")
                self.center.update_task(task["task_id"], delivered_at=now, latest_action=f"scheduled_v{version}_delivered", current_stage="completed", error=None)
                self._notify(task["task_id"], task, f"{task['task_id']} 已按计划把 V{version} 发送给「{contact}」。")
            except Exception as exc:
                log.exception("Scheduled delivery failed")
                self.center.update_task(task["task_id"], status="failed", error=str(exc), current_stage="scheduled_failed", latest_action="scheduled_delivery_failed")
                try:
                    self._notify(task["task_id"], task, f"{task['task_id']} 定时动作执行失败：{exc}")
                except Exception:
                    pass
