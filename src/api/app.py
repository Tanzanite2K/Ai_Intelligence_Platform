"""Operational API + minimal dashboard."""

from __future__ import annotations

import asyncio
import html
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config.settings import get_settings
from src.core.schemas import RecordType
from src.crawlers.registry import REGISTRY
from src.database.repository import Repository
from src.database.session import Database
from src.monitoring.logging import get_logger

from .v1 import api_v1
from .v1.deps import ApiError, RateLimiter, require_admin

log = get_logger(__name__)
state: dict[str, Any] = {}


def read_only_url(url: str) -> str:
    """sqlite+aiosqlite:///path -> SQLite URI opened with mode=ro (the API process can never write)."""
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix) or "mode=ro" in url:
        return url
    path = url[len(prefix):]
    if path in ("", ":memory:") or path.startswith("file:"):
        return url
    return f"{prefix}file:{path}?mode=ro&uri=true"


@asynccontextmanager
async def lifespan(app_: FastAPI):  # type: ignore[no-untyped-def]
    s = get_settings()
    ro = s.api_read_only and s.database_url.startswith("sqlite")
    if ro:
        path = s.database_url.split(":///", 1)[1]
        if path not in ("", ":memory:") and not path.startswith("file:") and not Path(path).exists():
            raise RuntimeError(f"database file not found ({path}); run `python -m src.main init-db` or set API_READ_ONLY=false")
    db = Database(read_only_url(s.database_url) if ro else s.database_url)
    if not ro:
        await db.create_all()  # never DDL against the live SQLite file from the web tier
    state["db"], state["repo"] = db, Repository(db)
    app_.state.read_db = db
    app_.state.limiter = RateLimiter(s.api_rate_limit_per_minute)
    log.info("api.start", read_only=ro, cors_origins=s.cors_origins, admin_enabled=s.admin_api_key is not None)
    warm = asyncio.create_task(_warm_intelligence(db.url))  # build the Frontier Intelligence index off the request path
    yield
    warm.cancel()
    await asyncio.gather(warm, return_exceptions=True)  # never dispose the engine while the warm-up still uses it
    await db.dispose()


async def _warm_intelligence(url: str) -> None:
    """Uses its own short-lived engine, so no pooled connection is left behind in the request pool."""
    from src.api.v1.intelligence import get_index

    warm_db = Database(url)
    try:
        async with warm_db.sessionmaker() as s:  # same (read-only) URL as the API
            await get_index(s)
    except asyncio.CancelledError:
        raise
    except Exception:  # never block or crash startup; the index is built on first use instead
        log.warning("intelligence.warmup_failed", exc_info=True)
    finally:
        await warm_db.dispose()


app = FastAPI(
    title="FrontierAtlas AI Intelligence API",
    version="1.1.0",
    description="Public read API over the ingested intelligence data (/api/v1) plus protected admin operations.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key"],
    max_age=600,
)


@app.middleware("http")
async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path.startswith("/api/v1"):
        limiter: RateLimiter | None = getattr(request.app.state, "limiter", None)
        retry = limiter.check(request.client.host if request.client else "unknown") if limiter else None
        if retry is not None:
            return JSONResponse({"error": {"code": "rate_limited", "message": "Too many requests"}}, status_code=429,
                                headers={"Retry-After": str(int(retry))})
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


@app.exception_handler(ApiError)
async def _api_error(_: Request, e: ApiError) -> JSONResponse:
    return JSONResponse({"error": {"code": e.code, "message": e.message, "details": e.details}}, status_code=e.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, e: RequestValidationError) -> JSONResponse:
    if not request.url.path.startswith("/api/v1"):
        return JSONResponse({"detail": e.errors()}, status_code=422)
    details = [{"loc": list(x.get("loc", [])), "msg": x.get("msg")} for x in e.errors()]
    return JSONResponse({"error": {"code": "invalid_request", "message": "Invalid query parameters", "details": details}}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, e: StarletteHTTPException) -> JSONResponse:
    if not request.url.path.startswith("/api/v1"):
        return JSONResponse({"detail": e.detail}, status_code=e.status_code, headers=getattr(e, "headers", None))
    return JSONResponse({"error": {"code": "http_error", "message": str(e.detail)}}, status_code=e.status_code)


@app.exception_handler(Exception)
async def _unhandled(request: Request, e: Exception) -> JSONResponse:
    log.error("api.unhandled", path=request.url.path, error=type(e).__name__)  # no message/stack to the client
    return JSONResponse({"error": {"code": "internal_error", "message": "Internal server error"}}, status_code=500)


app.include_router(api_v1)


def queue_crawl(source: str, background: BackgroundTasks, limit: int | None) -> dict[str, str]:
    try:
        from src.pipelines.tasks import crawl_source

        task = crawl_source.delay(source, limit)
        return {"queued": source, "task_id": task.id}
    except Exception:
        from src.main import run_crawl

        background.add_task(run_crawl, None, [source], limit, True, 1)
        return {"queued": source, "mode": "in-process"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
async def stats() -> dict[str, Any]:
    repo: Repository = state["repo"]
    runs = await repo.fetch_runs()
    return {"records": await repo.count_by_type(), "dead_letters": await repo.count_dead_letters(), "runs": runs[-50:]}


@app.get("/records/{record_type}")
async def records(record_type: RecordType, limit: int = Query(50, le=1000), offset: int = 0) -> list[dict[str, Any]]:
    return await state["repo"].fetch_records(record_type.value, limit=limit, offset=offset)


@app.get("/entity-mappings")
async def mappings(review_only: bool = False) -> list[dict[str, Any]]:
    rows = await state["repo"].fetch_mappings()
    return [m for m in rows if m["needsReview"]] if review_only else rows


@app.post("/crawl/{source}", dependencies=[Depends(require_admin)])
async def trigger(source: str, background: BackgroundTasks, limit: int | None = None) -> dict[str, str]:
    """Legacy crawl trigger — now requires X-Admin-Key (was unauthenticated)."""
    if source not in REGISTRY:
        raise HTTPException(404, f"unknown source; known: {sorted(REGISTRY)}")
    return queue_crawl(source, background, limit)


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    s = await stats()
    rows = "".join(
        f"<tr><td>{html.escape(r['source'])}</td><td>{r['vertical']}</td><td class='{r['status']}'>{r['status']}</td><td>{r['fetched']}</td><td>{r['stored']}</td>"
        f"<td>{r['duplicates']}</td><td>{r['rejected']}</td><td>{html.escape((r['error'] or '')[:120])}</td></tr>"
        for r in reversed(s["runs"])
    )
    cards = "".join(f"<div class='card'><b>{v}</b><span>{k}</span></div>" for k, v in s["records"].items())
    return f"""<!doctype html><html><head><meta charset=utf-8><title>FrontierAtlas Ingestion</title><meta http-equiv=refresh content=30>
<style>body{{font:14px system-ui;margin:2rem;color:#1f2328}}.cards{{display:flex;gap:1rem}}.card{{border:1px solid #d0d7de;border-radius:8px;padding:1rem 1.5rem}}
.card b{{display:block;font-size:1.6rem}}table{{border-collapse:collapse;margin-top:1.5rem;width:100%}}td,th{{border-bottom:1px solid #eee;padding:.4rem;text-align:left}}
.OK{{color:#1a7f37}}.PARTIAL{{color:#9a6700}}.INACCESSIBLE,.FAILED{{color:#cf222e}}.SKIPPED{{color:#57606a}}</style></head><body>
<h1>AI Intelligence Ingestion</h1><div class=cards>{cards}<div class=card><b>{s['dead_letters']}</b><span>dead letters</span></div></div>
<table><tr><th>source</th><th>vertical</th><th>status</th><th>fetched</th><th>stored</th><th>dupes</th><th>rejected</th><th>error</th></tr>{rows}</table></body></html>"""
