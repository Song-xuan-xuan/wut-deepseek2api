"""API Key 仓库 + 内存索引 + 限流。

设计取舍：
- key 明文存库（这是本机自用工具，客户端会把 key 明文写在配置里，加密没有意义）；
- 索引常驻内存，写操作后重建，避免每个请求都查 SQLite；
- 限流用滑动窗口（每分钟），配额用当天 token 累计。
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from typing import Any

from ...core.db import Database
from ...core.logging import log

KEY_PREFIX = "sk-whut-"


class KeyStore:
    def __init__(self, db: Database):
        self.db = db
        self._by_key: dict[str, dict[str, Any]] = {}
        self._by_id: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._hits: dict[int, deque[float]] = {}
        self.reload()

    # ------------------------------------------------------------ 索引

    def reload(self) -> None:
        rows = self.db.query("SELECT * FROM api_keys")
        by_key: dict[str, dict[str, Any]] = {}
        by_id: dict[int, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            by_key[item["key"]] = item
            by_id[item["id"]] = item
        self._by_key = by_key
        self._by_id = by_id

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._by_id.values(), key=lambda item: item["id"])

    def get(self, key_id: int) -> dict[str, Any] | None:
        return self._by_id.get(key_id)

    def resolve(self, raw_key: str) -> dict[str, Any] | None:
        """按 key 找配置；禁用的 key 返回 None。"""
        item = self._by_key.get(raw_key)
        if not item or not item["enabled"]:
            return None
        return item

    # ------------------------------------------------------------ CRUD

    def create(
        self,
        name: str,
        *,
        rpm_limit: int = 0,
        daily_token_limit: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        key = KEY_PREFIX + secrets.token_urlsafe(24)
        key_id = self.db.insert(
            "INSERT INTO api_keys (name, key, enabled, note, daily_token_limit, rpm_limit, created_at)"
            " VALUES (?,?,1,?,?,?,?)",
            (name.strip() or "未命名", key, note, int(daily_token_limit), int(rpm_limit), time.time()),
        )
        self.reload()
        log(f"新建分发 key #{key_id}（{name}）")
        return self.get(key_id) or {}

    def update(self, key_id: int, **fields: Any) -> dict[str, Any] | None:
        allowed = {"name", "enabled", "note", "daily_token_limit", "rpm_limit"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if updates:
            assignments = ", ".join(f"{k} = ?" for k in updates)
            params = [int(v) if isinstance(v, bool) else v for v in updates.values()]
            params.append(key_id)
            self.db.execute(f"UPDATE api_keys SET {assignments} WHERE id = ?", tuple(params))
            self.reload()
        return self.get(key_id)

    def delete(self, key_id: int) -> bool:
        removed = self.db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        self.reload()
        return bool(removed)

    def rotate(self, key_id: int) -> dict[str, Any] | None:
        key = KEY_PREFIX + secrets.token_urlsafe(24)
        self.db.execute("UPDATE api_keys SET key = ? WHERE id = ?", (key, key_id))
        self.reload()
        return self.get(key_id)

    # ------------------------------------------------------------ 用量 / 限流

    def usage_today(self, key_id: int) -> dict[str, int]:
        rows = self.db.query(
            "SELECT COUNT(*) AS requests,"
            " SUM(prompt_tokens + completion_tokens) AS tokens,"
            " SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors"
            " FROM usage WHERE key_id = ? AND day = ?",
            (key_id, time.strftime("%Y-%m-%d")),
        )
        row = dict(rows[0]) if rows else {}
        return {
            "requests": row.get("requests") or 0,
            "tokens": row.get("tokens") or 0,
            "errors": row.get("errors") or 0,
        }

    def quota_state(self, item: dict[str, Any]) -> dict[str, Any]:
        used = self.usage_today(item["id"])
        limit = item.get("daily_token_limit") or 0
        return {
            "daily_token_limit": limit,
            "tokens_used_today": used["tokens"],
            "remaining_today": max(0, limit - used["tokens"]) if limit else None,
            "exceeded": bool(limit and used["tokens"] >= limit),
        }

    def check_rate(self, item: dict[str, Any], default_rpm: int) -> tuple[bool, int]:
        """返回 (是否允许, 建议重试秒数)。"""
        rpm = item.get("rpm_limit") or default_rpm
        if not rpm:
            return True, 0
        window = 60.0
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(item["id"], deque())
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= rpm:
                return False, max(1, int(window - (now - hits[0])) + 1)
            hits.append(now)
        return True, 0

    def touch(self, key_id: int) -> None:
        self.db.execute(
            "UPDATE api_keys SET last_used_at = ?, request_count = request_count + 1 WHERE id = ?",
            (time.time(), key_id),
        )
        item = self._by_id.get(key_id)
        if item:
            item["request_count"] = (item.get("request_count") or 0) + 1
            item["last_used_at"] = time.time()

    def dashboard(self) -> list[dict[str, Any]]:
        result = []
        for item in self.list():
            quota = self.quota_state(item)
            result.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "key": item["key"],
                    "masked": mask(item["key"]),
                    "enabled": bool(item["enabled"]),
                    "note": item["note"],
                    "rpm_limit": item["rpm_limit"],
                    "created_at": item["created_at"],
                    "last_used_at": item["last_used_at"],
                    "request_count": item["request_count"],
                    **quota,
                }
            )
        return result


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return key[:4] + "…"
    return f"{key[:10]}…{key[-4:]}"
