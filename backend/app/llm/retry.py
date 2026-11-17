"""Exponential backoff with jitter for retryable provider/tool errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import (AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt,
                      wait_random_exponential)

from app.llm.base import LLMError

R = TypeVar("R")

OnRetry = Callable[[int, BaseException, float], Awaitable[None]]


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, LLMError) and exc.retryable


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
        wait=wait_random_exponential(multiplier=base, max=cap),
        retry=retry_if_exception(_is_retryable),
        before_sleep=_before_sleep,
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            return await fn()
    raise AssertionError("unreachable")  # pragma: no cover
