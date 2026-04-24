"""
Independent tests for the severity scoring engine.

All inputs are constructed in-memory — no file I/O, no network calls,
no Redis.  Component scores are computed analytically so the expected
values in each test are independently verifiable.

Score anatomy (total 0–100):
  fatalities  0–30   8=1-3, 16=4-10, 24=11-25, 30=26+
  event_type  0–25   Explosions=25, Battles=22, VaC=18, Riots=10, Protests=5
  gdelt_tone  0–20   <-15→20, <-10→15, <-5→10, <0→5, ≥0→0
  cpj_rate    0–15   0→0, <1→3, <2→6, <5→10, 5+→15
  rsf_baseline 0–10  ≥75→0, ≥50→3, ≥25→7, <25→10

Thresholds:  GREEN <25 | AMBER 25-49 | RED 50-74 | CRITICAL ≥75
"""

import math
from datetime import date

import pytest

from backend.ingestion.cpj_connector import CountryStats
from backend.ingestion.gdelt_connector import GdeltArticle
from backend.ingestion.gdeltcloud_connector import GdeltCloudEvent
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.alerts.severity_scorer import (
    SeverityLevel,
    SeverityResult,
    _RECENCY_HALF_LIFE_DAYS,
    _compute_confidence,
    _recency_weight,
    _score_cpj_rate,
    _score_event_type,
    _score_fatalities,
    _score_gdelt_tone,
    _score_rsf,
    score_severity,
)

# Matches make_event's event_date → decay weight = 1.0 → deterministic scores.
_EVENT_DATE = date(2026, 4, 20)

# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def make_event(event_type: str, fatalities: int = 0) -> GdeltCloudEvent:
    return GdeltCloudEvent(
        id="TEST001",
        event_date="2026-04-20",
        event_type=event_type,
        fatalities=fatalities,
    )


def make_article() -> GdeltArticle:
    return GdeltArticle(
        url="https://reuters.com/test",
        title="Test article",
        seendate="20260420T120000Z",
    )


def make_cpj(incidents_per_year: float, total: int = 1) -> CountryStats:
    return CountryStats(
        country="Palestine",
        total_incidents=total,
        incidents_per_year=incidents_per_year,
        earliest_year=2020 if total > 0 else 0,
        latest_year=2026 if total > 0 else 0,
    )


# ---------------------------------------------------------------------------
# Component scorer unit tests
# ---------------------------------------------------------------------------


class TestFatalityScore:
    def test_zero_fatalities(self) -> None:
        assert _score_fatalities([make_event("Battles", 0)], _EVENT_DATE) == 0.0

    def test_one_to_three(self) -> None:
        assert _score_fatalities([make_event("Battles", 2)], _EVENT_DATE) == 8.0

    def test_four_to_ten(self) -> None:
        events = [make_event("Battles", 5), make_event("Battles", 3)]
        assert _score_fatalities(events, _EVENT_DATE) == 16.0  # 8 weighted total

    def test_eleven_to_twenty_five(self) -> None:
        assert _score_fatalities([make_event("Battles", 12)], _EVENT_DATE) == 24.0

    def test_over_twenty_five(self) -> None:
        assert _score_fatalities([make_event("Battles", 30)], _EVENT_DATE) == 30.0

    def test_empty_events(self) -> None:
        assert _score_fatalities([], _EVENT_DATE) == 0.0

    def test_none_fatalities_treated_as_zero(self) -> None:
        event = GdeltCloudEvent(id="X", event_date="2026-04-20", fatalities=None)
        assert _score_fatalities([event], _EVENT_DATE) == 0.0


class TestEventTypeScore:
    def test_explosions(self) -> None:
        assert _score_event_type([make_event("Explosions/Remote violence")]) == 25.0

    def test_battles(self) -> None:
        assert _score_event_type([make_event("Battles")]) == 22.0

    def test_violence_against_civilians(self) -> None:
        assert _score_event_type([make_event("Violence against civilians")]) == 18.0

    def test_riots(self) -> None:
        assert _score_event_type([make_event("Riots")]) == 10.0

    def test_protests(self) -> None:
        assert _score_event_type([make_event("Protests")]) == 5.0

    def test_strategic_developments(self) -> None:
        assert _score_event_type([make_event("Strategic developments")]) == 3.0

    def test_unknown_type_gets_default(self) -> None:
        assert _score_event_type([make_event("Unknown type XYZ")]) == 5.0

    def test_none_event_type_gets_default(self) -> None:
        event = GdeltCloudEvent(id="X", event_date="2026-04-20", event_type=None)
        assert _score_event_type([event]) == 5.0

    def test_takes_max_across_events(self) -> None:
        events = [make_event("Protests"), make_event("Battles")]
        assert _score_event_type(events) == 22.0

    def test_battles_higher_than_protests(self) -> None:
        assert (
            _score_event_type([make_event("Battles")])
            > _score_event_type([make_event("Protests")])
        )

    def test_empty_events(self) -> None:
        assert _score_event_type([]) == 0.0

    # GDELT Cloud native event types
    def test_gdelt_air_drone_strike(self) -> None:
        assert _score_event_type([make_event("Air/Drone Strike")]) == 25.0

    def test_gdelt_shelling(self) -> None:
        assert _score_event_type([make_event("Shelling/Artillery/Missiles Fired")]) == 25.0

    def test_gdelt_armed_clash(self) -> None:
        assert _score_event_type([make_event("Armed Clash")]) == 22.0

    def test_gdelt_political_violence(self) -> None:
        assert _score_event_type([make_event("Political Violence")]) == 18.0


class TestGdeltToneScore:
    def test_very_hostile(self) -> None:
        assert _score_gdelt_tone(-18.0) == 20.0

    def test_hostile(self) -> None:
        assert _score_gdelt_tone(-12.0) == 15.0

    def test_negative(self) -> None:
        assert _score_gdelt_tone(-7.0) == 10.0

    def test_mildly_negative(self) -> None:
        assert _score_gdelt_tone(-3.0) == 5.0

    def test_neutral_or_positive(self) -> None:
        assert _score_gdelt_tone(2.0) == 0.0

    def test_zero_tone(self) -> None:
        assert _score_gdelt_tone(0.0) == 0.0

    def test_boundary_at_minus_fifteen(self) -> None:
        assert _score_gdelt_tone(-15.0) == 15.0   # not < -15, so next bucket

    def test_boundary_at_minus_ten(self) -> None:
        assert _score_gdelt_tone(-10.0) == 10.0   # not < -10, so next bucket

    def test_boundary_at_minus_five(self) -> None:
        assert _score_gdelt_tone(-5.0) == 5.0     # not < -5, so mildly-negative


class TestCpjRateScore:
    def test_zero_rate(self) -> None:
        assert _score_cpj_rate(make_cpj(0.0, total=0)) == 0.0

    def test_below_one(self) -> None:
        assert _score_cpj_rate(make_cpj(0.5)) == 3.0

    def test_one_to_two(self) -> None:
        assert _score_cpj_rate(make_cpj(1.5)) == 6.0

    def test_two_to_five(self) -> None:
        assert _score_cpj_rate(make_cpj(3.0)) == 10.0

    def test_five_or_more(self) -> None:
        assert _score_cpj_rate(make_cpj(8.0)) == 15.0


class TestRsfScore:
    def test_good_freedom(self) -> None:
        assert _score_rsf(85.0) == 0.0

    def test_satisfactory_freedom(self) -> None:
        assert _score_rsf(65.0) == 3.0

    def test_difficult_freedom(self) -> None:
        assert _score_rsf(40.0) == 7.0

    def test_very_serious(self) -> None:
        assert _score_rsf(15.0) == 10.0

    def test_boundary_at_75(self) -> None:
        assert _score_rsf(75.0) == 0.0

    def test_boundary_at_50(self) -> None:
        assert _score_rsf(50.0) == 3.0

    def test_boundary_at_25(self) -> None:
        assert _score_rsf(25.0) == 7.0


class TestConfidence:
    def test_full_data_gives_max_confidence(self) -> None:
        events = [make_event("Battles", 5), make_event("Battles", 3), make_event("Riots", 1)]
        articles = [make_article()]
        cpj = make_cpj(3.0, total=10)
        conf = _compute_confidence(events, articles, cpj, 40.0)
        assert conf == 1.0

    def test_no_events_reduces_confidence(self) -> None:
        articles = [make_article()]
        cpj = make_cpj(3.0, total=10)
        conf = _compute_confidence([], articles, cpj, 40.0)
        assert conf == pytest.approx(0.7)

    def test_sparse_events_reduces_confidence(self) -> None:
        events = [make_event("Battles", 5)]  # only 1 event < 3
        articles = [make_article()]
        cpj = make_cpj(3.0, total=10)
        conf = _compute_confidence(events, articles, cpj, 40.0)
        assert conf == pytest.approx(0.9)

    def test_no_articles_reduces_confidence(self) -> None:
        events = [make_event("Battles", 5), make_event("Riots", 2), make_event("Protests")]
        cpj = make_cpj(3.0, total=10)
        conf = _compute_confidence(events, [], cpj, 40.0)
        assert conf == pytest.approx(0.8)

    def test_no_cpj_data_reduces_confidence(self) -> None:
        events = [make_event("Battles", 5), make_event("Riots", 2), make_event("Protests")]
        articles = [make_article()]
        cpj = make_cpj(0.0, total=0)
        conf = _compute_confidence(events, articles, cpj, 40.0)
        assert conf == pytest.approx(0.95)

    def test_zero_rsf_reduces_confidence(self) -> None:
        events = [make_event("Battles", 5), make_event("Riots", 2), make_event("Protests")]
        articles = [make_article()]
        cpj = make_cpj(3.0, total=10)
        conf = _compute_confidence(events, articles, cpj, 0.0)
        assert conf == pytest.approx(0.9)

    def test_confidence_never_below_0_1(self) -> None:
        cpj = make_cpj(0.0, total=0)
        conf = _compute_confidence([], [make_article()], cpj, 0.0)
        assert conf >= 0.1


# ---------------------------------------------------------------------------
# score_severity — end-to-end severity level tests
# ---------------------------------------------------------------------------


class TestGreenLevel:
    def test_green_score(self) -> None:
        # Protests + 0 fatalities + tone=+2 + CPJ 0/yr + RSF 85
        # = 0 + 5 + 0 + 0 + 0 = 5 → GREEN
        result = score_severity(
            conflict_events=[make_event("Protests", 0)],
            gdelt_articles=[make_article()],
            cpj_stats=make_cpj(0.0, total=0),
            rsf_press_freedom=85.0,
        )
        assert result.level == SeverityLevel.GREEN
        assert result.score == pytest.approx(5.0)

    def test_green_has_high_confidence_with_full_data(self) -> None:
        events = [make_event("Protests"), make_event("Protests"), make_event("Protests")]
        result = score_severity(events, [make_article()], make_cpj(0.0, total=0), 90.0)
        assert result.confidence >= 0.95


class TestAmberLevel:
    def test_amber_score(self) -> None:
        # Riots + 5 fatalities + tone=-3 + CPJ 0/yr + RSF 85
        # = 16 + 10 + 5 + 0 + 0 = 31 → AMBER
        result = score_severity(
            conflict_events=[make_event("Riots", 5)],
            gdelt_articles=[make_article()],
            cpj_stats=make_cpj(0.0, total=0),
            rsf_press_freedom=85.0,
            reference_date=_EVENT_DATE,
            gdelt_aggregate_tone=-3.0,
        )
        assert result.level == SeverityLevel.AMBER
        assert result.score == pytest.approx(31.0)


class TestRedLevel:
    def test_red_score(self) -> None:
        # Battles + 12 fatalities + tone=-7 + CPJ 1.5/yr + RSF 40
        # = 24 + 22 + 10 + 6 + 7 = 69 → RED
        result = score_severity(
            conflict_events=[make_event("Battles", 12)],
            gdelt_articles=[make_article()],
            cpj_stats=make_cpj(1.5, total=5),
            rsf_press_freedom=40.0,
            reference_date=_EVENT_DATE,
            gdelt_aggregate_tone=-7.0,
        )
        assert result.level == SeverityLevel.RED
        assert result.score == pytest.approx(69.0)


class TestCriticalLevel:
    def test_critical_score(self) -> None:
        # Explosions + 30 fatalities + tone=-18 + CPJ 8/yr + RSF 15
        # = 30 + 25 + 20 + 15 + 10 = 100 → CRITICAL
        result = score_severity(
            conflict_events=[make_event("Explosions/Remote violence", 30)],
            gdelt_articles=[make_article()],
            cpj_stats=make_cpj(8.0, total=50),
            rsf_press_freedom=15.0,
            reference_date=_EVENT_DATE,
            gdelt_aggregate_tone=-18.0,
        )
        assert result.level == SeverityLevel.CRITICAL
        assert result.score == pytest.approx(100.0)

    def test_score_capped_at_100(self) -> None:
        # Multiple high-severity events push sum > 100 — should be capped.
        events = [make_event("Explosions/Remote violence", 30)] * 5
        articles = [make_article()] * 5
        result = score_severity(events, articles, make_cpj(10.0, total=100), 5.0,
                                gdelt_aggregate_tone=-20.0)
        assert result.score <= 100.0
        assert result.level == SeverityLevel.CRITICAL


class TestInsufficientData:
    def test_no_events_no_gdelt_no_historical_returns_insufficient(self) -> None:
        """INSUFFICIENT_DATA requires zero signal from all four sources."""
        result = score_severity(
            conflict_events=[],
            gdelt_articles=[],
            cpj_stats=make_cpj(0.0, total=0),
            rsf_press_freedom=0.0,
        )
        assert result.level == SeverityLevel.INSUFFICIENT_DATA
        assert result.score == 0.0
        assert result.confidence == 0.0
        assert result.component_scores == {}
        assert result.historical_only is False

    def test_insufficient_data_reasoning_is_descriptive(self) -> None:
        result = score_severity([], [], make_cpj(0.0, total=0), 0.0)
        assert "insufficient" in result.reasoning.lower()

    def test_non_zero_cpj_triggers_historical_fallback_not_insufficient(self) -> None:
        """With CPJ data, empty live sources must not produce INSUFFICIENT_DATA."""
        result = score_severity([], [], make_cpj(5.0, total=20), 20.0)
        assert result.level != SeverityLevel.INSUFFICIENT_DATA
        assert result.historical_only is True


# ---------------------------------------------------------------------------
# Historical fallback — CPJ + RSF only
# ---------------------------------------------------------------------------


class TestHistoricalFallback:
    def test_syria_profile_produces_amber(self) -> None:
        """Syria: CPJ 10.19/yr → cpj=15, RSF 15.82 → rsf=10, total=25 → AMBER."""
        result = score_severity(
            [], [], make_cpj(10.19, total=100), 15.82
        )
        assert result.level == SeverityLevel.AMBER
        assert result.historical_only is True
        assert result.score == pytest.approx(25.0)

    def test_historical_only_flag_set(self) -> None:
        result = score_severity([], [], make_cpj(2.0, total=10), 20.0)
        assert result.historical_only is True

    def test_historical_only_has_all_five_component_keys(self) -> None:
        result = score_severity([], [], make_cpj(2.0, total=10), 20.0)
        assert set(result.component_scores.keys()) == {
            "fatalities", "event_type", "gdelt_tone", "cpj_rate", "rsf_baseline"
        }

    def test_live_components_are_zero_in_historical_fallback(self) -> None:
        result = score_severity([], [], make_cpj(2.0, total=10), 20.0)
        assert result.component_scores["fatalities"] == 0.0
        assert result.component_scores["event_type"] == 0.0
        assert result.component_scores["gdelt_tone"] == 0.0

    def test_historical_confidence_is_below_normal_minimum(self) -> None:
        """Historical-only confidence must be ≤ 0.30 — clearly degraded."""
        result = score_severity([], [], make_cpj(2.0, total=10), 30.0)
        assert result.confidence <= 0.30

    def test_historical_confidence_deducted_for_zero_cpj(self) -> None:
        result_with_cpj = score_severity([], [], make_cpj(1.0, total=5), 20.0)
        result_zero_cpj = score_severity([], [], make_cpj(0.0, total=0), 20.0)
        assert result_zero_cpj.confidence < result_with_cpj.confidence

    def test_historical_reasoning_contains_historical_only_marker(self) -> None:
        result = score_severity([], [], make_cpj(2.0, total=10), 20.0)
        assert "HISTORICAL ONLY" in result.reasoning

    def test_rsf_only_signal_still_produces_fallback(self) -> None:
        """Even with zero CPJ, a non-zero RSF score should trigger the fallback."""
        result = score_severity([], [], make_cpj(0.0, total=0), 20.0)
        assert result.historical_only is True
        # rsf=20.0 < 25 → _score_rsf returns 10.0
        assert result.component_scores["rsf_baseline"] == 10.0

    def test_high_cpj_rate_caps_at_amber(self) -> None:
        """Maximum historical score is 15+10=25 → AMBER, never RED or CRITICAL."""
        result = score_severity([], [], make_cpj(10.0, total=50), 0.0)
        # rsf=0 → 10pts, cpj=15pts → 25 → AMBER
        assert result.level in {SeverityLevel.AMBER, SeverityLevel.GREEN}
        assert result.score <= 25.0

    def test_green_for_low_risk_country_without_live_data(self) -> None:
        """Low CPJ rate + moderate RSF → GREEN even without live data."""
        # cpj=0.5/yr → 3pts, rsf=60.0 → 3pts, total=6 → GREEN
        result = score_severity([], [], make_cpj(0.5, total=5), 60.0)
        assert result.level == SeverityLevel.GREEN
        assert result.historical_only is True


# ---------------------------------------------------------------------------
# Historical risk floor — live path (events=[], articles present)
# ---------------------------------------------------------------------------


class TestHistoricalRiskFloor:
    def test_yemen_profile_floors_to_amber_via_cpj_rate(self) -> None:
        """CPJ 3.5/yr ≥ 3.0 triggers floor; RSF 31.45 ≥ 30.0 does not."""
        # composite: cpj=10 + rsf=7 = 17 → GREEN → floor → AMBER
        result = score_severity([], [make_article()], make_cpj(3.5, total=35), 31.45)
        assert result.level == SeverityLevel.AMBER
        assert result.floor_applied is True
        assert "CPJ" in result.floor_reason

    def test_syria_profile_floors_to_amber_via_both_conditions(self) -> None:
        """CPJ 3.5/yr ≥ 3.0 AND RSF 15.82 < 30.0 — both conditions trigger."""
        # composite: cpj=10 + rsf=10 = 20 → GREEN → floor → AMBER
        result = score_severity([], [make_article()], make_cpj(3.5, total=35), 15.82)
        assert result.level == SeverityLevel.AMBER
        assert result.floor_applied is True
        assert "CPJ" in result.floor_reason
        assert "RSF" in result.floor_reason

    def test_sudan_profile_stays_green_no_floor(self) -> None:
        """CPJ 0.53 < 3.0 and RSF 30.34 ≥ 30.0 — neither condition met."""
        # composite: cpj=3 + rsf=7 = 10 → GREEN, no floor
        result = score_severity([], [make_article()], make_cpj(0.53, total=5), 30.34)
        assert result.level == SeverityLevel.GREEN
        assert result.floor_applied is False
        assert result.floor_reason == ""

    def test_palestine_with_events_floor_not_applied(self) -> None:
        """conflict_events non-empty → floor bypassed entirely."""
        result = score_severity(
            [make_event("Armed Clash", fatalities=5)], [make_article()],
            make_cpj(2.4, total=12), 27.41,
        )
        assert result.floor_applied is False

    def test_floor_not_applied_when_composite_already_amber(self) -> None:
        """Both conditions met, but composite already AMBER — floor_applied stays False."""
        # cpj=5.0/yr → 15pts, rsf=15.0 → 10pts, tone=-6.0 → 10pts → total=35 → AMBER
        result = score_severity(
            [], [make_article()], make_cpj(5.0, total=25), 15.0,
            gdelt_aggregate_tone=-6.0,
        )
        assert result.level == SeverityLevel.AMBER
        assert result.floor_applied is False

    def test_floor_reason_mentions_both_conditions_when_both_trigger(self) -> None:
        # cpj=4.0/yr → 10pts, rsf=20.0 → 10pts → total=20 → GREEN → floor
        result = score_severity([], [make_article()], make_cpj(4.0, total=20), 20.0)
        assert result.floor_applied is True
        assert "CPJ" in result.floor_reason
        assert "RSF" in result.floor_reason

    def test_floor_info_appears_in_reasoning_string(self) -> None:
        result = score_severity([], [make_article()], make_cpj(4.0, total=20), 20.0)
        assert result.floor_applied is True
        assert "floor" in result.reasoning.lower()

    def test_rsf_zero_sentinel_does_not_trigger_floor(self) -> None:
        """rsf=0.0 means country not in RSF index — must not trigger RSF floor condition."""
        result = score_severity([], [make_article()], make_cpj(0.5, total=5), 0.0)
        assert result.floor_applied is False


# ---------------------------------------------------------------------------
# Partial data scenarios
# ---------------------------------------------------------------------------


class TestPartialData:
    def test_conflict_events_only_no_gdelt_still_scores(self) -> None:
        result = score_severity(
            conflict_events=[make_event("Battles", 5)],
            gdelt_articles=[],
            cpj_stats=make_cpj(0.0, total=0),
            rsf_press_freedom=85.0,
        )
        assert result.level != SeverityLevel.INSUFFICIENT_DATA
        assert result.component_scores["gdelt_tone"] == 0.0

    def test_gdelt_only_no_conflict_events_still_scores(self) -> None:
        result = score_severity(
            conflict_events=[],
            gdelt_articles=[make_article()],
            cpj_stats=make_cpj(0.0, total=0),
            rsf_press_freedom=85.0,
            gdelt_aggregate_tone=-12.0,
        )
        assert result.level != SeverityLevel.INSUFFICIENT_DATA
        assert result.component_scores["fatalities"] == 0.0
        assert result.component_scores["gdelt_tone"] == 15.0

    def test_conflict_events_only_confidence_reduced(self) -> None:
        result = score_severity(
            conflict_events=[make_event("Battles"), make_event("Riots"), make_event("Protests")],
            gdelt_articles=[],
            cpj_stats=make_cpj(3.0, total=10),
            rsf_press_freedom=50.0,
        )
        assert result.confidence < 1.0

    def test_gdelt_only_confidence_reduced(self) -> None:
        result = score_severity(
            conflict_events=[],
            gdelt_articles=[make_article()],
            cpj_stats=make_cpj(3.0, total=10),
            rsf_press_freedom=50.0,
        )
        assert result.confidence < 1.0


# ---------------------------------------------------------------------------
# SeverityResult structure
# ---------------------------------------------------------------------------


class TestResultStructure:
    def test_component_scores_has_all_five_keys(self) -> None:
        result = score_severity(
            [make_event("Battles", 5)],
            [make_article()],
            make_cpj(1.0, total=5),
            50.0,
        )
        assert set(result.component_scores.keys()) == {
            "fatalities", "event_type", "gdelt_tone", "cpj_rate", "rsf_baseline"
        }

    def test_reasoning_contains_composite_score(self) -> None:
        result = score_severity(
            [make_event("Battles", 5)],
            [make_article()],
            make_cpj(1.0, total=5),
            50.0,
        )
        assert "composite=" in result.reasoning

    def test_reasoning_contains_level(self) -> None:
        result = score_severity(
            [make_event("Battles", 5)],
            [make_article()],
            make_cpj(1.0, total=5),
            50.0,
        )
        assert result.level.value in result.reasoning

    def test_reasoning_mentions_gdelt_cloud(self) -> None:
        result = score_severity(
            [make_event("Battles", 5)],
            [make_article()],
            make_cpj(1.0, total=5),
            50.0,
        )
        assert "GDELT Cloud" in result.reasoning

    def test_result_is_pydantic_model(self) -> None:
        result = score_severity(
            [make_event("Protests")],
            [make_article()],
            make_cpj(0.0, total=0),
            80.0,
        )
        assert isinstance(result, SeverityResult)

    def test_score_matches_sum_of_components(self) -> None:
        result = score_severity(
            [make_event("Riots", 5)],
            [make_article()],
            make_cpj(0.0, total=0),
            85.0,
            reference_date=_EVENT_DATE,
            gdelt_aggregate_tone=-3.0,
        )
        expected = sum(result.component_scores.values())
        assert result.score == pytest.approx(min(expected, 100.0))


# ---------------------------------------------------------------------------
# Recency decay
# ---------------------------------------------------------------------------


class TestRecencyDecay:
    def test_today_event_has_full_weight(self) -> None:
        event = make_event("Battles", 4)  # event_date = 2026-04-20
        assert _score_fatalities([event], _EVENT_DATE) == 16.0

    def test_seven_day_old_event_has_half_weight(self) -> None:
        # 7 days before reference → weight = 0.5 → 10 fat × 0.5 = 5.0 → bucket 4-10 → 16 pts
        old_event = GdeltCloudEvent(
            id="OLD001",
            event_date="2026-04-13",   # 7 days before _EVENT_DATE
            event_type="Battles",
            fatalities=10,
        )
        score = _score_fatalities([old_event], _EVENT_DATE)
        assert score == 16.0  # 10 × 0.5 = 5.0 → bucket 4-10

    def test_thirty_day_old_event_scores_lower_than_same_count_today(self) -> None:
        # 26 fat today → weight=1.0 → weighted=26 → 30 pts (max)
        # 26 fat 30 days ago → weight≈0.051 → weighted≈1.33 → 8 pts (lowest)
        recent = make_event("Battles", 26)  # event_date = _EVENT_DATE
        old_event = GdeltCloudEvent(
            id="OLD002",
            event_date="2026-03-21",   # 30 days before _EVENT_DATE
            event_type="Battles",
            fatalities=26,
        )
        recent_score = _score_fatalities([recent], _EVENT_DATE)
        old_score = _score_fatalities([old_event], _EVENT_DATE)
        assert recent_score == 30.0
        assert old_score == 8.0
        assert recent_score > old_score

    def test_future_date_treated_as_today(self) -> None:
        future_event = GdeltCloudEvent(
            id="FUT001",
            event_date="2026-05-01",   # 11 days after _EVENT_DATE
            event_type="Battles",
            fatalities=4,
        )
        assert _score_fatalities([future_event], _EVENT_DATE) == 16.0

    def test_unparseable_date_falls_back_to_weight_one(self) -> None:
        bad_date_event = GdeltCloudEvent(
            id="BAD001",
            event_date="April 20 2026",   # not ISO format
            event_type="Battles",
            fatalities=4,
        )
        assert _score_fatalities([bad_date_event], _EVENT_DATE) == 16.0

    def test_score_severity_threads_reference_date_to_fatality_scorer(self) -> None:
        event = make_event("Battles", 26)  # event_date = 2026-04-20
        cpj = make_cpj(0.0, total=0)
        articles = [make_article()]

        result_same_day = score_severity(
            [event], articles, cpj, 75.0,
            reference_date=_EVENT_DATE,           # 0 days → weight=1.0 → 30 pts
        )
        result_thirty_days_later = score_severity(
            [event], articles, cpj, 75.0,
            reference_date=date(2026, 5, 20),     # 30 days → weight≈0.051 → 8 pts
        )

        assert result_same_day.component_scores["fatalities"] == 30.0
        assert result_thirty_days_later.component_scores["fatalities"] == 8.0


# ---------------------------------------------------------------------------
# Iran — RSF alias resolution and score floor
# ---------------------------------------------------------------------------


class TestWatchZoneConfig:
    """Verify config.py settings that control per-country data quality fixes."""

    def test_iran_in_watch_zones(self) -> None:
        from backend.config import settings
        assert "Iran" in settings.WATCH_ZONES

    def test_no_fatalities_filter_countries_covers_expected_set(self) -> None:
        from backend.config import settings
        expected = {"Iran", "Sudan", "Myanmar", "Yemen", "Syria"}
        assert expected == settings.NO_FATALITIES_FILTER_COUNTRIES

    def test_palestine_not_in_no_fatalities_filter(self) -> None:
        """Palestine has confirmed fatalities events — keep the filter on."""
        from backend.config import settings
        assert "Palestine" not in settings.NO_FATALITIES_FILTER_COUNTRIES

    def test_gdelt_cloud_aliases_syria_not_aliased(self) -> None:
        """'Syrian Arab Republic' returns 400 from the GDELT Cloud API —
        Syria must be queried as 'Syria' (no alias)."""
        from backend.config import settings
        assert "Syria" not in settings.GDELT_CLOUD_ALIASES

    def test_gdelt_cloud_aliases_passthrough_for_unlisted(self) -> None:
        from backend.config import settings
        assert settings.GDELT_CLOUD_ALIASES.get("Palestine", "Palestine") == "Palestine"


class TestIranRsfResolution:
    def test_iran_alias_maps_to_islamic_rep(self) -> None:
        """RSF_ALIASES must translate "Iran" to the RSF index key."""
        assert RSF_ALIASES.get("Iran") == "Iran, Islamic Rep."

    def test_iran_rsf_score_resolves_via_alias(self) -> None:
        """Two-step lookup used in routes: RSF_ALIASES.get(region, region) then RSF_SCORES."""
        rsf_key = RSF_ALIASES.get("Iran", "Iran")
        score = RSF_SCORES.get(rsf_key, 0.0)
        assert score == pytest.approx(16.22)

    def test_iran_rsf_score_gives_max_baseline(self) -> None:
        """16.22 < 25 → _score_rsf must return 10.0 (max baseline contribution)."""
        assert _score_rsf(16.22) == 10.0

    def test_iran_zero_gdelt_events_floors_to_amber_via_rsf(self) -> None:
        """With 0 GDELT Cloud events, the historical risk floor raises Iran to AMBER.
        RSF 16.22 < 30.0 triggers the floor even though CPJ rate 0.75/yr < 3.0.
        Previously documented as a scoring gap — now fixed by _apply_floor."""
        cpj = make_cpj(0.75, total=21)   # ~Iran's real CPJ rate
        articles = [make_article()]

        result = score_severity(
            [],            # 0 GDELT Cloud events — simulates the Iran failure mode
            articles,
            cpj,
            16.22,         # Iran's RSF score (< 30.0 → floor triggers)
            gdelt_aggregate_tone=-3.0,
        )
        # fatalities=0, event_type=0, tone=5, cpj=3, rsf=10 → composite=18 → GREEN
        # → floor: RSF 16.22 < 30.0 → raised to AMBER
        assert result.level == SeverityLevel.AMBER
        assert result.floor_applied is True
        assert "RSF" in result.floor_reason
        assert result.component_scores["fatalities"] == 0.0
        assert result.component_scores["event_type"] == 0.0
        assert result.component_scores["rsf_baseline"] == 10.0


# ---------------------------------------------------------------------------
# Gaza / Gaza Strip alias resolution
# ---------------------------------------------------------------------------


class TestGazaAliasResolution:
    def test_gaza_rsf_alias_resolves_to_west_bank_and_gaza(self) -> None:
        assert RSF_ALIASES.get("Gaza") == "West Bank and Gaza"

    def test_gaza_strip_rsf_alias_resolves_to_west_bank_and_gaza(self) -> None:
        assert RSF_ALIASES.get("Gaza Strip") == "West Bank and Gaza"

    def test_gaza_rsf_score_resolves_via_alias(self) -> None:
        rsf_key = RSF_ALIASES.get("Gaza", "Gaza")
        assert rsf_key in RSF_SCORES, f"RSF key {rsf_key!r} not found in RSF_SCORES"

    def test_gaza_gdelt_cloud_alias_resolves_to_palestine(self) -> None:
        from backend.config import settings
        assert settings.GDELT_CLOUD_ALIASES.get("Gaza") == "Palestine"

    def test_gaza_strip_gdelt_cloud_alias_resolves_to_palestine(self) -> None:
        from backend.config import settings
        assert settings.GDELT_CLOUD_ALIASES.get("Gaza Strip") == "Palestine"
