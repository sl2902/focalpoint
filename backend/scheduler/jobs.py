"""APScheduler job for background alert refresh.

refresh_one_watch_zone rotates through WATCH_ZONES (from config) one
region per firing on an 8-hour cycle. The rotation index is module-level
so it persists across job firings within a process (resets on restart,
which is acceptable for an 8-hour cycle).

The job is added to an AsyncIOScheduler in the FastAPI lifespan (main.py).
It reads shared connectors from app.state to avoid re-initialising
per-job, but constructs short-lived GDELT connectors per-call (they are
lightweight and their Redis reference is stable on app.state).
"""

from __future__ import annotations

from loguru import logger

from backend.alerts.severity_scorer import score_severity
from backend.config import settings
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.scheduler import store

_rotation_index: int = 0


async def refresh_one_watch_zone(app) -> None:  # noqa: ANN001
    """Refresh the alert for the next region in the WATCH_ZONES rotation."""
    global _rotation_index
    zones = settings.WATCH_ZONES
    region = zones[_rotation_index % len(zones)].title()
    _rotation_index += 1
    logger.info(f"scheduler: refreshing {region!r}")

    cached = await store.get_cached_alert(app.state.db_path, region, days=1)
    if cached is not None:
        logger.info(f"scheduler: {region!r} days=1 still fresh — skipping Gemma and GDELT")
        return

    redis_client = getattr(app.state, "redis", None)
    gdelt_cloud = GdeltCloudConnector(redis_client=redis_client)
    gdelt = GdeltConnector(redis_client=redis_client)
    cpj = app.state.cpj
    generator = app.state.alert_generator

    try:
        gdelt_cloud_country = settings.GDELT_CLOUD_ALIASES.get(region, region)
        has_fatalities = region not in settings.NO_FATALITIES_FILTER_COUNTRIES
        events = await gdelt_cloud.fetch_events(
            gdelt_cloud_country, days=1, has_fatalities=has_fatalities
        )
        gdelt_resp = await gdelt.fetch_articles(f"conflict {region}", maxrecords=10)
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
        await store.upsert_alert(
            db_path=app.state.db_path,
            region=region,
            days=1,
            severity=alert.severity,
            summary=alert.summary,
            source_citations=alert.source_citations,
            confidence=severity_result.confidence,
            score=severity_result.score,
            timestamp=alert.timestamp.isoformat(),
        )
        logger.info(f"scheduler: {region!r} stored → {alert.severity}")
    except Exception as exc:
        logger.error(f"scheduler: failed to refresh {region!r} — {exc}")
