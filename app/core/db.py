"""统一 SQLite 数据层：用量、API Key、认证事件。

所有模块共用一个连接（WAL），避免多个 Store 各自开库。
表结构在这里集中管理，旧库自动迁移（目前只有 usage.key_id）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

from .logging import log

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL    NOT NULL,
    day               TEXT    NOT NULL,
    hour              TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    stream            INTEGER NOT NULL DEFAULT 0,
    tools             INTEGER NOT NULL DEFAULT 0,
    status            INTEGER NOT NULL,
    ms                REAL    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
    tool_calls        INTEGER NOT NULL DEFAULT 0,
    notes             TEXT    NOT NULL DEFAULT '',
    error             TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS api_keys (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    key                TEXT    NOT NULL UNIQUE,
    enabled            INTEGER NOT NULL DEFAULT 1,
    note               TEXT    NOT NULL DEFAULT '',
    daily_token_limit  INTEGER NOT NULL DEFAULT 0,
    rpm_limit          INTEGER NOT NULL DEFAULT 0,
    created_at         REAL    NOT NULL,
    last_used_at       REAL    NOT NULL DEFAULT 0,
    request_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS auth_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    ok      INTEGER NOT NULL,
    email   TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT '',
    source  TEXT NOT NULL DEFAULT ''
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_usage_ts      ON usage(ts);
CREATE INDEX IF NOT EXISTS idx_usage_day     ON usage(day);
CREATE INDEX IF NOT EXISTS idx_usage_hour    ON usage(hour);
CREATE INDEX IF NOT EXISTS idx_usage_key     ON usage(key_id);
CREATE INDEX IF NOT EXISTS idx_keys_key      ON api_keys(key);
CREATE INDEX IF NOT EXISTS idx_auth_ts       ON auth_events(ts);
"""

MIGRATIONS = [
    ("usage", "key_id", "ALTER TABLE usage ADD COLUMN key_id INTEGER NOT NULL DEFAULT 0"),
]


class Database:
    def __init__(self, path: str, enabled: bool = True):
        self.path = os.path.expanduser(path) if path else ""
        self.enabled = enabled
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self.last_error: str | None = None
        # 数据库损坏时把旧文件改名留档、再建新库，避免「统计静默归零」
        self.recovered_from: str | None = None

    # ------------------------------------------------------------ 生命周期

    @property
    def available(self) -> bool:
        return self.enabled and self._conn is not None

    def connect(self) -> None:
        if not self.enabled:
            return
        try:
            self._connect()
        except sqlite3.DatabaseError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if not self._recover_corrupt():
                log(f"数据库不可用：{self.last_error}")

    def _connect(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        self._conn = conn
        self._restrict_permissions()
        self._migrate()
        conn.executescript(INDEXES)
        conn.commit()
        self._restrict_permissions()

    def _recover_corrupt(self) -> bool:
        """数据库文件损坏（被其它进程写坏等）：改名留档，重新建一个空库。

        宁可丢历史统计，也不能让服务带着一个「每次写入都失败」的库继续跑 ——
        那样面板会一直显示 0，用户以为统计坏了却看不到任何报错。
        """
        if not self.path or not os.path.isfile(self.path):
            log(f"数据库不可用：{self.last_error}")
            return False
        stamp = time.strftime("%Y%m%d-%H%M%S")
        broken = f"{self.path}.corrupt-{stamp}"
        try:
            self.close()
            os.replace(self.path, broken)
            for suffix in ("-wal", "-shm"):
                sidecar = self.path + suffix
                if os.path.isfile(sidecar):
                    os.replace(sidecar, broken + suffix)
            self._connect()
        except (OSError, sqlite3.Error) as exc:
            log(f"数据库损坏且无法重建：{exc}")
            return False
        self.recovered_from = broken
        self.last_error = None
        log(f"⚠️ 数据库文件损坏，已改名为 {os.path.basename(broken)} 并新建空库（历史统计丢失）")
        return True

    def _restrict_permissions(self) -> None:
        """库里有明文分发 key，权限收到 600（WAL/SHM 同样处理）。"""
        for suffix in ("", "-wal", "-shm"):
            target = self.path + suffix
            if os.path.exists(target):
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass

    def _migrate(self) -> None:
        assert self._conn is not None
        for table, column, ddl in MIGRATIONS:
            columns = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                self._conn.execute(ddl)
                log(f"数据库迁移：{table}.{column} 已添加")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------ 基础操作

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        if self._conn is None:
            return []
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def execute(self, sql: str, params: tuple = ()) -> int:
        """返回 rowcount；失败只记录，不抛（统计类写入不能影响转发）。"""
        if self._conn is None:
            return 0
        try:
            with self._lock:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
                return cursor.rowcount
        except sqlite3.Error as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log(f"数据库写入失败：{self.last_error}")
            return 0

    def insert(self, sql: str, params: tuple = ()) -> int:
        if self._conn is None:
            return 0
        try:
            with self._lock:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
                return int(cursor.lastrowid or 0)
        except sqlite3.Error as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log(f"数据库写入失败：{self.last_error}")
            return 0

    def stat(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "path": self.path,
            "last_error": self.last_error,
            "recovered_from": self.recovered_from,
            "size_bytes": os.path.getsize(self.path) if self.path and os.path.isfile(self.path) else 0,
        }


def now() -> float:
    return time.time()
