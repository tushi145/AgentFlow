"""Live-suite setup.

Live tests print real model output (report Markdown with ✅/⚠️, non-ASCII source titles). On a
Windows console (cp1252) printing those under `pytest -s` raises UnicodeEncodeError *after* the
paid API call has been made, so make the console streams lossy-but-safe for this suite.
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # pragma: no cover - stream already detached/closed
            pass
