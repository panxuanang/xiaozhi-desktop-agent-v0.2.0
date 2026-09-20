# GitHub Windows 构建说明 — v0.2.0

1. 新建/清空一个 GitHub 仓库。
2. 将 `xiaozhi-desktop-agent-v0.2.0` 文件夹**里面**的内容上传到仓库根目录。
3. 打开 `Actions -> Build Windows Installer -> Run workflow`。
4. 下载 Artifact `XiaoZhiSetup-windows-x64`，解压得到 `XiaoZhiSetup.exe`。

构建仍使用 Python 3.12 私有运行时、固定 `deepseek-harness-sdk==0.1.5rc1`、Inno Setup 单 EXE 安装器，并在 Windows Runner 上执行安装后 smoke test。

v0.2.0 的源码 selfcheck 额外覆盖：

- TaskPlan / RouteDecision SQLite round-trip
- Planner 数据结构
- 定时 Local 路由与交付
- Windows Harness 权限策略
- DPAPI secret round-trip（包括 DeepSeek/OpenAI secret store 路径）

安装成功后，DeepSeek/OpenAI Key 在 UI 里分别保存；Planner/Direct 可以各自选择 provider/model，Harness 模型单独配置。

## v0.2.0 新增自检

构建阶段和安装后的 smoke test 都会运行 `scripts/selfcheck.py`。正常应包含：

```text
DPAPI_ROUNDTRIP_OK
PLANNER_SELF_CHECK_OK
OPENAI_RESPONSES_SELF_CHECK_OK
PLAN_GATE_SELF_CHECK_OK
SCHEDULE_ROUTING_SELF_CHECK_OK
SCHEDULE_DELIVERY_SELF_CHECK_OK
HARNESS_POLICY_SELF_CHECK_OK
SELF_CHECK_OK
```

`PLAN_GATE_SELF_CHECK_OK` 会验证：任务在用户确认前只保存计划和 `RouteDecision`，不会提前把定时/本地动作变成可执行任务。
