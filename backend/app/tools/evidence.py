"""Evidence retrieval: chunk source texts and rank chunks for a claim.

Default ranking is BM25 (lexical, local, free). If an embedding function is supplied (e.g.
Gemini embeddings when GEMINI_API_KEY is set) a hybrid score is used. The method actually used
is reported so evaluation results are interpretable.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+")
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass(frozen=True)
class Chunk:
    ref: str
    text: str
    index: int


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def chunk_text(ref: str, text: str, *, size: int = 900, overlap: int = 150) -> list[Chunk]:
    text = text.strip()
    if not text:
        return []
    chunks, start, i = [], 0, 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind(". ", start + size // 2, end)
            end = cut + 1 if cut != -1 else end
        chunks.append(Chunk(ref=ref, text=text[start:end].strip(), index=i))
        if end >= len(text):
            break
        start, i = max(end - overlap, start + 1), i + 1
    return chunks


class EvidenceIndex:
    def __init__(self, chunks: list[Chunk], embed: EmbedFn | None = None) -> None:
        self.chunks = chunks
        self._bm25 = BM25Okapi([tokenize(c.text) or ["_"] for c in chunks]) if chunks else None
        self._embed = embed
        self._vectors: np.ndarray | None = None
        self.method = "bm25"

    async def _ensure_vectors(self) -> None:
        if self._embed is None or self._vectors is not None or not self.chunks:
            return
        try:
            vecs = np.asarray(await self._embed([c.text for c in self.chunks]), dtype=float)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            self._vectors = vecs / np.where(norms == 0, 1, norms)
            self.method = "hybrid_bm25_embeddings"
        except Exception:
            self._embed = None  # degrade to BM25; method stays "bm25"

    async def search(self, query: str, *, k: int = 4, refs: set[str] | None = None) -> list[ScoredChunk]:
        if not self.chunks or self._bm25 is None:
            return []
        await self._ensure_vectors()
        bm = np.asarray(self._bm25.get_scores(tokenize(query) or ["_"]), dtype=float)
        if bm.max() > 0:
            bm = bm / bm.max()
        scores = bm
        if self._vectors is not None and self._embed is not None:
            q = np.asarray((await self._embed([query]))[0], dtype=float)
            q = q / (np.linalg.norm(q) or 1)
            scores = 0.5 * bm + 0.5 * (self._vectors @ q)
        order = np.argsort(-scores)
        out: list[ScoredChunk] = []
        for idx in order:
            c = self.chunks[int(idx)]
            if refs is not None and c.ref not in refs:
                continue
            out.append(ScoredChunk(c, float(scores[int(idx)])))
            if len(out) >= k:
                break
        return out
