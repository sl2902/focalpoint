"""
Independent tests for the GDELT ingestion connector.

All external I/O (httpx, Redis) is mocked — no real network calls, no real
Redis instance required.
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
    "tone": -12.5,
    "domain": "reuters.com",
}

SAMPLE_API_RESPONSE: dict = {
    "articles": [SAMPLE_ARTICLE],
}

EMPTY_API_RESPONSE: dict = {
    "articles": [],
}

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
def mock_http_client(articles_response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = articles_response
    return client


# ---------------------------------------------------------------------------
# Pydantic model tests — no I/O
# ---------------------------------------------------------------------------


class TestGdeltArticle:
    def test_valid_full_article(self) -> None:
        article = GdeltArticle(**SAMPLE_ARTICLE)
        assert article.url == "https://reuters.com/world/conflict-report-2026"
        assert article.tone == -12.5
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
        assert article.tone == 0.0
        assert article.domain == ""

    def test_negative_tone_accepted(self) -> None:
        article = GdeltArticle(**{**SAMPLE_ARTICLE, "tone": -25.0})
        assert article.tone == -25.0

    def test_positive_tone_accepted(self) -> None:
        article = GdeltArticle(**{**SAMPLE_ARTICLE, "tone": 3.2})
        assert article.tone == 3.2


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
    async def test_returns_articles(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            articles = await connector.fetch_articles("conflict Gaza")

        assert len(articles) == 1
        assert articles[0].domain == "reuters.com"

    async def test_get_request_uses_correct_base_url(
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

        args, kwargs = mock_http_client.get.call_args
        assert args[0] == GDELT_BASE_URL

    async def test_required_params_sent(
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

        _, kwargs = mock_http_client.get.call_args
        params = kwargs["params"]
        assert params["query"] == "journalist Syria"
        assert params["mode"] == "artlist"
        assert params["timespan"] == "7D"
        assert params["maxrecords"] == 10
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

        _, kwargs = mock_http_client.get.call_args
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

        _, kwargs = mock_http_client.get.call_args
        assert "country" not in kwargs["params"]

    async def test_articles_written_to_redis(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        query = "conflict Gaza"
        timespan = "24H"
        expected_hash = _query_hash(query, None)
        expected_key = f"gdelt:{expected_hash}:{timespan}"
        expected_payload = json.dumps([GdeltArticle(**SAMPLE_ARTICLE).model_dump()])

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles(query, timespan=timespan)

        mock_redis.set.assert_called_once_with(
            expected_key, expected_payload, ex=GDELT_CACHE_TTL
        )

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
    async def test_cached_articles_returned_without_api_call(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_redis.get.return_value = json.dumps([SAMPLE_ARTICLE]).encode()

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            articles = await connector.fetch_articles("conflict Gaza")

        assert len(articles) == 1
        assert articles[0].tone == -12.5
        mock_http_client.get.assert_not_called()

    async def test_cache_key_is_deterministic(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        """Same query/timespan should always hit the same Redis key."""
        cached_payload = json.dumps([SAMPLE_ARTICLE]).encode()
        mock_redis.get.return_value = cached_payload

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("journalist Sudan", timespan="7D")
            await connector.fetch_articles("journalist Sudan", timespan="7D")

        # Both calls should use the same key
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
            articles = await connector.fetch_articles("conflict Yemen")

        assert len(articles) == 1

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
            articles = await connector.fetch_articles("conflict Yemen")

        assert len(articles) == 1


# ---------------------------------------------------------------------------
# No Redis — caching disabled path
# ---------------------------------------------------------------------------


class TestNoRedis:
    async def test_fetch_without_redis_returns_articles(
        self,
        mock_http_client: AsyncMock,
    ) -> None:
        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=None)
            articles = await connector.fetch_articles("journalist Myanmar")

        assert len(articles) == 1

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

        # No redis client — set should never be called
        mock_http_client.get.assert_called_once()


# ---------------------------------------------------------------------------
# Empty API response
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    async def test_empty_articles_list_returned(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get.return_value.json.return_value = EMPTY_API_RESPONSE

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            articles = await connector.fetch_articles("no results query")

        assert articles == []

    async def test_empty_response_still_cached(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get.return_value.json.return_value = EMPTY_API_RESPONSE

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            await connector.fetch_articles("no results query")

        mock_redis.set.assert_called_once()
        written_payload = mock_redis.set.call_args.args[1]
        assert json.loads(written_payload) == []


# ---------------------------------------------------------------------------
# HTTP error propagation
# ---------------------------------------------------------------------------


class TestHttpErrors:
    async def test_api_http_error_propagates(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get.return_value.raise_for_status.side_effect = (
            httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock())
        )

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            with pytest.raises(httpx.HTTPStatusError):
                await connector.fetch_articles("conflict Gaza")

    async def test_network_error_propagates(
        self,
        mock_redis: AsyncMock,
        mock_http_client: AsyncMock,
    ) -> None:
        mock_http_client.get.side_effect = httpx.ConnectError("timeout")

        with patch(
            "backend.ingestion.gdelt_connector.httpx.AsyncClient",
            return_value=mock_http_client,
        ):
            connector = GdeltConnector(redis_client=mock_redis)
            with pytest.raises(httpx.ConnectError):
                await connector.fetch_articles("conflict Gaza")
