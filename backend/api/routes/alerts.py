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
from loguru import logger

from backend.alerts.severity_scorer import SeverityLevel, score_severity
from backend.security.output_validator import Citation
from backend.api.dependencies import (
    get_alert_generator,
    get_alerts_db_path,
    get_cpj_connector,
    get_gdelt_cloud_connector,
    get_gdelt_connector,
)
from backend.api.schemas import AlertResponse
from backend.config import settings
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.cpj_connector import CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.processors.alert_generator import AlertGenerator
from backend.scheduler import store
from backend.security.rate_limiter import ALERTS_RATE_LIMIT, limiter

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/feed", response_model=list[AlertResponse])
@limiter.limit(ALERTS_RATE_LIMIT)
async def get_alerts_feed(
    request: Request,
    db_path: str = Depends(get_alerts_db_path),
) -> list[AlertResponse]:
    """Return the latest stored alert per region, ordered by severity."""
    return await store.get_latest_per_region(db_path)


@router.get("/watchzone", response_model=AlertResponse)
@limiter.limit(ALERTS_RATE_LIMIT)
async def get_watchzone_alerts(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_km: float = Query(ge=1, le=500),
    label: str = Query(min_length=1, max_length=100),
    days: int = Query(default=1, ge=1, le=30),
    db_path: str = Depends(get_alerts_db_path),
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
    gdelt: GdeltConnector = Depends(get_gdelt_connector),
    cpj: CPJConnector = Depends(get_cpj_connector),
    generator: AlertGenerator = Depends(get_alert_generator),
) -> AlertResponse:
    """Return the latest severity alert for the journalist's pinned watch zone."""
    return await _build_alert(
        region=label,
        days=days,
        db_path=db_path,
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
    db_path: str = Depends(get_alerts_db_path),
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
    gdelt: GdeltConnector = Depends(get_gdelt_connector),
    cpj: CPJConnector = Depends(get_cpj_connector),
    generator: AlertGenerator = Depends(get_alert_generator),
) -> AlertResponse:
    """Return the latest severity alert for a named region."""
    cached = await store.get_cached_alert(db_path, region.title())
    if cached is not None:
        logger.info(f"alerts: cache hit for region={region.title()!r} — skipping Gemma 4")
        return cached
    logger.info(f"alerts: cache miss for region={region.title()!r} — running live pipeline")
    return await _build_alert(
        region=region,
        days=days,
        db_path=db_path,
        gdelt_cloud=gdelt_cloud,
        gdelt=gdelt,
        cpj=cpj,
        generator=generator,
    )


async def _build_alert(
    *,
    region: str,
    days: int,
    db_path: str,
    gdelt_cloud: GdeltCloudConnector,
    gdelt: GdeltConnector,
    cpj: CPJConnector,
    generator: AlertGenerator,
) -> AlertResponse:
    region = region.title()
    gdelt_cloud_country = settings.GDELT_CLOUD_ALIASES.get(region, region)
    has_fatalities = region not in settings.NO_FATALITIES_FILTER_COUNTRIES
    events = await gdelt_cloud.fetch_events(
        gdelt_cloud_country, days=days, has_fatalities=has_fatalities
    )
    gdelt_resp = await gdelt.fetch_articles(f"conflict {region}")
    cpj_stats = cpj.get_country_stats(region)
    rsf_key = RSF_ALIASES.get(region, region)
    rsf_score = RSF_SCORES.get(rsf_key, 0.0)

    severity_result = score_severity(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        cpj_stats=cpj_stats,
        rsf_press_freedom=rsf_score,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
    )
    logger.info(f"alerts: score breakdown for {region!r} — {severity_result.reasoning}")

    # Short-circuit: no data AND articles present — skip Gemma to avoid a quota call on
    # empty context. When articles are empty, fall through so the generator can use web
    # search to find live sources the deterministic scorer could not see.
    if severity_result.level == SeverityLevel.INSUFFICIENT_DATA and gdelt_resp.articles:
        from datetime import datetime, timezone
        response = AlertResponse(
            severity="INSUFFICIENT_DATA",
            summary=severity_result.reasoning,
            source_citations=[Citation(id="CPJ", description="No live or historical data available.")],
            region=region,
            timestamp=datetime.now(tz=timezone.utc),
            confidence=0.0,
        )
        await store.upsert_alert(
            db_path=db_path,
            region=region,
            severity=response.severity,
            summary=response.summary,
            source_citations=response.source_citations,
            confidence=response.confidence,
            score=0.0,
            timestamp=response.timestamp.isoformat(),
        )
        logger.info(f"alerts: INSUFFICIENT_DATA for {region!r} — skipped Gemma")
        return response

    alert = generator.generate(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
        cpj_stats=cpj_stats,
        rsf_score=rsf_score,
        region=region,
        journalist_query=f"journalist safety {region} current situation latest news",
        severity_result=severity_result,
    )

    response = AlertResponse(
        severity=alert.severity,
        summary=alert.summary,
        source_citations=alert.source_citations,
        region=region,
        timestamp=alert.timestamp,
        confidence=severity_result.confidence,
    )

    await store.upsert_alert(
        db_path=db_path,
        region=region,
        severity=response.severity,
        summary=response.summary,
        source_citations=response.source_citations,
        confidence=response.confidence,
        score=severity_result.score,
        timestamp=alert.timestamp.isoformat(),
    )
    logger.info(f"alerts: live result for {region!r} written to cache")

    return response
