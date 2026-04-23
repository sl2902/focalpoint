"""
Severity scoring engine for FocalPoint alerts.

Takes validated data from all four sources and returns a deterministic
severity level with a confidence score and human-readable reasoning string.

Scoring components (total 0–100, capped):
  fatalities  0–30 pts  — recency-weighted fatality count (7-day half-life)
  event_type  0–25 pts  — highest-weight event type in the window
  gdelt_tone  0–20 pts  — escalates with increasingly negative avg tone
  cpj_rate    0–15 pts  — historical journalist incident rate per year
  rsf_baseline 0–10 pts — inverse of RSF press freedom score

Thresholds:
  GREEN    0–24    Normal activity, no immediate threat signals
  AMBER   25–49    Elevated conflict activity, monitor closely
  RED     50–74    Active incidents near watch zone, restrict movement
  CRITICAL 75+     Imminent danger signals, evacuate or shelter
"""

from __future__ import annotations

import math
from datetime import date
from enum import Enum
from typing import Final

from pydantic import BaseModel, Field

from backend.ingestion.cpj_connector import CountryStats
from backend.ingestion.gdelt_connector import GdeltArticle
from backend.ingestion.gdeltcloud_connector import GdeltCloudEvent

# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class SeverityLevel(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    CRITICAL = "CRITICAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class SeverityResult(BaseModel):
    level: SeverityLevel
    score: float = Field(ge=0.0, le=100.0)       # composite 0–100
    confidence: float = Field(ge=0.0, le=1.0)    # 0 = no data, 1 = full coverage
    reasoning: str
    component_scores: dict[str, float]            # per-source breakdown


# ---------------------------------------------------------------------------
# Scoring tables
# ---------------------------------------------------------------------------

# Event type weights — battles explicitly higher than protests per spec.
# Covers both GDELT Cloud CAMEO-based event types and legacy ACLED strings.
# Unrecognised event_type values fall back to _DEFAULT_EVENT_WEIGHT.
_EVENT_TYPE_WEIGHTS: Final[dict[str, float]] = {
    # GDELT Cloud event types
    "Air/Drone Strike": 25.0,
    "Shelling/Artillery/Missiles Fired": 25.0,
    "Armed Clash": 22.0,
    "Political Violence": 18.0,
    "Attack": 18.0,
    "Mob Violence": 10.0,
    "Demonstration": 5.0,
    # ACLED-compatible strings (kept for cross-source consistency)
    "Explosions/Remote violence": 25.0,
    "Battles": 22.0,
    "Violence against civilians": 18.0,
    "Riots": 10.0,
    "Protests": 5.0,
    "Strategic developments": 3.0,
}
_DEFAULT_EVENT_WEIGHT: Final[float] = 5.0

# Recency decay: fatality weight halves every 7 days.
_RECENCY_HALF_LIFE_DAYS: Final[float] = 7.0


def _recency_weight(event_date_str: str, reference: date) -> float:
    """
    Exponential decay weight based on event age: weight = 2^(-days / 7).

      Today   → 1.000
      7 days  → 0.500
      14 days → 0.250
      30 days → ~0.051

    Future or unparseable dates fall back to weight = 1.0 (treat as current).
    """
    try:
        event_date = date.fromisoformat(event_date_str)
    except ValueError:
        return 1.0
    days = max((reference - event_date).days, 0)
    return math.exp(-math.log(2) * days / _RECENCY_HALF_LIFE_DAYS)


# (min_score, level) — evaluated highest-first.
_LEVEL_THRESHOLDS: Final[list[tuple[float, SeverityLevel]]] = [
    (75.0, SeverityLevel.CRITICAL),
    (50.0, SeverityLevel.RED),
    (25.0, SeverityLevel.AMBER),
    (0.0, SeverityLevel.GREEN),
]

# ---------------------------------------------------------------------------
# Component scorers (each returns a float in its declared range)
# ---------------------------------------------------------------------------


def _score_fatalities(
    events: list[GdeltCloudEvent],
    reference_date: date | None = None,
) -> float:
    """0–30 pts.  Escalates with recency-weighted fatality count (7-day half-life).

    Each event's fatalities are multiplied by its decay weight before summing,
    so a battle from 30 days ago contributes ~5% of its raw count.
    GdeltCloudEvent.fatalities is Optional[int] — None is treated as 0.
    """
    if not events:
        return 0.0
    ref = reference_date or date.today()
    weighted = sum(
        (e.fatalities or 0) * _recency_weight(e.event_date, ref) for e in events
    )
    if weighted == 0.0:
        return 0.0
    if weighted <= 3.0:
        return 8.0
    if weighted <= 10.0:
        return 16.0
    if weighted <= 25.0:
        return 24.0
    return 30.0


def _score_event_type(events: list[GdeltCloudEvent]) -> float:
    """0–25 pts.  Highest event-type weight among all events in the window.

    GdeltCloudEvent.event_type is Optional[str] — None events get the
    default weight rather than a KeyError.
    """
    if not events:
        return 0.0
    return max(
        _EVENT_TYPE_WEIGHTS.get(e.event_type or "", _DEFAULT_EVENT_WEIGHT)
        for e in events
    )


def _score_gdelt_tone(aggregate_tone: float) -> float:
    """0–20 pts.  Escalates with increasingly negative GDELT aggregate tone."""
    if aggregate_tone < -15.0:
        return 20.0
    if aggregate_tone < -10.0:
        return 15.0
    if aggregate_tone < -5.0:
        return 10.0
    if aggregate_tone < 0.0:
        return 5.0
    return 0.0


def _score_cpj_rate(stats: CountryStats) -> float:
    """0–15 pts.  Escalates with historical journalist incident rate (per year)."""
    rate = stats.incidents_per_year
    if rate == 0.0:
        return 0.0
    if rate < 1.0:
        return 3.0
    if rate < 2.0:
        return 6.0
    if rate < 5.0:
        return 10.0
    return 15.0


def _score_rsf(rsf_press_freedom: float) -> float:
    """0–10 pts.  Inverse of RSF press freedom score — lower freedom = higher risk."""
    if rsf_press_freedom >= 75.0:
        return 0.0
    if rsf_press_freedom >= 50.0:
        return 3.0
    if rsf_press_freedom >= 25.0:
        return 7.0
    return 10.0


def _compute_confidence(
    events: list[GdeltCloudEvent],
    articles: list[GdeltArticle],
    stats: CountryStats,
    rsf_press_freedom: float,
) -> float:
    """
    0.0–1.0.  Penalised when data sources are absent or sparse.

    Deductions:
      -0.30  no GDELT Cloud events at all
      -0.10  fewer than 3 GDELT Cloud events (but some)
      -0.20  no GDELT Doc API articles
      -0.05  CPJ has no recorded incidents for this country
      -0.10  RSF score is 0 (country not in index)
    """
    conf = 1.0
    if not events:
        conf -= 0.30
    elif len(events) < 3:
        conf -= 0.10
    if not articles:
        conf -= 0.20
    if stats.total_incidents == 0:
        conf -= 0.05
    if rsf_press_freedom == 0.0:
        conf -= 0.10
    return round(max(conf, 0.1), 2)


def _level_from_score(score: float) -> SeverityLevel:
    for threshold, level in _LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return SeverityLevel.GREEN  # unreachable but satisfies type checker


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def score_severity(
    conflict_events: list[GdeltCloudEvent],
    gdelt_articles: list[GdeltArticle],
    cpj_stats: CountryStats,
    rsf_press_freedom: float,
    reference_date: date | None = None,
    gdelt_aggregate_tone: float = 0.0,
) -> SeverityResult:
    """
    Compute a severity level from multi-source conflict intelligence inputs.

    Returns ``SeverityLevel.INSUFFICIENT_DATA`` when both conflict events
    and GDELT articles are empty — real-time data is required for a
    meaningful assessment. CPJ and RSF inputs are always considered when
    available.

    Args:
        conflict_events:   Validated GdeltCloudEvent list for the target region.
        gdelt_articles:    Validated GDELT Doc API articles for the target query.
        cpj_stats:         Historical CPJ incident stats for the target country.
        rsf_press_freedom: RSF Press Freedom Index score (0–100, higher = freer).
        reference_date:    Date used as "today" for recency decay. Defaults to
                           ``date.today()``. Pass a fixed date in tests.
        gdelt_aggregate_tone: Mean tone from GDELT Doc API timelinetone endpoint.
    """
    if not conflict_events and not gdelt_articles:
        return SeverityResult(
            level=SeverityLevel.INSUFFICIENT_DATA,
            score=0.0,
            confidence=0.0,
            reasoning=(
                "Insufficient data — conflict events and GDELT articles both"
                " returned empty results. Cannot produce a reliable safety assessment."
            ),
            component_scores={},
        )

    components: dict[str, float] = {
        "fatalities": _score_fatalities(conflict_events, reference_date),
        "event_type": _score_event_type(conflict_events),
        "gdelt_tone": _score_gdelt_tone(gdelt_aggregate_tone),
        "cpj_rate": _score_cpj_rate(cpj_stats),
        "rsf_baseline": _score_rsf(rsf_press_freedom),
    }
    total = min(sum(components.values()), 100.0)
    level = _level_from_score(total)
    confidence = _compute_confidence(
        conflict_events, gdelt_articles, cpj_stats, rsf_press_freedom
    )

    # Build structured reasoning string.
    total_fatalities = sum(e.fatalities or 0 for e in conflict_events)
    avg_tone_str = f"{gdelt_aggregate_tone:.1f}" if gdelt_articles else "n/a"
    reasoning = (
        f"GDELT Cloud: {total_fatalities} fatalities across {len(conflict_events)} events"
        f" (fatalities={components['fatalities']:.0f}/30,"
        f" type={components['event_type']:.0f}/25)"
        f" | GDELT Doc API: {len(gdelt_articles)} articles avg_tone={avg_tone_str}"
        f" (tone={components['gdelt_tone']:.0f}/20)"
        f" | CPJ: {cpj_stats.incidents_per_year:.2f}/yr"
        f" (cpj={components['cpj_rate']:.0f}/15)"
        f" | RSF: {rsf_press_freedom:.1f}"
        f" (rsf={components['rsf_baseline']:.0f}/10)"
        f" | composite={total:.1f} → {level.value}"
        f" (confidence={confidence:.2f})"
    )

    return SeverityResult(
        level=level,
        score=round(total, 2),
        confidence=confidence,
        reasoning=reasoning,
        component_scores=components,
    )
