"""Versioned public read API (/api/v1) for the intelligence discovery frontend."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from src.crawlers.registry import REGISTRY

from .deps import ApiError, require_admin
from .entities import router as entities_router
from .history import router as history_router
from .intelligence import router as intelligence_router
from .intelligence_history import router as intelligence_history_router
from .operations import router as operations_router
from .org_history import router as org_history_router
from .records import router as records_router
from .relationships import router as relationships_router

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(records_router)
api_v1.include_router(entities_router)
api_v1.include_router(operations_router)
api_v1.include_router(relationships_router)
api_v1.include_router(intelligence_router)
api_v1.include_router(history_router)
api_v1.include_router(org_history_router)
api_v1.include_router(intelligence_history_router)


@api_v1.post("/admin/crawl/{source}", tags=["admin"], dependencies=[Depends(require_admin)], status_code=202)
async def admin_crawl(source: str, background: BackgroundTasks, limit: int | None = None) -> dict[str, str]:
    """Queue a crawl. Requires X-Admin-Key. Not exposed in the frontend."""
    if source not in REGISTRY:
        raise ApiError(404, "unknown_source", f"Unknown source; known: {sorted(REGISTRY)}")
    from src.api.app import queue_crawl

    return queue_crawl(source, background, limit)


__all__ = ["api_v1"]
