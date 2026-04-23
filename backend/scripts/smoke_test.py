"""
Smoke test — end-to-end data fetch and severity score.

########################################################################
# NOT PART OF THE AUTOMATED TEST SUITE                                  #
#                                                                        #
# This script makes live network calls to external APIs and requires    #
# real credentials in .env:                                             #
#                                                                        #
#   GDELT_CLOUD_API_KEY  — from the GDELT Cloud API                    #
#                                                                        #
# GDELT Doc API requires no credentials. CPJ and RSF are loaded         #
# locally. Do not run this script in CI — use uv run pytest instead.   #
########################################################################

Loads credentials from .env via app Settings, fetches live data from
GDELT Cloud (conflict events) and GDELT Doc API (news sentiment),
runs the severity scorer, and prints a human-readable summary.

Usage:
    uv run python backend/scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio

from loguru import logger

from backend.config import settings
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.ingestion.cpj_connector import CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.alerts.severity_scorer import score_severity

# Silence loguru during the smoke test — we print our own output.
logger.disable("backend")

COUNTRY = "Palestine"
GDELT_QUERY = "conflict Gaza"
FETCH_LIMIT = 5


async def main() -> None:
    print("=" * 60)
    print("FocalPoint smoke test")
    print(f"GDELT Cloud country : {COUNTRY}")
    print(f"GDELT query         : {GDELT_QUERY!r}  timespan=24H  max={FETCH_LIMIT}")
    print("=" * 60)

    # ------------------------------------------------------------------ #
    # GDELT Cloud (conflict events)
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
    # GDELT Doc API (news sentiment)
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
    # CPJ + RSF (supporting inputs)
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
    # Severity scorer
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


if __name__ == "__main__":
    asyncio.run(main())
