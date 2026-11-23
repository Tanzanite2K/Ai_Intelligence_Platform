"""Alembic environment for FrontierAtlas.

* The database URL comes from ``-x db_url=...``, then ``ALEMBIC_DATABASE_URL``, then the application setting
  (``DATABASE_URL`` / ``settings.database_url``). Async drivers (aiosqlite, asyncpg) are used as-is through an async
  engine, so no extra database driver is needed.
* Safety: the live SQLite file ``data/platform.db`` is refused unless ``-x allow_live=yes`` is passed. Phase 7A runs
  only against copies.
* ``target_metadata`` is the application's ORM metadata, used only to compare the baseline tables. History tables
  (revision 0002) are deliberately not ORM models yet, so they are excluded from that comparison.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.models import Base  # noqa: E402

LIVE_DB = (ROOT / "data" / "platform.db").resolve()
# Tables created by 0002_history_tables. Not ORM-mapped in Phase 7A, so autogenerate must not report them.
HISTORY_TABLES = frozenset({
    "ingestion_batches", "record_observations", "record_versions", "metric_snapshots", "catalogue_scans",
    "presence_events", "entities", "entity_resolution_events", "claims", "evidence_links", "source_groups",
    "source_reliability_daily",
})

config = context.config
if config.config_file_name is not None and not getattr(config, "skip_logging_config", False):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


class LiveDatabaseRefused(RuntimeError):
    pass


def resolve_url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    url = x.get("db_url") or os.environ.get("ALEMBIC_DATABASE_URL")
    if not url:
        from src.config.settings import get_settings

        url = get_settings().database_url
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        if u.drivername == "sqlite":  # a plain sqlite:// URL -> use the async driver the app already ships
            u = u.set(drivername="sqlite+aiosqlite")
        db = u.database or ""
        if db and db != ":memory:":
            target = Path(db)
            target = (target if target.is_absolute() else Path.cwd() / target).resolve()
            if target == LIVE_DB and x.get("allow_live") != "yes":
                raise LiveDatabaseRefused(
                    f"Refusing to run migrations against the live database {target}. "
                    "Phase 7A runs on copies only. Pass -x allow_live=yes only with explicit approval."
                )
    elif u.drivername == "postgresql":
        u = u.set(drivername="postgresql+asyncpg")
    return u.render_as_string(hide_password=False)


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    if type_ == "table" and (name in HISTORY_TABLES or name == "alembic_version"):
        return False
    table = getattr(obj, "table", None)
    return not (table is not None and table.name in HISTORY_TABLES)


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    is_sqlite = (connection.dialect.name if connection is not None else make_url(url or "").get_backend_name()) == "sqlite"
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=is_sqlite,  # SQLite cannot ALTER most things; future migrations use batch mode
        compare_type=True,
        transaction_per_migration=True,
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"} if connection is None else {},
    )


def run_migrations_offline() -> None:
    _configure(url=resolve_url())
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(resolve_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_do_run)
            await conn.commit()
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:  # tests may hand in an open sync connection
        _do_run(connectable)
        return
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
