from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig, ConfigStore
from .harness_worker import HarnessWorker
from .llm_client import create_client
from .logging_setup import setup_logging
from .models import InboundMessage
from .paths import DATA_DIR, INBOX_DIR, LOG_DIR
from .secrets_store import SecretStore
from .task_center import TaskCenter
from .task_service import TaskService
from .weixin import WeixinChannel

log = logging.getLogger(__name__)


def _safe_filename(name: str) -> str:
    name = Path(name or "attachment.bin").name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "attachment.bin"


class XiaoZhiRuntime:
    def __init__(self):
        setup_logging()
        self.config_store = ConfigStore()
        self.secrets = SecretStore()
        self.center = TaskCenter()
        self.channel = WeixinChannel(self.secrets)
        self.task_service = TaskService(self.center, self.config_store, self.secrets, self.channel)
        self.channel.on_message = self.task_service.handle_message
        self.stop_event = threading.Event()
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="xiaozhi-scheduler")
        self.scheduler_thread.start()
        cfg = self.config_store.load()
        if cfg.auto_start_weixin and self.channel.has_credentials():
            self.channel.start_polling()

    def _scheduler_loop(self) -> None:
        while not self.stop_event.wait(5):
            try:
                self.task_service.run_due_deliveries()
            except Exception:
                log.exception("Scheduler tick failed")

    def shutdown(self) -> None:
        self.stop_event.set()
        self.channel.stop()
        if self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=2.0)
        try:
            self.task_service.pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self.center.close()

    def status(self) -> dict[str, Any]:
        cfg = self.config_store.load()
        tasks = self.center.list_tasks(100)
        for task in tasks:
            task.pop("context_token", None)
            task.pop("decision", None)
        planner_key = self.secrets.has("openai_api_key" if cfg.planner_provider == "openai" else "deepseek_api_key")
        return {
            "configured": bool(planner_key),
            "workspace": cfg.workspace,
            "planner_provider": cfg.planner_provider,
            "planner_model": cfg.planner_model,
            "direct_provider": cfg.direct_provider,
            "direct_model": cfg.direct_model,
            "model": cfg.model,
            "deepseek_base_url": cfg.deepseek_base_url,
            "openai_base_url": cfg.openai_base_url,
            "approval_mode": cfg.approval_mode,
            "speech_language": cfg.speech_language,
            "deepseek_api_configured": self.secrets.has("deepseek_api_key"),
            "openai_api_configured": self.secrets.has("openai_api_key"),
            "harness_safe_mode": cfg.harness_safe_mode,
            "allow_harness_full_access_fallback": cfg.allow_harness_full_access_fallback,
            "weixin": self.channel.get_status(),
            "weixin_has_credentials": self.channel.has_credentials(),
            "tasks": tasks,
        }

    def save_config(self, data: dict[str, Any]) -> AppConfig:
        cfg = self.config_store.load()
        workspace = str(data.get("workspace") if "workspace" in data else cfg.workspace).strip()
        if workspace:
            p = Path(os.path.expandvars(os.path.expanduser(workspace))).resolve()
            p.mkdir(parents=True, exist_ok=True)
            workspace = str(p)
        cfg.workspace = workspace

        cfg.planner_provider = str(data.get("planner_provider") or cfg.planner_provider or "deepseek").strip().lower()
        cfg.planner_model = str(data.get("planner_model") or cfg.planner_model or "deepseek-flash").strip()
        cfg.direct_provider = str(data.get("direct_provider") or cfg.direct_provider or "deepseek").strip().lower()
        cfg.direct_model = str(data.get("direct_model") or cfg.direct_model or "deepseek-flash").strip()
        if cfg.planner_provider not in {"deepseek", "openai"}:
            raise ValueError("Planner provider 只支持 deepseek / openai")
        if cfg.direct_provider not in {"deepseek", "openai"}:
            raise ValueError("Direct provider 只支持 deepseek / openai")
        cfg.model = str(data.get("model") or cfg.model or "deepseek-flash").strip()
        if not cfg.model.lower().startswith("deepseek"):
            raise ValueError("Harness v0.2 当前保持 DeepSeek provider；GPT-5.6 Sol 请用于 Planner / Direct")
        cfg.deepseek_base_url = str(data.get("deepseek_base_url") or cfg.deepseek_base_url or "https://api.deepseek.com").strip().rstrip("/")
        cfg.openai_base_url = str(data.get("openai_base_url") or cfg.openai_base_url or "https://api.openai.com/v1").strip().rstrip("/")
        cfg.approval_mode = str(data.get("approval_mode") or cfg.approval_mode or "plan_first").strip()
        cfg.speech_language = str(data.get("speech_language") or cfg.speech_language or "zh-CN").strip()
        if "harness_safe_mode" in data:
            cfg.harness_safe_mode = bool(data["harness_safe_mode"])
        if "allow_harness_full_access_fallback" in data:
            cfg.allow_harness_full_access_fallback = bool(data["allow_harness_full_access_fallback"])
        if "auto_start_weixin" in data:
            cfg.auto_start_weixin = bool(data["auto_start_weixin"])
        cfg.last_updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.config_store.save(cfg)

        deepseek_key = str(data.get("deepseek_api_key") or data.get("api_key") or "").strip()
        openai_key = str(data.get("openai_api_key") or "").strip()
        if deepseek_key:
            self.secrets.set("deepseek_api_key", deepseek_key)
        if openai_key:
            self.secrets.set("openai_api_key", openai_key)

        if cfg.workspace and self.secrets.has("deepseek_api_key"):
            try:
                HarnessWorker(cfg, self.secrets.get("deepseek_api_key")).deploy_workspace_support()
            except Exception:
                log.exception("Deploying workspace support failed")
        return cfg

    def test_api(self, provider: str) -> str:
        cfg = self.config_store.load()
        provider = (provider or "deepseek").lower()
        if provider == "openai":
            key = self.secrets.get("openai_api_key")
            client = create_client("openai", api_key=key, model=cfg.planner_model if cfg.planner_provider == "openai" else "gpt-5.6-sol", openai_base_url=cfg.openai_base_url, deepseek_base_url=cfg.deepseek_base_url)
        else:
            key = self.secrets.get("deepseek_api_key")
            client = create_client("deepseek", api_key=key, model=cfg.planner_model if cfg.planner_provider == "deepseek" else cfg.model, openai_base_url=cfg.openai_base_url, deepseek_base_url=cfg.deepseek_base_url)
        return client.test()

    # ----------------------------- desktop chat -----------------------------
    def _save_uploads(self, attachments: list[dict[str, Any]]) -> list[Path]:
        saved: list[Path] = []
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        total = 0
        for item in attachments[:8]:
            name = _safe_filename(str(item.get("name") or "attachment.bin"))
            raw_b64 = str(item.get("data_base64") or "")
            if not raw_b64:
                continue
            blob = base64.b64decode(raw_b64, validate=True)
            total += len(blob)
            if total > 25 * 1024 * 1024:
                raise RuntimeError("桌面附件总大小暂时限制为 25MB")
            dst = INBOX_DIR / f"desktop_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}_{name}"
            dst.write_bytes(blob)
            saved.append(dst)
        return saved

    def desktop_chat(self, text: str, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        saved = self._save_uploads(attachments or [])
        msg = InboundMessage(
            channel="desktop",
            user_id="desktop-user",
            message_id=f"desktop-{secrets.token_hex(8)}",
            message_type="text",
            text=text,
            attachments=saved,
            context_token="",
            received_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        )
        return self.task_service.submit_message(msg, notify=False)

    def approve_plan(self, task_id: str) -> dict[str, Any]:
        return self.task_service.approve_plan(task_id)

    def cancel_plan(self, task_id: str) -> dict[str, Any]:
        return self.task_service.cancel_plan(task_id)

    def replan(self, task_id: str, instruction: str) -> dict[str, Any]:
        return self.task_service.replan(task_id, instruction)

    def approve_output(self, task_id: str, contact: str | None = None) -> dict[str, Any]:
        return self.task_service.approve_output(task_id, contact=contact)

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = self.center.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        task.pop("context_token", None)
        task.pop("decision", None)
        version = int(task.get("version") or 0)
        if version > 0:
            v = self.center.get_version(task_id, version)
            task["version_files"] = (v or {}).get("files", [])
        else:
            task["version_files"] = []
        return task

    def open_path(self, raw_path: str) -> None:
        p = Path(os.path.expandvars(os.path.expanduser(raw_path))).resolve()
        if not p.exists():
            raise FileNotFoundError(str(p))
        # Only open user workspace or XiaoZhi-managed data/task files.
        cfg = self.config_store.load()
        roots = [DATA_DIR.resolve()]
        if cfg.workspace_path:
            roots.append(cfg.workspace_path.resolve())
        if not any(p == root or root in p.parents for root in roots):
            raise PermissionError("只能从小智工作空间或小智数据目录打开文件")
        os.startfile(str(p))  # type: ignore[attr-defined]

    # ----------------------------- existing UI actions -----------------------------
    def start_weixin_login(self, force: bool = False) -> None:
        self.channel.start_login(force=force)

    def submit_weixin_verify(self, code: str) -> None:
        self.channel.submit_verify_code(code)

    def browse_workspace(self) -> str:
        if os.name != "nt":
            return ""
        try:
            import win32com.client  # type: ignore
            shell = win32com.client.Dispatch("Shell.Application")
            folder = shell.BrowseForFolder(0, "选择 Harness 工作空间", 0, 0)
            if folder is None:
                return ""
            return str(folder.Self.Path)
        except Exception:
            log.exception("BrowseForFolder failed")
            return ""

    def open_workspace(self) -> None:
        cfg = self.config_store.load()
        if cfg.workspace:
            os.startfile(cfg.workspace)  # type: ignore[attr-defined]

    def open_logs(self) -> None:
        os.startfile(str(LOG_DIR))  # type: ignore[attr-defined]
