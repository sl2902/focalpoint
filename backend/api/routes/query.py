"""Query endpoint.

POST /query — accepts a journalist's natural language query, sanitises it,
fetches live conflict data, and returns a grounded Gemma 4 assessment.

The sanitisation result is surfaced to the client via was_sanitised so the
mobile app can display a notice when the query was modified.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import (
    get_alert_generator,
    get_cpj_connector,
    get_gdelt_cloud_connector,
    get_gdelt_connector,
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

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
@limiter.limit(QUERY_RATE_LIMIT)
async def query(
    request: Request,
    body: JournalistQuery,
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
    gdelt: GdeltConnector = Depends(get_gdelt_connector),
    cpj: CPJConnector = Depends(get_cpj_connector),
    generator: AlertGenerator = Depends(get_alert_generator),
) -> QueryResponse:
    """Accept a journalist's natural language query and return a grounded assessment."""
    sanitised = sanitise_query(body.text)

    events = await gdelt_cloud.fetch_events(body.region)
    gdelt_resp = await gdelt.fetch_articles(body.region)
    cpj_stats = cpj.get_country_stats(body.region)
    rsf_key = RSF_ALIASES.get(body.region, body.region)
    rsf_score = RSF_SCORES.get(rsf_key, 0.0)

    alert = generator.generate(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
        cpj_stats=cpj_stats,
        rsf_score=rsf_score,
        region=body.region,
        journalist_query=sanitised.text,
    )

    return QueryResponse(
        answer=alert.summary,
        severity=alert.severity,
        source_citations=alert.source_citations,
        region=alert.region,
        timestamp=alert.timestamp,
        was_sanitised=sanitised.was_modified,
    )
