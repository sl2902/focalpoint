"""
GDELT 2.0 Doc API ingestion connector.

Fetches news articles via the GDELT Doc API (mode=artlist) and aggregate
sentiment via a second call (mode=timelinetone). No authentication required.
Default cache TTL is 900 s (matches GDELT's 15-minute update cadence) so the
scheduler always gets fresh articles. Callers that tolerate stale data (e.g.
the /query route) may pass a longer cache_ttl to reduce API pressure.

Redis key pattern : gdelt:articles:{query}:{timespan}  TTL: caller-controlled

The artlist API does not return per-article tone scores; tone is only available
via the timelinetone endpoint. Both calls are made together and the aggregate
tone (mean of non-zero 15-minute-window values) is stored in GdeltResponse.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import redis.asyncio as aioredis
from loguru import logger
from pydantic import BaseModel

from backend.config import settings

GDELT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_CACHE_TTL = settings.GDELT_DOC_CACHE_TTL

# Query variants tried in order — specific phrases filter out sports/entertainment
# noise while surfacing press-safety articles. If the first variant returns
# 0 articles (empty or 429), the next is tried before falling back to web search.
_JOURNALIST_QUERY_VARIANTS = ["journalist safety {}", "press freedom {}", "media censorship {}"]

# Prevents simultaneous GDELT Doc API requests from hammering the no-auth endpoint.
_GDELT_SEM = asyncio.Semaphore(1)

_MAX_RETRIES = 3          # number of retries after the initial attempt
_RETRY_DELAY_S = 2        # seconds between attempts
_REQUEST_TIMEOUT = httpx.Timeout(15.0)  # per-attempt timeout

# Tone thresholds (see docs/data-sources.md)
TONE_HOSTILE = -5.0      # below this: hostile/dangerous media environment
TONE_POSITIVE = 0.0      # above this: unusually positive (rare in conflict zones)


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
        cache_ttl: int = GDELT_CACHE_TTL,
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
            cache_ttl:  Redis TTL in seconds (default 900). Pass a longer value
                        (e.g. 86400) for callers that tolerate stale articles.
        """
        cache_key = f"gdelt:articles:{query}:{timespan}"

        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    logger.debug(f"gdelt: cache hit for query='{query}' timespan='{timespan}'")
                    data = json.loads(cached)
                    return GdeltResponse(
                        articles=[GdeltArticle(**a) for a in data["articles"]],
                        aggregate_tone=data["aggregate_tone"],
                    )
            except Exception as exc:
                logger.warning(f"Redis read failed, calling API directly: {exc}")
        else:
            logger.warning(f"gdelt: no Redis client — cache bypassed for query='{query}'")

        logger.debug(f"gdelt: cache miss for query='{query}' timespan='{timespan}' — hitting API")

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

        if _GDELT_SEM.locked():
            logger.warning(f"gdelt: semaphore busy — queuing request for query={query!r}")
        articles: list[GdeltArticle] = []
        aggregate_tone: float = 0.0
        async with _GDELT_SEM:
          async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            for attempt in range(_MAX_RETRIES + 1):
                # Phase 1: network call — retry on timeout or HTTP error.
                try:
                    artlist_resp, tone_resp = await asyncio.gather(
                        client.get(GDELT_BASE_URL, params=artlist_params),
                        client.get(GDELT_BASE_URL, params=tone_params),
                    )
                    artlist_resp.raise_for_status()
                    tone_resp.raise_for_status()
                except Exception as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        logger.warning(
                            f"gdelt: 429 Too Many Requests for query={query!r}"
                            " — returning empty response (rate limit)"
                        )
                        return GdeltResponse()
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
                    continue

                # Phase 2: parse — GDELT returns empty or non-JSON bodies when
                # it has no results. Treat these as 0 articles and stop retrying
                # — a bad parse won't improve on the next attempt.
                artlist_body = artlist_resp.text.strip()
                try:
                    articles = GdeltResponse(**artlist_resp.json()).articles if artlist_body else []
                except (json.JSONDecodeError, ValueError):
                    logger.debug(
                        f"GDELT: non-JSON artlist body for {query!r} — 0 articles"
                        f" (body[:80]={artlist_body[:80]!r})"
                    )
                    articles = []

                tone_body = tone_resp.text.strip()
                try:
                    aggregate_tone = _parse_aggregate_tone(tone_resp.json()) if tone_body else 0.0
                except (json.JSONDecodeError, ValueError):
                    aggregate_tone = 0.0

                break

        logger.info(
            f"GDELT: fetched {len(articles)} articles"
            f" for {query!r} timespan={timespan}"
            f" aggregate_tone={aggregate_tone:.2f}"
        )

        response = GdeltResponse(articles=articles, aggregate_tone=aggregate_tone)

        if self._redis and articles:
            try:
                payload = json.dumps({
                    "articles": [a.model_dump() for a in articles],
                    "aggregate_tone": aggregate_tone,
                })
                await self._redis.set(cache_key, payload, ex=cache_ttl)
                logger.debug(f"gdelt: cached articles for query='{query}' timespan='{timespan}' TTL={cache_ttl}s key='gdelt:articles:{query}:{timespan}'")
            except Exception as exc:
                logger.warning(f"Redis write failed: {exc}")

        return response

    async def fetch_articles_for_region(
        self,
        region: str,
        timespan: str = "24H",
        maxrecords: int = 20,
        country: str | None = None,
        cache_ttl: int = GDELT_CACHE_TTL,
    ) -> GdeltResponse:
        """
        Fetch journalist-safety articles for *region* using rotating query variants.

        Tries each variant in _JOURNALIST_QUERY_VARIANTS ("journalist safety {region}",
        "press freedom {region}", "media censorship {region}") and returns the first response that
        contains articles. If all variants return empty (including 429-induced empty
        responses), returns the last empty GdeltResponse so the caller can fall
        through to web search as normal.

        Each variant has its own Redis cache key, so a cached empty result for one
        term does not prevent the next from being attempted.
        """
        last: GdeltResponse = GdeltResponse()
        for template in _JOURNALIST_QUERY_VARIANTS:
            query = template.format(region)
            result = await self.fetch_articles(
                query, timespan=timespan, maxrecords=maxrecords, country=country, cache_ttl=cache_ttl
            )
            if result.articles:
                return result
            logger.info(f"gdelt: query={query!r} returned 0 articles — trying next variant")
            last = result
        logger.info(f"gdelt: all query variants exhausted for region={region!r} — returning empty")
        return last
