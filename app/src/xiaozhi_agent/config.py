from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .paths import CONFIG_FILE


@dataclass(slots=True)
class AppConfig:
    workspace: str = ""

    # Planner / Direct can use different providers.  Harness remains on the
    # DeepSeek Harness runtime and therefore keeps its own DeepSeek model.
    planner_provider: str = "deepseek"
    planner_model: str = "deepseek-flash"
    direct_provider: str = "deepseek"
    direct_model: str = "deepseek-flash"
    model: str = "deepseek-flash"  # Harness model (backward-compatible name)

    deepseek_base_url: str = "https://api.deepseek.com"
    openai_base_url: str = "https://api.openai.com/v1"

    # plan_first: every substantive request is planned and must be approved.
    # auto_low_risk: reserved for users who explicitly want safe local actions
    # to run immediately; v0.2 still defaults to plan_first.
    approval_mode: str = "plan_first"

    harness_safe_mode: bool = True
    allow_harness_full_access_fallback: bool = True
    auto_start_weixin: bool = True
    auto_start_windows: bool = False
    web_port: int = 8765
    max_workers: int = 2
    speech_language: str = "zh-CN"
    last_updated: str = ""

    @property
    def workspace_path(self) -> Path | None:
        if not self.workspace:
            return None
        return Path(os.path.expandvars(os.path.expanduser(self.workspace))).resolve()


class ConfigStore:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = {f.name for f in fields(AppConfig)}
            cfg = AppConfig(**{k: v for k, v in data.items() if k in allowed})
            # Upgrade old configs in-memory without breaking existing installs.
            if not cfg.planner_model:
                cfg.planner_model = cfg.model or "deepseek-flash"
            if not cfg.direct_model:
                cfg.direct_model = cfg.model or "deepseek-flash"
            return cfg
        except Exception:
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def update(self, **patch: Any) -> AppConfig:
        cfg = self.load()
        allowed = {f.name for f in fields(AppConfig)}
        for key, value in patch.items():
            if key in allowed:
                setattr(cfg, key, value)
        self.save(cfg)
        return cfg
