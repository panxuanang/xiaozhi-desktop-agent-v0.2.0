from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import DB_FILE, VERSIONS_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def version_digest(files: list[Path]) -> str:
    rows = []
    for p in sorted(files, key=lambda x: x.name.lower()):
        rows.append({"name": p.name, "sha256": file_sha256(p)})
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TaskCenter:
    def __init__(self, path: Path = DB_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.versions_dir = VERSIONS_DIR if path == DB_FILE else path.parent / "versions"
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        """Flush and close SQLite so Windows can release tasks.db deterministically."""
        with self.lock:
            conn = getattr(self, "conn", None)
            if conn is None:
                return
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            try:
                conn.close()
            finally:
                self.conn = None  # type: ignore[assignment]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _migrate(self) -> None:
        with self.lock, self.conn:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    harness_session_id TEXT,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    channel TEXT,
                    channel_user_id TEXT,
                    source_message_id TEXT,
                    context_token TEXT,
                    original_message TEXT,
                    input_files_json TEXT NOT NULL DEFAULT '[]',
                    output_files_json TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL DEFAULT 0,
                    approved_version INTEGER,
                    approved_hash TEXT,
                    review_contact TEXT,
                    delivery_contact TEXT,
                    delivery_mode TEXT,
                    delivery_channel TEXT,
                    completion_action TEXT,
                    scheduled_at TEXT,
                    scheduled_action TEXT,
                    scheduled_payload_json TEXT NOT NULL DEFAULT '{}',
                    scheduled_claimed_at TEXT,
                    scheduled_attempts INTEGER NOT NULL DEFAULT 0,
                    current_stage TEXT,
                    latest_action TEXT,
                    last_activity_at TEXT,
                    error TEXT,
                    route TEXT,
                    route_summary TEXT,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    result_text TEXT,
                    approval_required INTEGER NOT NULL DEFAULT 1,
                    approved_at TEXT,
                    planner_provider TEXT,
                    planner_model TEXT,
                    delivered_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_user_created
                    ON tasks(channel_user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_status
                    ON tasks(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS task_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    files_json TEXT NOT NULL,
                    aggregate_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, version)
                );

                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    contact TEXT,
                    channel TEXT,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_task_columns()

    def _ensure_task_columns(self) -> None:
        """Forward-only schema migration for installations created by older MVPs."""
        rows = self.conn.execute("PRAGMA table_info(tasks)").fetchall()
        existing = {str(row[1]) for row in rows}
        migrations = {
            "delivery_channel": "TEXT",
            "scheduled_action": "TEXT",
            "scheduled_payload_json": "TEXT NOT NULL DEFAULT '{}'",
            "scheduled_claimed_at": "TEXT",
            "scheduled_attempts": "INTEGER NOT NULL DEFAULT 0",
            "plan_json": "TEXT NOT NULL DEFAULT '{}'",
            "decision_json": "TEXT NOT NULL DEFAULT '{}'",
            "result_text": "TEXT",
            "approval_required": "INTEGER NOT NULL DEFAULT 1",
            "approved_at": "TEXT",
            "planner_provider": "TEXT",
            "planner_model": "TEXT",
        }
        for name, ddl in migrations.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for key in ("input_files_json", "output_files_json"):
            raw = data.pop(key, "[]")
            try:
                data[key.removesuffix("_json")] = json.loads(raw or "[]")
            except Exception:
                data[key.removesuffix("_json")] = []
        raw_payload = data.pop("scheduled_payload_json", "{}")
        try:
            data["scheduled_payload"] = json.loads(raw_payload or "{}")
        except Exception:
            data["scheduled_payload"] = {}
        for source_key, target_key in (("plan_json", "plan"), ("decision_json", "decision")):
            raw = data.pop(source_key, "{}")
            try:
                data[target_key] = json.loads(raw or "{}")
            except Exception:
                data[target_key] = {}
        data["approval_required"] = bool(data.get("approval_required", 1))
        return data

    def create_task(
        self,
        *,
        task_id: str,
        workspace: str,
        channel: str,
        channel_user_id: str,
        source_message_id: str,
        context_token: str,
        original_message: str,
        input_files: list[str],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO tasks (
                    task_id, harness_session_id, workspace, status, channel,
                    channel_user_id, source_message_id, context_token,
                    original_message, input_files_json, output_files_json,
                    current_stage, latest_action, last_activity_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    f"xiaozhi-{task_id}",
                    workspace,
                    channel,
                    channel_user_id,
                    source_message_id,
                    context_token,
                    original_message,
                    json.dumps(input_files, ensure_ascii=False),
                    "queued",
                    "task_created",
                    now,
                    now,
                    now,
                ),
            )
        return self.get_task(task_id) or {}

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "harness_session_id", "workspace", "status", "context_token",
            "input_files", "output_files", "version", "approved_version",
            "approved_hash", "review_contact", "delivery_contact",
            "delivery_mode", "delivery_channel", "completion_action", "scheduled_at",
            "scheduled_action", "scheduled_payload", "scheduled_claimed_at",
            "scheduled_attempts", "current_stage", "latest_action", "last_activity_at", "error",
            "route", "route_summary", "plan", "decision", "result_text",
            "approval_required", "approved_at", "planner_provider", "planner_model",
            "delivered_at", "original_message",
        }
        cols: list[str] = []
        vals: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            db_key = key
            if key in ("input_files", "output_files"):
                db_key = key + "_json"
                value = json.dumps(value or [], ensure_ascii=False)
            elif key == "scheduled_payload":
                db_key = "scheduled_payload_json"
                value = json.dumps(value or {}, ensure_ascii=False)
            elif key in ("plan", "decision"):
                db_key = key + "_json"
                value = json.dumps(value or {}, ensure_ascii=False)
            elif key == "approval_required":
                value = 1 if value else 0
            cols.append(f"{db_key}=?")
            vals.append(value)
        if not cols:
            return
        now = utc_now()
        cols.extend(["updated_at=?", "last_activity_at=?"])
        vals.extend([now, now, task_id])
        with self.lock, self.conn:
            self.conn.execute(f"UPDATE tasks SET {', '.join(cols)} WHERE task_id=?", vals)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._row(row)

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._row(r) or {} for r in rows]

    def latest_for_user(self, user_id: str, statuses: tuple[str, ...] | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM tasks WHERE channel_user_id=?"
        params: list[Any] = [user_id]
        if statuses:
            marks = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({marks})"
            params.extend(statuses)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
        return self._row(row)

    def add_version(self, task_id: str, files: list[Path]) -> tuple[int, str]:
        """Create an immutable Task Center snapshot for a deliverable version.

        Harness/local workers may write into mutable work/outbox locations. Approval
        and delivery must never point at those mutable paths, otherwise a later task
        could silently overwrite the file the user approved. Every version is
        therefore copied into Task Center-owned storage before hashing/persisting.
        """
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        version = int(task.get("version") or 0) + 1
        source_files = [Path(p).resolve() for p in files]
        missing = [str(p) for p in source_files if not p.exists() or not p.is_file()]
        if missing:
            raise RuntimeError("输出文件不存在: " + ", ".join(missing))

        snapshot_dir = self.versions_dir / task_id / f"v{version}"
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshots: list[Path] = []
        used_names: set[str] = set()
        for idx, src in enumerate(source_files, start=1):
            name = src.name
            if name.lower() in used_names:
                name = f"{src.stem}_{idx}{src.suffix}"
            used_names.add(name.lower())
            dst = snapshot_dir / name
            shutil.copy2(src, dst)
            snapshots.append(dst)

        rows = [
            {"path": str(p), "name": p.name, "sha256": file_sha256(p), "size": p.stat().st_size}
            for p in snapshots
        ]
        digest = version_digest(snapshots)
        now = utc_now()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO task_versions(task_id, version, files_json, aggregate_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, version, json.dumps(rows, ensure_ascii=False), digest, now),
            )
            self.conn.execute(
                "UPDATE tasks SET version=?, output_files_json=?, updated_at=?, last_activity_at=? WHERE task_id=?",
                (version, json.dumps([str(p) for p in snapshots], ensure_ascii=False), now, now, task_id),
            )
        return version, digest

    def get_version(self, task_id: str, version: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM task_versions WHERE task_id=? AND version=?", (task_id, version)
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["files"] = json.loads(data.pop("files_json"))
        return data

    def approve_version(self, task_id: str, version: int | None = None) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        version = int(version or task.get("version") or 0)
        if version <= 0:
            raise RuntimeError("task has no output version to approve")
        row = self.get_version(task_id, version)
        if not row:
            raise RuntimeError(f"version V{version} not found")
        self.update_task(
            task_id,
            approved_version=version,
            approved_hash=row["aggregate_hash"],
            latest_action=f"approved_v{version}",
        )
        return {"version": version, "hash": row["aggregate_hash"], "files": row["files"]}


    def verified_approved_version(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        version = int(task.get("approved_version") or 0)
        approved_hash = str(task.get("approved_hash") or "")
        if version <= 0 or not approved_hash:
            raise RuntimeError("任务尚未锁定批准版本")
        row = self.get_version(task_id, version)
        if not row:
            raise RuntimeError(f"批准版本 V{version} 不存在")
        paths = [Path(item["path"]).resolve() for item in row["files"]]
        missing = [str(p) for p in paths if not p.exists() or not p.is_file()]
        if missing:
            raise RuntimeError("批准文件已丢失: " + ", ".join(missing))
        current = version_digest(paths)
        if current != approved_hash or current != str(row.get("aggregate_hash") or ""):
            raise RuntimeError(
                f"批准文件 SHA256 校验失败：V{version} 已发生变化，禁止发送"
            )
        return {"version": version, "hash": current, "files": row["files"]}

    def record_delivery(
        self,
        *,
        task_id: str,
        version: int,
        contact: str,
        channel: str,
        status: str,
        detail: str = "",
    ) -> None:
        now = utc_now()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO deliveries(task_id, version, contact, channel, status, detail, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, version, contact, channel, status, detail, now, now),
            )

    def scheduled_ready(self, now_iso: str, stale_before_iso: str | None = None) -> list[dict[str, Any]]:
        stale_before_iso = stale_before_iso or now_iso
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT * FROM tasks
                WHERE scheduled_at IS NOT NULL
                  AND scheduled_at <= ?
                  AND delivered_at IS NULL
                  AND (scheduled_claimed_at IS NULL OR scheduled_claimed_at <= ?)
                  AND (
                        (scheduled_action IS NOT NULL AND status IN ('queued','running'))
                     OR (scheduled_action IS NULL AND version > 0 AND status IN ('completed','awaiting_review'))
                  )
                ORDER BY scheduled_at ASC
                """,
                (now_iso, stale_before_iso),
            ).fetchall()
        return [self._row(r) or {} for r in rows]

    def claim_scheduled(self, task_id: str, claimed_at: str, stale_before_iso: str) -> bool:
        """Claim one due action so a single runtime tick cannot execute it twice."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                """
                UPDATE tasks
                   SET scheduled_claimed_at=?,
                       scheduled_attempts=COALESCE(scheduled_attempts, 0) + 1,
                       current_stage='scheduled_delivering',
                       status=CASE WHEN scheduled_action IS NOT NULL THEN 'running' ELSE status END,
                       updated_at=?,
                       last_activity_at=?
                 WHERE task_id=?
                   AND delivered_at IS NULL
                   AND (scheduled_claimed_at IS NULL OR scheduled_claimed_at <= ?)
                """,
                (claimed_at, claimed_at, claimed_at, task_id, stale_before_iso),
            )
            return cur.rowcount == 1
