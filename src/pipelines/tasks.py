"""Celery tasks: horizontally scalable workers over a Redis broker.

* One task per *source* (and per page range for bulk sources) -> add workers
  to scale; no code change.
* ``acks_late`` + ``reject_on_worker_lost``: a task is only acknowledged after
  it finishes, so a killed worker's task is redelivered (idempotent upserts
  make redelivery safe).
* ``worker_prefetch_multiplier=1`` + bounded queues = backpressure.
* Retries with exponential backoff + jitter; after ``max_retries`` the task is
  written to the ``dead_letters`` table and pushed to the ``aip.dlq`` list.
* Beat schedule: news/jobs every 15 minutes (freshness), bulk verticals daily.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from celery import Celery, Task
from celery.schedules import crontab
from celery.signals import worker_process_init

from src.config.settings import get_settings
from src.crawlers.registry import REGISTRY, specs_for

s = get_settings()
app = Celery("aip", broker=s.redis_url or "redis://localhost:6379/0", backend=s.redis_url or "redis://localhost:6379/0")
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="ingest",
    task_routes={"src.pipelines.tasks.crawl_source": {"queue": "ingest"}, "src.pipelines.tasks.export_all": {"queue": "export"}},
    task_time_limit=60 * 60 * 3,
    task_soft_time_limit=60 * 60 * 2,
    broker_transport_options={"visibility_timeout": 60 * 60 * 4},
    result_expires=60 * 60 * 24,
    timezone="UTC",
    beat_schedule={
        **{f"fresh-{n}": {"task": "src.pipelines.tasks.crawl_source", "schedule": crontab(minute="*/15"), "args": (n,)} for n, sp in REGISTRY.items() if sp.vertical in ("news", "jobs") and not sp.disabled_reason},
        **{f"bulk-{n}": {"task": "src.pipelines.tasks.crawl_source", "schedule": crontab(minute=7, hour=2), "args": (n,)} for n, sp in REGISTRY.items() if sp.vertical in ("startups", "products", "research") and not sp.disabled_reason},
        "export": {"task": "src.pipelines.tasks.export_all", "schedule": crontab(minute=30, hour="*/6")},
    },
)


@worker_process_init.connect
def _metrics_server(**_: Any) -> None:
    """Each worker process exposes Prometheus metrics (port 9100 + pid offset avoids clashes)."""
    import os

    from prometheus_client import start_http_server

    try:
        start_http_server(9100 + (os.getpid() % 50))
    except OSError:
        pass


class DLQTask(Task):
    def on_failure(self, exc: BaseException, task_id: str, args: tuple, kwargs: dict, einfo: Any) -> None:
        from src.database.repository import Repository
        from src.database.session import Database

        async def _store() -> None:
            db = Database(s.database_url)
            await db.create_all()
            await Repository(db).dead_letter(f"celery.{self.name}", task_id, {"args": list(args), "kwargs": kwargs}, repr(exc))
            await db.dispose()

        asyncio.run(_store())
        try:
            import redis

            redis.from_url(app.conf.broker_url).lpush("aip.dlq", json.dumps({"task": self.name, "id": task_id, "args": list(args), "error": repr(exc)}))
        except Exception:
            pass


@app.task(bind=True, base=DLQTask, autoretry_for=(ConnectionError, TimeoutError), retry_backoff=True, retry_backoff_max=600, retry_jitter=True, max_retries=5)
def crawl_source(self: Task, name: str, limit: int | None = None) -> dict[str, Any]:
    from src.pipelines.runtime import pipeline_context

    async def _run() -> dict[str, Any]:
        async with pipeline_context() as pipe:
            summary = await pipe.run_spec(specs_for(names=[name])[0], limit=limit)
            return summary.__dict__

    return asyncio.run(_run())


@app.task(bind=True, base=DLQTask, max_retries=3, retry_backoff=True)
def export_all(self: Task, sheets: bool = True) -> dict[str, Any]:
    from src.main import run_export

    return asyncio.run(run_export(sheets=sheets, share_public=False))
