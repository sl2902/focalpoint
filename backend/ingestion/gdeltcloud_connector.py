"""
GDELT Cloud conflict events connector.

Fetches geolocated conflict events from the GDELT Cloud Events API using
Bearer token authentication (GDELT_CLOUD_API_KEY).

IMPORTANT — FREE TIER LIMIT: 100 queries/month.
Cache TTL is set to 28800 s (8 hours) to stay within quota.
Never reduce this TTL without upgrading the API plan.

Redis key pattern : gdeltcloud:{country}:{days}  TTL: 28800 s
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import redis.asyncio as aioredis
from loguru import logger
from pydantic import BaseModel

from backend.config import settings as _settings

GDELT_CLOUD_BASE_URL = "https://api.gdeltproject.org/api/v2/events/events"

# 8 hours — preserves the 100 query/month free tier limit.
# Do NOT lower this without upgrading the API plan.
GDELT_CLOUD_CACHE_TTL = 28800


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GdeltCloudEvent(BaseModel):
    """
    Single conflict event from the GDELT Cloud Events API.

    Only id and event_date are guaranteed present. All other fields are
    optional because GDELT Cloud events vary in coverage by region and
    reporting density. Callers must handle None before using numeric fields.
    """

    id: str
    event_date: str

    disorder_type: str | None = None      # e.g. "Political Violence", "Demonstrations"
    event_type: str | None = None         # CAMEO-based event description
    sub_event_type: str | None = None     # more granular classification
    actor1: str | None = None
    actor2: str | None = None
    fatalities: int | None = None         # None means unreported, not zero
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = None
    admin1: str | None = None             # first-level admin division
    location: str | None = None
    notes: str | None = None
    confidence: int | None = None         # GDELT confidence score 0–100


class GdeltCloudResponse(BaseModel):
    events: list[GdeltCloudEvent] = []
    count: int = 0


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class GdeltCloudConnector:
    """
    Async GDELT Cloud conflict events connector.

    Uses Bearer token authentication — GDELT_CLOUD_API_KEY is sent in the
    Authorization header on every request. There is no token refresh flow;
    the key itself is the long-lived credential.

    Results are cached in Redis for 28800 s (8 hours) to protect the
    100 query/month free tier. Falls back to direct API calls when Redis
    is unavailable — latency increases but functionality is preserved.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        app_settings: Any = None,
    ) -> None:
        self._redis = redis_client
        self._settings = app_settings or _settings

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._settings.GDELT_CLOUD_API_KEY}"}

    async def fetch_events(
        self,
        country: str,
        days: int = 1,
        limit: int = 20,
    ) -> list[GdeltCloudEvent]:
        """
        Return GDELT Cloud conflict events for *country*.

        Results are served from Redis when available. On cache miss the
        connector calls the API, validates the payload with Pydantic,
        writes to Redis with a 28800 s TTL, and returns the events.

        IMPORTANT: Each cache miss consumes one of the 100 free monthly
        quota. The 8-hour TTL is intentional — do not reduce it.

        Args:
            country: ISO country name or code recognised by the GDELT API.
            days:    Lookback window in days (default 1).
            limit:   Max events to return (default 20).
        """
        cache_key = f"gdeltcloud:{country}:{days}"

        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    logger.debug(f"Cache hit: {cache_key}")
                    return [GdeltCloudEvent(**e) for e in json.loads(cached)]
            except Exception as exc:
                logger.warning(f"Redis read failed, calling API directly: {exc}")

        params: dict[str, Any] = {
            "country": country,
            "days": days,
            "limit": limit,
            "format": "json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                GDELT_CLOUD_BASE_URL,
                params=params,
                headers=self._auth_headers(),
            )
            response.raise_for_status()

        gdelt_resp = GdeltCloudResponse(**response.json())
        logger.info(
            f"GDELT Cloud: fetched {gdelt_resp.count} events"
            f" for {country!r}, days={days}"
            f" — quota reminder: 100 queries/month free tier"
        )

        if self._redis:
            try:
                await self._redis.set(
                    cache_key,
                    json.dumps([e.model_dump() for e in gdelt_resp.events]),
                    ex=GDELT_CLOUD_CACHE_TTL,
                )
            except Exception as exc:
                logger.warning(f"Redis write failed: {exc}")

        return gdelt_resp.events
