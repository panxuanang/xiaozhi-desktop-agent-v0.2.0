# v0.2.0 — Planner-first desktop workbench

This release turns the v0.1.x control page into a task workbench while preserving the validated Task Center / WeixinChannel / Local Driver / DeepSeek Harness chain.

## Added

- Unified Planner gate for Weixin and desktop requests.
- Durable `TaskPlan` and exact `RouteDecision` stored in SQLite before execution.
- New states: `awaiting_approval` before execution and existing `awaiting_review` after file generation.
- Weixin commands: `开始执行`, `修改：...`, `取消` for plan control.
- Desktop chat, file attachment upload, drag-and-drop, and voice-to-text via Edge/Chrome Web Speech.
- Modern five-section UI: 对话 / 任务 / 自动化 / 渠道 / 设置.
- Separate Planner, Direct and Harness model settings.
- Planner/Direct can use DeepSeek or OpenAI Responses API.
- OpenAI API key is stored with the same Windows DPAPI secret store as DeepSeek.
- GPT-5.6 Sol model ids can be selected for Planner/Direct.
- Harness remains pinned to `deepseek-harness-sdk==0.1.5rc1` and DeepSeek provider in this release so the already-validated Windows execution path is not destabilized.
- Desktop-origin reminders to “我” become desktop notifications/tasks instead of attempting to send to a fake Weixin user id.

## Approval semantics

Execution approval and deliverable approval are deliberately separate:

1. Incoming request -> Planner -> `awaiting_approval`.
2. User approves -> exact stored decision executes.
3. File-producing task -> immutable V1/V2 snapshot -> `awaiting_review`.
4. User approves a version -> Task Center stores approved version + SHA256 before delivery.

The executor does not rerun the router after approval, so the action the user approved is the action that is executed.

## CI gates added

- `OPENAI_RESPONSES_SELF_CHECK_OK`: validates the packaged OpenAI Responses request shape without network access.
- `PLAN_GATE_SELF_CHECK_OK`: verifies a scheduled Weixin request remains `awaiting_approval` and has no executable schedule fields until approval; approval materializes the exact stored decision.
- Browser UI JavaScript is kept dependency-free so the existing single-EXE Python/Inno build remains simple.
