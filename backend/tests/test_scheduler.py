"""Tests for backend/scheduler/ — store.py persistence and jobs.py logic.

All store tests use tmp_path (real temp file) so aiosqlite connections
across calls share the same data. In-memory :memory: does not persist
across separate aiosqlite.connect() calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.scheduler import store
from backend.security.output_validator import Citation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CITATION = Citation(
    id="conflict_test_001",
    description="Armed Clash — Test, 2026-04-23 (1 fatalities)",
)


def _ts() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def _insert(
    db_path: str,
    *,
    region: str = "Gaza",
    days: int = 1,
    severity: str = "RED",
    summary: str = "Test summary for conflict region.",
    confidence: float = 0.75,
    score: float = 55.0,
    timestamp: str | None = None,
    created_at_override: str | None = None,
) -> None:
    """Insert one row, optionally overriding created_at for staleness tests."""
    if timestamp is None:
        timestamp = _ts()
    await store.upsert_alert(
        db_path,
        region=region,
        days=days,
        severity=severity,
        summary=summary,
        source_citations=[_CITATION],
        confidence=confidence,
        score=score,
        timestamp=timestamp,
    )
    if created_at_override is not None:
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE alerts SET created_at = ? WHERE region = ? AND days = ?",
                (created_at_override, region, days),
            )
            await db.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test_alerts.db")
    await store.init_db(path)
    return path


# ---------------------------------------------------------------------------
# TestInitDb
# ---------------------------------------------------------------------------


class TestInitDb:
    async def test_alerts_table_created(self, tmp_path) -> None:
        import aiosqlite

        path = str(tmp_path / "init_test.db")
        await store.init_db(path)
        async with aiosqlite.connect(path) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "alerts"

    async def test_init_is_idempotent(self, tmp_path) -> None:
        """Calling init_db twice must not raise."""
        path = str(tmp_path / "idempotent.db")
        await store.init_db(path)
        await store.init_db(path)


# ---------------------------------------------------------------------------
# TestUpsertAlert
# ---------------------------------------------------------------------------


class TestUpsertAlert:
    async def test_inserts_row(self, db_path: str) -> None:
        import aiosqlite

        await _insert(db_path, region="Gaza")
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT count(*) FROM alerts") as cursor:
                row = await cursor.fetchone()
        assert row[0] == 1

    async def test_replaces_existing_row_for_same_region(self, db_path: str) -> None:
        import aiosqlite

        await _insert(db_path, region="Gaza", severity="AMBER")
        await _insert(db_path, region="Gaza", severity="RED")
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT count(*), severity FROM alerts WHERE region = 'Gaza'"
            ) as cursor:
                row = await cursor.fetchone()
        assert row[0] == 1
        assert row[1] == "RED"

    async def test_source_citations_serialised_as_json(self, db_path: str) -> None:
        import aiosqlite

        await _insert(db_path, region="Gaza")
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT source_citations FROM alerts WHERE region = 'Gaza'"
            ) as cursor:
                row = await cursor.fetchone()
        parsed = json.loads(row[0])
        assert isinstance(parsed, list)
        assert parsed[0]["id"] == "conflict_test_001"


# ---------------------------------------------------------------------------
# TestGetCachedAlert
# ---------------------------------------------------------------------------


class TestGetCachedAlert:
    async def test_returns_alert_response_for_fresh_row(self, db_path: str) -> None:
        from backend.api.schemas import AlertResponse

        await _insert(db_path, region="Ukraine")
        result = await store.get_cached_alert(db_path, "Ukraine")
        assert result is not None
        assert isinstance(result, AlertResponse)
        assert result.region == "Ukraine"

    async def test_returns_none_for_stale_row(self, db_path: str) -> None:
        stale_ts = (
            datetime.now(tz=timezone.utc) - timedelta(hours=9)
        ).isoformat()
        await _insert(db_path, region="Sudan", created_at_override=stale_ts)
        result = await store.get_cached_alert(db_path, "Sudan")
        assert result is None

    async def test_returns_none_for_unknown_region(self, db_path: str) -> None:
        result = await store.get_cached_alert(db_path, "Atlantis")
        assert result is None

    async def test_citations_deserialised_correctly(self, db_path: str) -> None:
        await _insert(db_path, region="Yemen")
        result = await store.get_cached_alert(db_path, "Yemen")
        assert result is not None
        assert result.source_citations[0].id == "conflict_test_001"
        assert "Armed Clash" in result.source_citations[0].description

    async def test_different_days_cached_independently(self, db_path: str) -> None:
        """days=1 and days=7 rows must not collide — composite (region, days) PK."""
        await _insert(db_path, region="Iran", days=1, severity="GREEN")
        await _insert(db_path, region="Iran", days=7, severity="AMBER")
        result_1 = await store.get_cached_alert(db_path, "Iran", days=1)
        result_7 = await store.get_cached_alert(db_path, "Iran", days=7)
        assert result_1 is not None
        assert result_7 is not None
        assert result_1.severity == "GREEN"
        assert result_7.severity == "AMBER"


# ---------------------------------------------------------------------------
# TestGetLatestPerRegion
# ---------------------------------------------------------------------------


class TestGetLatestPerRegion:
    async def test_returns_empty_list_when_no_rows(self, db_path: str) -> None:
        result = await store.get_latest_per_region(db_path)
        assert result == []

    async def test_returns_one_per_region(self, db_path: str) -> None:
        await _insert(db_path, region="Gaza")
        await _insert(db_path, region="Ukraine")
        result = await store.get_latest_per_region(db_path)
        regions = [r.region for r in result]
        assert len(regions) == 2
        assert "Gaza" in regions
        assert "Ukraine" in regions

    async def test_severity_ordering(self, db_path: str) -> None:
        await _insert(db_path, region="Gaza", severity="AMBER")
        await _insert(db_path, region="Ukraine", severity="CRITICAL")
        await _insert(db_path, region="Sudan", severity="RED")
        result = await store.get_latest_per_region(db_path)
        severities = [r.severity for r in result]
        assert severities == ["CRITICAL", "RED", "AMBER"]


# ---------------------------------------------------------------------------
# TestRefreshAllWatchZones
# ---------------------------------------------------------------------------


class TestRefreshAllWatchZones:
    """Tests for jobs.refresh_all_watch_zones.

    The job touches external services (GDELT, Gemma) so all dependencies are
    mocked. store.get_cached_alert and store.upsert_alert are patched so no
    real SQLite file is required for these tests.
    """

    def _make_app(self, db_path: str = "/fake/alerts.db"):
        """Return a minimal mock app with the attributes jobs.py reads."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        return SimpleNamespace(
            state=SimpleNamespace(
                db_path=db_path,
                redis=None,
                cpj=MagicMock(),
                alert_generator=MagicMock(),
            )
        )

    def _make_alert_output(self, region: str = "Gaza", severity: str = "RED"):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        from backend.security.output_validator import Citation

        alert = MagicMock()
        alert.severity = severity
        alert.summary = f"Test summary for {region}."
        alert.source_citations = [Citation(id="evt_001", description="Test event")]
        alert.timestamp = datetime.now(tz=timezone.utc)
        return alert

    def _make_severity_result(self, score: float = 55.0, confidence: float = 0.75):
        from unittest.mock import MagicMock

        sr = MagicMock()
        sr.score = score
        sr.confidence = confidence
        return sr

    async def test_all_stale_zones_are_refreshed(self, tmp_path) -> None:
        """Every zone triggers a live refresh when none are cached."""
        from unittest.mock import AsyncMock, MagicMock, patch

        zones = ["Palestine", "Gaza", "Ukraine"]
        app = self._make_app()

        gdelt_resp = MagicMock()
        gdelt_resp.articles = []
        gdelt_resp.aggregate_tone = 0.0

        app.state.alert_generator.generate.return_value = self._make_alert_output()
        refreshed: list[str] = []

        async def fake_cached(db_path, region, days=1):
            return None

        async def fake_upsert(db_path, *, region, **kwargs):
            refreshed.append(region)

        with (
            patch("backend.scheduler.jobs.settings") as mock_settings,
            patch("backend.scheduler.jobs.store.get_cached_alert", side_effect=fake_cached),
            patch("backend.scheduler.jobs.store.upsert_alert", side_effect=fake_upsert),
            patch("backend.scheduler.jobs.GdeltCloudConnector") as MockCloud,
            patch("backend.scheduler.jobs.GdeltConnector") as MockGdelt,
            patch("backend.scheduler.jobs.score_severity", return_value=self._make_severity_result()),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_settings.WATCH_ZONES = zones
            mock_settings.GDELT_CLOUD_ALIASES = {}
            mock_settings.NO_FATALITIES_FILTER_COUNTRIES = set()

            cloud_inst = AsyncMock()
            cloud_inst.fetch_events = AsyncMock(return_value=[])
            MockCloud.return_value = cloud_inst

            gdelt_inst = AsyncMock()
            gdelt_inst.fetch_articles = AsyncMock(return_value=gdelt_resp)
            MockGdelt.return_value = gdelt_inst

            from backend.scheduler.jobs import refresh_all_watch_zones
            await refresh_all_watch_zones(app)

        assert set(refreshed) == {"Palestine", "Gaza", "Ukraine"}

    async def test_fresh_zones_are_skipped(self, tmp_path) -> None:
        """Zones with a fresh cached alert must not trigger GDELT/Gemma calls."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.api.schemas import AlertResponse
        from backend.security.output_validator import Citation

        zones = ["Gaza", "Ukraine"]
        app = self._make_app()

        fresh_alert = MagicMock(spec=AlertResponse)

        async def fake_cached(db_path, region, days=1):
            return fresh_alert  # all zones are fresh

        with (
            patch("backend.scheduler.jobs.settings") as mock_settings,
            patch("backend.scheduler.jobs.store.get_cached_alert", side_effect=fake_cached),
            patch("backend.scheduler.jobs.GdeltCloudConnector") as MockCloud,
            patch("backend.scheduler.jobs.GdeltConnector") as MockGdelt,
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_settings.WATCH_ZONES = zones
            mock_settings.GDELT_CLOUD_ALIASES = {}
            mock_settings.NO_FATALITIES_FILTER_COUNTRIES = set()

            from backend.scheduler.jobs import refresh_all_watch_zones
            await refresh_all_watch_zones(app)

        MockCloud.assert_not_called()
        MockGdelt.assert_not_called()
        mock_sleep.assert_not_called()

    async def test_sleep_between_refreshed_zones_not_first(self) -> None:
        """asyncio.sleep(5) is called between live refreshes but NOT before the first."""
        from unittest.mock import AsyncMock, MagicMock, patch

        zones = ["Palestine", "Gaza", "Ukraine"]
        app = self._make_app()

        gdelt_resp = MagicMock()
        gdelt_resp.articles = []
        gdelt_resp.aggregate_tone = 0.0

        app.state.alert_generator.generate.return_value = self._make_alert_output()

        async def fake_cached(db_path, region, days=1):
            return None

        with (
            patch("backend.scheduler.jobs.settings") as mock_settings,
            patch("backend.scheduler.jobs.store.get_cached_alert", side_effect=fake_cached),
            patch("backend.scheduler.jobs.store.upsert_alert", AsyncMock()),
            patch("backend.scheduler.jobs.GdeltCloudConnector") as MockCloud,
            patch("backend.scheduler.jobs.GdeltConnector") as MockGdelt,
            patch("backend.scheduler.jobs.score_severity", return_value=self._make_severity_result()),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_settings.WATCH_ZONES = zones
            mock_settings.GDELT_CLOUD_ALIASES = {}
            mock_settings.NO_FATALITIES_FILTER_COUNTRIES = set()

            cloud_inst = AsyncMock()
            cloud_inst.fetch_events = AsyncMock(return_value=[])
            MockCloud.return_value = cloud_inst

            gdelt_inst = AsyncMock()
            gdelt_inst.fetch_articles = AsyncMock(return_value=gdelt_resp)
            MockGdelt.return_value = gdelt_inst

            from backend.scheduler.jobs import refresh_all_watch_zones
            await refresh_all_watch_zones(app)

        # 3 zones refreshed → sleep called exactly twice (between 1→2 and 2→3)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(10)

    async def test_error_in_one_zone_does_not_stop_others(self) -> None:
        """An exception during one zone's refresh must not prevent subsequent zones."""
        from unittest.mock import AsyncMock, MagicMock, patch

        zones = ["Gaza", "Ukraine", "Sudan"]
        app = self._make_app()

        gdelt_resp = MagicMock()
        gdelt_resp.articles = []
        gdelt_resp.aggregate_tone = 0.0

        app.state.alert_generator.generate.return_value = self._make_alert_output()
        refreshed: list[str] = []

        async def fake_cached(db_path, region, days=1):
            return None

        async def fake_upsert(db_path, *, region, **kwargs):
            refreshed.append(region)

        call_count = 0

        async def fetch_events_flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("GDELT Cloud timeout")
            return []

        with (
            patch("backend.scheduler.jobs.settings") as mock_settings,
            patch("backend.scheduler.jobs.store.get_cached_alert", side_effect=fake_cached),
            patch("backend.scheduler.jobs.store.upsert_alert", side_effect=fake_upsert),
            patch("backend.scheduler.jobs.GdeltCloudConnector") as MockCloud,
            patch("backend.scheduler.jobs.GdeltConnector") as MockGdelt,
            patch("backend.scheduler.jobs.score_severity", return_value=self._make_severity_result()),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_settings.WATCH_ZONES = zones
            mock_settings.GDELT_CLOUD_ALIASES = {}
            mock_settings.NO_FATALITIES_FILTER_COUNTRIES = set()

            cloud_inst = AsyncMock()
            cloud_inst.fetch_events = AsyncMock(side_effect=fetch_events_flaky)
            MockCloud.return_value = cloud_inst

            gdelt_inst = AsyncMock()
            gdelt_inst.fetch_articles = AsyncMock(return_value=gdelt_resp)
            MockGdelt.return_value = gdelt_inst

            from backend.scheduler.jobs import refresh_all_watch_zones
            await refresh_all_watch_zones(app)

        # Gaza errored, Ukraine and Sudan should still have been stored
        assert "Gaza" not in refreshed
        assert "Ukraine" in refreshed
        assert "Sudan" in refreshed

    async def test_no_sleep_when_only_one_zone_refreshed(self) -> None:
        """No sleep when only one zone needs a live refresh."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.api.schemas import AlertResponse

        zones = ["Gaza", "Ukraine"]
        app = self._make_app()

        gdelt_resp = MagicMock()
        gdelt_resp.articles = []
        gdelt_resp.aggregate_tone = 0.0
        app.state.alert_generator.generate.return_value = self._make_alert_output()

        fresh_alert = MagicMock(spec=AlertResponse)

        async def fake_cached(db_path, region, days=1):
            # Only Gaza is stale; Ukraine is fresh.
            return None if region == "Gaza" else fresh_alert

        with (
            patch("backend.scheduler.jobs.settings") as mock_settings,
            patch("backend.scheduler.jobs.store.get_cached_alert", side_effect=fake_cached),
            patch("backend.scheduler.jobs.store.upsert_alert", AsyncMock()),
            patch("backend.scheduler.jobs.GdeltCloudConnector") as MockCloud,
            patch("backend.scheduler.jobs.GdeltConnector") as MockGdelt,
            patch("backend.scheduler.jobs.score_severity", return_value=self._make_severity_result()),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_settings.WATCH_ZONES = zones
            mock_settings.GDELT_CLOUD_ALIASES = {}
            mock_settings.NO_FATALITIES_FILTER_COUNTRIES = set()

            cloud_inst = AsyncMock()
            cloud_inst.fetch_events = AsyncMock(return_value=[])
            MockCloud.return_value = cloud_inst

            gdelt_inst = AsyncMock()
            gdelt_inst.fetch_articles = AsyncMock(return_value=gdelt_resp)
            MockGdelt.return_value = gdelt_inst

            from backend.scheduler.jobs import refresh_all_watch_zones
            await refresh_all_watch_zones(app)

        mock_sleep.assert_not_called()
