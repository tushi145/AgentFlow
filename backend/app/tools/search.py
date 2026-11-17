"""Web search tool (Tavily). Results carry provenance; nothing is synthesised here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from tavily import AsyncTavilyClient
from tavily import errors as tavily_errors

from app.llm.base import LLMError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    raw_content: str
    score: float | None
    provider: str

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.lower().removeprefix("www.")


@dataclass(frozen=True)
class SearchResponse:
    query: str
    hits: list[SearchHit]
    credits: float
    provider: str
    is_demo: bool = False


class SearchProvider(Protocol):
    name: str
    is_demo: bool

    async def search(self, query: str, *, max_results: int) -> SearchResponse: ...


class ToolError(LLMError):
    """Tool failures reuse the LLMError retry taxonomy."""


class TavilySearch:
    name = "tavily"
    is_demo = False

    def __init__(self, api_key: str, *, credits_per_query: float = 1.0, timeout_s: float = 30.0,
                 client: AsyncTavilyClient | None = None) -> None:
        self._client = client or AsyncTavilyClient(api_key=api_key)
        self._credits = credits_per_query
        self._timeout = timeout_s

    async def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        try:
            data = await self._client.search(
                query=query[:400], search_depth="basic", max_results=max(1, min(max_results, 10)),
                include_raw_content="text", include_answer=False, timeout=self._timeout)
        except (tavily_errors.InvalidAPIKeyError, tavily_errors.MissingAPIKeyError) as exc:
            raise ToolError("Tavily rejected the API key.", code="search_auth_failed") from exc
        except tavily_errors.UsageLimitExceededError as exc:
            raise ToolError("Tavily usage limit exceeded.", code="search_quota") from exc
        except tavily_errors.BadRequestError as exc:
            raise ToolError("Search request rejected.", code="search_bad_request") from exc
        except (tavily_errors.TimeoutError, TimeoutError) as exc:
            raise ToolError("Search timed out.", code="search_timeout", retryable=True) from exc
        except Exception as exc:  # network errors from httpx etc.
            raise ToolError(f"Search failed: {type(exc).__name__}", code="search_error",
                            retryable=True) from exc

        hits = []
        for r in data.get("results", []) or []:
            url = (r.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            hits.append(SearchHit(url=url, title=(r.get("title") or "").strip(),
                                  snippet=(r.get("content") or "").strip(),
                                  raw_content=(r.get("raw_content") or "").strip(),
                                  score=r.get("score"), provider=self.name))
        return SearchResponse(query=query, hits=hits, credits=self._credits, provider=self.name)
