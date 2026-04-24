"""
Watch zone data verification script.

Iterates over all WATCH_ZONES from config.py and for each country fetches
live data from every source, runs severity scoring, and prints a summary
table. No Gemma 4 call — data verification only.

########################################################################
# NOT PART OF THE AUTOMATED TEST SUITE                                  #
#                                                                        #
# Makes live network calls to GDELT Cloud and GDELT Doc API.           #
# GDELT Cloud burns one quota unit per watch zone (7 total).           #
# GDELT Doc API requires no credentials.                                #
#                                                                        #
# Do not run in CI — use uv run pytest instead.                         #
########################################################################

Usage:
    uv run python backend/scripts/verify_watch_zones.py

    # Widen GDELT Cloud to include events without confirmed fatalities
    # (useful for countries like Iran that return 0 with the default filter):
    uv run python backend/scripts/verify_watch_zones.py --no-fatalities-filter
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from backend.alerts.severity_scorer import score_severity
from backend.config import settings
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.cpj_connector import CPJ_ALIASES, CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector

# Silence loguru — we print our own structured output.
logger.disable("backend")

# ---------------------------------------------------------------------------
# Column widths
# ---------------------------------------------------------------------------

_W_COUNTRY = 10
_W_EVENTS = 7
_W_TONE = 7
_W_CPJ = 8
_W_RSF = 6
_W_FAT = 5
_W_ETYPE = 6
_W_GTONE = 7
_W_CPJC = 6
_W_RSFC = 6
_W_COMP = 7
_W_LEVEL = 14


def _header() -> str:
    return (
        f"{'Country':<{_W_COUNTRY}}  "
        f"{'Events':>{_W_EVENTS}}  "
        f"{'Tone':>{_W_TONE}}  "
        f"{'CPJ/yr':>{_W_CPJ}}  "
        f"{'RSF':>{_W_RSF}}  "
        f"{'fat':>{_W_FAT}}  "
        f"{'etype':>{_W_ETYPE}}  "
        f"{'gtone':>{_W_GTONE}}  "
        f"{'cpj':>{_W_CPJC}}  "
        f"{'rsf':>{_W_RSFC}}  "
        f"{'score':>{_W_COMP}}  "
        f"{'severity':<{_W_LEVEL}}"
    )


def _separator() -> str:
    return (
        f"{'-'*_W_COUNTRY}  "
        f"{'-'*_W_EVENTS}  "
        f"{'-'*_W_TONE}  "
        f"{'-'*_W_CPJ}  "
        f"{'-'*_W_RSF}  "
        f"{'-'*_W_FAT}  "
        f"{'-'*_W_ETYPE}  "
        f"{'-'*_W_GTONE}  "
        f"{'-'*_W_CPJC}  "
        f"{'-'*_W_RSFC}  "
        f"{'-'*_W_COMP}  "
        f"{'-'*_W_LEVEL}"
    )


def _row(
    country: str,
    n_events: int,
    tone: float,
    cpj_rate: float,
    rsf_score: float,
    c_fat: float,
    c_etype: float,
    c_gtone: float,
    c_cpj: float,
    c_rsf: float,
    composite: float,
    level: str,
) -> str:
    tone_str = f"{tone:+.2f}" if tone != 0.0 else "  0.00"
    return (
        f"{country:<{_W_COUNTRY}}  "
        f"{n_events:>{_W_EVENTS}}  "
        f"{tone_str:>{_W_TONE}}  "
        f"{cpj_rate:>{_W_CPJ}.2f}  "
        f"{rsf_score:>{_W_RSF}.1f}  "
        f"{c_fat:>{_W_FAT}.0f}  "
        f"{c_etype:>{_W_ETYPE}.0f}  "
        f"{c_gtone:>{_W_GTONE}.0f}  "
        f"{c_cpj:>{_W_CPJC}.0f}  "
        f"{c_rsf:>{_W_RSFC}.0f}  "
        f"{composite:>{_W_COMP}.1f}  "
        f"{level:<{_W_LEVEL}}"
    )


# ---------------------------------------------------------------------------
# Per-country fetch and score
# ---------------------------------------------------------------------------


async def check_country(
    country: str,
    gdelt_cloud: GdeltCloudConnector,
    gdelt: GdeltConnector,
    cpj: CPJConnector,
    force_no_fatalities: bool = False,
) -> dict:
    """Fetch all data sources for *country* and return a result dict."""
    errors: list[str] = []

    # GDELT Cloud — apply per-country alias and fatalities filter from config
    gdelt_cloud_country = settings.GDELT_CLOUD_ALIASES.get(country, country)
    has_fatalities = (
        False
        if force_no_fatalities
        else country not in settings.NO_FATALITIES_FILTER_COUNTRIES
    )

    try:
        events = await gdelt_cloud.fetch_events(
            gdelt_cloud_country, days=1, has_fatalities=has_fatalities
        )
    except Exception as exc:
        events = []
        errors.append(f"GDELT Cloud error: {exc}")

    # GDELT Doc API — 4-step fallback chain:
    #   1. "conflict {country}"  — tightest match
    #   2. "{country} war"       — alternate framing
    #   3. "{country} conflict"  — another alternate
    #   4. bare country name     — widest net
    gdelt_resp = None
    gdelt_query_used = f"conflict {country}"
    _gdelt_queries = [
        f"conflict {country}",
        f"{country} war",
        f"{country} conflict",
        country,
    ]
    try:
        for _query in _gdelt_queries:
            _resp = await gdelt.fetch_articles(_query)
            if _resp.articles:
                gdelt_resp = _resp
                gdelt_query_used = _query
                break
        if gdelt_resp is None:
            # All queries returned 0 articles — keep the last response for tone
            gdelt_resp = _resp  # type: ignore[possibly-undefined]
            gdelt_query_used = _gdelt_queries[-1]
        tone = gdelt_resp.aggregate_tone
        n_articles = len(gdelt_resp.articles)
    except Exception as exc:
        tone = 0.0
        n_articles = 0
        errors.append(f"GDELT Doc API error: {exc}")

    # CPJ (local — no network)
    cpj_stats = cpj.get_country_stats(country)

    # RSF (local — no network)
    rsf_key = RSF_ALIASES.get(country, country)
    rsf_score = RSF_SCORES.get(rsf_key, 0.0)

    # Severity scoring
    articles = gdelt_resp.articles if gdelt_resp else []
    if n_articles == 0 and not any("GDELT Doc API error" in e for e in errors):
        errors.append(f"GDELT Doc API: 0 articles for query {gdelt_query_used!r} (tone defaulted to 0.0)")
    result = score_severity(
        conflict_events=events,
        gdelt_articles=articles,
        cpj_stats=cpj_stats,
        rsf_press_freedom=rsf_score,
        gdelt_aggregate_tone=tone,
    )

    return {
        "country": country,
        "n_events": len(events),
        "n_articles": n_articles,
        "tone": tone,
        "gdelt_query_used": gdelt_query_used,
        "cpj_rate": cpj_stats.incidents_per_year,
        "cpj_total": cpj_stats.total_incidents,
        "rsf_score": rsf_score,
        "rsf_key": rsf_key,
        "components": result.component_scores,
        "composite": result.score,
        "level": result.level.value,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(force_no_fatalities: bool) -> None:
    zones = settings.WATCH_ZONES

    print()
    print("FocalPoint — Watch Zone Data Verification")
    print(f"Zones     : {', '.join(zones)}")
    no_fat = settings.NO_FATALITIES_FILTER_COUNTRIES
    print(f"No-fatalities filter: {', '.join(sorted(no_fat))} (from config)")
    aliases = settings.GDELT_CLOUD_ALIASES
    if aliases:
        print(f"GDELT Cloud aliases : {aliases}")
    print(f"GDELT quota reminder: each zone burns one of 100 free monthly queries")
    print()

    gdelt_cloud = GdeltCloudConnector(redis_client=None, app_settings=settings)
    gdelt = GdeltConnector(redis_client=None)
    cpj = CPJConnector()

    results = []
    for country in zones:
        print(f"  Fetching {country}...", end=" ", flush=True)
        r = await check_country(country, gdelt_cloud, gdelt, cpj, force_no_fatalities=force_no_fatalities)
        results.append(r)
        status = f"{r['n_events']} events, tone={r['tone']:+.2f}"
        if r["errors"]:
            status += f"  ⚠ {r['errors'][0]}"
        print(status)

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    print()
    print("Component score columns: fat=fatalities/30  etype=event_type/25")
    print("  gtone=gdelt_tone/20  cpj=cpj_rate/15  rsf=rsf_baseline/10")
    print()

    header = _header()
    sep = _separator()
    print(header)
    print(sep)

    for r in results:
        c = r["components"]
        print(_row(
            country=r["country"],
            n_events=r["n_events"],
            tone=r["tone"],
            cpj_rate=r["cpj_rate"],
            rsf_score=r["rsf_score"],
            c_fat=c.get("fatalities", 0.0),
            c_etype=c.get("event_type", 0.0),
            c_gtone=c.get("gdelt_tone", 0.0),
            c_cpj=c.get("cpj_rate", 0.0),
            c_rsf=c.get("rsf_baseline", 0.0),
            composite=r["composite"],
            level=r["level"],
        ))

    print(sep)
    print()

    # ------------------------------------------------------------------
    # Per-country detail block
    # ------------------------------------------------------------------
    print("Detail")
    print("------")
    for r in results:
        rsf_alias_note = (
            f" (alias → {r['rsf_key']!r})"
            if r["rsf_key"] != r["country"]
            else " (direct)"
        )
        cpj_key = CPJ_ALIASES.get(r["country"], r["country"])
        cpj_alias_note = (
            f" (alias → {cpj_key!r})"
            if cpj_key != r["country"]
            else " (direct)"
        )
        gdelt_cloud_key = settings.GDELT_CLOUD_ALIASES.get(r["country"], r["country"])
        gdelt_cloud_note = (
            f" (alias → {gdelt_cloud_key!r})"
            if gdelt_cloud_key != r["country"]
            else ""
        )
        print(f"\n{r['country']}")
        print(f"  GDELT Cloud : {r['n_events']} events{gdelt_cloud_note}")
        print(f"  GDELT Doc   : {r['n_articles']} articles  tone={r['tone']:+.2f}  query={r['gdelt_query_used']!r}")
        print(f"  CPJ         : {r['cpj_total']} total incidents  {r['cpj_rate']:.2f}/yr{cpj_alias_note}")
        print(f"  RSF         : {r['rsf_score']:.2f}{rsf_alias_note}")
        print(f"  Reasoning   : {r['reasoning']}")
        if r["errors"]:
            print(f"  ERRORS      : {'; '.join(r['errors'])}")

    # ------------------------------------------------------------------
    # Flag countries with potential data quality issues
    # ------------------------------------------------------------------
    print()
    flagged = [
        r for r in results
        if r["n_events"] == 0 or r["n_articles"] == 0
        or r["rsf_score"] == 0.0 or r["cpj_total"] == 0
    ]
    if flagged:
        print("Data quality flags")
        print("------------------")
        for r in flagged:
            issues = []
            if r["n_events"] == 0:
                in_no_fat = r["country"] in settings.NO_FATALITIES_FILTER_COUNTRIES
                issues.append(
                    "0 GDELT Cloud events — try --no-fatalities-filter"
                    if not in_no_fat and not force_no_fatalities
                    else "0 GDELT Cloud events even without fatalities filter"
                )
            if r["rsf_score"] == 0.0:
                issues.append(f"RSF score missing for {r['rsf_key']!r}")
            if r["n_articles"] == 0:
                issues.append(
                    f"0 GDELT Doc API articles (tried 'conflict {r['country']}' + bare name fallback)"
                )
            if r["cpj_total"] == 0:
                issues.append("0 CPJ incidents — check country name or add CPJ_ALIASES entry")
            for issue in issues:
                print(f"  {r['country']}: {issue}")
    else:
        print("No data quality flags — all watch zones have events, RSF, and CPJ coverage.")

    print()

    any_errors = any(r["errors"] for r in results)
    if any_errors:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify data coverage for all FocalPoint watch zones."
    )
    parser.add_argument(
        "--no-fatalities-filter",
        action="store_true",
        help="Disable GDELT Cloud has_fatalities=true filter (widens results for countries like Iran).",
    )
    args = parser.parse_args()

    asyncio.run(main(force_no_fatalities=args.no_fatalities_filter))
