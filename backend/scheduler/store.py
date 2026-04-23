"""SQLite persistence for background alert scheduler.

Stores the latest alert per region so GET /alerts/{region} can serve
cached data instantly for known watch zones without hitting GDELT Cloud
or Gemma 4 on every request.

Schema: one row per region (region TEXT PRIMARY KEY). upsert_alert
replaces the existing row in-place so the table never grows beyond
len(WATCH_ZONES) rows.

Cache freshness: get_cached_alert compares created_at (UTC ISO-8601)
against now - max_age_hours. String comparison is correct here because
all timestamps are UTC ISO-8601 and therefore lexicographically ordered.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aiosqlite

from backend.api.schemas import AlertResponse
from backend.security.output_validator import Citation

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    region           TEXT PRIMARY KEY,
    severity         TEXT NOT NULL,
    summary          TEXT NOT NULL,
    source_citations TEXT NOT NULL,
    confidence       REAL NOT NULL,
    score            REAL NOT NULL,
    timestamp        TEXT NOT NULL,
    created_at       TEXT NOT NULL
)
"""

_UPSERT_SQL = """
INSERT INTO alerts (region, severity, summary, source_citations,
                    confidence, score, timestamp, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(region) DO UPDATE SET
    severity         = excluded.severity,
    summary          = excluded.summary,
    source_citations = excluded.source_citations,
    confidence       = excluded.confidence,
    score            = excluded.score,
    timestamp        = excluded.timestamp,
    created_at       = excluded.created_at
"""

_SELECT_FRESH_SQL = """
SELECT * FROM alerts WHERE region = ? AND created_at > ?
"""

_SELECT_ALL_ORDERED_SQL = """
SELECT * FROM alerts
ORDER BY CASE severity
    WHEN 'CRITICAL' THEN 0
    WHEN 'RED'      THEN 1
    WHEN 'AMBER'    THEN 2
    WHEN 'GREEN'    THEN 3
    ELSE 4
END
"""


async def init_db(db_path: str) -> None:
    """Create the alerts table if it does not already exist."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_CREATE_TABLE_SQL)
        await db.commit()


async def upsert_alert(
    db_path: str,
    *,
    region: str,
    severity: str,
    summary: str,
    source_citations: list[Citation],
    confidence: float,
    score: float,
    timestamp: str,
) -> None:
    """Insert or replace the alert row for *region*."""
    citations_json = json.dumps([c.model_dump() for c in source_citations])
    created_at = datetime.now(tz=timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            _UPSERT_SQL,
            (region, severity, summary, citations_json,
             confidence, score, timestamp, created_at),
        )
        await db.commit()


async def get_cached_alert(
    db_path: str,
    region: str,
    max_age_hours: float = 8.0,
) -> AlertResponse | None:
    """Return a fresh cached AlertResponse for *region*, or None if stale/missing."""
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(_SELECT_FRESH_SQL, (region, cutoff)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_alert_response(row)


async def get_latest_per_region(db_path: str) -> list[AlertResponse]:
    """Return the latest stored alert per region, ordered CRITICAL → RED → AMBER → GREEN."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(_SELECT_ALL_ORDERED_SQL) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_alert_response(row) for row in rows]


def _row_to_alert_response(row: aiosqlite.Row) -> AlertResponse:
    citations = [Citation(**c) for c in json.loads(row["source_citations"])]
    return AlertResponse(
        severity=row["severity"],
        summary=row["summary"],
        source_citations=citations,
        region=row["region"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        confidence=row["confidence"],
    )
