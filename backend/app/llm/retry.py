"""Exponential backoff with jitter for retryable provider/tool errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import (AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt,
                      wait_random_exponential)

from app.llm.base import LLMError

R = TypeVar("R")

OnRetry = Callable[[int, BaseException, float], Awaitable[None]]


MAX_RETRY_AFTER_S = 60.0


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, LLMError) and exc.retryable


def _make_wait(base: float, cap: float) -> Callable[[RetryCallState], float]:
    """Full-jitter exponential backoff, but never shorter than a server-sent `retry-after`
    (bounded by MAX_RETRY_AFTER_S so a hostile/odd header cannot stall a task)."""
    jitter = wait_random_exponential(multiplier=base, max=cap)

    def _wait(state: RetryCallState) -> float:
        delay = jitter(state)
        exc = state.outcome.exception() if state.outcome else None
        after = getattr(exc, "retry_after_s", None)
        if after:
            delay = max(delay, min(float(after), MAX_RETRY_AFTER_S))
        return delay

    return _wait


async def with_backoff(fn: Callable[[], Awaitable[R]], *, max_attempts: int,
                       on_retry: OnRetry | None = None, base: float = 1.0,
                       cap: float = 30.0) -> R:
    """Run `fn`, retrying retryable `LLMError`s with full-jitter exponential backoff.

    `on_retry(attempt, error, sleep_s)` is awaited before each sleep so callers can persist a
    visible "retrying" event.
    """

    async def _before_sleep(state: RetryCallState) -> None:
        if on_retry and state.outcome is not None:
            exc = state.outcome.exception()
            sleep = state.next_action.sleep if state.next_action else 0.0
            if exc is not None:
                await on_retry(state.attempt_number, exc, sleep)

    retrying = AsyncRetrying(
        stop=stop_after_attempt(max(1, max_attempts)),
        wait=_make_wait(base, cap),
        retry=retry_if_exception(_is_retryable),
        before_sleep=_before_sleep,
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            return await fn()
    raise AssertionError("unreachable")  # pragma: no cover
