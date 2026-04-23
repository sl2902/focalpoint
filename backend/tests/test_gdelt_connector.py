"""
Independent tests for the GDELT ingestion connector.

All external I/O (httpx, Redis) is mocked — no real network calls, no real
Redis instance required.

fetch_articles() makes two GET calls per request:
  1. mode=artlist  — returns article list
  2. mode=timelinetone — returns 15-minute tone time series

Both results are packed into GdeltResponse and cached together under one
Redis key as {"articles": [...], "aggregate_tone": <float>}.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.ingestion.gdelt_connector import (
    GDELT_BASE_URL,
    GDELT_CACHE_TTL,
    GdeltArticle,
    GdeltConnector,
    GdeltResponse,
    _MAX_RETRIES,
    _RETRY_DELAY_S,
    _parse_aggregate_tone,
    _query_hash,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_ARTICLE: dict = {
    "url": "https://reuters.com/world/conflict-report-2026",
    "title": "Clashes reported in northern Gaza",
    "seendate": "20260420T140000Z",
    "sourcecountry": "United Kingdom",
    "language": "English",
    "domain": "reuters.com",
}

SAMPLE_API_RESPONSE: dict = {
    "articles": [SAMPLE_ARTICLE],
}

EMPTY_API_RESPONSE: dict = {
    "articles": [],
}

# Timelinetone response with two non-zero values and one zero (empty window).
SAMPLE_TONE_RESPONSE: dict = {
    "query_details": {"title": "conflict Gaza", "date_resolution": "15m"},
    "timeline": [
        {
            "series": "Average Tone",
            "data": [
                {"date": "20260420T000000Z", "value": -6.0},
                {"date": "20260420T001500Z", "value": 0},    # empty window
                {"date": "20260420T003000Z", "value": -4.0},
            ],
        }
    ],
}
# Expected aggregate_tone: mean of non-zero values = (-6.0 + -4.0) / 2 = -5.0
_EXPECTED_TONE = -5.0

EMPTY_TONE_RESPONSE: dict = {"timeline": []}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Redis client defaulting to cache miss."""
    r = AsyncMock()
    r.get.return_value = None
    r.set.return_value = True
    return r


@pytest.fixture
def articles_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = SAMPLE_API_RESPONSE
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def tone_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = SAMPLE_TONE_RESPONSE
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def mock_http_client(articles_response: MagicMock, tone_response: MagicMock) -> AsyncMock:
    """
    AsyncClient mock that routes by mode param:
      mode=artlist      → articles_response
      mode=timelinetone → tone_response
    """
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False

    def _get_by_mode(url, **kwargs):
        mode = kwargs.get("params", {}).get("mode", "artlist")
        return tone_response if mode == "timelinetone" else articles_response

    client.get = AsyncMock(side_effect=_get_by_mode)
    return client


# ---------------------------------------------------------------------------
# _parse_aggregate_tone — unit tests, no I/O
# ---------------------------------------------------------------------------


class TestParseAggregateTone:
    def test_mean_of_nonzero_values(self) -> None:
        assert _parse_aggregate_tone(SAMPLE_TONE_RESPONSE) == pytest.approx(-5.0)

    def test_zero_values_excluded(self) -> None:
        data = {"timeline": [{"series": "Average Tone", "data": [
            {"date": "d1", "value": -10.0},
            {"date": "d2", "value": 0},
            {"date": "d3", "value": 0},
        ]}]}
        assert _parse_aggregate_tone(data) == pytest.approx(-10.0)

    def test_all_zeros_returns_zero(self) -> None:
        data = {"timeline": [{"series": "Average Tone", "data": [
            {"date": "d1", "value": 0},
        ]}]}
        assert _parse_aggregate_tone(data) == 0.0

    def test_empty_timeline_returns_zero(self) -> None:
        assert _parse_aggregate_tone({"timeline": []}) == 0.0

    def test_missing_timeline_key_returns_zero(self) -> None:
        assert _parse_aggregate_tone({}) == 0.0

    def test_positive_values_averaged(self) -> None:
        data = {"timeline": [{"series": "Average Tone", "data": [
            {"date": "d1", "value": 2.0},
            {"date": "d2", "value": 4.0},
        ]}]}
        assert _parse_aggregate_tone(data) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Pydantic model tests — no I/O
# ---------------------------------------------------------------------------


class TestGdeltArticle:
    def test_valid_full_article(self) -> None:
        article = GdeltArticle(**SAMPLE_ARTICLE)
        assert article.url == "https://reuters.com/world/conflict-report-2026"
        assert article.domain == "reuters.com"

    def test_optional_fields_default(self) -> None:
        minimal = {
            "url": "https://example.com/story",
            "title": "War update",
            "seendate": "20260420T000000Z",
        }
        article = GdeltArticle(**minimal)
        assert article.sourcecountry == ""
        assert article.language == ""
        assert article.domain == ""

    def test_no_tone_field(self) -> None:
        article = GdeltArticle(**SAMPLE_ARTICLE)
        assert not hasattr(article, "tone")


class TestGdeltResponse:
    def test_wraps_article_list(self) -> None:
        resp = GdeltResponse(**SAMPLE_API_RESPONSE)
        assert len(resp.articles) == 1
        assert isinstance(resp.articles[0], GdeltArticle)

    def test_empty_articles_list(self) -> None:
        resp = GdeltResponse(**EMPTY_API_RESPONSE)
        assert resp.articles == []

    def test_missing_articles_key_defaults_to_empty(self) -> None:
        resp = GdeltResponse()
        assert resp.articles == []

    def test_aggregate_tone_defaults_to_zero(self) -> None:
        resp = GdeltResponse()
        assert resp.aggregate_tone == 0.0

    def test_aggregate_tone_set_explicitly(self) -> None:
        resp = GdeltResponse(aggregate_tone=-7.5)
        assert resp.aggregate_tone == -7.5


# ---------------------------------------------------------------------------
# Query hash
# ---------------------------------------------------------------------------


class TestQueryHash:
    def test_same_inputs_produce_same_hash(self) -> None:
        assert _query_hash("conflict Gaza", "PS") == _query_hash("conflict Gaza", "PS")

    def test_different_query_produces_different_hash(self) -> None:
        assert _query_hash("conflict Gaza", None) != _query_hash("journalist Sudan", None)

    def test_different_country_produces_different_hash(self) -> None:
        assert _query_hash("war", "PS") != _query_hash("war", "UA")

    def test_none_country_differs_from_explicit_country(self) -> None:
        assert _query_hash("war", None) != _query_hash("war", "PS")

    def test_hash_length_is_twelve(self) -> None:
        assert len(_query_hash("anything", None)) == 12


# ---------------------------------------------------------------------------
# Cache miss — full API flow
# ---------------------------------------------------------------------------


class TestCacheMiss:
    async def test_returns_gdelt_response(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            result = await connector.fetch_articles("conflict Gaza")

        assert isinstance(result, GdeltResponse)
        assert len(result.articles) == 1
        assert result.articles[0].domain == "reuters.com"

    async def test_aggregate_tone_populated(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            result = await connector.fetch_articles("conflict Gaza")

        assert result.aggregate_tone == pytest.approx(_EXPECTED_TONE)

    async def test_two_get_requests_made(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("conflict Gaza")

        assert mock_http_client.get.call_count == 2

    async def test_artlist_request_uses_correct_base_url(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("conflict Gaza")

        # First call is artlist
        args, _ = mock_http_client.get.call_args_list[0]
        assert args[0] == GDELT_BASE_URL

    async def test_artlist_params_sent(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("journalist Syria", timespan="7D", maxrecords=10)

        _, kwargs = mock_http_client.get.call_args_list[0]
        params = kwargs["params"]
        assert params["query"] == "journalist Syria"
        assert params["mode"] == "artlist"
        assert params["timespan"] == "7D"
        assert params["maxrecords"] == 10
        assert params["format"] == "json"

    async def test_timelinetone_params_sent(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("journalist Syria", timespan="7D")

        _, kwargs = mock_http_client.get.call_args_list[1]
        params = kwargs["params"]
        assert params["query"] == "journalist Syria"
        assert params["mode"] == "timelinetone"
        assert params["timespan"] == "7D"
        assert params["format"] == "json"

    async def test_country_param_included_when_provided(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("war", country="UA")

        _, kwargs = mock_http_client.get.call_args_list[0]
        assert kwargs["params"]["country"] == "UA"

    async def test_country_param_absent_when_not_provided(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("war")

        _, kwargs = mock_http_client.get.call_args_list[0]
        assert "country" not in kwargs["params"]

    async def test_result_written_to_redis(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        query = "conflict Gaza"
        timespan = "24H"
        expected_key = f"gdelt:{_query_hash(query, None)}:{timespan}"

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles(query, timespan=timespan)

        mock_redis.set.assert_called_once()
        written_key, written_payload = mock_redis.set.call_args.args[:2]
        assert written_key == expected_key
        loaded = json.loads(written_payload)
        assert loaded["articles"] == [GdeltArticle(**SAMPLE_ARTICLE).model_dump()]
        assert loaded["aggregate_tone"] == pytest.approx(_EXPECTED_TONE)

    async def test_cache_key_includes_country_in_hash(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        query = "war"
        country = "PS"
        timespan = "24H"
        expected_key = f"gdelt:{_query_hash(query, country)}:{timespan}"

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles(query, country=country)

        written_key = mock_redis.set.call_args.args[0]
        assert written_key == expected_key


# ---------------------------------------------------------------------------
# Cache hit — no API calls expected
# ---------------------------------------------------------------------------


class TestCacheHit:
    async def test_cached_result_returned_without_api_call(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        cached_payload = json.dumps({
            "articles": [SAMPLE_ARTICLE],
            "aggregate_tone": -8.0,
        }).encode()
        mock_redis.get.return_value = cached_payload

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            result = await connector.fetch_articles("conflict Gaza")

        assert len(result.articles) == 1
        assert result.aggregate_tone == -8.0
        mock_http_client.get.assert_not_called()

    async def test_cache_key_is_deterministic(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        cached_payload = json.dumps({
            "articles": [SAMPLE_ARTICLE],
            "aggregate_tone": -5.0,
        }).encode()
        mock_redis.get.return_value = cached_payload

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("journalist Sudan", timespan="7D")
            await connector.fetch_articles("journalist Sudan", timespan="7D")

        keys_checked = [call.args[0] for call in mock_redis.get.call_args_list]
        assert keys_checked[0] == keys_checked[1]


# ---------------------------------------------------------------------------
# Redis failure — graceful fallback
# ---------------------------------------------------------------------------


class TestRedisFailure:
    async def test_redis_read_error_falls_back_to_api(
        self,
        mock_http_client: AsyncMock,
    ) -> None:
        broken_redis = AsyncMock()
        broken_redis.get.side_effect = ConnectionError("Redis down")

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=broken_redis)
            result = await connector.fetch_articles("conflict Yemen")

        assert len(result.articles) == 1

    async def test_redis_write_error_does_not_raise(
        self,
        mock_http_client: AsyncMock,
    ) -> None:
        broken_redis = AsyncMock()
        broken_redis.get.return_value = None
        broken_redis.set.side_effect = ConnectionError("Redis down")

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=broken_redis)
            result = await connector.fetch_articles("conflict Yemen")

        assert len(result.articles) == 1


# ---------------------------------------------------------------------------
# No Redis — caching disabled path
# ---------------------------------------------------------------------------


class TestNoRedis:
    async def test_fetch_without_redis_returns_response(
        self,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=None)
            result = await connector.fetch_articles("journalist Myanmar")

        assert len(result.articles) == 1

    async def test_no_redis_means_no_cache_write(
        self,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=None)
            await connector.fetch_articles("journalist Myanmar")

        # Two API calls (artlist + timelinetone) but no Redis writes.
        assert mock_http_client.get.call_count == 2


# ---------------------------------------------------------------------------
# Empty API response
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    async def test_empty_articles_list_returned(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get = AsyncMock(side_effect=lambda url, **kw: (
            MagicMock(
                json=lambda: EMPTY_API_RESPONSE,
                raise_for_status=MagicMock(),
            )
            if kw.get("params", {}).get("mode") == "artlist"
            else MagicMock(
                json=lambda: EMPTY_TONE_RESPONSE,
                raise_for_status=MagicMock(),
            )
        ))

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            result = await connector.fetch_articles("no results query")

        assert result.articles == []

    async def test_empty_response_still_cached(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get = AsyncMock(side_effect=lambda url, **kw: (
            MagicMock(
                json=lambda: EMPTY_API_RESPONSE,
                raise_for_status=MagicMock(),
            )
            if kw.get("params", {}).get("mode") == "artlist"
            else MagicMock(
                json=lambda: EMPTY_TONE_RESPONSE,
                raise_for_status=MagicMock(),
            )
        ))

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("no results query")

        mock_redis.set.assert_called_once()
        written_payload = mock_redis.set.call_args.args[1]
        loaded = json.loads(written_payload)
        assert loaded["articles"] == []
        assert isinstance(loaded["aggregate_tone"], float)


# ---------------------------------------------------------------------------
# HTTP errors — exhausted retries return empty GdeltResponse
# ---------------------------------------------------------------------------


class TestHttpErrors:
    async def test_api_http_error_returns_empty_response(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get.side_effect = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=MagicMock()
        )

        with patch("backend.ingestion.gdelt_connector.httpx.AsyncClient", return_value=mock_http_client):
            with patch("backend.ingestion.gdelt_connector.asyncio.sleep"):
                connector = GdeltConnector(redis_client=mock_redis)
                result = await connector.fetch_articles("conflict Gaza")

        assert result.articles == []
        assert result.aggregate_tone == 0.0

    async def test_network_error_returns_empty_response(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get.side_effect = httpx.ConnectError("unreachable")

        with patch("backend.ingestion.gdelt_connector.httpx.AsyncClient", return_value=mock_http_client):
            with patch("backend.ingestion.gdelt_connector.asyncio.sleep"):
                connector = GdeltConnector(redis_client=mock_redis)
                result = await connector.fetch_articles("conflict Gaza")

        assert result.articles == []
        assert result.aggregate_tone == 0.0


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetry:
    async def test_all_retries_exhausted_returns_empty_gdelt_response(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get.side_effect = httpx.ConnectError("down")

        with patch("backend.ingestion.gdelt_connector.httpx.AsyncClient", return_value=mock_http_client):
            with patch("backend.ingestion.gdelt_connector.asyncio.sleep"):
                connector = GdeltConnector(redis_client=mock_redis)
                result = await connector.fetch_articles("conflict Gaza")

        assert isinstance(result, GdeltResponse)
        assert result.articles == []
        assert result.aggregate_tone == 0.0

    async def test_retry_attempts_total_is_max_retries_plus_one(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        """Initial attempt + _MAX_RETRIES retries = _MAX_RETRIES + 1 total GET calls."""
        mock_http_client.get.side_effect = httpx.ConnectError("down")

        with patch("backend.ingestion.gdelt_connector.httpx.AsyncClient", return_value=mock_http_client):
            with patch("backend.ingestion.gdelt_connector.asyncio.sleep"):
                connector = GdeltConnector(redis_client=mock_redis)
                await connector.fetch_articles("conflict Gaza")

        assert mock_http_client.get.call_count == _MAX_RETRIES + 1

    async def test_sleep_called_between_retries(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        """asyncio.sleep must be called _MAX_RETRIES times (not after the final failure)."""
        mock_http_client.get.side_effect = httpx.ConnectError("down")

        with patch("backend.ingestion.gdelt_connector.httpx.AsyncClient", return_value=mock_http_client):
            with patch("backend.ingestion.gdelt_connector.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                connector = GdeltConnector(redis_client=mock_redis)
                await connector.fetch_articles("conflict Gaza")

        assert mock_sleep.call_count == _MAX_RETRIES
        mock_sleep.assert_called_with(_RETRY_DELAY_S)

    async def test_sleep_delay_is_two_seconds(self) -> None:
        assert _RETRY_DELAY_S == 2

    async def test_max_retries_is_three(self) -> None:
        assert _MAX_RETRIES == 3

    async def test_succeeds_on_retry_after_initial_failure(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
        articles_response: MagicMock,
        tone_response: MagicMock,
    ) -> None:
        """First GET fails, second attempt succeeds — result is the real response."""
        call_count = 0

        def _get_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("transient failure")
            mode = kwargs.get("params", {}).get("mode", "artlist")
            return tone_response if mode == "timelinetone" else articles_response

        mock_http_client.get = AsyncMock(side_effect=_get_side_effect)

        with patch("backend.ingestion.gdelt_connector.httpx.AsyncClient", return_value=mock_http_client):
            with patch("backend.ingestion.gdelt_connector.asyncio.sleep"):
                connector = GdeltConnector(redis_client=mock_redis)
                result = await connector.fetch_articles("conflict Gaza")

        assert len(result.articles) == 1
        assert result.articles[0].domain == "reuters.com"

    async def test_no_redis_write_on_retry_exhaustion(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        """When all retries fail, nothing should be written to Redis."""
        mock_http_client.get.side_effect = httpx.ConnectError("down")

        with patch("backend.ingestion.gdelt_connector.httpx.AsyncClient", return_value=mock_http_client):
            with patch("backend.ingestion.gdelt_connector.asyncio.sleep"):
                connector = GdeltConnector(redis_client=mock_redis)
                await connector.fetch_articles("conflict Gaza")

        mock_redis.set.assert_not_called()
