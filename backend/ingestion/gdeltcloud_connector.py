"""
GDELT Cloud conflict events connector.

Fetches geolocated conflict events from the GDELT Cloud Events API using
Bearer token authentication (GDELT_CLOUD_API_KEY).

IMPORTANT — FREE TIER LIMIT: 100 queries/month.
Cache TTL is set to 28800 s (8 hours) to stay within quota.
Never reduce this TTL without upgrading the API plan.

Redis key pattern : gdeltcloud:{country}:{days}:{has_fatalities}  TTL: 28800 s

Real API response structure (confirmed from live curl):
  {
    "success": true,
    "data": [                    ← top-level key is "data", not "events"
      {
        "id": "conflict_...",
        "event_date": "2026-04-23",
        "category": "...",       ← our event_type
        "subcategory": "...",    ← our sub_event_type
        "fatalities": 2,         ← top-level int
        "summary": "...",
        "geo": {                 ← nested object
          "country": "...",
          "admin1": "...",
          "location": "...",
          "latitude": 32.009,
          "longitude": 35.311
        },
        "actors": [              ← list; extract by role field
          {"name": "...", "country": "...", "role": "actor1"},
          {"name": "...", "country": "...", "role": "actor2"}
        ],
        "metrics": {             ← nested object
          "significance": 0.374,
          "goldstein_scale": -9,
          "confidence": 0.83,
          "article_count": 1
        }
      }
    ]
  }
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import httpx
import redis.asyncio as aioredis
from loguru import logger
from pydantic import BaseModel, ConfigDict

from backend.config import settings as _settings

GDELT_CLOUD_BASE_URL = "https://gdeltcloud.com/api/v2/events"

# 8 hours — preserves the 100 query/month free tier limit.
# Do NOT lower this without upgrading the API plan.
GDELT_CLOUD_CACHE_TTL = 28800


# ---------------------------------------------------------------------------
# Public Pydantic models (callers import these)
# ---------------------------------------------------------------------------


class GdeltCloudGeo(BaseModel):
    """Nested geo block from the GDELT Cloud API."""

    country: str | None = None
    region: str | None = None
    admin1: str | None = None       # first-level administrative division
    location: str | None = None     # human-readable place name
    latitude: float | None = None
    longitude: float | None = None


class GdeltCloudActor(BaseModel):
    """Single actor entry from the actors list in a GDELT Cloud event."""

    name: str | None = None
    country: str | None = None
    role: str | None = None         # "actor1" or "actor2"


class GdeltCloudMetrics(BaseModel):
    """Nested metrics block from the GDELT Cloud API."""

    significance: float | None = None
    goldstein_scale: float | None = None   # conflict intensity -10 to +10
    confidence: float | None = None        # 0.0–1.0
    article_count: int | None = None


class GdeltCloudEvent(BaseModel):
    """
    Parsed conflict event from the GDELT Cloud Events API.

    Only id and event_date are guaranteed present. All other fields are
    optional because coverage varies by region and reporting density.
    Callers must handle None before using numeric fields.

    Field mapping from raw API:
      category    → event_type
      subcategory → sub_event_type
      summary     → summary  (free-text description)
      geo.*       → geo nested object
      actors[]    → actors list (filter by .role for actor1/actor2)
      metrics.*   → metrics nested object
    """

    id: str
    event_date: str

    event_type: str | None = None       # from API field "category"
    sub_event_type: str | None = None   # from API field "subcategory"
    fatalities: int | None = None       # top-level in API; None ≠ zero
    has_fatalities: bool | None = None
    title: str | None = None
    summary: str | None = None

    geo: GdeltCloudGeo | None = None
    actors: list[GdeltCloudActor] = []
    metrics: GdeltCloudMetrics | None = None


class GdeltCloudResponse(BaseModel):
    """Wrapper returned by the connector after parsing the raw API response."""

    events: list[GdeltCloudEvent] = []


# ---------------------------------------------------------------------------
# Internal raw-API models (used only inside this module for parsing)
# ---------------------------------------------------------------------------


class _RawGeo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    country: str | None = None
    region: str | None = None
    admin1: str | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class _RawActor(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    country: str | None = None
    role: str | None = None


class _RawMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")
    significance: float | None = None
    goldstein_scale: float | None = None
    confidence: float | None = None
    article_count: int | None = None


class _RawEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    event_date: str
    category: str | None = None
    subcategory: str | None = None
    fatalities: int | None = None
    has_fatalities: bool | None = None
    title: str | None = None
    summary: str | None = None
    geo: _RawGeo | None = None
    actors: list[_RawActor] = []
    metrics: _RawMetrics | None = None


class _RawApiResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    success: bool = True
    data: list[_RawEvent] = []


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_event(raw: _RawEvent) -> GdeltCloudEvent:
    """Convert a raw API event into a clean GdeltCloudEvent."""
    geo = (
        GdeltCloudGeo(
            country=raw.geo.country,
            region=raw.geo.region,
            admin1=raw.geo.admin1,
            location=raw.geo.location,
            latitude=raw.geo.latitude,
            longitude=raw.geo.longitude,
        )
        if raw.geo
        else None
    )
    metrics = (
        GdeltCloudMetrics(
            significance=raw.metrics.significance,
            goldstein_scale=raw.metrics.goldstein_scale,
            confidence=raw.metrics.confidence,
            article_count=raw.metrics.article_count,
        )
        if raw.metrics
        else None
    )
    actors = [
        GdeltCloudActor(name=a.name, country=a.country, role=a.role)
        for a in raw.actors
    ]
    return GdeltCloudEvent(
        id=raw.id,
        event_date=raw.event_date,
        event_type=raw.category,
        sub_event_type=raw.subcategory,
        fatalities=raw.fatalities,
        has_fatalities=raw.has_fatalities,
        title=raw.title,
        summary=raw.summary,
        geo=geo,
        actors=actors,
        metrics=metrics,
    )


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
        has_fatalities: bool = True,
    ) -> list[GdeltCloudEvent]:
        """
        Return GDELT Cloud conflict events for *country*.

        Results are served from Redis when available. On cache miss the
        connector calls the API, parses the nested response structure into
        GdeltCloudEvent models, writes to Redis with a 28800 s TTL, and
        returns the events.

        IMPORTANT: Each cache miss consumes one of the 100 free monthly
        quota. The 8-hour TTL is intentional — do not reduce it.

        Args:
            country:        Country name recognised by the GDELT Cloud API.
            days:           Number of calendar days to query (inclusive window ending
                            today). `days=1` queries today only; `days=7` queries the
                            last 7 days. Sent to the API as `date_start` / `date_end`.
            limit:          Max events to return (default 20).
            has_fatalities: When True (default) filters to confirmed-fatality events only.
                            Set False for countries where this filter returns 0 results —
                            the fatalities field will be None rather than absent.
                            See docs/data-sources.md — GDELT Cloud section.
        """
        date_end = datetime.date.today()
        date_start = date_end - datetime.timedelta(days=days - 1)
        date_end_str = date_end.strftime("%Y-%m-%d")
        date_start_str = date_start.strftime("%Y-%m-%d")

        cache_key = f"gdeltcloud:{country}:{days}:{has_fatalities}"

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
            "event_family": "conflict",
            "date_start": date_start_str,
            "date_end": date_end_str,
            "sort": "recent",
            "limit": limit,
        }
        if has_fatalities:
            params["has_fatalities"] = "true"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                GDELT_CLOUD_BASE_URL,
                params=params,
                headers=self._auth_headers(),
            )
            response.raise_for_status()

        api_resp = _RawApiResponse(**response.json())
        events = [_parse_event(raw) for raw in api_resp.data]

        logger.info(
            f"GDELT Cloud: fetched {len(events)} events"
            f" for {country!r}, {date_start_str} → {date_end_str}"
            f" — quota reminder: 100 queries/month free tier"
        )

        if self._redis:
            try:
                await self._redis.set(
                    cache_key,
                    json.dumps([e.model_dump() for e in events]),
                    ex=GDELT_CLOUD_CACHE_TTL,
                )
            except Exception as exc:
                logger.warning(f"Redis write failed: {exc}")

        return events
