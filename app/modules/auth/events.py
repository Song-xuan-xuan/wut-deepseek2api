"""认证事件历史（落库），用于在面板上回溯"什么时候认的证/为什么失败"。"""

from __future__ import annotations

import time
from typing import Any

from ...core.db import Database


class AuthEventStore:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        kind: str,
        ok: bool,
        *,
        email: str = "",
        detail: str = "",
        source: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO auth_events (ts, kind, ok, email, detail, source) VALUES (?,?,?,?,?,?)",
            (time.time(), kind, 1 if ok else 0, email, detail[:500], source),
        )

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM auth_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)
        )
        return [
            {
                "id": row["id"],
                "time": time.strftime("%m-%d %H:%M:%S", time.localtime(row["ts"])),
                "ts": row["ts"],
                "kind": row["kind"],
                "ok": bool(row["ok"]),
                "email": row["email"],
                "detail": row["detail"],
                "source": row["source"],
            }
            for row in rows
        ]

    def last_success(self) -> dict[str, Any] | None:
        rows = self.db.query(
            "SELECT * FROM auth_events WHERE ok = 1 ORDER BY id DESC LIMIT 1",
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"])),
            "kind": row["kind"],
            "email": row["email"],
        }

    def clear(self) -> int:
        return self.db.execute("DELETE FROM auth_events")
