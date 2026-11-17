"""Serialise writes from concurrent agent branches (needed for SQLite; harmless on Postgres)."""

from __future__ import annotations

import asyncio

_lock: asyncio.Lock | None = None
_loop: asyncio.AbstractEventLoop | None = None


def db_write_lock() -> asyncio.Lock:
    global _lock, _loop
    loop = asyncio.get_running_loop()
    if _lock is None or _loop is not loop:  # new event loop (e.g. per-test) → new lock
        _lock, _loop = asyncio.Lock(), loop
    return _lock
