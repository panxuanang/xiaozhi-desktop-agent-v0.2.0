# Third-party components

This project builds an installer around several independently licensed components.

- DeepSeek Harness Python SDK/runtime (`deepseek-harness-sdk==0.1.5rc1`) — DeepSeek AI, MIT.
- Tencent `openclaw-weixin` protocol behavior referenced by the local WeixinChannel implementation — Tencent, client repository MIT. Server-side iLink service rights are separate from the client source license.
- `@deepseek-ai/dsh-skill-office@0.1.6-alpha.2` — optional build-time fetch. The build script copies the package's license when present.
- Python and PyPI libraries listed in `requirements-runtime.txt` retain their respective licenses.

Before redistributing commercially, generate and review a complete third-party notices bundle from the exact built dependency set and separately confirm server-side service terms for iLink/Weixin.
