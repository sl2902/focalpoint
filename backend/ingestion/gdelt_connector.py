"""
GDELT 2.0 Doc API ingestion connector.

Fetches news articles via the GDELT Doc API (mode=artlist) and aggregate
sentiment via a second call (mode=timelinetone). No authentication required.
Results are cached in Redis for 900 s to match GDELT's 15-minute update cadence.

Redis key pattern : gdelt:{query_hash}:{timespan}  TTL: 900 s
query_hash        : MD5 of "{query}:{country}" — stable across calls

The artlist API does not return per-article tone scores; tone is only available
via the timelinetone endpoint. Both calls are made together and the aggregate
tone (mean of non-zero 15-minute-window values) is stored in GdeltResponse.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import httpx
import redis.asyncio as aioredis
from loguru import logger
from pydantic import BaseModel

GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_CACHE_TTL = 900  # seconds — matches GDELT 15-minute update cadence

_MAX_RETRIES = 3          # number of retries after the initial attempt
_RETRY_DELAY_S = 2        # seconds between attempts
_REQUEST_TIMEOUT = httpx.Timeout(15.0)  # per-attempt timeout

# Tone thresholds (see docs/data-sources.md)
TONE_HOSTILE = -5.0      # below this: hostile/dangerous media environment
TONE_POSITIVE = 0.0      # above this: unusually positive (rare in conflict zones)


def _query_hash(query: str, country: str | None) -> str:
    """Return a stable 12-char hex hash of the query + country combination."""
    raw = f"{query}:{country or ''}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _parse_aggregate_tone(data: dict) -> float:
    """
    Compute mean of non-zero values from a GDELT timelinetone response.

    GDELT returns a 15-minute-resolution time series. Windows with no
    matching articles have value=0 and are excluded from the mean so
    they don't dilute periods of genuine coverage.

    Returns 0.0 when no non-zero data points are present.
    """
    timeline = data.get("timeline", [])
    if not timeline:
        return 0.0
    values = [
        point["value"]
        for series in timeline
        for point in series.get("data", [])
        if point.get("value", 0) != 0
    ]
    return sum(values) / len(values) if values else 0.0


class GdeltArticle(BaseModel):
    url: str
    title: str
    seendate: str           # GDELT format: "20260423T120000Z"
    sourcecountry: str = ""
    language: str = ""
    domain: str = ""
    # Note: tone is NOT returned by the artlist API — use GdeltResponse.aggregate_tone
    # for the window-level sentiment signal derived from the timelinetone endpoint.


class GdeltResponse(BaseModel):
    articles: list[GdeltArticle] = []
    aggregate_tone: float = 0.0   # mean non-zero tone across the timespan window


class GdeltConnector:
    """
    Async GDELT Doc API connector.

    Makes two calls per fetch: artlist (articles) and timelinetone (sentiment).
    Both results are packed into a GdeltResponse and cached together under a
    single Redis key. Falls back to direct API calls when Redis is unavailable.
    """

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis = redis_client

    async def fetch_articles(
        self,
        query: str,
        timespan: str = "24H",
        maxrecords: int = 20,
        country: str | None = None,
    ) -> GdeltResponse:
        """
        Return GDELT articles and aggregate tone for *query*.

        Results are served from Redis when available. On cache miss the
        connector makes two API calls (artlist + timelinetone), packages
        both into a GdeltResponse, writes to Redis, and returns the result.

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
                    data = json.loads(cached)
                    return GdeltResponse(
                        articles=[GdeltArticle(**a) for a in data["articles"]],
                        aggregate_tone=data["aggregate_tone"],
                    )
            except Exception as exc:
                logger.warning(f"Redis read failed, calling API directly: {exc}")

        artlist_params: dict[str, Any] = {
            "query": query,
            "mode": "artlist",
            "maxrecords": maxrecords,
            "timespan": timespan,
            "format": "json",
        }
        tone_params: dict[str, Any] = {
            "query": query,
            "mode": "timelinetone",
            "timespan": timespan,
            "format": "json",
        }
        if country:
            artlist_params["country"] = country
            tone_params["country"] = country

        articles: list[GdeltArticle] = []
        aggregate_tone: float = 0.0
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                    artlist_resp = await client.get(GDELT_BASE_URL, params=artlist_params)
                    artlist_resp.raise_for_status()
                    tone_resp = await client.get(GDELT_BASE_URL, params=tone_params)
                    tone_resp.raise_for_status()
                articles = GdeltResponse(**artlist_resp.json()).articles
                aggregate_tone = _parse_aggregate_tone(tone_resp.json())
                break
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        f"GDELT fetch attempt {attempt + 1}/{_MAX_RETRIES + 1} failed: {exc}"
                        f" — retrying in {_RETRY_DELAY_S}s"
                    )
                    await asyncio.sleep(_RETRY_DELAY_S)
                else:
                    logger.error(
                        f"GDELT fetch failed after {_MAX_RETRIES + 1} attempts: {exc}"
                        " — returning empty response"
                    )
                    return GdeltResponse()

        logger.info(
            f"GDELT: fetched {len(articles)} articles"
            f" for {query!r} timespan={timespan}"
            f" aggregate_tone={aggregate_tone:.2f}"
        )

        response = GdeltResponse(articles=articles, aggregate_tone=aggregate_tone)

        if self._redis:
            try:
                payload = json.dumps({
                    "articles": [a.model_dump() for a in articles],
                    "aggregate_tone": aggregate_tone,
                })
                await self._redis.set(cache_key, payload, ex=GDELT_CACHE_TTL)
            except Exception as exc:
                logger.warning(f"Redis write failed: {exc}")

        return response
