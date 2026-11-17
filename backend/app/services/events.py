"""Event bus: every event is persisted (timeline + SSE replay) and fanned out to live subscribers."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.database.session import session_factory
from app.models.execution import ExecutionEvent
from app.services.db_lock import db_write_lock

log = logging.getLogger(__name__)

TERMINAL_EVENT = "task_finished"


@dataclass(frozen=True)
class EventOut:
    id: int
    task_id: str
    type: str
    message: str
    payload: dict[str, Any]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "task_id": self.task_id, "type": self.type, "message": self.message,
                "payload": self.payload, "created_at": self.created_at.isoformat()}


def _out(row: ExecutionEvent) -> EventOut:
    return EventOut(row.id, row.task_id, row.type, row.message, row.payload or {}, row.created_at)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[EventOut]]] = defaultdict(set)

    async def publish(self, task_id: str, type_: str, message: str = "",
                      payload: dict[str, Any] | None = None,
                      agent_execution_id: int | None = None) -> EventOut:
        async with db_write_lock():
            async with session_factory()() as s:
                row = ExecutionEvent(task_id=task_id, type=type_, message=message[:2000],
                                     payload=payload or {}, agent_execution_id=agent_execution_id)
                s.add(row)
                await s.commit()
                await s.refresh(row)
        ev = _out(row)
        for q in list(self._subs.get(task_id, ())):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:  # slow consumer: it will re-sync from the DB on reconnect
                log.warning("Dropping live event for slow subscriber on task %s", task_id)
        return ev

    def subscribe(self, task_id: str) -> asyncio.Queue[EventOut]:
        q: asyncio.Queue[EventOut] = asyncio.Queue(maxsize=1000)
        self._subs[task_id].add(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue[EventOut]) -> None:
        self._subs[task_id].discard(q)
        if not self._subs[task_id]:
            self._subs.pop(task_id, None)

    async def history(self, task_id: str, after_id: int = 0, limit: int = 2000) -> list[EventOut]:
        async with session_factory()() as s:
            rows = (await s.scalars(
                select(ExecutionEvent).where(ExecutionEvent.task_id == task_id,
                                             ExecutionEvent.id > after_id)
                .order_by(ExecutionEvent.id).limit(limit))).all()
        return [_out(r) for r in rows]
