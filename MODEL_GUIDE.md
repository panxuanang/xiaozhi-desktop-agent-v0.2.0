# 模型配置说明（v0.2.0）

小智把模型分成三层，避免为了换一个模型而破坏已经验证过的执行链。

## Planner

负责：理解用户需求、给出执行前建议、生成 TaskPlan、辅助判断 local/direct/harness。

可选：

- DeepSeek：例如 `deepseek-flash`
- OpenAI：例如 `gpt-5.6-sol`（或 `gpt-5.6` 别名）

如果希望“先想清楚、再让我确认”，可以把 Planner 设置成 GPT-5.6 Sol。

## Direct

负责：一次模型调用即可完成的通知、总结、文案、改写、材料等。

可选 DeepSeek 或 OpenAI，模型可与 Planner 不同。例如 Planner 用 Sol，Direct 用成本更低的模型。

## Harness

负责复杂自主任务：联网研究、复杂 Excel、Word/PPT、写代码、运行、报错修复、验证。

本项目继续固定：

- `deepseek-harness-sdk==0.1.5rc1`
- `deepseek-harness-runtime-bin==0.1.5rc1`
- 已验证的 `sdk` profile / `deepseek-official` 路线

因此 v0.2.0 的 Harness 设置只接受 DeepSeek 模型。不要把 Planner/Direct 的 OpenAI Provider 设置误认为 Harness Provider。

上游新版 DeepSeek Harness 已提供多 Provider/自定义 Provider 能力，但把 OpenAI 接进 Harness 需要单独配置 provider/profile/credential，并重新验证 Windows sandbox、Shell、Office Skills、长任务闭环；这不属于 v0.2.0 的默认稳定路径。
