"""
Independent tests for the GDELT Cloud conflict events connector.

All external I/O (httpx, Redis) is mocked — no real network calls, no real
Redis instance required. Tests are grouped by scenario.
"""

import datetime as real_datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.ingestion.gdeltcloud_connector import (
    GDELT_CLOUD_CACHE_TTL,
    GdeltCloudActor,
    GdeltCloudConnector,
    GdeltCloudEvent,
    GdeltCloudGeo,
    GdeltCloudMetrics,
    GdeltCloudResponse,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

# Raw API response shape — matches what https://gdeltcloud.com/api/v2/events
# actually returns.  "category" and "subcategory" are the raw API field names;
# the connector maps them to event_type / sub_event_type.
SAMPLE_RAW_EVENT: dict = {
    "id": "conflict_001",
    "event_date": "2026-04-23",
    "category": "Armed Clash",
    "subcategory": "Armed clash",
    "fatalities": 3,
    "has_fatalities": True,
    "title": "Clashes reported near Aleppo",
    "summary": "Clashes reported near checkpoint.",
    "geo": {
        "country": "Syria",
        "admin1": "Aleppo",
        "location": "Northern Aleppo",
        "latitude": 36.2,
        "longitude": 37.1,
    },
    "actors": [
        {"name": "Syrian Armed Forces", "country": "Syria", "role": "actor1"},
        {"name": "Opposition Forces", "country": "Syria", "role": "actor2"},
    ],
    "metrics": {
        "significance": 0.374,
        "goldstein_scale": -9.0,
        "confidence": 0.83,
        "article_count": 1,
    },
}

# Matches the top-level shape returned by the real API.
SAMPLE_API_RESPONSE: dict = {
    "success": True,
    "data": [SAMPLE_RAW_EVENT],
}

# The parsed GdeltCloudEvent produced after the connector processes
# SAMPLE_RAW_EVENT through _parse_event().
PARSED_EVENT = GdeltCloudEvent(
    id="conflict_001",
    event_date="2026-04-23",
    event_type="Armed Clash",
    sub_event_type="Armed clash",
    fatalities=3,
    has_fatalities=True,
    title="Clashes reported near Aleppo",
    summary="Clashes reported near checkpoint.",
    geo=GdeltCloudGeo(
        country="Syria",
        admin1="Aleppo",
        location="Northern Aleppo",
        latitude=36.2,
        longitude=37.1,
    ),
    actors=[
        GdeltCloudActor(name="Syrian Armed Forces", country="Syria", role="actor1"),
        GdeltCloudActor(name="Opposition Forces", country="Syria", role="actor2"),
    ],
    metrics=GdeltCloudMetrics(
        significance=0.374,
        goldstein_scale=-9.0,
        confidence=0.83,
        article_count=1,
    ),
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Frozen date used to make date_start / date_end calculations deterministic.
_FROZEN_DATE = real_datetime.date(2026, 4, 25)


@pytest.fixture
def frozen_today():
    """Patch datetime.date.today() in the connector to return _FROZEN_DATE."""
    mock_dt = MagicMock()
    mock_dt.date.today.return_value = _FROZEN_DATE
    mock_dt.timedelta = real_datetime.timedelta
    with patch("backend.ingestion.gdeltcloud_connector.datetime", mock_dt):
        yield _FROZEN_DATE


@pytest.fixture
def mock_settings() -> MagicMock:
    s = MagicMock()
    s.GDELT_CLOUD_API_KEY = "test-gdelt-cloud-key"
    return s


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Redis client with default cache-miss behaviour."""
    r = AsyncMock()
    r.get.return_value = None
    r.set.return_value = True
    return r


@pytest.fixture
def events_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = SAMPLE_API_RESPONSE
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def mock_http_client(events_response: MagicMock) -> AsyncMock:
    """httpx.AsyncClient mock that returns the sample event response."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = events_response
    return client


# ---------------------------------------------------------------------------
# Pydantic model tests — no I/O
# ---------------------------------------------------------------------------


class TestGdeltCloudEvent:
    def test_valid_full_event(self) -> None:
        event = PARSED_EVENT
        assert event.id == "conflict_001"
        assert event.event_type == "Armed Clash"
        assert event.fatalities == 3
        assert event.geo is not None
        assert event.geo.latitude == pytest.approx(36.2)
        assert event.geo.country == "Syria"
        assert event.metrics is not None
        assert event.metrics.confidence == pytest.approx(0.83)
        assert event.metrics.goldstein_scale == pytest.approx(-9.0)
        assert len(event.actors) == 2
        assert event.actors[0].role == "actor1"
        assert event.actors[1].role == "actor2"

    def test_only_required_fields(self) -> None:
        event = GdeltCloudEvent(id="MIN001", event_date="2026-04-23")
        assert event.id == "MIN001"
        assert event.event_date == "2026-04-23"
        assert event.fatalities is None
        assert event.event_type is None
        assert event.geo is None
        assert event.actors == []
        assert event.metrics is None

    def test_all_optional_fields_default_to_none(self) -> None:
        event = GdeltCloudEvent(id="X", event_date="2026-01-01")
        for field in (
            "event_type", "sub_event_type", "fatalities", "has_fatalities",
            "title", "summary", "geo", "metrics",
        ):
            assert getattr(event, field) is None, f"{field} should default to None"
        assert event.actors == []

    def test_zero_fatalities_accepted(self) -> None:
        event = GdeltCloudEvent(id="X", event_date="2026-04-23", fatalities=0)
        assert event.fatalities == 0

    def test_negative_goldstein_scale_accepted(self) -> None:
        event = GdeltCloudEvent(
            id="X",
            event_date="2026-04-23",
            metrics=GdeltCloudMetrics(goldstein_scale=-9.0),
        )
        assert event.metrics is not None
        assert event.metrics.goldstein_scale == pytest.approx(-9.0)

    def test_nested_geo_model(self) -> None:
        geo = GdeltCloudGeo(latitude=36.2, longitude=37.1, country="Syria")
        event = GdeltCloudEvent(id="GEO001", event_date="2026-04-23", geo=geo)
        assert event.geo is not None
        assert event.geo.latitude == pytest.approx(36.2)
        assert event.geo.country == "Syria"

    def test_nested_metrics_model(self) -> None:
        metrics = GdeltCloudMetrics(confidence=0.9, article_count=5)
        event = GdeltCloudEvent(id="MET001", event_date="2026-04-23", metrics=metrics)
        assert event.metrics is not None
        assert event.metrics.confidence == pytest.approx(0.9)
        assert event.metrics.article_count == 5

    def test_actors_list_populated(self) -> None:
        actors = [
            GdeltCloudActor(name="Group A", role="actor1"),
            GdeltCloudActor(name="Group B", role="actor2"),
        ]
        event = GdeltCloudEvent(id="ACT001", event_date="2026-04-23", actors=actors)
        assert len(event.actors) == 2
        assert event.actors[0].name == "Group A"
        assert event.actors[1].role == "actor2"


class TestGdeltCloudResponse:
    def test_wraps_event_list(self) -> None:
        resp = GdeltCloudResponse(events=[PARSED_EVENT])
        assert len(resp.events) == 1
        assert isinstance(resp.events[0], GdeltCloudEvent)
        assert resp.events[0].id == "conflict_001"

    def test_empty_events_list(self) -> None:
        resp = GdeltCloudResponse(events=[])
        assert resp.events == []

    def test_defaults_to_empty_list(self) -> None:
        resp = GdeltCloudResponse()
        assert resp.events == []


# ---------------------------------------------------------------------------
# Cache miss — full API flow
# ---------------------------------------------------------------------------


class TestCacheMiss:
    async def test_returns_events(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert len(events) == 1
        assert events[0].id == "conflict_001"

    async def test_returns_gdelt_cloud_event_instances(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert isinstance(events[0], GdeltCloudEvent)
        assert events[0].geo is not None
        assert events[0].metrics is not None
        assert len(events[0].actors) == 2

    async def test_category_mapped_to_event_type(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        """API field 'category' must be mapped to GdeltCloudEvent.event_type."""
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert events[0].event_type == "Armed Clash"   # from raw "category"

    async def test_get_uses_bearer_token(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Syria")

        _, kwargs = mock_http_client.get.call_args
        assert kwargs["headers"] == {
            "Authorization": "Bearer test-gdelt-cloud-key"
        }

    async def test_confirmed_query_params_sent(
        self,
        frozen_today,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        # days=3 → date_start = 2026-04-25 - 2 = 2026-04-23, date_end = 2026-04-25
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Syria", days=3, limit=10)

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["country"] == "Syria"
        assert params["event_family"] == "conflict"
        assert params["has_fatalities"] == "true"
        assert params["sort"] == "recent"
        assert params["limit"] == 10
        assert params["date_start"] == "2026-04-23"
        assert params["date_end"] == "2026-04-25"
        assert "days" not in params
        assert "format" not in params

    async def test_events_written_to_redis_with_correct_ttl(
        self,
        frozen_today,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria", days=1)

        # days=1
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "gdeltcloud:Syria:1:True"
        assert call_args[1]["ex"] == GDELT_CLOUD_CACHE_TTL
        # Payload must be JSON-deserializable back to a list of event dicts
        payload = json.loads(call_args[0][1])
        assert len(payload) == 1
        assert payload[0]["id"] == "conflict_001"

    async def test_cache_ttl_is_28800_seconds(self) -> None:
        """8-hour TTL must be enforced to protect the 100 query/month quota."""
        assert GDELT_CLOUD_CACHE_TTL == 28800

    async def test_cache_key_includes_country_and_date_range(
        self,
        frozen_today,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        # days=7 → date_start = 2026-04-25 - 6 = 2026-04-19, date_end = 2026-04-25
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Ukraine", days=7)

        mock_redis.get.assert_called_once_with("gdeltcloud:Ukraine:7:True")


# ---------------------------------------------------------------------------
# Cache hit — no API calls expected
# ---------------------------------------------------------------------------


class TestCacheHit:
    async def test_cached_events_returned_without_api_call(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        # Cache stores model_dump() output of GdeltCloudEvent
        mock_redis.get.return_value = json.dumps([PARSED_EVENT.model_dump()]).encode()

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert len(events) == 1
        assert events[0].id == "conflict_001"
        mock_http_client.get.assert_not_called()

    async def test_cached_events_deserialised_to_gdelt_cloud_event(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_redis.get.return_value = json.dumps([PARSED_EVENT.model_dump()]).encode()

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert isinstance(events[0], GdeltCloudEvent)

    async def test_cached_event_preserves_nested_geo(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_redis.get.return_value = json.dumps([PARSED_EVENT.model_dump()]).encode()

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert events[0].geo is not None
        assert events[0].geo.latitude == pytest.approx(36.2)


# ---------------------------------------------------------------------------
# Redis failure — graceful fallback
# ---------------------------------------------------------------------------


class TestRedisFailure:
    async def test_redis_read_error_falls_back_to_api(
        self,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        broken_redis = AsyncMock()
        broken_redis.get.side_effect = ConnectionError("Redis down")

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=broken_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert len(events) == 1

    async def test_redis_write_error_does_not_raise(
        self,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        broken_redis = AsyncMock()
        broken_redis.get.return_value = None
        broken_redis.set.side_effect = ConnectionError("Redis down")

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=broken_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert len(events) == 1  # result returned despite write failure


# ---------------------------------------------------------------------------
# No Redis — caching disabled path
# ---------------------------------------------------------------------------


class TestNoRedis:
    async def test_fetch_without_redis_returns_events(
        self,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=None, app_settings=mock_settings
            )
            events = await connector.fetch_events("Yemen")

        assert len(events) == 1

    async def test_no_redis_no_cache_write_attempted(
        self,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=None, app_settings=mock_settings
            )
            # Should not raise even though there is no Redis client
            events = await connector.fetch_events("Yemen")

        assert isinstance(events[0], GdeltCloudEvent)


# ---------------------------------------------------------------------------
# HTTP error propagation
# ---------------------------------------------------------------------------


class TestHttpErrors:
    async def test_http_error_propagates(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_http_client.get.return_value.raise_for_status.side_effect = (
            httpx.HTTPStatusError("429", request=MagicMock(), response=MagicMock())
        )

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            with pytest.raises(httpx.HTTPStatusError):
                await connector.fetch_events("Syria")

    async def test_401_propagates(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_http_client.get.return_value.raise_for_status.side_effect = (
            httpx.HTTPStatusError("401", request=MagicMock(), response=MagicMock())
        )

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            with pytest.raises(httpx.HTTPStatusError):
                await connector.fetch_events("Syria")


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


class TestParameters:
    async def test_default_sort_is_recent(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Syria")

        _, kwargs = mock_http_client.get.call_args
        assert kwargs["params"]["sort"] == "recent"

    async def test_default_limit_is_twenty(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Syria")

        _, kwargs = mock_http_client.get.call_args
        assert kwargs["params"]["limit"] == 20

    async def test_custom_days_reflected_in_cache_key_as_date_range(
        self,
        frozen_today,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        # days=14 → date_start = 2026-04-25 - 13 = 2026-04-12, date_end = 2026-04-25
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Sudan", days=14)

        mock_redis.get.assert_called_once_with("gdeltcloud:Sudan:14:True")

    async def test_has_fatalities_false_omits_filter_from_params(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        """has_fatalities=False must not send has_fatalities to the API — for
        countries like Iran where the filter returns 0 results."""
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Iran", days=7, has_fatalities=False)

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["country"] == "Iran"
        assert "has_fatalities" not in params

    async def test_has_fatalities_false_uses_distinct_cache_key(
        self,
        frozen_today,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        """Cache keys for has_fatalities=True and False must not collide."""
        # days=7 → date_start = 2026-04-19, date_end = 2026-04-25
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Iran", days=7, has_fatalities=False)

        mock_redis.get.assert_called_once_with("gdeltcloud:Iran:7:False")

    async def test_date_start_and_date_end_sent_in_params(
        self,
        frozen_today,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        """date_start and date_end are sent to the API; the raw 'days' integer
        is never forwarded. days=7 → 7-day inclusive window ending today."""
        # days=7 → date_start = 2026-04-25 - 6 = 2026-04-19, date_end = 2026-04-25
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Iran", days=7)

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["date_start"] == "2026-04-19"
        assert params["date_end"] == "2026-04-25"
        assert "days" not in params

    async def test_days_one_gives_single_day_window(
        self,
        frozen_today,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        """days=1 must produce date_start == date_end == today (today only)."""
        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            await connector.fetch_events("Yemen", days=1)

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["date_start"] == "2026-04-25"
        assert params["date_end"] == "2026-04-25"
