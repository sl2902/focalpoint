"""
GDELT 2.0 Doc API ingestion connector.

Fetches news articles and media signals via the GDELT Doc API (mode=artlist).
No authentication required. Results are cached in Redis for 900 s to match
GDELT's 15-minute update cadence.

Redis key pattern : gdelt:{query_hash}:{timespan}  TTL: 900 s
query_hash        : MD5 of "{query}:{country}" — stable across calls
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import redis.asyncio as aioredis
from loguru import logger
from pydantic import BaseModel

GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_CACHE_TTL = 900  # seconds — matches GDELT 15-minute update cadence

# Tone thresholds (see docs/data-sources.md)
TONE_HOSTILE = -5.0      # below this: hostile/dangerous media environment
TONE_POSITIVE = 0.0      # above this: unusually positive (rare in conflict zones)


def _query_hash(query: str, country: str | None) -> str:
    """Return a stable 12-char hex hash of the query + country combination."""
    raw = f"{query}:{country or ''}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class GdeltArticle(BaseModel):
    url: str
    title: str
    seendate: str           # GDELT format: "20260423T120000Z"
    sourcecountry: str = ""
    language: str = ""
    tone: float = 0.0       # negative = hostile; see TONE_* constants above
    domain: str = ""


class GdeltResponse(BaseModel):
    articles: list[GdeltArticle] = []


class GdeltConnector:
    """
    Async GDELT Doc API connector.

    Fetches news articles for a query term and optional country filter.
    Redis caching prevents redundant calls within the 15-minute GDELT
    update window. Falls back to direct API calls when Redis is unavailable.
    """

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis = redis_client

    async def fetch_articles(
        self,
        query: str,
        timespan: str = "24H",
        maxrecords: int = 20,
        country: str | None = None,
    ) -> list[GdeltArticle]:
        """
        Return GDELT news articles matching *query*.

        Results are served from Redis when available. On cache miss the
        connector fetches from the API, validates with Pydantic, writes to
        Redis, and returns the articles.

        Args:
            query:      Search query (keywords / phrase).
            timespan:   Lookback window — e.g. "24H", "7D" (default "24H").
            maxrecords: Max articles to return — max 250 (default 20).
            country:    Optional FIPS 2-letter country code to filter by.
        """
        qhash = _query_hash(query, country)
        cache_key = f"gdelt:{qhash}:{timespan}"

        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    logger.debug(f"Cache hit: {cache_key}")
                    return [GdeltArticle(**a) for a in json.loads(cached)]
            except Exception as exc:
                logger.warning(f"Redis read failed, calling API directly: {exc}")

        params: dict[str, Any] = {
            "query": query,
            "mode": "artlist",
            "maxrecords": maxrecords,
            "timespan": timespan,
            "format": "json",
        }
        if country:
            params["country"] = country

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(GDELT_BASE_URL, params=params)
            response.raise_for_status()

        gdelt_resp = GdeltResponse(**response.json())
        logger.info(
            f"GDELT: fetched {len(gdelt_resp.articles)} articles"
            f" for {query!r} timespan={timespan}"
        )

        if self._redis:
            try:
                await self._redis.set(
                    cache_key,
                    json.dumps([a.model_dump() for a in gdelt_resp.articles]),
                    ex=GDELT_CACHE_TTL,
                )
            except Exception as exc:
                logger.warning(f"Redis write failed: {exc}")

        return gdelt_resp.articles
