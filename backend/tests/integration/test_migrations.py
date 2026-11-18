"""Schema migrations: fresh databases, pre-Alembic (legacy) databases, backups and idempotency."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine

import app.models  # noqa: F401
from app.config.settings import Settings
from app.database import migrations
from app.database.base import Base
from app.database.migrations import MigrationError, head_revision
from app.main import create_app
from tests.conftest import make_settings

LEGACY_TASK = "11111111-1111-1111-1111-111111111111"


def db_file(settings: Settings) -> Path:
    path = migrations.sqlite_path(settings.resolved_database_url)
    assert path is not None
    return path


def make_legacy_db(path: Path, *, with_runtime_state: bool = False) -> None:
    """A database as `create_all` produced it before Alembic: no alembic_version table and, for
    databases created before Stage 2D, no tasks.runtime_state column. Seeded with history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    engine.dispose()
    con = sqlite3.connect(path)
    if not with_runtime_state:
        con.execute("ALTER TABLE tasks DROP COLUMN runtime_state")
    con.execute(
        "INSERT INTO tasks (id, prompt, title, mode, status, execution_mode, approval_mode, limits, "
        "agent_overrides, progress_done, progress_total, iterations, input_tokens, output_tokens, "
        "llm_calls, tool_calls, budget_exhausted, current_stage, created_at, finished_at) VALUES "
        "(?, 'Old objective from before the migration', 'Old task', 'demo', 'completed', 'parallel', "
        "'none', '{}', '{}', 7, 7, 0, 10, 5, 7, 4, 0, 'finished', '2026-11-17 10:00:00', "
        "'2026-11-17 10:01:00')", (LEGACY_TASK,))
    for i, t in enumerate(["task_started", "agent_started", "task_finished"], start=1):
        payload = {"status": "completed", "error_code": None} if t == "task_finished" else {}
        con.execute("INSERT INTO execution_events (id, task_id, type, message, payload, created_at) "
                    "VALUES (?, ?, ?, ?, ?, '2026-11-17 10:00:0" + str(i) + "')",
                    (i, LEGACY_TASK, t, f"legacy {t}", json.dumps(payload)))
    con.commit()
    con.close()


def columns(path: Path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def scalar(path: Path, sql: str):  # type: ignore[no-untyped-def]
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def backups(settings: Settings) -> list[Path]:
    d = settings.resolved_data_dir / "backups"
    return sorted(d.glob("*.db")) if d.exists() else []


async def start_and_stop(settings: Settings) -> None:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        pass


# ------------------------------------------------------------------------------------------------

async def test_fresh_database_is_created_at_head_without_backup(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    await start_and_stop(settings)
    path = db_file(settings)
    assert scalar(path, "SELECT version_num FROM alembic_version") == head_revision()
    assert "runtime_state" in columns(path, "tasks")
    assert backups(settings) == []


async def test_legacy_database_is_backed_up_migrated_and_keeps_history(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    path = db_file(settings)
    make_legacy_db(path)
    assert "runtime_state" not in columns(path, "tasks")

    application = create_app(settings)
    async with application.router.lifespan_context(application):
        # Backup taken before the change: it is the untouched legacy schema with the same rows.
        [backup] = backups(settings)
        assert "pre-migration-legacy" in backup.name
        assert "runtime_state" not in columns(backup, "tasks")
        assert scalar(backup, "SELECT count(*) FROM execution_events") == 3
        assert scalar(backup, "SELECT title FROM tasks") == "Old task"

        # Migrated database: new column, old rows intact and readable through the API.
        assert scalar(path, "SELECT version_num FROM alembic_version") == head_revision()
        assert scalar(path, f"SELECT runtime_state FROM tasks WHERE id='{LEGACY_TASK}'") == "{}"
        assert scalar(path, "SELECT count(*) FROM execution_events") == 3

        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            assert (await c.post("/api/v1/auth/login", json={"username": "admin", "password": "pw-test"})).status_code == 200
            old = (await c.get(f"/api/v1/tasks/{LEGACY_TASK}")).json()
            assert old["title"] == "Old task" and old["status"] == "completed" and old["llm_calls"] == 7
            # SSE replay of the pre-migration history still works.
            stream = (await c.get(f"/api/v1/tasks/{LEGACY_TASK}/events")).text
            assert stream.count("event: agentflow") == 3 and "legacy task_finished" in stream
            # New tasks can be created and run on the migrated database (this failed before).
            r = await c.post("/api/v1/tasks", json={"prompt": "Run after migrating a legacy database",
                                                    "mode": "demo"})
            assert r.status_code == 201, r.text
            new_id = r.json()["id"]
            for _ in range(200):
                if (await c.get(f"/api/v1/tasks/{new_id}")).json()["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.05)
            assert (await c.get(f"/api/v1/tasks/{new_id}")).json()["status"] == "completed"
            listed = {t["id"] for t in (await c.get("/api/v1/tasks")).json()["items"]}
            assert {LEGACY_TASK, new_id} <= listed


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    make_legacy_db(db_file(settings))
    await start_and_stop(settings)
    await start_and_stop(settings)  # second start: already at head → no backup, no change
    assert len(backups(settings)) == 1
    assert scalar(db_file(settings), "SELECT count(*) FROM alembic_version") == 1


async def test_legacy_database_that_already_has_the_column_is_adopted(tmp_path: Path) -> None:
    """Created by create_all after Stage 2D (column present) but before Alembic."""
    settings = make_settings(tmp_path)
    path = db_file(settings)
    make_legacy_db(path, with_runtime_state=True)
    await start_and_stop(settings)
    assert scalar(path, "SELECT version_num FROM alembic_version") == head_revision()
    assert scalar(path, f"SELECT runtime_state FROM tasks WHERE id='{LEGACY_TASK}'") is not None
    assert scalar(path, "SELECT count(*) FROM execution_events") == 3
    assert len(backups(settings)) == 1


async def test_failed_backup_aborts_the_migration_and_changes_nothing(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    path = db_file(settings)
    make_legacy_db(path)

    def boom(*_a: object, **_k: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(migrations, "backup_sqlite", boom)
    with pytest.raises(MigrationError, match="refusing to migrate"):
        await start_and_stop(settings)
    assert "runtime_state" not in columns(path, "tasks")
    assert scalar(path, "SELECT count(*) FROM sqlite_master WHERE name='alembic_version'") == 0
    assert scalar(path, "SELECT count(*) FROM tasks") == 1


def test_downgrade_and_upgrade_round_trip(tmp_path: Path) -> None:
    """0002 is reversible; data in other columns survives a down/up cycle."""
    from alembic import command

    path = tmp_path / "rt.db"
    make_legacy_db(path)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        command.stamp(migrations.alembic_config(conn), migrations.BASELINE_REVISION)
        command.upgrade(migrations.alembic_config(conn), "head")
    assert "runtime_state" in columns(path, "tasks")
    with engine.begin() as conn:
        command.downgrade(migrations.alembic_config(conn), migrations.BASELINE_REVISION)
    assert "runtime_state" not in columns(path, "tasks")
    with engine.begin() as conn:
        command.upgrade(migrations.alembic_config(conn), "head")
    engine.dispose()
    assert "runtime_state" in columns(path, "tasks")
    assert scalar(path, "SELECT title FROM tasks") == "Old task"
    assert scalar(path, "SELECT count(*) FROM execution_events") == 3
