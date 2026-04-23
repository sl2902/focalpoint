"""
ACLED ingestion connector — DISABLED.

This connector is preserved for reactivation if ACLED API access is granted.
ACLED requires institutional OAuth2 credentials that are not currently
available. The active conflict events source is GDELT Cloud:
  backend/ingestion/gdeltcloud_connector.py

To reactivate:
1. Rename this file to acled_connector.py
2. Restore ACLED_* env vars in .env (template kept commented in .env.example)
3. Update backend/alerts/severity_scorer.py to import AcledEvent from
   acled_connector and pass AcledEvent lists to score_severity()
4. Run: uv run pytest

Original connector below — no modifications.
---

ACLED ingestion connector.

Fetches conflict events from the ACLED API using OAuth2 Bearer token auth.
Tokens and event responses are cached in Redis to avoid redundant API calls.

Redis key pattern : acled:{country}:{page}  TTL: 3600 s
Token key         : acled:token              TTL: expires_in - 60 s
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import redis.asyncio as aioredis
from loguru import logger
from pydantic import BaseModel

from backend.config import settings as _settings

ACLED_BASE_URL = "https://api.acleddata.com/acled/read"
ACLED_TOKEN_REDIS_KEY = "acled:token"
ACLED_CACHE_TTL = 3600  # seconds
_TOKEN_TTL_BUFFER = 60  # shaved off token expiry to avoid using stale tokens

# Fields requested from the ACLED API — only what alert generation needs.
_ACLED_FIELDS = (
    "event_id_cnty|event_date|event_type|actor1|actor2"
    "|country|location|latitude|longitude|fatalities|notes"
)


class AcledEvent(BaseModel):
    event_id_cnty: str
    event_date: str
    event_type: str
    actor1: str
    actor2: str = ""
    country: str
    location: str
    latitude: float
    longitude: float
    fatalities: int = 0
    notes: str = ""


class AcledResponse(BaseModel):
    status: int
    success: bool
    count: int
    data: list[AcledEvent]


class AcledConnector:
    """
    Async ACLED event connector.

    Handles OAuth2 token acquisition and refresh, cursor-style page
    pagination, and Redis caching of both tokens and event payloads.
    Falls back to direct API calls when Redis is unavailable.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        app_settings: Any = None,
    ) -> None:
        self._redis = redis_client
        self._settings = app_settings or _settings

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        """Return a valid Bearer token, preferring the Redis-cached value."""
        if self._redis:
            try:
                cached = await self._redis.get(ACLED_TOKEN_REDIS_KEY)
                if cached:
                    return cached.decode()
            except Exception as exc:
                logger.warning(f"Redis token read failed: {exc}")

        response = await client.post(
            self._settings.ACLED_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "username": self._settings.ACLED_USERNAME,
                "password": self._settings.ACLED_PASSWORD,
                "grant_type": "password",
                "client_id": "acled",
            },
        )
        response.raise_for_status()
        payload = response.json()
        token: str = payload["access_token"]
        expires_in: int = payload.get("expires_in", 3600)
        ttl = max(expires_in - _TOKEN_TTL_BUFFER, 1)

        if self._redis:
            try:
                await self._redis.set(ACLED_TOKEN_REDIS_KEY, token, ex=ttl)
            except Exception as exc:
                logger.warning(f"Redis token write failed: {exc}")

        return token

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def fetch_events(
        self,
        country: str,
        page: int = 1,
        limit: int = 20,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[AcledEvent]:
        """
        Return ACLED conflict events for *country*.

        Results are served from Redis when available.  On cache miss the
        connector fetches from the API, validates the payload with Pydantic,
        writes the result to Redis, and returns the events.

        Falls back to a direct API call if Redis is unavailable — latency
        increases but functionality is preserved.

        Args:
            country:   Country name as recognised by the ACLED API.
            page:      1-based page number (default 1).
            limit:     Max events per page (default 20, API max 5000).
            date_from: Start date filter, ISO format YYYY-MM-DD (inclusive).
            date_to:   End date filter, ISO format YYYY-MM-DD (inclusive).
        """
        cache_key = f"acled:{country}:{page}"

        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    logger.debug(f"Cache hit: {cache_key}")
                    return [AcledEvent(**e) for e in json.loads(cached)]
            except Exception as exc:
                logger.warning(f"Redis read failed, calling API directly: {exc}")

        # Build parameterised request — never interpolate user values directly.
        params: dict[str, Any] = {
            "_format": "json",
            "country": country,
            "limit": limit,
            "page": page,
            "fields": _ACLED_FIELDS,
        }
        if date_from and date_to:
            params["event_date"] = f"{date_from}|{date_to}"
            params["event_date_where"] = "BETWEEN"

        async with httpx.AsyncClient(timeout=30.0) as client:
            token = await self._get_token(client)
            response = await client.get(
                ACLED_BASE_URL,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

        acled_resp = AcledResponse(**response.json())
        logger.info(
            f"ACLED: fetched {acled_resp.count} events for {country!r}, page {page}"
        )

        if self._redis:
            try:
                await self._redis.set(
                    cache_key,
                    json.dumps([e.model_dump() for e in acled_resp.data]),
                    ex=ACLED_CACHE_TTL,
                )
            except Exception as exc:
                logger.warning(f"Redis write failed: {exc}")

        return acled_resp.data
