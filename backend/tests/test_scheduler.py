"""Tests for backend/scheduler/store.py — SQLite persistence layer.

All tests use tmp_path (real temp file) so aiosqlite connections
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
