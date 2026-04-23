"""
Smoke test — end-to-end data fetch and severity score.

Loads credentials from .env via app Settings, fetches live data from
ACLED and GDELT (no Redis), runs the severity scorer, and prints a
human-readable summary.

Usage:
    uv run python -m backend.scripts.smoke_test
"""

from __future__ import annotations

import asyncio

from loguru import logger

from backend.config import settings
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.acled_connector import AcledConnector
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
    print(f"ACLED country : {COUNTRY}")
    print(f"GDELT query   : {GDELT_QUERY!r}  timespan=24H  max={FETCH_LIMIT}")
    print("=" * 60)

    # ------------------------------------------------------------------ #
    # ACLED
    # ------------------------------------------------------------------ #
    acled = AcledConnector(redis_client=None, app_settings=settings)
    acled_events = await acled.fetch_events(COUNTRY, limit=FETCH_LIMIT)

    print(f"\nACLED — {len(acled_events)} events fetched")
    print(f"  {'Date':<12}  {'Type':<35}  {'Fatalities':>10}  Location")
    print(f"  {'-'*12}  {'-'*35}  {'-'*10}  --------")
    for ev in acled_events:
        print(
            f"  {ev.event_date:<12}  {ev.event_type:<35}"
            f"  {ev.fatalities:>10}  {ev.location}"
        )

    # ------------------------------------------------------------------ #
    # GDELT
    # ------------------------------------------------------------------ #
    gdelt = GdeltConnector(redis_client=None)
    gdelt_response = await gdelt.fetch_articles(GDELT_QUERY, maxrecords=FETCH_LIMIT)

    print(f"\nGDELT — {len(gdelt_response.articles)} articles fetched"
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
        acled_events=acled_events,
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
