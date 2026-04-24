"""Query endpoint.

POST /query — accepts a journalist's natural language query, sanitises it,
fetches live conflict data, and returns a grounded Gemma 4 assessment.

The sanitisation result is surfaced to the client via was_sanitised so the
mobile app can display a notice when the query was modified.

Caching strategy
----------------
Responses backed by GDELT data (use_web_search=False) are cached in Redis
with key pattern ``query:{region}:{query_hash}`` and TTL of 3600 seconds.
Responses that used web search are never cached — live web results are
time-sensitive and should not be served stale.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from loguru import logger

from backend.api.dependencies import (
    get_alert_generator,
    get_cpj_connector,
    get_gdelt_cloud_connector,
    get_gdelt_connector,
    get_redis,
)
from backend.api.schemas import QueryResponse
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.cpj_connector import CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.processors.alert_generator import AlertGenerator
from backend.security.output_validator import JournalistQuery
from backend.security.rate_limiter import QUERY_RATE_LIMIT, limiter
from backend.security.sanitiser import sanitise_query

_CACHE_TTL = 3600  # seconds

router = APIRouter(tags=["query"])


def _cache_key(region: str, query_text: str) -> str:
    query_hash = hashlib.sha256(query_text.encode()).hexdigest()[:16]
    return f"query:{region}:{query_hash}"


@router.post("/query", response_model=QueryResponse)
@limiter.limit(QUERY_RATE_LIMIT)
async def query(
    request: Request,
    body: JournalistQuery,
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
    gdelt: GdeltConnector = Depends(get_gdelt_connector),
    cpj: CPJConnector = Depends(get_cpj_connector),
    generator: AlertGenerator = Depends(get_alert_generator),
    redis: Annotated[aioredis.Redis | None, Depends(get_redis)] = None,
) -> QueryResponse:
    """Accept a journalist's natural language query and return a grounded assessment."""
    region = body.region.title()
    sanitised = sanitise_query(body.text)

    events = await gdelt_cloud.fetch_events(region)
    gdelt_resp = await gdelt.fetch_articles(sanitised.text)
    cpj_stats = cpj.get_country_stats(region)
    rsf_key = RSF_ALIASES.get(region, region)
    rsf_score = RSF_SCORES.get(rsf_key, 0.0)

    use_web_search = len(gdelt_resp.articles) == 0

    # Cache check — only for GDELT-backed responses.
    if not use_web_search and redis is not None:
        key = _cache_key(region, sanitised.text)
        try:
            cached = await redis.get(key)
            if cached:
                logger.debug(f"query: cache hit for key={key!r}")
                data = json.loads(cached)
                data["was_sanitised"] = sanitised.was_modified
                return QueryResponse(**data)
        except Exception as exc:
            logger.warning(f"query: Redis read failed — {exc}")

    alert = generator.generate(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
        cpj_stats=cpj_stats,
        rsf_score=rsf_score,
        region=region,
        journalist_query=sanitised.text,
    )

    response = QueryResponse(
        answer=alert.summary,
        severity=alert.severity,
        source_citations=alert.source_citations,
        region=alert.region,
        timestamp=alert.timestamp,
        was_sanitised=sanitised.was_modified,
    )

    # Cache write — only when GDELT data was used.
    if not use_web_search and redis is not None:
        key = _cache_key(region, sanitised.text)
        try:
            payload = response.model_dump(mode="json")
            await redis.setex(key, _CACHE_TTL, json.dumps(payload))
            logger.debug(f"query: cached response under key={key!r} ttl={_CACHE_TTL}s")
        except Exception as exc:
            logger.warning(f"query: Redis write failed — {exc}")

    return response
