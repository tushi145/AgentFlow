"""SSRF-safe page fetching + main-text extraction.

Only URLs previously returned by the search provider for the same task may be fetched (enforced
by the caller via `allowed_urls`), and every hop is checked against private address ranges.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from app.tools.search import ToolError

MAX_BYTES = 2_000_000
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
USER_AGENT = "AgentFlow/0.1 (research assistant; +https://github.com/)"


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified)


async def assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("Only public http(s) URLs can be fetched.", code="url_blocked")
    if parsed.port not in (None, 80, 443):
        raise ToolError("Non-standard ports are not allowed.", code="url_blocked")
    host = parsed.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolError("Host could not be resolved.", code="dns_error") from exc
    ips = {i[4][0] for i in infos}
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise ToolError("URL resolves to a non-public address.", code="url_blocked")


async def fetch_page(url: str, *, timeout_s: float = 15.0, max_redirects: int = 3,
                     client: httpx.AsyncClient | None = None) -> FetchedPage:
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False,
                                         headers={"User-Agent": USER_AGENT})
    try:
        current = url
        for _ in range(max_redirects + 1):
            await assert_public_url(current)
            try:
                async with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            raise ToolError("Redirect without location.", code="fetch_error")
                        current = urljoin(current, loc)
                        continue
                    if resp.status_code >= 400:
                        raise ToolError(f"Page returned HTTP {resp.status_code}.",
                                        code="fetch_http_error",
                                        retryable=resp.status_code in (429, 502, 503, 504))
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype and ctype not in ALLOWED_CONTENT_TYPES:
                        raise ToolError(f"Unsupported content type '{ctype}'.",
                                        code="fetch_unsupported")
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            break
            except httpx.TimeoutException as exc:
                raise ToolError("Page fetch timed out.", code="fetch_timeout", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise ToolError(f"Page fetch failed: {type(exc).__name__}", code="fetch_error",
                                retryable=True) from exc
            html_text = body.decode(resp.encoding or "utf-8", errors="replace")
            text = await asyncio.to_thread(
                trafilatura.extract, html_text, url=current, include_comments=False,
                include_tables=True, favor_precision=True) if ctype != "text/plain" else html_text
            meta_title = ""
            if ctype != "text/plain":
                meta = await asyncio.to_thread(trafilatura.extract_metadata, html_text)
                meta_title = (getattr(meta, "title", "") or "") if meta else ""
            return FetchedPage(url=url, final_url=current, title=meta_title, text=text or "")
        raise ToolError("Too many redirects.", code="fetch_error")
    finally:
        if own:
            await client.aclose()
