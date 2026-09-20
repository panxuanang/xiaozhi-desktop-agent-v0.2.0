from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from xiaozhi_agent.router import IntentRouter
from xiaozhi_agent.planner import TaskPlanner
from xiaozhi_agent.models import InboundMessage, RouteDecision
from xiaozhi_agent.config import AppConfig, ConfigStore
from xiaozhi_agent.harness_worker import HarnessWorker
from xiaozhi_agent.secrets_store import SecretStore
from xiaozhi_agent.task_center import TaskCenter
from xiaozhi_agent.task_service import TaskService
import xiaozhi_agent.task_service as task_service_module
from xiaozhi_agent.llm_client import OpenAIResponsesClient
from xiaozhi_agent.scheduling import parse_scheduled_request
from xiaozhi_agent.weixin.channel import OFFICIAL_PLUGIN_VERSION, version_number


class NoNetworkClient:
    def json_chat(self, *_args, **_kwargs):
        raise AssertionError("heuristic route unexpectedly called network")


def check_task_center() -> None:
    # On Windows, an open sqlite3 connection keeps tasks.db locked.  Always close
    # TaskCenter *before* TemporaryDirectory tries to remove the directory.
    with tempfile.TemporaryDirectory(prefix="xiaozhi-selfcheck-", ignore_cleanup_errors=True) as td:
        root = Path(td)
        center = TaskCenter(root / "tasks.db")
        try:
            center.create_task(
                task_id="TASK-TEST", workspace=str(root), channel="weixin", channel_user_id="u1",
                source_message_id="m1", context_token="ctx", original_message="test", input_files=[]
            )
            center.update_task(
                "TASK-TEST", status="awaiting_approval",
                plan={"title": "Selfcheck plan", "steps": ["one"]},
                decision={"route": "local", "local_action": "open_app", "local_args": {"name": "微信"}},
                planner_provider="deepseek", planner_model="deepseek-flash",
            )
            planned = center.get_task("TASK-TEST")
            assert planned and planned["plan"]["title"] == "Selfcheck plan"
            assert planned["decision"]["local_action"] == "open_app"
            out = root / "a.txt"
            out.write_text("hello", encoding="utf-8")
            version, digest = center.add_version("TASK-TEST", [out])
            assert version == 1 and len(digest) == 64
            approved = center.approve_version("TASK-TEST")
            assert approved["version"] == 1 and approved["hash"] == digest
            verified = center.verified_approved_version("TASK-TEST")
            assert verified["version"] == 1 and verified["hash"] == digest
        finally:
            center.close()



def check_secret_store() -> None:
    with tempfile.TemporaryDirectory(prefix="xiaozhi-secret-selfcheck-", ignore_cleanup_errors=True) as td:
        path = Path(td) / "secrets.json"
        store = SecretStore(path)
        secret = "sk-xiaozhi-selfcheck-中文-123456"
        store.set("deepseek_api_key", secret)
        assert path.exists(), "secret store file was not created"
        raw = path.read_text(encoding="utf-8")
        if sys.platform == "win32":
            assert "dpapi:" in raw, "Windows secret was not DPAPI-protected"
            assert secret not in raw, "plaintext secret leaked to disk"
        assert store.get("deepseek_api_key") == secret, "secret round-trip mismatch"
        assert store.has("deepseek_api_key"), "stored secret not reported as present"
        store.set("openai_api_key", "openai-selfcheck-secret-123")
        assert store.get("openai_api_key") == "openai-selfcheck-secret-123"
        store.set("openai_api_key", "")
        store.set("deepseek_api_key", "")
        assert not store.has("deepseek_api_key"), "secret delete failed"
    print("DPAPI_ROUNDTRIP_OK" if sys.platform == "win32" else "SECRET_STORE_ROUNDTRIP_OK")

def check_router() -> None:
    router = IntentRouter(NoNetworkClient())
    assert router.route("打开微信", []).route == "local"
    assert router.plan_local("打开微信", Path(".")) == ("open_app", {"name": "微信"})
    assert router.plan_local("关闭 Excel", Path(".")) == ("close_app", {"name": "excel"})
    assert router.route("研究这个行业，做一份 PPT", []).route == "harness"
    assert router.route("帮我写一份通知", []).route == "direct"



def check_planner() -> None:
    class FakePlannerClient:
        def json_chat(self, *_args, **_kwargs):
            return {
                "title": "打开微信",
                "understanding": "用户希望打开本机微信。",
                "steps": ["调用本地白名单打开微信"],
                "recommendations": [],
                "risks": [],
                "expected_outputs": ["微信窗口打开"],
                "estimated_minutes": 1,
                "approval_prompt": "确认后执行。",
            }
    plan = TaskPlanner(FakePlannerClient()).plan(
        request="打开微信",
        decision=RouteDecision(route="local", summary="确定性本地操作", local_action="open_app", local_args={"name": "微信"}),
        attachments=[],
    )
    assert plan.route == "local"
    assert plan.steps and plan.title == "打开微信"
    print("PLANNER_SELF_CHECK_OK")



def check_openai_responses_client() -> None:
    class FakeResponse:
        status_code = 200
        text = ''
        def json(self):
            return {"output": [{"content": [{"type": "output_text", "text": "XIAOZHI_OPENAI_OK"}]}]}

    class FakeSession:
        def __init__(self):
            self.calls = []
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse()

    client = OpenAIResponsesClient("sk-test", model="gpt-5.6-sol")
    fake = FakeSession()
    client.session = fake
    assert client.test() == "XIAOZHI_OPENAI_OK"
    assert fake.calls, "OpenAI client made no request"
    url, kwargs = fake.calls[-1]
    assert url.endswith("/responses")
    body = kwargs["json"]
    assert body["model"] == "gpt-5.6-sol"
    assert body["store"] is False
    assert body["input"][0]["role"] == "user"
    assert "instructions" in body

    class JsonResponse:
        status_code = 200
        text = ''
        def json(self):
            return {"output": [{"content": [{"type": "output_text", "text": "{\"ok\": true}"}]}]}
    fake_json = FakeSession()
    fake_json.post = lambda url, **kwargs: (fake_json.calls.append((url, kwargs)) or JsonResponse())
    client.session = fake_json
    parsed = client.json_chat("Return JSON", "test")
    assert parsed == {"ok": True}
    assert fake_json.calls[-1][1]["json"]["text"]["format"]["type"] == "json_object"
    print("OPENAI_RESPONSES_SELF_CHECK_OK")


def check_plan_gate() -> None:
    class FakeChannel:
        def __init__(self):
            self.last_context_by_user = {}
        def send_text(self, *_args, **_kwargs):
            return {"ret": 0}

    class FakePlannerClient:
        def json_chat(self, system, user, **_kwargs):
            # The schedule itself is routed deterministically; the model only
            # turns it into a user-facing plan.
            return {
                "title": "五分钟后微信提醒",
                "understanding": "五分钟后通过当前微信会话发送指定文字。",
                "steps": ["等待到计划时间", "发送指定文字"],
                "recommendations": [],
                "risks": [],
                "expected_outputs": ["微信文字消息"],
                "estimated_minutes": 5,
                "approval_prompt": "确认后创建定时动作。",
            }

    with tempfile.TemporaryDirectory(prefix="xiaozhi-plan-gate-", ignore_cleanup_errors=True) as td:
        root = Path(td)
        center = TaskCenter(root / "tasks.db")
        cfg_store = ConfigStore(root / "config.json")
        cfg_store.save(AppConfig(workspace=str(root), planner_provider="deepseek", planner_model="deepseek-flash"))
        secret_store = SecretStore(root / "secrets.json")
        secret_store.set("deepseek_api_key", "deepseek-plan-selfcheck-secret")
        service = TaskService(center, cfg_store, secret_store, FakeChannel())
        original_create_client = task_service_module.create_client
        task_service_module.create_client = lambda *a, **k: FakePlannerClient()
        try:
            msg = InboundMessage(
                channel="weixin", user_id="u-plan", message_id="m-plan",
                message_type="text", text="五分钟后给我发一句 今天过得好吗",
                attachments=[], context_token="ctx-plan",
                received_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            )
            planned = service.submit_message(msg, notify=False)
            assert planned["status"] == "awaiting_approval"
            assert planned["route"] == "local"
            assert planned["scheduled_action"] is None, "executable schedule was materialized before approval"
            assert planned["decision"]["scheduled_action"] == "weixin_send_text"
            approved = service.approve_plan(planned["task_id"], msg)
            assert approved["status"] == "queued"
            assert approved["scheduled_action"] == "weixin_send_text"
            assert approved["scheduled_payload"]["text"] == "今天过得好吗"
        finally:
            task_service_module.create_client = original_create_client
            service.pool.shutdown(wait=True, cancel_futures=True)
            center.close()
    print("PLAN_GATE_SELF_CHECK_OK")

def check_schedule_parser() -> None:
    fixed = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
    req = parse_scheduled_request("\u4e94\u5206\u949f\u540e\u7ed9\u6211\u53d1\u4e00\u53e5 \u4eca\u5929\u8fc7\u5f97\u597d\u5417", now=fixed)
    assert req is not None
    assert req.kind == "send_text"
    assert req.recipient == "\u6211"
    assert req.delivery_channel == "weixin"
    assert req.text == "\u4eca\u5929\u8fc7\u5f97\u597d\u5417"
    assert req.scheduled_at == "2026-09-20T10:05:00+00:00"

    named = parse_scheduled_request("5\u5206\u949f\u540e\u7ed9\u738b\u603b\u53d1\u4e00\u53e5 \u4eca\u5929\u8fc7\u5f97\u597d\u5417", now=fixed)
    assert named is not None
    assert named.recipient == "\u738b\u603b"
    assert named.delivery_channel == "desktop_wechat"

    local = parse_scheduled_request("5\u5206\u949f\u540e\u6253\u5f00\u5fae\u4fe1", now=fixed)
    assert local is not None and local.kind == "local_command"
    assert local.command_text == "\u6253\u5f00\u5fae\u4fe1"

    router = IntentRouter(NoNetworkClient())
    decision = router.route("5\u5206\u949f\u540e\u7ed9\u6211\u53d1\u4e00\u53e5 \u4eca\u5929\u8fc7\u5f97\u597d\u5417", [])
    assert decision.route == "local"
    assert decision.scheduled_action == "weixin_send_text"
    assert decision.delivery_channel == "weixin"
    local_decision = router.route("5\u5206\u949f\u540e\u6253\u5f00\u5fae\u4fe1", [])
    assert local_decision.route == "local"
    assert local_decision.local_action == "schedule_local_command"
    print("SCHEDULE_ROUTING_SELF_CHECK_OK")


def check_scheduled_delivery() -> None:
    class FakeChannel:
        def __init__(self):
            self.sent = []
            self.last_context_by_user = {}

        def send_text(self, user_id, text, context_token=""):
            self.sent.append((str(user_id), str(text), str(context_token)))
            return {"ret": 0}

    class FakeDesktop:
        def __init__(self):
            self.sent = []

        def send_text(self, contact, text):
            self.sent.append((str(contact), str(text)))

        def send_files(self, contact, files):
            raise AssertionError("scheduled text self-check must not send files")

    with tempfile.TemporaryDirectory(prefix="xiaozhi-schedule-selfcheck-", ignore_cleanup_errors=True) as td:
        root = Path(td)
        center = TaskCenter(root / "tasks.db")
        channel = FakeChannel()
        service = TaskService(center, ConfigStore(root / "config.json"), SecretStore(root / "secrets.json"), channel)
        desktop = FakeDesktop()
        service.wechat_driver = desktop
        past = "2000-01-01T00:00:00+00:00"
        try:
            center.create_task(
                task_id="TASK-SCHEDULE-SELF", workspace=str(root), channel="weixin", channel_user_id="u1",
                source_message_id="m1", context_token="ctx1", original_message="scheduled self", input_files=[]
            )
            center.update_task(
                "TASK-SCHEDULE-SELF", route="local", delivery_contact="\u6211", delivery_channel="weixin",
                delivery_mode="scheduled", completion_action="send", scheduled_at=past,
                scheduled_action="weixin_send_text", scheduled_payload={"text": "\u4eca\u5929\u8fc7\u5f97\u597d\u5417"},
                status="queued", current_stage="scheduled_waiting",
            )
            center.create_task(
                task_id="TASK-SCHEDULE-CONTACT", workspace=str(root), channel="weixin", channel_user_id="u1",
                source_message_id="m2", context_token="ctx2", original_message="scheduled contact", input_files=[]
            )
            center.update_task(
                "TASK-SCHEDULE-CONTACT", route="local", delivery_contact="\u738b\u603b", delivery_channel="desktop_wechat",
                delivery_mode="scheduled", completion_action="send", scheduled_at=past,
                scheduled_action="wechat_send_text", scheduled_payload={"text": "hello"},
                status="queued", current_stage="scheduled_waiting",
            )
            service.run_due_deliveries()
            own = center.get_task("TASK-SCHEDULE-SELF")
            contact = center.get_task("TASK-SCHEDULE-CONTACT")
            assert own and own["status"] == "completed" and own["delivered_at"]
            assert contact and contact["status"] == "completed" and contact["delivered_at"]
            assert ("u1", "\u4eca\u5929\u8fc7\u5f97\u597d\u5417", "ctx1") in channel.sent
            assert ("\u738b\u603b", "hello") in desktop.sent
            assert center.scheduled_ready(datetime.now(timezone.utc).isoformat(timespec="seconds")) == []
        finally:
            service.pool.shutdown(wait=True, cancel_futures=True)
            center.close()
    print("SCHEDULE_DELIVERY_SELF_CHECK_OK")

def check_harness_policy() -> None:
    with tempfile.TemporaryDirectory(prefix="xiaozhi-harness-policy-", ignore_cleanup_errors=True) as td:
        root = Path(td)
        cfg = AppConfig(
            workspace=str(root),
            harness_safe_mode=True,
            allow_harness_full_access_fallback=True,
        )
        worker = HarnessWorker(cfg, "sk-selfcheck")
        assert worker._select_modes("nt") == ["danger-full-access"]
        assert worker._select_modes("posix") == ["workspace-write", "danger-full-access"]
        cfg.allow_harness_full_access_fallback = False
        assert worker._select_modes("nt") == ["workspace-write"]
        cfg.harness_safe_mode = False
        assert worker._select_modes("nt") == ["danger-full-access"]

        legacy = root / "fake.doc"
        legacy.write_text("<html>fake word</html>", encoding="utf-8")
        ok, problems = worker._verify_outputs([legacy], request_text="collect notes into Word")
        assert not ok and any(".doc" in item for item in problems)

        from docx import Document
        good = root / "good.docx"
        doc = Document()
        doc.add_paragraph("selfcheck")
        doc.save(good)
        ok, problems = worker._verify_outputs([good], request_text="collect notes into Word")
        assert ok, problems

        assert worker._looks_like_execution_degraded("error: --profile <name> is required")
        assert worker._looks_like_execution_degraded("requires approval, but no approval channel is available")
        first = worker._attempt_session_id("s1", "workspace-write", 0)
        second = worker._attempt_session_id("s1", "danger-full-access", 1)
        assert first == "s1" and second != first
    print("HARNESS_POLICY_SELF_CHECK_OK")


def main() -> int:
    cfg = AppConfig()
    assert cfg.approval_mode == "plan_first"
    assert cfg.planner_provider == "deepseek" and cfg.direct_provider == "deepseek"
    check_secret_store()
    check_task_center()
    check_router()
    check_planner()
    check_openai_responses_client()
    check_plan_gate()
    check_schedule_parser()
    check_scheduled_delivery()
    check_harness_policy()
    assert OFFICIAL_PLUGIN_VERSION == "2.4.9"
    assert version_number("2.4.9") == 132105
    print("SELF_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
