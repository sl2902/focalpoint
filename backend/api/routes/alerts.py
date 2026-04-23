"""Alert endpoints.

GET /alerts/watchzone  — severity alert for the journalist's pinned watch zone.
GET /alerts/{region}   — severity alert for any named region.

Both endpoints run the same pipeline:
  1. Fetch GDELT Cloud conflict events and GDELT Doc API articles.
  2. Run deterministic severity scoring (no Gemma 4 required).
  3. Call alert generator for the grounded Gemma 4 natural-language summary.
  4. Return combined AlertResponse.

The watchzone route is declared before the parameterised region route so
FastAPI matches the literal path /alerts/watchzone first.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from backend.alerts.severity_scorer import score_severity
from backend.api.dependencies import (
    get_alert_generator,
    get_cpj_connector,
    get_gdelt_cloud_connector,
    get_gdelt_connector,
)
from backend.api.schemas import AlertResponse
from backend.data.rsf_scores import RSF_SCORES
from backend.ingestion.cpj_connector import CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.processors.alert_generator import AlertGenerator
from backend.security.rate_limiter import ALERTS_RATE_LIMIT, limiter

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/watchzone", response_model=AlertResponse)
@limiter.limit(ALERTS_RATE_LIMIT)
async def get_watchzone_alerts(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_km: float = Query(ge=1, le=500),
    label: str = Query(min_length=1, max_length=100),
    days: int = Query(default=1, ge=1, le=30),
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
    gdelt: GdeltConnector = Depends(get_gdelt_connector),
    cpj: CPJConnector = Depends(get_cpj_connector),
    generator: AlertGenerator = Depends(get_alert_generator),
) -> AlertResponse:
    """Return the latest severity alert for the journalist's pinned watch zone."""
    return await _build_alert(
        region=label,
        days=days,
        gdelt_cloud=gdelt_cloud,
        gdelt=gdelt,
        cpj=cpj,
        generator=generator,
    )


@router.get("/{region}", response_model=AlertResponse)
@limiter.limit(ALERTS_RATE_LIMIT)
async def get_region_alerts(
    request: Request,
    region: str = Path(min_length=2, max_length=100),
    days: int = Query(default=1, ge=1, le=30),
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
    gdelt: GdeltConnector = Depends(get_gdelt_connector),
    cpj: CPJConnector = Depends(get_cpj_connector),
    generator: AlertGenerator = Depends(get_alert_generator),
) -> AlertResponse:
    """Return the latest severity alert for a named region."""
    return await _build_alert(
        region=region,
        days=days,
        gdelt_cloud=gdelt_cloud,
        gdelt=gdelt,
        cpj=cpj,
        generator=generator,
    )


async def _build_alert(
    *,
    region: str,
    days: int,
    gdelt_cloud: GdeltCloudConnector,
    gdelt: GdeltConnector,
    cpj: CPJConnector,
    generator: AlertGenerator,
) -> AlertResponse:
    events = await gdelt_cloud.fetch_events(region, days=days)
    gdelt_resp = await gdelt.fetch_articles(region)
    cpj_stats = cpj.get_country_stats(region)
    rsf_score = RSF_SCORES.get(region, 0.0)

    severity_result = score_severity(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        cpj_stats=cpj_stats,
        rsf_press_freedom=rsf_score,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
    )

    alert = generator.generate(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
        cpj_stats=cpj_stats,
        rsf_score=rsf_score,
        region=region,
    )

    return AlertResponse(
        severity=severity_result.level.value,
        summary=alert.summary,
        source_citations=alert.source_citations,
        region=region,
        timestamp=alert.timestamp,
        score=severity_result.score,
        confidence=severity_result.confidence,
        reasoning=severity_result.reasoning,
    )
