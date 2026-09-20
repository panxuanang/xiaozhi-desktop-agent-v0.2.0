from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class InboundMessage:
    channel: str
    user_id: str
    message_id: str
    message_type: str
    text: str
    attachments: list[Path] = field(default_factory=list)
    context_token: str = ""
    received_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RouteDecision:
    route: str
    summary: str = ""
    local_action: str | None = None
    local_args: dict[str, Any] = field(default_factory=dict)
    review_contact: str | None = None
    delivery_contact: str | None = None
    delivery_mode: str | None = None
    delivery_channel: str | None = None
    completion_action: str | None = None
    scheduled_at: str | None = None
    scheduled_action: str | None = None
    scheduled_payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RouteDecision":
        data = dict(data or {})
        return cls(
            route=str(data.get("route") or "harness"),
            summary=str(data.get("summary") or ""),
            local_action=data.get("local_action"),
            local_args=data.get("local_args") if isinstance(data.get("local_args"), dict) else {},
            review_contact=data.get("review_contact"),
            delivery_contact=data.get("delivery_contact"),
            delivery_mode=data.get("delivery_mode"),
            delivery_channel=data.get("delivery_channel"),
            completion_action=data.get("completion_action"),
            scheduled_at=data.get("scheduled_at"),
            scheduled_action=data.get("scheduled_action"),
            scheduled_payload=data.get("scheduled_payload") if isinstance(data.get("scheduled_payload"), dict) else {},
            reason=str(data.get("reason") or ""),
        )


@dataclass(slots=True)
class TaskPlan:
    title: str
    understanding: str
    route: str
    steps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    estimated_minutes: int | None = None
    blockers: list[str] = field(default_factory=list)
    approval_prompt: str = "确认后我再开始执行。"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkResult:
    ok: bool
    text: str = ""
    files: list[Path] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
