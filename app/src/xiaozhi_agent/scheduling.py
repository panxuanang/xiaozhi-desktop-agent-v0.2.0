from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


SELF_ALIASES = {"\u6211", "\u6211\u81ea\u5df1", "\u81ea\u5df1", "\u672c\u4eba", "\u8fd9\u91cc", "\u5f53\u524d\u5fae\u4fe1"}
_CN_DIGITS = {
    "\u96f6": 0,
    "\u3007": 0,
    "\u4e00": 1,
    "\u4e8c": 2,
    "\u4e24": 2,
    "\u4e09": 3,
    "\u56db": 4,
    "\u4e94": 5,
    "\u516d": 6,
    "\u4e03": 7,
    "\u516b": 8,
    "\u4e5d": 9,
}
_NUM = r"(?:\d+(?:\.\d+)?|[\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e]+|\u534a)"


@dataclass(slots=True)
class ScheduledRequest:
    scheduled_at: str
    command_text: str
    time_text: str
    kind: str
    recipient: str | None = None
    text: str | None = None
    delivery_channel: str | None = None


def _number(token: str) -> float:
    token = token.strip()
    if token == "\u534a":
        return 0.5
    try:
        return float(token)
    except ValueError:
        pass
    if not token:
        raise ValueError("empty number")
    total = 0
    current = 0
    for ch in token:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch == "\u5341":
            total += (current or 1) * 10
            current = 0
        elif ch == "\u767e":
            total += (current or 1) * 100
            current = 0
        else:
            raise ValueError(f"unsupported Chinese number: {token}")
    return float(total + current)


def _local_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.astimezone()
    return now


def _to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _relative_due(text: str, now: datetime) -> tuple[datetime, tuple[int, int], str] | None:
    pattern = re.compile(
        rf"(?P<phrase>(?:{_NUM}\s*(?:\u4e2a)?\s*(?:\u5929|\u5c0f\u65f6|\u949f\u5934|\u5206\u949f|\u5206|\u79d2\u949f|\u79d2)\s*)+)(?:\u4ee5\u540e|\u4e4b\u540e|\u540e)"
    )
    m = pattern.search(text)
    if not m:
        return None
    seconds = 0.0
    part_re = re.compile(rf"(?P<n>{_NUM})\s*(?:\u4e2a)?\s*(?P<u>\u5929|\u5c0f\u65f6|\u949f\u5934|\u5206\u949f|\u5206|\u79d2\u949f|\u79d2)")
    for p in part_re.finditer(m.group("phrase")):
        n = _number(p.group("n"))
        unit = p.group("u")
        if unit == "\u5929":
            seconds += n * 86400
        elif unit in {"\u5c0f\u65f6", "\u949f\u5934"}:
            seconds += n * 3600
        elif unit in {"\u5206\u949f", "\u5206"}:
            seconds += n * 60
        else:
            seconds += n
    if seconds <= 0:
        return None
    return now + timedelta(seconds=seconds), m.span(), m.group(0)


def _apply_daypart(hour: int, part: str) -> int:
    if part in {"\u4e0b\u5348", "\u665a\u4e0a", "\u4eca\u665a"} and hour < 12:
        return hour + 12
    if part == "\u4e2d\u5348" and hour < 11:
        return hour + 12
    if part == "\u51cc\u6668" and hour == 12:
        return 0
    return hour


def _clock_due(text: str, now: datetime) -> tuple[datetime, tuple[int, int], str] | None:
    tz = now.tzinfo
    iso = re.search(
        r"(?P<y>20\d{2})[-/]?(?P<mo>\d{1,2})[-/](?P<d>\d{1,2})\s*(?P<h>\d{1,2}):(?P<mi>\d{2})",
        text,
    )
    if iso:
        due = datetime(
            int(iso.group("y")), int(iso.group("mo")), int(iso.group("d")),
            int(iso.group("h")), int(iso.group("mi")), tzinfo=tz,
        )
        return due, iso.span(), iso.group(0)

    md = re.search(
        rf"(?P<mo>\d{{1,2}})\s*\u6708\s*(?P<d>\d{{1,2}})\s*\u65e5?\s*(?P<part>\u51cc\u6668|\u65e9\u4e0a|\u4e0a\u5348|\u4e2d\u5348|\u4e0b\u5348|\u665a\u4e0a)?\s*(?P<h>{_NUM})\s*\u70b9(?:\s*(?P<half>\u534a)|\s*(?P<mi>{_NUM})\s*\u5206?)?",
        text,
    )
    if md:
        year = now.year
        hour = _apply_daypart(int(_number(md.group("h"))), md.group("part") or "")
        minute = 30 if md.group("half") else int(_number(md.group("mi"))) if md.group("mi") else 0
        due = datetime(year, int(md.group("mo")), int(md.group("d")), hour, minute, tzinfo=tz)
        if due < now - timedelta(minutes=1):
            due = due.replace(year=year + 1)
        return due, md.span(), md.group(0)

    colon = re.search(
        r"(?P<day>\u4eca\u5929|\u660e\u5929|\u540e\u5929)?\s*(?P<part>\u51cc\u6668|\u65e9\u4e0a|\u4e0a\u5348|\u4e2d\u5348|\u4e0b\u5348|\u665a\u4e0a|\u4eca\u665a)?\s*(?P<h>\d{1,2}):(?P<mi>\d{2})",
        text,
    )
    if colon:
        day = colon.group("day") or ""
        part = colon.group("part") or ""
        offset = {"": 0, "\u4eca\u5929": 0, "\u660e\u5929": 1, "\u540e\u5929": 2}[day]
        hour = _apply_daypart(int(colon.group("h")), part)
        base = (now + timedelta(days=offset)).date()
        due = datetime(base.year, base.month, base.day, hour, int(colon.group("mi")), tzinfo=tz)
        if not day and due <= now:
            due += timedelta(days=1)
        return due, colon.span(), colon.group(0)

    point = re.search(
        rf"(?P<day>\u4eca\u5929|\u660e\u5929|\u540e\u5929)?\s*(?P<part>\u51cc\u6668|\u65e9\u4e0a|\u4e0a\u5348|\u4e2d\u5348|\u4e0b\u5348|\u665a\u4e0a|\u4eca\u665a)?\s*(?P<h>{_NUM})\s*\u70b9(?:\s*(?P<half>\u534a)|\s*(?P<mi>{_NUM})\s*\u5206?)?",
        text,
    )
    if point:
        day = point.group("day") or ""
        part = point.group("part") or ""
        if not day and not part and not re.search(r"(?:\u540e|\u53d1|\u63d0\u9192|\u6253\u5f00|\u5173\u95ed)", text):
            return None
        offset = {"": 0, "\u4eca\u5929": 0, "\u660e\u5929": 1, "\u540e\u5929": 2}[day]
        hour = _apply_daypart(int(_number(point.group("h"))), part)
        minute = 30 if point.group("half") else int(_number(point.group("mi"))) if point.group("mi") else 0
        base = (now + timedelta(days=offset)).date()
        due = datetime(base.year, base.month, base.day, hour, minute, tzinfo=tz)
        if not day and due <= now:
            due += timedelta(days=1)
        return due, point.span(), point.group(0)
    return None


def extract_schedule_time(text: str, now: datetime | None = None) -> tuple[str, str, str] | None:
    current = _local_now(now)
    found = _relative_due(text, current) or _clock_due(text, current)
    if not found:
        return None
    due, span, time_text = found
    command = (text[: span[0]] + " " + text[span[1] :]).strip()
    command = re.sub(r"^[\s\uff0c,\u3002.\uff1a:;\uff1b]+|[\s\uff0c,\u3002.]+$", "", command).strip()
    return _to_utc_iso(due), command, time_text


def _parse_send_command(command: str) -> tuple[str, str] | None:
    c = command.strip()
    patterns = [
        re.compile(
            r"^(?:\u8bf7)?(?:\u5e2e\u6211)?\u7ed9(?P<who>\u6211\u81ea\u5df1|\u6211|\u81ea\u5df1|\u672c\u4eba|[^\s\uff0c\u3002,:\uff1a\uff01!\uff1f?]{1,12})(?:\u7528\u5fae\u4fe1)?(?:\u53d1|\u53d1\u9001)(?:\u4e00\u6761|\u4e00\u53e5|\u4e00\u4e2a)?(?:\u6d88\u606f|\u6587\u5b57|\u5fae\u4fe1)?[\s\uff1a:\uff0c,]*(?P<body>.+)$"
        ),
        re.compile(
            r"^(?:\u8bf7)?(?:\u5e2e\u6211)?(?:\u7528\u5fae\u4fe1)?(?:\u53d1|\u53d1\u9001)(?:\u4e00\u6761|\u4e00\u53e5|\u4e00\u4e2a)?(?:\u6d88\u606f|\u6587\u5b57)?\u7ed9(?P<who>\u6211\u81ea\u5df1|\u6211|\u81ea\u5df1|\u672c\u4eba|[^\s\uff0c\u3002,:\uff1a\uff01!\uff1f?]{1,12})[\s\uff1a:\uff0c,]*(?P<body>.+)$"
        ),
    ]
    for pat in patterns:
        m = pat.match(c)
        if m:
            who = m.group("who").strip()
            body = m.group("body").strip().strip("\"'\u201c\u201d")
            body = re.sub(r"^(?:\u8bf4|\u5185\u5bb9)(?:\u662f)?[\uff1a:\s]*", "", body).strip()
            if body:
                return who, body
    remind = re.match(r"^(?:\u8bf7)?(?:\u5e2e\u6211)?(?:\u63d0\u9192\u6211|\u544a\u8bc9\u6211)[\s\uff1a:\uff0c,]*(?P<body>.+)$", c)
    if remind:
        body = remind.group("body").strip()
        if body:
            return "\u6211", body
    return None


def parse_scheduled_request(text: str, now: datetime | None = None) -> ScheduledRequest | None:
    extracted = extract_schedule_time(text, now=now)
    if not extracted:
        return None
    scheduled_at, command, time_text = extracted
    send = _parse_send_command(command)
    if send:
        recipient, body = send
        channel = "weixin" if recipient in SELF_ALIASES else "desktop_wechat"
        canonical_recipient = "\u6211" if recipient in SELF_ALIASES else recipient
        return ScheduledRequest(
            scheduled_at=scheduled_at,
            command_text=command,
            time_text=time_text,
            kind="send_text",
            recipient=canonical_recipient,
            text=body,
            delivery_channel=channel,
        )
    return ScheduledRequest(
        scheduled_at=scheduled_at,
        command_text=command,
        time_text=time_text,
        kind="local_command",
    )
