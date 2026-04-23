"""FocalPoint FastAPI application.

Lifespan initialises shared resources once at startup:
  - Redis client (lazy connection — falls back gracefully if unavailable)
  - CPJConnector (loads CSV into memory)
  - GemmaClient / AlertGenerator (reads API key; no network call until generate)

Rate limiting via slowapi is keyed on the device_id request header.
HTTP 429 responses include a Retry-After header (handled by slowapi).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.api.routes import alerts, map, query
from backend.config import settings
from backend.ingestion.cpj_connector import CPJConnector
from backend.processors.alert_generator import AlertGenerator
from backend.processors.gemma_client import GemmaClient
from backend.security.rate_limiter import limiter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # --- startup ---
    try:
        redis_client: aioredis.Redis = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True
        )
        await redis_client.ping()
        app.state.redis = redis_client
        logger.info("Redis client initialised and reachable")
    except Exception as exc:
        logger.warning(f"Redis unavailable — running without cache: {exc}")
        app.state.redis = None

    try:
        app.state.cpj = CPJConnector()
    except Exception as exc:  # pragma: no cover
        logger.error(f"CPJ connector init failed: {exc}")
        app.state.cpj = None

    gemma_client = GemmaClient()
    app.state.alert_generator = AlertGenerator(gemma_client)
    logger.info("Alert generator ready")

    yield

    # --- shutdown ---
    if app.state.redis is not None:
        await app.state.redis.aclose()


app = FastAPI(title="FocalPoint", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(alerts.router)
app.include_router(query.router)
app.include_router(map.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}
