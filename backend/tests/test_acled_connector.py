"""
Independent tests for the ACLED ingestion connector.

All external I/O (httpx, Redis) is mocked — no real network calls, no real
Redis instance required.  Tests are grouped by scenario.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.ingestion.acled_connector import (
    ACLED_CACHE_TTL,
    ACLED_TOKEN_REDIS_KEY,
    AcledConnector,
    AcledEvent,
    AcledResponse,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_EVENT: dict = {
    "event_id_cnty": "PSE1234",
    "event_date": "2026-04-20",
    "event_type": "Explosions/Remote violence",
    "actor1": "Israeli Forces",
    "actor2": "Hamas",
    "country": "Palestine",
    "location": "Gaza",
    "latitude": 31.5,
    "longitude": 34.47,
    "fatalities": 3,
    "notes": "Airstrike reported in northern Gaza.",
}

SAMPLE_API_RESPONSE: dict = {
    "status": 200,
    "success": True,
    "count": 1,
    "data": [SAMPLE_EVENT],
}

TOKEN_API_RESPONSE: dict = {
    "access_token": "test-bearer-token",
    "expires_in": 3600,
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> MagicMock:
    s = MagicMock()
    s.ACLED_USERNAME = "testuser"
    s.ACLED_PASSWORD = "testpass"
    s.ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"
    return s


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Redis client with default cache-miss behaviour."""
    r = AsyncMock()
    r.get.return_value = None
    r.set.return_value = True
    return r


@pytest.fixture
def token_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = TOKEN_API_RESPONSE
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def events_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = SAMPLE_API_RESPONSE
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def mock_http_client(token_response: MagicMock, events_response: MagicMock) -> AsyncMock:
    """httpx.AsyncClient mock that handles both token POST and events GET."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post.return_value = token_response
    client.get.return_value = events_response
    return client


# ---------------------------------------------------------------------------
# Pydantic model tests — no I/O
# ---------------------------------------------------------------------------


class TestAcledEvent:
    def test_valid_full_event(self) -> None:
        event = AcledEvent(**SAMPLE_EVENT)
        assert event.event_id_cnty == "PSE1234"
        assert event.fatalities == 3
        assert event.latitude == 31.5

    def test_optional_fields_default(self) -> None:
        minimal = {
            "event_id_cnty": "SYR001",
            "event_date": "2026-04-01",
            "event_type": "Battles",
            "actor1": "SDF",
            "country": "Syria",
            "location": "Aleppo",
            "latitude": 36.2,
            "longitude": 37.16,
        }
        event = AcledEvent(**minimal)
        assert event.actor2 == ""
        assert event.fatalities == 0
        assert event.notes == ""


class TestAcledResponse:
    def test_wraps_event_list(self) -> None:
        resp = AcledResponse(**SAMPLE_API_RESPONSE)
        assert resp.count == 1
        assert isinstance(resp.data[0], AcledEvent)
        assert resp.data[0].event_id_cnty == "PSE1234"

    def test_empty_data_list(self) -> None:
        resp = AcledResponse(status=200, success=True, count=0, data=[])
        assert resp.data == []


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
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            events = await connector.fetch_events("Palestine")

        assert len(events) == 1
        assert events[0].event_id_cnty == "PSE1234"

    async def test_posts_credentials_to_token_url(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events("Palestine")

        mock_http_client.post.assert_called_once_with(
            mock_settings.ACLED_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "username": "testuser",
                "password": "testpass",
                "grant_type": "password",
                "client_id": "acled",
            },
        )

    async def test_get_uses_bearer_token(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events("Palestine")

        _, kwargs = mock_http_client.get.call_args
        assert kwargs["headers"] == {"Authorization": "Bearer test-bearer-token"}

    async def test_events_written_to_redis(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events("Palestine", page=1)

        expected_payload = json.dumps([AcledEvent(**SAMPLE_EVENT).model_dump()])
        mock_redis.set.assert_any_call(
            "acled:Palestine:1", expected_payload, ex=ACLED_CACHE_TTL
        )

    async def test_token_written_to_redis_with_buffered_ttl(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        # TOKEN_API_RESPONSE expires_in=3600, buffer=60 → expected TTL=3540
        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events("Palestine")

        mock_redis.set.assert_any_call(ACLED_TOKEN_REDIS_KEY, "test-bearer-token", ex=3540)


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
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            events = await connector.fetch_events("Ukraine")

        assert len(events) == 1
        assert events[0].event_id_cnty == "PSE1234"
        mock_http_client.get.assert_not_called()
        mock_http_client.post.assert_not_called()

    async def test_cached_token_skips_token_post(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        # First call (events cache key) → miss; second call (token key) → hit.
        mock_redis.get.side_effect = [None, b"cached-token-xyz"]

        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events("Ukraine")

        mock_http_client.post.assert_not_called()
        _, kwargs = mock_http_client.get.call_args
        assert kwargs["headers"] == {"Authorization": "Bearer cached-token-xyz"}


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
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(
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
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(
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
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=None, app_settings=mock_settings)
            events = await connector.fetch_events("Yemen")

        assert len(events) == 1


# ---------------------------------------------------------------------------
# Date filter parameters
# ---------------------------------------------------------------------------


class TestDateFilters:
    async def test_date_range_added_to_params(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events(
                "Yemen", date_from="2026-04-01", date_to="2026-04-23"
            )

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["event_date"] == "2026-04-01|2026-04-23"
        assert params["event_date_where"] == "BETWEEN"

    async def test_no_date_params_when_omitted(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events("Yemen")

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert "event_date" not in params
        assert "event_date_where" not in params

    async def test_custom_page_and_limit_in_params(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            await connector.fetch_events("Sudan", page=3, limit=10)

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["page"] == 3
        assert params["limit"] == 10
        assert mock_redis.get.call_args_list[0].args[0] == "acled:Sudan:3"


# ---------------------------------------------------------------------------
# HTTP error propagation
# ---------------------------------------------------------------------------


class TestHttpErrors:
    async def test_events_http_error_propagates(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_http_client.get.return_value.raise_for_status.side_effect = (
            httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        )

        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            with pytest.raises(httpx.HTTPStatusError):
                await connector.fetch_events("Palestine")

    async def test_token_http_error_propagates(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        mock_settings: MagicMock,
    ) -> None:
        mock_http_client.post.return_value.raise_for_status.side_effect = (
            httpx.HTTPStatusError("401", request=MagicMock(), response=MagicMock())
        )

        with patch(
            "backend.ingestion.acled_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = AcledConnector(redis_client=mock_redis, app_settings=mock_settings)
            with pytest.raises(httpx.HTTPStatusError):
                await connector.fetch_events("Palestine")
