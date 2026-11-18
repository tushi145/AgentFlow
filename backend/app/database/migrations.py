"""Schema migrations (Alembic) with automatic SQLite backups and adoption of pre-Alembic databases.

States handled by `migrate_database()`:
* fresh database (no application tables) → tables created from the current models, stamped at head;
* legacy database (application tables, no `alembic_version`; created by `create_all` before
  migrations existed) → backed up, missing tables created, stamped at the baseline, upgraded;
* managed database behind head → backed up, upgraded;
* managed database at head → nothing to do.

A SQLite database is always backed up (online backup API, consistent with WAL) and the backup
verified BEFORE any schema change; if the backup fails, nothing is migrated. Existing data is never
deleted. Run manually with `python -m app.database.migrations` (from backend/).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.config.settings import Settings
from app.database.base import Base
from app.database.session import get_engine

log = logging.getLogger("agentflow.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
BASELINE_REVISION = "0001_baseline"
SQLITE_PREFIX = "sqlite+aiosqlite:///"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationResult:
    action: str                 # "created" | "adopted" | "upgraded" | "up_to_date"
    from_revision: str | None   # None for fresh and legacy databases
    to_revision: str
    backup_path: Path | None


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:  # pragma: no cover - repository invariant
        raise MigrationError("No migration revisions found.")
    return head


def _state(conn: Connection) -> tuple[bool, str | None]:
    """(has application tables, current alembic revision)."""
    tables = set(inspect(conn).get_table_names())
    has_app_tables = bool(tables & set(Base.metadata.tables))
    return has_app_tables, MigrationContext.configure(conn).get_current_revision()


def sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith(SQLITE_PREFIX):
        return None
    raw = database_url[len(SQLITE_PREFIX):]
    return None if not raw or raw == ":memory:" else Path(raw)


def backup_sqlite(db_path: Path, backup_dir: Path, label: str) -> Path:
    """Consistent copy via SQLite's online backup API (includes committed WAL content), verified."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    dest = backup_dir / f"{db_path.stem}.pre-migration-{label}-{stamp}.db"
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        expected = {t: src.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        src.close()
    check = sqlite3.connect(f"file:{dest.as_posix()}?mode=ro", uri=True)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        actual = {t: check.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in expected}
    finally:
        check.close()
    if ok != "ok" or actual != expected:
        raise MigrationError(f"Backup verification failed for {dest} (integrity={ok}).")
    return dest


async def migrate_database(settings: Settings) -> MigrationResult:
    """Bring the configured database to the latest schema. Safe to call on every startup."""
    import app.models  # noqa: F401  (register mappers)

    engine = get_engine()
    head = head_revision()
    async with engine.connect() as conn:
        has_tables, current = await conn.run_sync(_state)

    if not has_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(lambda c: command.stamp(alembic_config(c), head))
        log.info("Created a new database schema at revision %s.", head)
        return MigrationResult("created", None, head, None)

    if current == head:
        return MigrationResult("up_to_date", current, head, None)

    legacy = current is None
    backup: Path | None = None
    db_file = sqlite_path(settings.resolved_database_url)
    if db_file is not None:
        try:
            backup = backup_sqlite(db_file, settings.resolved_data_dir / "backups", current or "legacy")
        except Exception as exc:
            raise MigrationError(f"Could not back up {db_file}; refusing to migrate: {exc}") from exc
        log.warning("Database backed up to %s before migrating %s → %s.", backup, current or "legacy", head)
    else:
        log.warning("Migrating a non-SQLite database %s → %s without an automatic backup; "
                    "take a backup (e.g. pg_dump) before upgrading production.", current or "legacy", head)

    async with engine.begin() as conn:
        if legacy:
            # Pre-Alembic database: add any tables it lacks (never alters existing ones), then
            # record it at the baseline so every later revision is applied.
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(lambda c: command.stamp(alembic_config(c), BASELINE_REVISION))
        await conn.run_sync(lambda c: command.upgrade(alembic_config(c), "head"))
    log.warning("Database migrated %s → %s.", current or "legacy (pre-Alembic)", head)
    return MigrationResult("adopted" if legacy else "upgraded", current, head, backup)


def _main() -> None:  # pragma: no cover - thin CLI wrapper
    import asyncio

    from app.config.settings import get_settings
    from app.database.session import dispose_engine, init_engine

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    async def run() -> None:
        init_engine(settings.resolved_database_url)
        try:
            r = await migrate_database(settings)
            print(f"{r.action}: {r.from_revision or '-'} -> {r.to_revision}"
                  + (f" (backup: {r.backup_path})" if r.backup_path else ""))
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    _main()
