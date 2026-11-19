"""Logging setup with secret redaction and request-id propagation."""

from __future__ import annotations

import contextvars
import logging
import re
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# Patterns that look like credentials. Redaction is a safety net; code must still never log secrets.
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|x-api-key|password|secret|token)(\"?\s*[:=]\s*\"?)([^\s\",]+)"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 3:
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            return True
        redacted = redact(msg)
        if redacted != msg:
            record.msg, record.args = redacted, ()
        return True


class RedactingFormatter(logging.Formatter):
    """Redacts the *fully formatted* record, including exception tracebacks and stack info,
    which the filter (message only) cannot reach."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter(
        "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "httpx2", "trafilatura", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
