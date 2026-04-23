"""FastAPI dependency providers for FocalPoint routes.

All stateful resources (Redis, CPJ, alert generator) live on app.state,
initialised once in the lifespan. Connector instances that need a Redis
client are constructed per-request — they are lightweight wrappers.

Overriding any of these in tests via app.dependency_overrides keeps
routes fully decoupled from real network calls.
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, Request

from backend.ingestion.cpj_connector import CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.processors.alert_generator import AlertGenerator


async def get_redis(request: Request) -> aioredis.Redis | None:
    """Return the app-level Redis client, or None if unavailable."""
    return getattr(request.app.state, "redis", None)


async def get_gdelt_cloud_connector(
    redis: Annotated[aioredis.Redis | None, Depends(get_redis)],
) -> GdeltCloudConnector:
    return GdeltCloudConnector(redis_client=redis)


async def get_gdelt_connector(
    redis: Annotated[aioredis.Redis | None, Depends(get_redis)],
) -> GdeltConnector:
    return GdeltConnector(redis_client=redis)


async def get_cpj_connector(request: Request) -> CPJConnector:
    return request.app.state.cpj


async def get_alert_generator(request: Request) -> AlertGenerator:
    return request.app.state.alert_generator
