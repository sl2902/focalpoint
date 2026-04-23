"""
Independent tests for the GDELT Cloud conflict events connector.

All external I/O (httpx, Redis) is mocked — no real network calls, no real
Redis instance required. Tests are grouped by scenario.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.ingestion.gdeltcloud_connector import (
    GDELT_CLOUD_CACHE_TTL,
    GdeltCloudConnector,
    GdeltCloudEvent,
    GdeltCloudResponse,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_EVENT: dict = {
    "id": "GDELT20260423001",
    "event_date": "2026-04-23",
    "disorder_type": "Political Violence",
    "event_type": "Armed Clash",
    "sub_event_type": "Armed clash",
    "actor1": "Syrian Armed Forces",
    "actor2": "Opposition Forces",
    "fatalities": 3,
    "latitude": 36.2,
    "longitude": 37.1,
    "country": "Syria",
    "admin1": "Aleppo",
    "location": "Northern Aleppo",
    "notes": "Clashes reported near checkpoint.",
    "confidence": 85,
}

SAMPLE_API_RESPONSE: dict = {
    "events": [SAMPLE_EVENT],
    "count": 1,
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        event = GdeltCloudEvent(**SAMPLE_EVENT)
        assert event.id == "GDELT20260423001"
        assert event.fatalities == 3
        assert event.latitude == 36.2
        assert event.confidence == 85

    def test_only_required_fields(self) -> None:
        event = GdeltCloudEvent(id="MIN001", event_date="2026-04-23")
        assert event.id == "MIN001"
        assert event.event_date == "2026-04-23"
        assert event.fatalities is None
        assert event.event_type is None
        assert event.actor1 is None
        assert event.country is None

    def test_all_optional_fields_default_to_none(self) -> None:
        event = GdeltCloudEvent(id="X", event_date="2026-01-01")
        for field in (
            "disorder_type", "event_type", "sub_event_type", "actor1", "actor2",
            "fatalities", "latitude", "longitude", "country", "admin1",
            "location", "notes", "confidence",
        ):
            assert getattr(event, field) is None, f"{field} should default to None"

    def test_zero_fatalities_accepted(self) -> None:
        event = GdeltCloudEvent(id="X", event_date="2026-04-23", fatalities=0)
        assert event.fatalities == 0

    def test_negative_goldstein_scale_accepted(self) -> None:
        event = GdeltCloudEvent(id="X", event_date="2026-04-23", latitude=-33.9)
        assert event.latitude == pytest.approx(-33.9)


class TestGdeltCloudResponse:
    def test_wraps_event_list(self) -> None:
        resp = GdeltCloudResponse(**SAMPLE_API_RESPONSE)
        assert resp.count == 1
        assert isinstance(resp.events[0], GdeltCloudEvent)
        assert resp.events[0].id == "GDELT20260423001"

    def test_empty_events_list(self) -> None:
        resp = GdeltCloudResponse(events=[], count=0)
        assert resp.events == []

    def test_defaults_to_empty_list(self) -> None:
        resp = GdeltCloudResponse()
        assert resp.events == []
        assert resp.count == 0


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
        assert events[0].id == "GDELT20260423001"

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

    async def test_country_and_days_in_params(
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
            await connector.fetch_events("Syria", days=3, limit=10)

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["country"] == "Syria"
        assert params["days"] == 3
        assert params["limit"] == 10

    async def test_events_written_to_redis_with_correct_ttl(
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
            await connector.fetch_events("Syria", days=1)

        expected_payload = json.dumps(
            [GdeltCloudEvent(**SAMPLE_EVENT).model_dump()]
        )
        mock_redis.set.assert_called_once_with(
            "gdeltcloud:Syria:1", expected_payload, ex=GDELT_CLOUD_CACHE_TTL
        )

    async def test_cache_ttl_is_28800_seconds(self) -> None:
        """8-hour TTL must be enforced to protect the 100 query/month quota."""
        assert GDELT_CLOUD_CACHE_TTL == 28800

    async def test_cache_key_includes_country_and_days(
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
            await connector.fetch_events("Ukraine", days=7)

        # Redis get must have been called with the correct key
        mock_redis.get.assert_called_once_with("gdeltcloud:Ukraine:7")


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
        mock_redis.get.return_value = json.dumps([SAMPLE_EVENT]).encode()

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert len(events) == 1
        assert events[0].id == "GDELT20260423001"
        mock_http_client.get.assert_not_called()

    async def test_cached_events_deserialised_to_gdelt_cloud_event(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_redis.get.return_value = json.dumps([SAMPLE_EVENT]).encode()

        with patch(
            "backend.ingestion.gdeltcloud_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltCloudConnector(
                redis_client=mock_redis, app_settings=mock_settings
            )
            events = await connector.fetch_events("Syria")

        assert isinstance(events[0], GdeltCloudEvent)


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
    async def test_default_days_is_one(
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
        assert kwargs["params"]["days"] == 1

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

    async def test_custom_days_reflected_in_cache_key(
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
            await connector.fetch_events("Sudan", days=14)

        mock_redis.get.assert_called_once_with("gdeltcloud:Sudan:14")
