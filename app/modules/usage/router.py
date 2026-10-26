"""用量统计的 HTTP 接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ...core.paths import shorten_path
from ...core.settings import Settings
from ..access.store import KeyStore
from ..shared import error_response
from .store import RANGES, UsageStore


def attach(app: FastAPI, settings: Settings, store: UsageStore, keys: KeyStore) -> None:
    router = APIRouter(prefix="/api/usage", tags=["usage"])

    def guard(request: Request) -> JSONResponse | None:
        allowed = getattr(app.state, "allowed", None)
        if allowed and not allowed(request):
            return error_response("需要本地 API key", 401)
        return None

    def key_name(key_id: int) -> str:
        if key_id == 0:
            return "主 key"
        item = keys.get(key_id)
        return item["name"] if item else f"#{key_id}"

    @router.get("/overview")
    async def overview(request: Request, range: str = "7d", model: str = "", key: str = "") -> Any:
        blocked = guard(request)
        if blocked:
            return blocked
        range = range if range in RANGES else "7d"
        model = model or None
        key_id = int(key) if key not in ("", None) and str(key).isdigit() else None
        by_key = store.by_key(range)
        for row in by_key:
            row["key_name"] = key_name(row["key_id"])
        return {
            "range": range,
            "ranges": list(RANGES),
            "summary": store.summary(range, model, key_id),
            "series": store.series(range),
            "by_model": store.by_model(range),
            "by_status": store.by_status(range),
            "by_key": by_key,
            "recent": store.recent(30, key_id=key_id),
            "models": store.models(),
            "available": store.available,
            "db_path": shorten_path(store.db_path),
            "total_rows": store.total_count(),
            "last_error": store.last_error,
        }

    @router.get("/recent")
    async def recent(request: Request, limit: int = 50, status: str = "", model: str = "") -> Any:
        blocked = guard(request)
        if blocked:
            return blocked
        return {"items": store.recent(limit, status or None, model or None)}

    @router.get("/export.csv", response_class=PlainTextResponse)
    async def export(request: Request, range: str = "all") -> Any:
        blocked = guard(request)
        if blocked:
            return blocked
        range = range if range in RANGES else "all"
        csv_text = store.export_csv(range)
        return PlainTextResponse(
            csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="whut-usage-{range}.csv"'},
        )

    @router.delete("")
    async def clear(request: Request) -> Any:
        blocked = guard(request)
        if blocked:
            return blocked
        removed = store.clear()
        return {"ok": True, "removed": removed}

    app.include_router(router)
