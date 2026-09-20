# Runtime fixes in v0.1.5

This release fixes deterministic scheduled actions and keeps the v0.1.4 Harness/Office validation protections.

## What the real test exposed

A message such as `five minutes later send me a sentence` was routed to Harness. The task then completed immediately as a Harness task instead of being persisted as a local future action. That violated two product rules: deterministic work should stay local, and a future promise must be stored before the assistant confirms it.

## v0.1.5 changes

1. Added a deterministic schedule parser for common relative and absolute times. It handles examples such as 5 minutes later, 1 hour 30 minutes later, tomorrow 9:00, and common Chinese clock expressions.
2. Scheduled text to `me` is route=`local`, stored in Task Center first, and later sent through the current Weixin/Claw channel. It does not launch Harness and does not need the desktop WeChat UI.
3. Scheduled text to a named real contact is route=`local`, stored first, and later sent by the desktop WeChat Driver. At due time the Driver opens/focuses the logged-in desktop WeChat client, searches the exact contact, and sends the exact stored text. If no visible WeChat window exists, the Driver first attempts to launch Weixin.exe/WeChat.exe and waits for a UIA-visible logged-in window.
4. Common scheduled local operations such as `five minutes later open WeChat` are planned once, persisted as a concrete Local Driver action, and executed by the local scheduler at the due time.
5. Scheduled tasks remain `queued / scheduled_waiting` until their due time. They are no longer marked `completed` immediately.
6. Task Center schema migration adds `delivery_channel`, `scheduled_action`, `scheduled_payload`, claim time, and attempt count without deleting existing tasks.
7. The scheduler ticks every 5 seconds and claims due actions before execution. Completed deliveries are marked with `delivered_at`; failed actions fail loudly instead of silently falling back to Harness.
8. Local deterministic routing can work without a DeepSeek API call. DeepSeek/Harness tasks still require the API key and Harness workspace.
9. The control UI now shows scheduled time and delivery channel so `weixin:me` and `desktop_wechat:contact` are distinguishable.
10. CI self-checks cover schedule parsing, no-network local routing, Task Center persistence/migration, and due delivery through fake Weixin/Desktop-WeChat endpoints before the installer is accepted.

## Delivery semantics

- `send me ...` from the Claw conversation means the current Weixin channel. No desktop WeChat window is required.
- `send Wang ...` means a real WeChat contact and uses the desktop WeChat Driver.
- Existing approval/version/SHA256 rules remain unchanged for generated files.
