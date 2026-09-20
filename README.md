# 小智电脑助手 v0.2.0

Windows 本地 AI 工作台：**微信远程入口 + 电脑本地对话/语音 + Planner 审批 + Task Center + Local / Direct / Harness + 文件版本与交付**。

## v0.2.0 的核心变化

所有新任务默认先走：

```text
微信 / 电脑文字 / 电脑语音
        ↓
Task Center 创建 task_id
        ↓
Planner 大模型理解需求
        ↓
固定 RouteDecision + TaskPlan
        ↓
awaiting_approval
        ↓
你确认“开始执行”
        ↓
Local / Direct / DeepSeek Harness
        ↓
结果 / V1 文件
        ↓
最终版本审批 / SHA256 / 交付
```

不会再收到一句复杂需求就立刻执行。计划和真正执行使用同一个持久化的 `RouteDecision`，批准后不会重新让模型偷偷换方案。

## 桌面工作台

安装后打开 `小智电脑助手`，主界面包含：

- **对话**：电脑直接输入任务、拖文件、选文件、麦克风语音转文字；计划卡可开始/修改/取消。
- **任务**：查看任务来源、状态、路由、模型、版本、最后动作、错误。
- **自动化**：查看已经持久化的定时任务。
- **渠道**：微信扫码、监听状态、电脑语音说明。
- **设置**：工作空间、DeepSeek/OpenAI API、Planner/Direct/Harness 模型、Harness 权限模式。

语音输入使用 Edge/Chrome 的 Web Speech 能力，只负责把语音变成文字。**语音不会绕过 Planner 审批。**

## 模型分工

v0.2.0 把模型拆开：

| 层 | 可选提供方 | 作用 |
|---|---|---|
| Planner | DeepSeek / OpenAI | 理解、整理建议、生成执行前计划、辅助路由 |
| Direct | DeepSeek / OpenAI | 通知、总结、文案、一次模型即可完成的任务 |
| Harness | DeepSeek（当前固定） | 复杂研究、Excel/Word/PPT、写代码、运行、修复、验证 |

OpenAI 通过 Responses API 调用。界面可直接填写 `gpt-5.6-sol` 或 `gpt-5.6` 作为 Planner/Direct 模型。

**本版本不把 GPT-5.6 Sol 强塞进现有 Harness 0.1.5rc1。** 当前打包的 Harness 使用已验证的 `sdk` profile + `deepseek-official` 路线。这样 Planner 可以先用 GPT-5.6 Sol，而复杂执行仍保持已跑通的 DeepSeek Harness。

## API Key

DeepSeek 和 OpenAI API Key 都写入 `%LOCALAPPDATA%\XiaoZhiAssistant` 下的 secret store；Windows 上使用当前用户 DPAPI 加密，网页不会回读明文 Key。

## 微信审批示例

你：

```text
把下载目录里的销售表整理一下，分析异常，做一份 PPT
```

小智先回复：

```text
🧭 TASK-... 执行前计划
任务：销售数据整理与异常分析
理解：...
执行方式：Harness 复杂任务
将要做：
1. ...
2. ...

回复「开始执行」继续；回复「修改：……」调整计划；回复「取消」停止。
```

你：`开始执行`

任务才真正开始。

文件生成后仍进入 `awaiting_review`。你批准 V2 后，Task Center 锁定 `approved_version=V2` 与 SHA256，交付时发送的必须是这个原文件。

## 定时任务

例如微信里：

```text
五分钟后给我发一句 今天过得好吗
```

也先生成计划。确认后才把 `scheduled_at / scheduled_action / payload` 写成可执行定时动作。

电脑对话里说“5分钟后提醒我休息”，会落成电脑端提醒，不会把桌面伪用户当作微信用户。

## Harness 与 Office

- 固定 `deepseek-harness-sdk==0.1.5rc1`，不自动升级。
- Windows 兼容模式继续支持已验证的 `danger-full-access` 诊断/个人 MVP 路线。
- `.doc` 或 HTML 伪装 Word 不接受；Word 任务必须产出可被 `python-docx` 重开的真实 `.docx`。
- Excel/Word/PPT 会使用安装的 Python 库做结构重开验证。
- 官方 Office Skills 构建时优先获取，失败时使用项目内 fallback Skills。

## GitHub 编译

将本 ZIP **里面的文件**放到 GitHub 仓库根目录，确保首页直接看到：

```text
.github/
app/
installer/
scripts/
requirements-runtime.txt
VERSION
```

然后：

```text
Actions
→ Build Windows Installer
→ Run workflow
```

成功后 Artifact 为 `XiaoZhiSetup-windows-x64`，其中包含 `XiaoZhiSetup.exe`。

如果推送 tag，例如 `v0.2.0`，Workflow 也会把 EXE 附到 GitHub Release。

## Windows 安装

安装器使用当前用户目录，不要求管理员权限。开机自启使用：

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\XiaoZhiAssistant
```

不再写 Windows Startup 文件夹快捷方式。

## 安全边界

当前 Harness full-access 是个人 MVP 的兼容方案，不是最终商业隔离设计。确定性本地操作优先走白名单 Driver。删除、注册表修改、格式化、自动交易等高风险动作不应由通用 Agent 无确认执行。

参见 `BUILD_FIXES_v0.2.0.md`。
