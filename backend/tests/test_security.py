"""
Tests for backend/security/rate_limiter.py and backend/security/output_validator.py.

No network calls, no Redis, no real FastAPI app required.
Request objects are mocked with MagicMock(headers={...}).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from slowapi import Limiter

from backend.security.rate_limiter import (
    ALERTS_RATE_LIMIT,
    MAP_RATE_LIMIT,
    QUERY_RATE_LIMIT,
    _get_device_id,
    limiter,
)
from backend.security.output_validator import (
    AlertOutput,
    Citation,
    JournalistQuery,
    WatchZone,
    validate_output,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_TIMESTAMP = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc).isoformat()


def _valid_alert(**overrides) -> dict:
    base = {
        "severity": "RED",
        "summary": "Active clashes reported near the watch zone.",
        "source_citations": [{"id": "https://acleddata.com/event/12345", "description": "Test citation description"}],
        "region": "Gaza",
        "timestamp": _VALID_TIMESTAMP,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestRateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_query_rate_limit_constant(self) -> None:
        assert QUERY_RATE_LIMIT == "10/minute"

    def test_alerts_rate_limit_constant(self) -> None:
        assert ALERTS_RATE_LIMIT == "30/minute"

    def test_map_rate_limit_constant(self) -> None:
        assert MAP_RATE_LIMIT == "30/minute"

    def test_get_device_id_from_header(self) -> None:
        request = MagicMock()
        request.headers = {"device_id": "abc123"}
        assert _get_device_id(request) == "abc123"

    def test_get_device_id_fallback_when_header_missing(self) -> None:
        request = MagicMock()
        request.headers = {}
        assert _get_device_id(request) == "unknown-device"

    def test_limiter_is_slowapi_limiter_instance(self) -> None:
        assert isinstance(limiter, Limiter)


# ---------------------------------------------------------------------------
# TestJournalistQuery
# ---------------------------------------------------------------------------


class TestJournalistQuery:
    def test_valid_query(self) -> None:
        q = JournalistQuery(text="What is the situation in Kyiv?", region="Ukraine")
        assert q.text == "What is the situation in Kyiv?"
        assert q.region == "Ukraine"
        assert q.language == "en"

    def test_text_too_short_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JournalistQuery(text="", region="Gaza")

    def test_text_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            JournalistQuery(text="x" * 501, region="Gaza")

    def test_language_pattern_rejects_digits(self) -> None:
        with pytest.raises(ValidationError):
            JournalistQuery(text="query", region="Gaza", language="e1")

    def test_language_default_is_en(self) -> None:
        q = JournalistQuery(text="query", region="Gaza")
        assert q.language == "en"


# ---------------------------------------------------------------------------
# TestWatchZone
# ---------------------------------------------------------------------------


class TestWatchZone:
    def test_valid_watch_zone(self) -> None:
        wz = WatchZone(latitude=31.5, longitude=34.47, radius_km=10.0, label="Gaza City")
        assert wz.latitude == 31.5
        assert wz.radius_km == 10.0

    def test_latitude_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WatchZone(latitude=91.0, longitude=34.47, radius_km=10.0, label="Test")

    def test_longitude_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WatchZone(latitude=31.5, longitude=181.0, radius_km=10.0, label="Test")

    def test_radius_too_large_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WatchZone(latitude=31.5, longitude=34.47, radius_km=501.0, label="Test")

    def test_radius_too_small_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WatchZone(latitude=31.5, longitude=34.47, radius_km=0.0, label="Test")


# ---------------------------------------------------------------------------
# TestAlertOutput
# ---------------------------------------------------------------------------


class TestAlertOutput:
    def test_valid_output_with_url_citation(self) -> None:
        alert = AlertOutput(**_valid_alert())
        assert alert.severity == "RED"
        assert alert.source_citations[0].id == "https://acleddata.com/event/12345"

    def test_valid_output_with_gdelt_cloud_id_citation(self) -> None:
        alert = AlertOutput(**_valid_alert(source_citations=[
            {"id": "conflict_PSE20240415", "description": "Armed Clash — Gaza City, 2026-04-15 (3 fatalities)"}
        ]))
        assert alert.source_citations[0].id == "conflict_PSE20240415"

    def test_valid_output_with_mixed_citations(self) -> None:
        alert = AlertOutput(**_valid_alert(source_citations=[
            {"id": "conflict_PSE20240415", "description": "Armed Clash — Gaza City"},
            {"id": "https://gdeltproject.org/article/123", "description": "Conflict escalates in Gaza"},
        ]))
        assert len(alert.source_citations) == 2

    @pytest.mark.parametrize("severity", [
        "GREEN", "AMBER", "RED", "CRITICAL", "INSUFFICIENT_DATA"
    ])
    def test_all_severity_values_accepted(self, severity: str) -> None:
        alert = AlertOutput(**_valid_alert(severity=severity))
        assert alert.severity == severity

    def test_invalid_citation_free_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AlertOutput(**_valid_alert(source_citations=[
                {"id": "ACLED event near Gaza", "description": "some description"}
            ]))

    # ------------------------------------------------------------------
    # CPJ / RSF historical-source citation IDs
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("citation_id", [
        "CPJ",
        "RSF",
        "CPJ:Syria-2024",
        "RSF:Press Freedom Index 2025",
        "CPJ:Israel and the Occupied Palestinian Territory",
    ])
    def test_cpj_rsf_citation_ids_accepted(self, citation_id: str) -> None:
        alert = AlertOutput(**_valid_alert(source_citations=[
            {"id": citation_id, "description": "Historical journalist safety data"}
        ]))
        assert alert.source_citations[0].id == citation_id

    @pytest.mark.parametrize("citation_id", [
        "CPJX",        # wrong prefix
        "RSF_score",   # underscore not colon
        "cpj",         # lowercase not accepted
        "rsf:score",   # lowercase not accepted
        "CPJ:",        # colon with nothing after
    ])
    def test_invalid_cpj_rsf_variants_rejected(self, citation_id: str) -> None:
        with pytest.raises(ValidationError):
            AlertOutput(**_valid_alert(source_citations=[
                {"id": citation_id, "description": "some description"}
            ]))

    def test_empty_citations_list_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AlertOutput(**_valid_alert(source_citations=[]))

    def test_summary_too_short_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AlertOutput(**_valid_alert(summary="Too short"))  # 9 chars

    def test_summary_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AlertOutput(**_valid_alert(summary="x" * 1001))

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AlertOutput(**_valid_alert(severity="UNKNOWN"))

    def test_timestamp_required(self) -> None:
        data = _valid_alert()
        del data["timestamp"]
        with pytest.raises(ValidationError):
            AlertOutput(**data)


# ---------------------------------------------------------------------------
# TestValidateOutput
# ---------------------------------------------------------------------------


class TestValidateOutput:
    def test_validate_output_returns_alert_on_valid_input(self) -> None:
        result = validate_output(_valid_alert(), region="Gaza")
        assert isinstance(result, AlertOutput)
        assert result.severity == "RED"
        assert result.region == "Gaza"
        assert result.source_citations[0].id == "https://acleddata.com/event/12345"

    def test_validate_output_returns_fallback_on_invalid_input(self) -> None:
        result = validate_output({"severity": "INVALID", "summary": "x"}, region="Gaza")
        assert isinstance(result, AlertOutput)
        assert result.severity == "INSUFFICIENT_DATA"

    def test_fallback_severity_is_insufficient_data(self) -> None:
        result = validate_output({}, region="Kyiv")
        assert result.severity == "INSUFFICIENT_DATA"

    def test_fallback_region_matches_input_region(self) -> None:
        result = validate_output({}, region="Kabul")
        assert result.region == "Kabul"
