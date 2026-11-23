"""Security review checks for the HTTP surface (route inventory, admin protection, safe errors).

Uses a SYNTHETIC empty database; never the production DB.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.api.v1.deps import require_admin
from src.config import settings as cfg
from src.database.session import Database

ADMIN = "unit-test-admin-key"


@pytest.fixture
def client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "sec.db"

    async def build() -> None:
        db = Database(f"sqlite+aiosqlite:///{path}")
        await db.create_all()
        await db.dispose()

    asyncio.run(build())
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("API_READ_ONLY", "true")
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN)
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    cfg.get_settings.cache_clear()
    from src.api.app import app

    with TestClient(app) as c:
        yield c
    cfg.get_settings.cache_clear()


def _routes():  # type: ignore[no-untyped-def]
    from src.api.app import app

    return [r for r in app.routes if isinstance(r, APIRoute)]


def _deps(route: APIRoute) -> set:  # type: ignore[type-arg]
    return {d.call for d in route.dependant.dependencies}


def test_every_state_changing_route_requires_admin() -> None:
    mutating = [r for r in _routes() if r.methods - {"GET", "HEAD", "OPTIONS"}]
    assert mutating, "expected at least the crawl triggers"
    for r in mutating:
        assert require_admin in _deps(r), f"{sorted(r.methods)} {r.path} is not admin-protected"
    assert {r.path for r in mutating} == {"/crawl/{source}", "/api/v1/admin/crawl/{source}"}


def test_no_review_decision_or_write_endpoints_exist() -> None:
    paths = {r.path for r in _routes()}
    for p in paths:
        assert not any(w in p for w in ("/approve", "/reject", "/defer", "/retry", "/rebuild", "/enrich")), p


def test_all_v1_reads_are_get_only() -> None:
    for r in _routes():
        if r.path.startswith("/api/v1/") and not r.path.startswith("/api/v1/admin/"):
            assert r.methods == {"GET"}, r.path


def test_admin_key_never_echoed(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/admin/crawl/nope", headers={"X-Admin-Key": "wrong-key-value"})
    assert r.status_code == 401 and "wrong-key-value" not in r.text and ADMIN not in r.text


def test_unknown_methods_and_paths_are_safe(client) -> None:  # type: ignore[no-untyped-def]
    assert client.delete("/api/v1/search").status_code == 405
    assert client.put("/api/v1/startups/1").status_code == 405
    r = client.get("/api/v1/../../etc/passwd")
    assert r.status_code == 404 and "root:" not in r.text
    r = client.get("/api/v1/search", params={"q": "' OR 1=1 --"})
    assert r.status_code == 200 and r.json()["meta"]["total"] == 0  # bound parameter, not SQL


def test_openapi_lists_admin_security_header(client) -> None:  # type: ignore[no-untyped-def]
    spec = client.get("/openapi.json").json()
    params = spec["paths"]["/api/v1/admin/crawl/{source}"]["post"].get("parameters", [])
    assert any(p["name"] == "X-Admin-Key" and p["in"] == "header" for p in params)
