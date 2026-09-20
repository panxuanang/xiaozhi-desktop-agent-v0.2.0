from __future__ import annotations

import json
import re
from typing import Any

import requests


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com", model: str = "deepseek-flash"):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = requests.Session()

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
            raise RuntimeError("DeepSeek API Key 未配置")
        body: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = self.session.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:1000]}")
        data = resp.json()
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except Exception as exc:
            raise RuntimeError(f"DeepSeek 返回结构异常: {json.dumps(data, ensure_ascii=False)[:1200]}") from exc

    def json_chat(self, system: str, user: str, *, max_tokens: int = 4096) -> dict[str, Any]:
        text = self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            json_mode=True,
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise RuntimeError(f"模型没有返回 JSON: {text[:1000]}")
            return json.loads(match.group(0))

    def test(self) -> str:
        return self.chat(
            [
                {"role": "system", "content": "只回复 XIAOZHI_API_OK。"},
                {"role": "user", "content": "测试连接"},
            ],
            max_tokens=32,
            timeout=45,
        )
