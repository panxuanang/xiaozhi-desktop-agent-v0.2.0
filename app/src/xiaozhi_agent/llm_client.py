from __future__ import annotations

import json
import re
from typing import Any, Protocol

import requests

from .deepseek_client import DeepSeekClient


class LLMClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 8192,
        json_mode: bool = False,
        timeout: float = 180,
    ) -> str: ...

    def json_chat(self, system: str, user: str, *, max_tokens: int = 4096) -> dict[str, Any]: ...
    def test(self) -> str: ...


class OpenAIResponsesClient:
    """Minimal OpenAI Responses API client using requests.

    Keeping this dependency-free makes the packaged runtime smaller and avoids
    binding the installer to one OpenAI SDK version.  The raw Responses API is
    stable enough for Planner/Direct text use and returns text in output items.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-5.6-sol",
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = requests.Session()

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        direct = data.get("output_text")
        if isinstance(direct, str) and direct:
            return direct
        chunks: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "refusal":
                    refusal = content.get("refusal") or content.get("text") or "OpenAI 模型拒绝了该请求"
                    raise RuntimeError(str(refusal))
                if content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
        if chunks:
            return "\n".join(chunks)
        raise RuntimeError(f"OpenAI 返回结构异常: {json.dumps(data, ensure_ascii=False)[:1200]}")

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 8192,
        json_mode: bool = False,
        timeout: float = 180,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("OpenAI API Key 未配置")
        instructions: list[str] = []
        inputs: list[dict[str, str]] = []
        for m in messages:
            role = str(m.get("role") or "user")
            content = str(m.get("content") or "")
            if role in {"system", "developer"}:
                instructions.append(content)
            else:
                inputs.append({"role": role if role in {"user", "assistant"} else "user", "content": content})
        body: dict[str, Any] = {
            "model": model or self.model,
            "input": inputs or "",
            "max_output_tokens": max_tokens,
            "store": False,
        }
        if instructions:
            body["instructions"] = "\n\n".join(instructions)
        # Planner JSON is prompted explicitly and parsed defensively.  We avoid
        # relying on provider-specific schema syntax so custom gateways can work.
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        resp = self.session.post(
            self.base_url + "/responses",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI HTTP {resp.status_code}: {resp.text[:1200]}")
        return self._extract_text(resp.json())

    def json_chat(self, system: str, user: str, *, max_tokens: int = 4096) -> dict[str, Any]:
        text = self.chat(
            [
                {"role": "system", "content": system + "\n只输出一个 JSON 对象，不要使用 Markdown 代码块。"},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            json_mode=True,
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise RuntimeError(f"OpenAI 模型没有返回 JSON: {text[:1000]}")
            return json.loads(match.group(0))

    def test(self) -> str:
        return self.chat(
            [
                {"role": "system", "content": "只回复 XIAOZHI_OPENAI_OK。"},
                {"role": "user", "content": "测试连接"},
            ],
            max_tokens=64,
            timeout=60,
        )


def create_client(
    provider: str,
    *,
    api_key: str,
    model: str,
    deepseek_base_url: str = "https://api.deepseek.com",
    openai_base_url: str = "https://api.openai.com/v1",
) -> LLMClient:
    provider = (provider or "deepseek").strip().lower()
    if provider == "deepseek":
        return DeepSeekClient(api_key, deepseek_base_url, model)
    if provider == "openai":
        return OpenAIResponsesClient(api_key, openai_base_url, model)
    raise RuntimeError(f"不支持的模型提供方: {provider}")
