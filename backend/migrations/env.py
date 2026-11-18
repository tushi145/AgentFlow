"""Alembic environment.

Normally migrations run through `app.database.migrations.migrate_database()` (on app startup, or
`python -m app.database.migrations`), which backs up SQLite databases first and adopts legacy
databases that were created by `create_all` before Alembic existed. That path hands an open
connection to this module via `config.attributes["connection"]`.

The plain `alembic` CLI also works (e.g. `alembic revision -m "..."` to author a migration), but
`alembic upgrade` skips the automatic backup and legacy adoption — prefer the module entry point.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

import app.models  # noqa: F401  (register all tables on Base.metadata)
from app.database.base import Base

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True,  # SQLite needs batch mode for most ALTERs
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def _cli_url() -> str:
    from app.config.settings import get_settings
    return get_settings().resolved_database_url


if context.is_offline_mode():
    context.configure(url=_cli_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
elif (conn := config.attributes.get("connection")) is not None:
    _run(conn)
else:
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _main() -> None:
        engine = create_async_engine(_cli_url())
        try:
            async with engine.begin() as c:
                await c.run_sync(_run)
        finally:
            await engine.dispose()

    asyncio.run(_main())
