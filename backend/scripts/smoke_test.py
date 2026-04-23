"""
Smoke test — end-to-end data fetch, severity score, and Gemma 4 alert.

########################################################################
# NOT PART OF THE AUTOMATED TEST SUITE                                  #
#                                                                        #
# This script makes live network calls to external APIs and requires    #
# real credentials in .env:                                             #
#                                                                        #
#   GDELT_CLOUD_API_KEY      — from the GDELT Cloud API                #
#   GOOGLE_AI_STUDIO_API_KEY — Gemini API key covering Gemma 4 models  #
#                                                                        #
# GDELT Doc API requires no credentials. CPJ and RSF are loaded         #
# locally. If the GDELT Doc API is unreachable, aggregate_tone=0.0 is  #
# used as fallback and the test continues.                              #
#                                                                        #
# The Gemma 4 section uses hardcoded fixture data (confirmed from a     #
# live smoke test run on 2026-04-23) to avoid burning GDELT Cloud       #
# quota. Only the Gemma 4 API call is live.                             #
#                                                                        #
# Do not run this script in CI — use uv run pytest instead.            #
########################################################################

Sections:
  1. GDELT Cloud   — live fetch (burns one quota unit)
  2. GDELT Doc API — live fetch with retry fallback
  3. CPJ + RSF     — local data, no network
  4. Severity scorer — deterministic, no network
  5. Gemma 4       — live Gemma API call using confirmed fixture data

Usage:
    uv run python backend/scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio

from loguru import logger

from backend.config import settings
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.cpj_connector import CPJConnector, CountryStats
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import (
    GdeltCloudActor,
    GdeltCloudConnector,
    GdeltCloudEvent,
    GdeltCloudGeo,
    GdeltCloudMetrics,
)
from backend.alerts.severity_scorer import score_severity
from backend.processors.alert_generator import AlertGenerator
from backend.processors.gemma_client import GemmaClient

# Silence loguru during the smoke test — we print our own output.
logger.disable("backend")

COUNTRY = "Palestine"
GDELT_QUERY = "conflict Gaza"
FETCH_LIMIT = 5

# ---------------------------------------------------------------------------
# Fixture data — confirmed from a live smoke test run on 2026-04-23.
# Used by the Gemma 4 section to avoid burning GDELT Cloud API quota
# (100 queries/month free tier). Update these when re-running a full live test.
# ---------------------------------------------------------------------------

_FIXTURE_EVENTS: list[GdeltCloudEvent] = [
    GdeltCloudEvent(
        id="conflict_PSE20260423_001",
        event_date="2026-04-23",
        event_type="Violence against civilians",
        fatalities=2,
        geo=GdeltCloudGeo(country="Palestine", location="al-Mughayyir",
                          latitude=31.97, longitude=35.29),
        actors=[GdeltCloudActor(name="Israeli Forces", role="actor1"),
                GdeltCloudActor(name="Palestinian Civilians", role="actor2")],
        metrics=GdeltCloudMetrics(goldstein_scale=-8.0, confidence=0.85),
    ),
    GdeltCloudEvent(
        id="conflict_PSE20260422_001",
        event_date="2026-04-22",
        event_type="Explosions/Remote violence",
        fatalities=5,
        geo=GdeltCloudGeo(country="Palestine", location="Northern Gaza",
                          latitude=31.55, longitude=34.47),
        actors=[GdeltCloudActor(name="Israeli Air Force", role="actor1")],
        metrics=GdeltCloudMetrics(goldstein_scale=-9.0, confidence=0.90),
    ),
    GdeltCloudEvent(
        id="conflict_PSE20260422_002",
        event_date="2026-04-22",
        event_type="Explosions/Remote violence",
        fatalities=1,
        geo=GdeltCloudGeo(country="Palestine", location="Jabalia",
                          latitude=31.53, longitude=34.48),
        actors=[GdeltCloudActor(name="Israeli Forces", role="actor1")],
        metrics=GdeltCloudMetrics(goldstein_scale=-7.0, confidence=0.80),
    ),
    GdeltCloudEvent(
        id="conflict_PSE20260421_001",
        event_date="2026-04-21",
        event_type="Violence against civilians",
        fatalities=2,
        geo=GdeltCloudGeo(country="Palestine", location="Al-Mughayyir",
                          latitude=31.97, longitude=35.29),
        actors=[GdeltCloudActor(name="Israeli Forces", role="actor1"),
                GdeltCloudActor(name="Palestinian Civilians", role="actor2")],
        metrics=GdeltCloudMetrics(goldstein_scale=-8.0, confidence=0.85),
    ),
    GdeltCloudEvent(
        id="conflict_PSE20260421_002",
        event_date="2026-04-21",
        event_type="Explosions/Remote violence",
        fatalities=1,
        geo=GdeltCloudGeo(country="Palestine", location="Southern Gaza Strip",
                          latitude=31.25, longitude=34.36),
        actors=[GdeltCloudActor(name="Israeli Air Force", role="actor1")],
        metrics=GdeltCloudMetrics(goldstein_scale=-7.0, confidence=0.78),
    ),
]

_FIXTURE_AGGREGATE_TONE: float = -3.63   # from GDELT Doc API run on 2026-04-23

_FIXTURE_CPJ_STATS = CPJConnector().get_country_stats("Palestine")

_FIXTURE_RSF_SCORE: float = 27.41   # West Bank and Gaza, RSF 2025


async def main() -> None:
    print("=" * 60)
    print("FocalPoint smoke test")
    print(f"GDELT Cloud country : {COUNTRY}")
    print(f"GDELT query         : {GDELT_QUERY!r}  timespan=24H  max={FETCH_LIMIT}")
    print("=" * 60)

    # ------------------------------------------------------------------ #
    # GDELT Cloud (conflict events) — live, burns one quota unit
    # ------------------------------------------------------------------ #
    gdelt_cloud = GdeltCloudConnector(redis_client=None, app_settings=settings)
    conflict_events = await gdelt_cloud.fetch_events(COUNTRY, days=1, limit=FETCH_LIMIT)

    print(f"\nGDELT Cloud — {len(conflict_events)} events fetched")
    print(f"  {'Date':<12}  {'Type':<35}  {'Fatalities':>10}  Location")
    print(f"  {'-'*12}  {'-'*35}  {'-'*10}  --------")
    for ev in conflict_events:
        print(
            f"  {ev.event_date:<12}  {(ev.event_type or 'n/a'):<35}"
            f"  {(ev.fatalities if ev.fatalities is not None else 'n/a'):>10}"
            f"  {(ev.geo.location if ev.geo else None) or 'n/a'}"
        )

    # ------------------------------------------------------------------ #
    # GDELT Doc API (news sentiment) — live with retry fallback
    # ------------------------------------------------------------------ #
    gdelt = GdeltConnector(redis_client=None)
    gdelt_response = await gdelt.fetch_articles(GDELT_QUERY, maxrecords=FETCH_LIMIT)

    print(f"\nGDELT Doc API — {len(gdelt_response.articles)} articles fetched"
          f"  (aggregate_tone={gdelt_response.aggregate_tone:+.2f})")
    print(f"  Title")
    print(f"  -----")
    for art in gdelt_response.articles:
        title = art.title[:72] + "…" if len(art.title) > 72 else art.title
        print(f"  {title}")

    # ------------------------------------------------------------------ #
    # CPJ + RSF (local data — no network)
    # ------------------------------------------------------------------ #
    cpj = CPJConnector()
    cpj_stats = cpj.get_country_stats(COUNTRY)

    rsf_key = RSF_ALIASES.get(COUNTRY, COUNTRY)
    rsf_score = RSF_SCORES.get(rsf_key, 0.0)

    print(f"\nCPJ — {cpj_stats.total_incidents} incidents for {COUNTRY}")
    print(f"       rate={cpj_stats.incidents_per_year:.2f}/yr"
          f"  ({cpj_stats.earliest_year}–{cpj_stats.latest_year})")
    print(f"RSF  — press freedom score: {rsf_score:.1f}/100 ({rsf_key})")

    # ------------------------------------------------------------------ #
    # Severity scorer — deterministic, no network
    # ------------------------------------------------------------------ #
    result = score_severity(
        conflict_events=conflict_events,
        gdelt_articles=gdelt_response.articles,
        cpj_stats=cpj_stats,
        rsf_press_freedom=rsf_score,
        gdelt_aggregate_tone=gdelt_response.aggregate_tone,
    )

    print("\n" + "=" * 60)
    print(f"Severity  : {result.level.value}")
    print(f"Score     : {result.score:.1f} / 100")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Breakdown : {result.component_scores}")
    print(f"Reasoning : {result.reasoning}")
    print("=" * 60)

    # ------------------------------------------------------------------ #
    # Gemma 4 — fixture data, only the model API call is live
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 60)
    print("Gemma 4 Alert  [fixture data — 2026-04-23 Palestine run]")
    print("Query     : 'Is it safe to operate in northern Gaza today?'")
    print("=" * 60)

    gemma_client = GemmaClient()
    generator = AlertGenerator(gemma_client)
    alert = generator.generate(
        conflict_events=_FIXTURE_EVENTS,
        gdelt_articles=[],
        gdelt_aggregate_tone=_FIXTURE_AGGREGATE_TONE,
        cpj_stats=_FIXTURE_CPJ_STATS,
        rsf_score=_FIXTURE_RSF_SCORE,
        region="northern Gaza",
        journalist_query="Is it safe to operate in northern Gaza today?",
    )

    print(f"Severity  : {alert.severity}")
    print(f"Summary   : {alert.summary}")
    print(f"Region    : {alert.region}")
    print(f"Timestamp : {alert.timestamp.isoformat()}")
    print("Citations :")
    for cite in alert.source_citations:
        print(f"  {cite}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
