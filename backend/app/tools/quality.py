"""Deterministic research-quality checks: URL canonicalisation, near-duplicate content detection,
relevance/diversity selection of search hits and evidence-quote grounding.

Everything here is code, not model judgement, so it is cheap, reproducible and cannot be
talked out of its decision by retrieved text.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ----------------------------------------------------------------------------- URLs
_TRACKING_PARAMS = re.compile(r"^(utm_[a-z]+|gclid|fbclid|dclid|msclkid|mc_cid|mc_eid|_ga|_hs\w+|"
                              r"ref|ref_src|igshid|spm|cmpid|ocid)$", re.I)


def canonical_url(url: str) -> str:
    """Scheme-insensitive identity key: lower-case host without `www.`/default port, no fragment,
    no tracking parameters, sorted query, no trailing slash. Used only for de-duplication — the
    original URL is what gets stored and cited."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if parts.scheme not in ("http", "https"):
        return url.strip()
    host = (parts.hostname or "").lower().removeprefix("www.")
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not _TRACKING_PARAMS.match(k))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("", netloc, path, urlencode(query), ""))


# ----------------------------------------------------------------------------- text normalisation
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).lower()
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _shingles(tokens: Sequence[str], n: int) -> set[str]:
    if len(tokens) < n:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


# ----------------------------------------------------------------------------- near duplicates
NEAR_DUPLICATE_JACCARD = 0.85
MIN_DEDUP_TOKENS = 40  # very short texts (snippets) are not compared


@dataclass(frozen=True)
class Fingerprint:
    digest: str
    shingles: frozenset[str]

    @classmethod
    def of(cls, text: str) -> Fingerprint:
        tokens = normalise(text).split()[:3000]
        hashed = frozenset(hashlib.blake2b(s.encode(), digest_size=8).hexdigest()
                           for s in _shingles(tokens, 8)) if len(tokens) >= MIN_DEDUP_TOKENS else frozenset()
        return cls(hashlib.sha256(" ".join(tokens).encode()).hexdigest(), hashed)

    def similarity(self, other: Fingerprint) -> float:
        if self.digest == other.digest and self.shingles:
            return 1.0
        if not self.shingles or not other.shingles:
            return 0.0
        return len(self.shingles & other.shingles) / len(self.shingles | other.shingles)


# ----------------------------------------------------------------------------- hit selection
H = TypeVar("H")
MIN_RELEVANCE = 0.30
MAX_PER_DOMAIN = 2


def select_hits(hits: Iterable[tuple[str, H]], *, limit: int,
                score: Callable[[H], float | None], url: Callable[[H], str],
                domain: Callable[[H], str], min_relevance: float = MIN_RELEVANCE,
                max_per_domain: int = MAX_PER_DOMAIN) -> tuple[list[tuple[str, H]], dict[str, int]]:
    """Best-first, de-duplicated (canonical URL), relevance-thresholded, domain-diverse selection.

    Hits with no score (provider gave none) are kept. If every hit is below the threshold the
    single best one is kept so the subtask is not silently left without evidence. Returns the
    chosen hits and counts of what was dropped (reported to the UI and metrics)."""
    def _rank(qh: tuple[str, H]) -> float:
        sc = score(qh[1])
        return -(sc if sc is not None else 1.0)

    ranked = sorted(hits, key=_rank)
    seen: set[str] = set()
    unique: list[tuple[str, H]] = []
    stats = {"duplicate_urls": 0, "low_relevance": 0, "domain_capped": 0}
    for qh in ranked:
        key = canonical_url(url(qh[1]))
        if key in seen:
            stats["duplicate_urls"] += 1
            continue
        seen.add(key)
        unique.append(qh)
    relevant = [qh for qh in unique if (sc := score(qh[1])) is None or sc >= min_relevance]
    stats["low_relevance"] = len(unique) - len(relevant)
    if not relevant and unique:
        relevant, stats["low_relevance"] = unique[:1], len(unique) - 1

    # Two passes: first one source per domain (diversity), then up to max_per_domain.
    chosen: list[tuple[str, H]] = []
    per_domain: dict[str, int] = {}
    for cap in (1, max_per_domain):
        for qh in relevant:
            if len(chosen) >= limit:
                break
            if qh in chosen:
                continue
            d = domain(qh[1])
            if per_domain.get(d, 0) < cap:
                chosen.append(qh)
                per_domain[d] = per_domain.get(d, 0) + 1
    stats["domain_capped"] = max(0, min(limit, len(relevant)) - len(chosen))
    return chosen, stats


# ----------------------------------------------------------------------------- quote grounding
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")
QUOTE_SHINGLE_MATCH = 0.6


def quote_found(quote: str, text: str) -> bool:
    """Is `quote` (claimed to be verbatim) actually present in `text`?

    Tolerates case, punctuation, whitespace and typographic quote differences, and `...` elisions
    (each elided segment must appear). Falls back to 4-gram overlap >= 60% for long quotes, which
    absorbs a dropped word or two but not invented content."""
    q = normalise(quote)
    if not q:
        return False
    t = normalise(text)
    if q in t:
        return True
    segments = [normalise(s) for s in _ELLIPSIS.split(quote) if normalise(s)]
    if len(segments) > 1 and all(s in t for s in segments):
        return True
    q_tokens = q.split()
    if len(q_tokens) < 8:
        return False
    t_tokens = t.split()
    # Anchors (numbers and capitalised names) must all be present: fuzzy matching may absorb a
    # dropped filler word, never a substituted entity or figure ("India" for "China", 520 for 447).
    if not _anchors(quote) <= set(t_tokens):
        return False
    q_sh = _shingles(q_tokens, 4)
    t_sh = _shingles(t_tokens, 4)
    return len(q_sh & t_sh) / len(q_sh) >= QUOTE_SHINGLE_MATCH


_ANCHOR = re.compile(r"\b(?:\d[\d.,]*|[A-Z][A-Za-z0-9-]+)")
_STOP = {"the", "a", "an", "in", "on", "of", "and", "or", "but", "this", "that", "it", "its", "as",
         "for", "to", "by", "with", "at", "from", "while", "however", "also"}


def _anchors(quote: str) -> set[str]:
    out: set[str] = set()
    for m in _ANCHOR.finditer(unicodedata.normalize("NFKC", quote)):
        for tok in normalise(m.group(0)).split():
            if tok not in _STOP:
                out.add(tok)
    return out


def locate_quote(quote: str, cited: Sequence[str], texts: dict[str, str]) -> tuple[str, list[str]]:
    """Ground a fact's quote. Returns (status, refs):
    * ("verified", cited refs that contain the quote)
    * ("corrected", other provided refs that contain it — the citation was wrong, now fixed)
    * ("ungrounded", []) — the quote appears in none of the provided sources."""
    hits = [r for r in cited if r in texts and quote_found(quote, texts[r])]
    if hits:
        return "verified", hits
    others = [r for r, txt in texts.items() if r not in cited and quote_found(quote, txt)]
    if others:
        return "corrected", others[:2]
    return "ungrounded", []
