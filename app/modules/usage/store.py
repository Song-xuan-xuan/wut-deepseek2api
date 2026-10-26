"""用量存储（SQLite，连接与建表在 core/db.py）。

为什么落盘：重启一次就清零的统计没有意义。这里每次请求写一行，
按天/小时/模型做 SQL 聚合，另有保留期清理。

写入频率很低（agent 场景每秒最多几次），WAL + 单行 INSERT 实测 < 0.3ms，
直接同步写即可，不引入线程池复杂度；写失败只记日志，绝不影响转发。
"""

from __future__ import annotations

import csv
import io
import time
from typing import Any

from ...core.db import Database
from ...core.settings import UsageSettings

TOTAL_COLUMNS = """
       COUNT(*)                                       AS requests,
       SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors,
       SUM(stream)                                    AS streams,
       SUM(tool_calls)                                AS tool_calls,
       SUM(prompt_tokens)                             AS prompt_tokens,
       SUM(completion_tokens)                         AS completion_tokens,
       SUM(reasoning_tokens)                          AS reasoning_tokens
"""

RANGES = {
    "today": 0,
    "24h": 1,
    "7d": 7,
    "30d": 30,
    "all": None,
}

class UsageStore:
    def __init__(self, db: Database, settings: UsageSettings):
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------ 基础

    @property
    def available(self) -> bool:
        return self.settings.enabled and self.db.available

    @property
    def db_path(self) -> str:
        return self.db.path

    @property
    def last_error(self) -> str | None:
        return self.db.last_error

    def connect(self) -> None:
        self.db.connect()
        self._prune()

    def close(self) -> None:
        self.db.close()

    def _query(self, sql: str, params: tuple = ()):
        return self.db.query(sql, params)

    def _write(self, sql: str, params: tuple) -> None:
        self.db.execute(sql, params)

    def _prune(self) -> None:
        if not self.settings.retention_days:
            return
        cutoff = time.time() - self.settings.retention_days * 86400
        self._write("DELETE FROM usage WHERE ts < ?", (cutoff,))

    # ------------------------------------------------------------ 写入

    def record(
        self,
        *,
        model: str,
        status: int,
        ms: float,
        stream: bool = False,
        tools: int = 0,
        usage: dict[str, Any] | None = None,
        tool_calls: int = 0,
        notes: list[str] | None = None,
        error: str | None = None,
        key_id: int = 0,
    ) -> None:
        if not self.db.available:
            return
        usage = usage or {}
        now = time.time()
        self._write(
            "INSERT INTO usage (ts, day, hour, model, stream, tools, status, ms,"
            " prompt_tokens, completion_tokens, reasoning_tokens, tool_calls, notes, error, key_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now,
                time.strftime("%Y-%m-%d", time.localtime(now)),
                time.strftime("%Y-%m-%dT%H:00", time.localtime(now)),
                model or "(未指定)",
                1 if stream else 0,
                tools,
                status,
                round(ms, 1),
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                tool_calls,
                ",".join(notes or []),
                (error or "")[:500],
                key_id,
            ),
        )

    # ------------------------------------------------------------ 查询

    def _where(self, range_key: str, model: str | None, key_id: int | None = None) -> tuple[str, tuple]:
        clauses = []
        params: list[Any] = []
        if range_key == "today":
            clauses.append("day = ?")
            params.append(time.strftime("%Y-%m-%d", time.localtime()))
        elif range_key == "24h":
            clauses.append("ts >= ?")
            params.append(time.time() - 86400)
        elif range_key in ("7d", "30d"):
            clauses.append("ts >= ?")
            params.append(time.time() - RANGES[range_key] * 86400)
        if model:
            clauses.append("model = ?")
            params.append(model)
        if key_id is not None:
            clauses.append("key_id = ?")
            params.append(key_id)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", tuple(params)

    def summary(
        self, range_key: str = "7d", model: str | None = None, key_id: int | None = None
    ) -> dict[str, Any]:
        where, params = self._where(range_key, model, key_id)
        rows = self._query(f"SELECT {TOTAL_COLUMNS} FROM usage{where}", params)
        row = rows[0] if rows else None
        totals = {k: (v or 0) for k, v in dict(row).items()} if row else {}

        latencies = [
            r["ms"]
            for r in self._query(f"SELECT ms FROM usage{where} ORDER BY ms", params)
        ]
        return {
            "range": range_key,
            "model": model,
            "totals": totals,
            "latency_ms": {
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "max": round(latencies[-1], 1) if latencies else 0.0,
            },
        }

    def by_model(self, range_key: str = "7d") -> list[dict[str, Any]]:
        where, params = self._where(range_key, None)
        rows = self._query(
            f"SELECT model, {TOTAL_COLUMNS} FROM usage{where} GROUP BY model ORDER BY requests DESC",
            params,
        )
        return [_row_to_dict(r) for r in rows]

    def by_status(self, range_key: str = "7d") -> list[dict[str, Any]]:
        where, params = self._where(range_key, None)
        rows = self._query(
            f"SELECT status, COUNT(*) AS count FROM usage{where} GROUP BY status ORDER BY status",
            params,
        )
        return [{"status": r["status"], "count": r["count"]} for r in rows]

    def series(self, range_key: str = "7d") -> dict[str, Any]:
        """today/24h 按小时，其余按天。"""
        where, params = self._where(range_key, None)
        bucket = "hour" if range_key in ("today", "24h") else "day"
        rows = self._query(
            f"SELECT {bucket} AS bucket, {TOTAL_COLUMNS} FROM usage{where}"
            f" GROUP BY {bucket} ORDER BY {bucket}",
            params,
        )
        return {"bucket": bucket, "points": [_row_to_dict(r, key="bucket") for r in rows]}

    def recent(
        self,
        limit: int = 50,
        status: str | None = None,
        model: str | None = None,
        key_id: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status == "errors":
            clauses.append("status >= 400")
        elif status:
            clauses.append("status = ?")
            params.append(int(status))
        if model:
            clauses.append("model = ?")
            params.append(model)
        if key_id is not None:
            clauses.append("key_id = ?")
            params.append(key_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self._query(
            f"SELECT * FROM usage{where} ORDER BY id DESC LIMIT ?", tuple(params)
        )
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "time": time.strftime("%m-%d %H:%M:%S", time.localtime(r["ts"])),
                "model": r["model"],
                "stream": bool(r["stream"]),
                "tools": r["tools"],
                "status": r["status"],
                "ms": r["ms"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "reasoning_tokens": r["reasoning_tokens"],
                "tool_calls": r["tool_calls"],
                "notes": [n for n in (r["notes"] or "").split(",") if n],
                "error": r["error"],
                "key_id": r["key_id"],
            }
            for r in rows
        ]

    def by_key(self, range_key: str = "7d") -> list[dict[str, Any]]:
        where, params = self._where(range_key, None)
        rows = self._query(
            f"SELECT key_id, {TOTAL_COLUMNS} FROM usage{where} GROUP BY key_id ORDER BY requests DESC",
            params,
        )
        return [_row_to_dict(r, key="key_id") for r in rows]

    def models(self) -> list[str]:
        return [r["model"] for r in self._query("SELECT DISTINCT model FROM usage ORDER BY model")]

    def total_count(self) -> int:
        rows = self._query("SELECT COUNT(*) AS c FROM usage")
        return rows[0]["c"] if rows else 0

    def export_csv(self, range_key: str = "all") -> str:
        where, params = self._where(range_key, None)
        rows = self._query(f"SELECT * FROM usage{where} ORDER BY id", params)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "时间",
                "模型",
                "流式",
                "工具数",
                "状态码",
                "耗时ms",
                "输入tokens",
                "输出tokens",
                "思维链tokens",
                "工具调用数",
                "备注",
                "错误",
                "key_id",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
                    r["model"],
                    "是" if r["stream"] else "否",
                    r["tools"],
                    r["status"],
                    r["ms"],
                    r["prompt_tokens"],
                    r["completion_tokens"],
                    r["reasoning_tokens"],
                    r["tool_calls"],
                    r["notes"],
                    r["error"],
                    r["key_id"],
                ]
            )
        return buffer.getvalue()

    def clear(self) -> int:
        count = self.total_count()
        self._write("DELETE FROM usage", ())
        return count


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round((pct / 100) * (len(values) - 1))))
    return round(values[index], 1)


def _row_to_dict(row: Any, key: str = "model") -> dict[str, Any]:
    data = {k: (v or 0) for k, v in dict(row).items()}
    data[key] = row[key]
    data["requests"] = row["requests"] or 0
    return data
